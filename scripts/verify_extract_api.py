"""APIの`/extract`(AIで抽出する入口)を、同梱の申し送り100件で確認する。有料API通信を行う。

`--yes`を付けたときだけ通信する(付けないと、予定を表示して終わる)。
100件を10件ずつ10回に分けて`/extract`へ送り、返った結果をCSVに保存して、正解表と比べる。
自動で再試行はしない。途中で失敗したら、そこで止まる(それまでの結果は保存する)。

既存の`output/ai_predictions.csv`(CLIで実行した結果)は上書きしない。
APIの結果は、別のファイル(`output/ai_predictions_api.csv`など)に保存する。
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evaluate_predictions import run_evaluation  # noqa: E402
from llm_extractor import MAX_TRIAL_RECORDS, read_trial_rows, write_csv  # noqa: E402


# ============================================================
# 1. 設定
# ============================================================

BATCH_SIZE = 10  # api.MAX_EXTRACT_RECORDS(1回の上限)と同じ
RAW_INPUT_PATH = PROJECT_ROOT / "data" / "raw_handover.csv"
EXPECTED_PATH = PROJECT_ROOT / "data" / "expected_labels.csv"
CLI_PREDICTIONS_PATH = PROJECT_ROOT / "output" / "ai_predictions.csv"
API_PREDICTIONS_PATH = PROJECT_ROOT / "output" / "ai_predictions_api.csv"
API_SUMMARY_PATH = PROJECT_ROOT / "output" / "evaluation_summary_api.csv"
API_DETAILS_PATH = PROJECT_ROOT / "output" / "evaluation_details_api.csv"

COMPARE_FIELDS = [
    "extracted_action",
    "extracted_assignee",
    "extracted_deadline",
    "extracted_priority",
    "action_required",
]


class VerificationError(Exception):
    """`/extract`の応答が想定と違うとき(原因が分かる文面にする)。"""


# ============================================================
# 2. 送信と検証
# ============================================================

def split_batches(rows: List[Dict[str, str]], size: int = BATCH_SIZE) -> List[List[Dict[str, str]]]:
    """件数を、1回の上限以内に分ける。"""

    if size < 1:
        raise ValueError("size は1以上にしてください")
    return [rows[start : start + size] for start in range(0, len(rows), size)]


def raw_request_record(row: Dict[str, str]) -> Dict[str, str]:
    """`/extract`に送る項目だけにする(正解や抽出結果は送らない)。"""

    return {
        "record_id": row["record_id"],
        "submitted_at": row["submitted_at"],
        "source_department": row.get("source_department", ""),
        "message_text": row["message_text"],
    }


def check_response(batch_number: int, batch: List[Dict[str, str]], response) -> Dict[str, object]:
    """応答が、200・件数一致・識別番号が送った順のまま、であることを確かめる。"""

    if response.status_code != 200:
        raise VerificationError(
            f"{batch_number}回目: HTTP {response.status_code} が返りました"
        )
    data = response.json()
    records = data.get("records", [])
    if data.get("count") != len(batch) or len(records) != len(batch):
        raise VerificationError(
            f"{batch_number}回目: 送った{len(batch)}件に対して、"
            f"件数が{data.get('count')}件・{len(records)}行で返りました"
        )
    sent_ids = [row["record_id"] for row in batch]
    got_ids = [record.get("record_id") for record in records]
    if sent_ids != got_ids:
        raise VerificationError(f"{batch_number}回目: 識別番号が、送った順と一致しません")
    return data


def run_batches(
    post: Callable[..., object],
    rows: List[Dict[str, str]],
    log: Callable[[str], None] = print,
) -> Tuple[List[Dict[str, str]], Dict[str, float], Optional[str]]:
    """10件ずつ`/extract`へ送る。失敗したらそこで止め、(結果の行, 使用量, エラー文)を返す。

    エラーがなければ、3つ目はNone。再試行はしない。
    """

    predictions: List[Dict[str, str]] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0, "requests": 0}
    batches = split_batches(rows)
    for number, batch in enumerate(batches, start=1):
        body = {
            "records": [raw_request_record(row) for row in batch],
            "confirm_paid_api": True,
        }
        try:
            response = post("/extract", json=body)
            data = check_response(number, batch, response)
        except VerificationError as error:
            return predictions, usage, str(error)
        except Exception as error:  # 通信の想定外。中身(キー等)は表示しない
            return predictions, usage, f"{number}回目: 送信に失敗しました({type(error).__name__})"

        predictions.extend(data["records"])
        usage["requests"] += 1
        usage["input_tokens"] += int(data["usage"]["input_tokens"])
        usage["output_tokens"] += int(data["usage"]["output_tokens"])
        cost = data["usage"].get("estimated_cost_usd")
        if cost is not None:
            usage["estimated_cost_usd"] += float(cost)
        log(f"  {number}/{len(batches)}回目: {len(batch)}件 完了")
    return predictions, usage, None


# ============================================================
# 3. CLIの結果との比較(参考)
# ============================================================

def compare_predictions(
    api_rows: List[Dict[str, str]], cli_rows: List[Dict[str, str]]
) -> Dict[str, object]:
    """同じ識別番号の行どうしで、抽出した項目が同じかを数える(参考。AIの答えは毎回少し変わる)。"""

    cli_by_id = {row["record_id"]: row for row in cli_rows}
    common = [row for row in api_rows if row["record_id"] in cli_by_id]
    same_all = 0
    per_field = {field: 0 for field in COMPARE_FIELDS}
    for row in common:
        other = cli_by_id[row["record_id"]]
        matches = {field: row.get(field, "") == other.get(field, "") for field in COMPARE_FIELDS}
        for field, matched in matches.items():
            per_field[field] += matched
        same_all += all(matches.values())
    return {"common": len(common), "same_all": same_all, "per_field": per_field}


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


# ============================================================
# 4. コマンド実行
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="/extractを同梱の申し送りで確認する(有料)")
    parser.add_argument("--input", type=Path, default=RAW_INPUT_PATH)
    parser.add_argument("--limit", type=int, default=MAX_TRIAL_RECORDS, help="送る件数(1〜100)")
    parser.add_argument("--output", type=Path, default=API_PREDICTIONS_PATH)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="有料のAPI通信を実行する(付けない場合は、予定を表示して終わる)",
    )
    args = parser.parse_args()

    rows = read_trial_rows(args.input, args.limit)
    batches = split_batches(rows)
    print(f"対象: {len(rows)}件 / {len(batches)}回に分けて /extract へ送ります(1回{BATCH_SIZE}件まで)")
    print(f"結果の保存先: {args.output}(既存の output/ai_predictions.csv は上書きしません)")
    if not args.yes:
        print("まだ通信していません。有料のAPI通信を実行するには、--yes を付けてください")
        return 0

    from fastapi.testclient import TestClient

    import api  # noqa: E402

    client = TestClient(api.app)
    print("有料のAPI通信を始めます(自動の再試行はしません)")
    started = time.time()
    predictions, usage, error = run_batches(client.post, rows)
    elapsed = time.time() - started

    if predictions:
        write_csv(args.output, predictions)
    print(
        f"送信 {usage['requests']}回 / 受け取り {len(predictions)}件 / "
        f"入力 {usage['input_tokens']}・出力 {usage['output_tokens']}トークン / "
        f"概算 ${usage['estimated_cost_usd']:.6f} / {elapsed:.0f}秒"
    )
    if error:
        print(f"エラー: {error}")
        print("そこで止めました(それまでの結果は保存しています。採点はしていません)")
        return 1

    allow_partial = len(rows) < MAX_TRIAL_RECORDS
    summary = run_evaluation(
        EXPECTED_PATH, args.output, API_SUMMARY_PATH, API_DETAILS_PATH, allow_partial
    )
    print("採点(APIの結果):")
    for row in summary:
        print(f"  {row['metric']}: {row['correct']}/{row['total']} ({row['accuracy']})")

    if CLI_PREDICTIONS_PATH.exists():
        result = compare_predictions(predictions, read_rows(CLI_PREDICTIONS_PATH))
        print(
            f"CLIの結果との比較(参考): 共通{result['common']}件のうち、"
            f"5項目すべて同じ {result['same_all']}件"
        )
        for field, count in result["per_field"].items():
            print(f"  {field}: {count}/{result['common']}")
    print(f"採点の明細: {API_DETAILS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
