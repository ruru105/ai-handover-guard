"""AI Handover Guard V0.3 - 申し送り抽出結果の監査。"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, Iterable, List


# ============================================================
# 1. パス設定
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "mock_ai_output.csv"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "output" / "audit_result.csv"

REQUIRED_ACTION_FIELDS = {
    "extracted_action": "作業内容",
    "extracted_assignee": "担当者",
    "extracted_deadline": "期限",
}

# 原文に明確なリスクがある場合に使う一般化キーワード
HIGH_PRIORITY_KEYWORDS = (
    "至急",
    "緊急",
    "事故",
    "安全",
    "異音",
    "停止",
    "予備電源",
    "未到着",
    "届いていません",
)

# 通常業務として扱う代表的なキーワード
LOW_PRIORITY_KEYWORDS = (
    "共有のみ",
    "対応不要",
    "照明",
    "連絡網",
    "定例",
)


# ============================================================
# 2. 共通変換
# ============================================================

def to_bool(value: str) -> bool:
    """CSVの真偽値表現をPythonのboolへ変換する。"""

    return value.strip().lower() in {"true", "1", "yes", "y"}


def determine_rule_priority(record: Dict[str, str]) -> str:
    """原文と対応要否からPython側の優先度を独立判定する。"""

    if not to_bool(record.get("action_required", "")):
        return "低"

    source_text = " ".join(
        (
            record.get("message_text", ""),
            record.get("extracted_action", ""),
        )
    )
    if any(keyword in source_text for keyword in HIGH_PRIORITY_KEYWORDS):
        return "高"
    if any(keyword in source_text for keyword in LOW_PRIORITY_KEYWORDS):
        return "低"
    return "中"


def add_priority_audit(result: Dict[str, str]) -> None:
    """AI優先度とPython判定を比較し、独立した監査結果を追加する。"""

    ai_priority = result.get("extracted_priority", "").strip()
    rule_priority = determine_rule_priority(result)
    result["rule_priority"] = rule_priority

    if ai_priority == rule_priority:
        result["priority_audit_status"] = "MATCH"
        result["priority_audit_message"] = "AI判定とPython判定が一致しています"
    else:
        result["priority_audit_status"] = "NEEDS_REVIEW"
        result["priority_audit_message"] = (
            f"優先度要確認：AI={ai_priority or '未設定'} / Python={rule_priority}"
        )


# ============================================================
# 3. 1件分の監査
# ============================================================

def audit_record(record: Dict[str, str]) -> Dict[str, str]:
    """1件の申し送りを監査し、状態と警告理由を追加する。"""

    result = dict(record)
    add_priority_audit(result)

    if not to_bool(record.get("action_required", "")):
        result["audit_status"] = "INFO_ONLY"
        result["missing_fields"] = ""
        result["audit_message"] = "対応不要の共有情報です"
        return result

    missing_labels = [
        label
        for field_name, label in REQUIRED_ACTION_FIELDS.items()
        if not record.get(field_name, "").strip()
    ]

    if missing_labels:
        result["audit_status"] = "NEEDS_REVIEW"
        result["missing_fields"] = " / ".join(missing_labels)
        result["audit_message"] = f"要確認：{'・'.join(missing_labels)}が未確定です"
    else:
        result["audit_status"] = "READY"
        result["missing_fields"] = ""
        result["audit_message"] = "実行に必要な基本項目がそろっています"

    return result


# ============================================================
# 4. 複数件の監査
# ============================================================

def audit_records(records: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    """複数の申し送りをまとめて監査する。"""

    return [audit_record(record) for record in records]


# ============================================================
# 5. CSV入出力
# ============================================================

def read_csv(path: Path) -> List[Dict[str, str]]:
    """UTF-8 BOM対応でCSVを読み込む。"""

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def write_csv(path: Path, records: List[Dict[str, str]]) -> None:
    """監査結果をExcelでも開きやすいUTF-8 BOM付きCSVへ出力する。"""

    if not records:
        raise ValueError("出力対象のデータがありません")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


# ============================================================
# 6. メイン処理
# ============================================================

def run_audit(input_path: Path, output_path: Path) -> List[Dict[str, str]]:
    """指定CSVを監査して結果を書き出す。"""

    source_records = read_csv(input_path)
    audited_records = audit_records(source_records)
    write_csv(output_path, audited_records)
    return audited_records


def main() -> None:
    """指定データを監査し、結果CSVを作成する。"""

    parser = argparse.ArgumentParser(description="AI抽出結果の不足項目を監査する")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    audited_records = run_audit(args.input, args.output)

    needs_review_count = sum(
        record["audit_status"] == "NEEDS_REVIEW" for record in audited_records
    )
    print(f"監査完了: {len(audited_records)}件")
    print(f"要確認: {needs_review_count}件")
    print(f"出力先: {args.output.resolve()}")


if __name__ == "__main__":
    main()
