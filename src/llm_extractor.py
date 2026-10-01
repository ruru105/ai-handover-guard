"""OpenAI APIで申し送り原文を構造化する安全な試験(既定10件、上限100件)。"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Dict, List


# ============================================================
# 1. 安全設定
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "raw_handover.csv"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "output" / "ai_predictions.csv"
USAGE_OUTPUT_PATH = PROJECT_ROOT / "output" / "api_usage.csv"
DEFAULT_MODEL = "gpt-5.6-luna"
MAX_TRIAL_RECORDS = 100
MAX_OUTPUT_TOKENS_PER_RECORD = 300

# 2026-09-16時点のGPT-5.6 Luna標準API料金（100万トークン当たり）
LUNA_INPUT_USD_PER_MILLION = 0.20
LUNA_OUTPUT_USD_PER_MILLION = 1.20

SYSTEM_PROMPT = """あなたは職場の申し送り文章を構造化する抽出担当です。
原文に明記されている内容だけを使い、作業内容・担当者・期限・緊急度・対応要否を抽出してください。

必須ルール：
- 原文にない担当者・期限・作業を推測しない。
- 不明な文字列項目は空文字にする。
- 「至急」「早めに」だけでは具体的な期限とせず、期限は空文字にする。
- 相対的な期限は、登録日時を基準に YYYY-MM-DD HH:MM 形式へ変換する。
- 「共有のみ」「対応不要」と明記されている場合、対応要否はfalseにする。
- 対応要否がfalseの場合、作業内容・担当者・期限は空文字にする。
- 担当者名の「さん」「様」「氏」などの敬称は除いて出力する。
- 緊急度は必ず「低」「中」「高」のいずれかにする。
- 高：事故、安全問題、重要設備停止、配送停止、明確な至急など、即時確認が必要な内容。
- 設備の異音、予備電源の燃料不足は、安全・継続運転リスクとして「高」にする。
- 中：業務への影響があり、具体的な期限までに確認・対応する内容。
- 低：通常点検、軽微な交換、定例更新、共有情報など、緊急性が明記されていない内容。
- 連絡網などの定例更新は、明確な緊急表現がなければ「低」にする。
- 期限があるだけで機械的に高へ上げない。作業の影響と原文の緊急表現を優先する。
- 説明や補足は追加しない。"""


# ============================================================
# 2. 入力文と出力行の作成
# ============================================================

def make_user_message(record: Dict[str, str]) -> str:
    """1件のCSVデータをAIへ渡す文章へ変換する。"""

    return (
        f"登録日時: {record['submitted_at']}\n"
        f"発信部署: {record['source_department']}\n"
        f"申し送り: {record['message_text']}"
    )


def normalize_assignee(value: object) -> str:
    """担当者名の末尾に付いた一般的な敬称を除く。"""

    normalized = str(value).strip()
    for suffix in ("さん", "様", "氏"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)].strip()
    return normalized


def normalize_extraction(parsed: Dict[str, object]) -> Dict[str, object]:
    """AI出力を監査しやすい一定形式へ正規化する。"""

    normalized = dict(parsed)
    normalized["assignee"] = normalize_assignee(parsed.get("assignee", ""))

    if not bool(parsed.get("action_required", False)):
        normalized["action"] = ""
        normalized["assignee"] = ""
        normalized["deadline"] = ""

    return normalized


def make_output_row(record: Dict[str, str], parsed: Dict[str, object]) -> Dict[str, str]:
    """構造化されたAI回答を監査用CSVの1行へ変換する。"""

    normalized = normalize_extraction(parsed)

    return {
        **record,
        "extracted_action": str(normalized["action"]),
        "extracted_assignee": str(normalized["assignee"]),
        "extracted_deadline": str(normalized["deadline"]),
        "extracted_priority": str(normalized["priority"]),
        "action_required": str(normalized["action_required"]).lower(),
    }


# ============================================================
# 3. 料金概算
# ============================================================

def estimate_luna_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """Luna標準処理の概算料金を米ドルで計算する。"""

    input_cost = input_tokens / 1_000_000 * LUNA_INPUT_USD_PER_MILLION
    output_cost = output_tokens / 1_000_000 * LUNA_OUTPUT_USD_PER_MILLION
    return input_cost + output_cost


# ============================================================
# 4. CSV入出力
# ============================================================

def read_trial_rows(path: Path, limit: int) -> List[Dict[str, str]]:
    """試験データを読み込み、安全上限の100件以内に制限する。"""

    if not 1 <= limit <= MAX_TRIAL_RECORDS:
        raise ValueError(f"試験実行は1～{MAX_TRIAL_RECORDS}件に制限されています")

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))[:limit]


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    """辞書の一覧をUTF-8 BOM付きCSVへ保存する。"""

    if not rows:
        raise ValueError("出力対象のデータがありません")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# 5. 有料API実行
# ============================================================

def execute_paid_trial(rows: List[Dict[str, str]], model: str) -> None:
    """明示的に許可された場合だけ、最大100件をAPIへ送信する。"""

    from dotenv import load_dotenv
    from openai import OpenAI
    from pydantic import BaseModel
    from typing import Literal

    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEYが設定されていません")

    class HandoverExtraction(BaseModel):
        action: str
        assignee: str
        deadline: str
        priority: Literal["低", "中", "高"]
        action_required: bool

    # 自動再試行を無効にして、想定外の重複課金を防ぐ
    client = OpenAI(max_retries=0, timeout=30.0)
    output_rows: List[Dict[str, str]] = []
    total_input_tokens = 0
    total_output_tokens = 0

    for record in rows:
        response = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": make_user_message(record)},
            ],
            text_format=HandoverExtraction,
            max_output_tokens=MAX_OUTPUT_TOKENS_PER_RECORD,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError(f"{record['record_id']}の構造化結果を取得できませんでした")

        output_rows.append(make_output_row(record, parsed.model_dump()))
        if response.usage:
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

    write_csv(DEFAULT_OUTPUT_PATH, output_rows)

    cost_usd = (
        estimate_luna_cost_usd(total_input_tokens, total_output_tokens)
        if model == DEFAULT_MODEL
        else 0.0
    )
    usage_rows = [
        {
            "model": model,
            "records": str(len(output_rows)),
            "input_tokens": str(total_input_tokens),
            "output_tokens": str(total_output_tokens),
            "estimated_cost_usd": f"{cost_usd:.6f}" if cost_usd else "要別計算",
        }
    ]
    write_csv(USAGE_OUTPUT_PATH, usage_rows)

    print(f"API処理完了: {len(output_rows)}件")
    print(f"入力トークン: {total_input_tokens}")
    print(f"出力トークン: {total_output_tokens}")
    print(f"概算料金: ${cost_usd:.6f}" if cost_usd else "概算料金: 要別計算")


# ============================================================
# 6. コマンド実行
# ============================================================

def main() -> None:
    """通常は確認だけ行い、--execute指定時だけ有料APIを呼び出す。"""

    parser = argparse.ArgumentParser(description="申し送りAI抽出の試験(既定10件、上限100件)")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="有料API通信を実行する",
    )
    args = parser.parse_args()

    rows = read_trial_rows(DEFAULT_INPUT_PATH, args.limit)
    if not args.execute:
        print("安全確認モード：API通信は行っていません")
        print(f"予定モデル: {args.model}")
        print(f"予定件数: {len(rows)}件")
        print("実行する場合のみ --execute を付けてください")
        return

    execute_paid_trial(rows, args.model)


if __name__ == "__main__":
    main()
