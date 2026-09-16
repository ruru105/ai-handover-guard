"""AIの抽出結果を人間が決めた正解表と比較する。"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, List


# ============================================================
# 1. パスと比較項目
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PATH = PROJECT_ROOT / "data" / "expected_labels.csv"
PREDICTION_PATH = PROJECT_ROOT / "output" / "audit_result.csv"
SUMMARY_PATH = PROJECT_ROOT / "output" / "evaluation_summary.csv"
DETAIL_PATH = PROJECT_ROOT / "output" / "evaluation_details.csv"

METRIC_FIELDS = {
    "action_exact": ("expected_action", "extracted_action"),
    "action_normalized": ("expected_action", "extracted_action"),
    "assignee": ("expected_assignee", "extracted_assignee"),
    "deadline": ("expected_deadline", "extracted_deadline"),
    "priority_ai": ("expected_priority", "extracted_priority"),
    "priority_rule": ("expected_priority", "rule_priority"),
    "action_required": ("expected_action_required", "action_required"),
    "audit_status": ("expected_audit_status", "audit_status"),
}

ALL_FIELDS_EXACT = (
    "action_exact",
    "assignee",
    "deadline",
    "priority_ai",
    "action_required",
    "audit_status",
)

ALL_FIELDS_NORMALIZED = (
    "action_normalized",
    "assignee",
    "deadline",
    "priority_ai",
    "action_required",
    "audit_status",
)


# ============================================================
# 2. 比較前の正規化
# ============================================================

def normalize_assignee(value: str) -> str:
    """採点時に担当者名の一般的な敬称を無視する。"""

    normalized = value.strip()
    for suffix in ("さん", "様", "氏"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)].strip()
    return normalized


def normalize_action(value: str) -> str:
    """空白・句読点・末尾の「する」をそろえて作業表現を比較する。"""

    normalized = re.sub(r"[\s　。、，,]+", "", value.strip())
    if normalized.endswith("する"):
        normalized = normalized[:-2]
    return normalized


def normalize_value(metric_name: str, value: str) -> str:
    """項目ごとの採点ルールに沿って値をそろえる。"""

    normalized = value.strip()
    if metric_name == "action_normalized":
        return normalize_action(normalized)
    if metric_name == "assignee":
        return normalize_assignee(normalized)
    if metric_name == "action_required":
        return normalized.lower()
    return normalized


def metric_matches(
    metric_name: str,
    expected_record: Dict[str, str],
    predicted_record: Dict[str, str],
) -> bool:
    """指定した評価項目が一致するか判定する。"""

    expected_field, predicted_field = METRIC_FIELDS[metric_name]
    return normalize_value(metric_name, expected_record.get(expected_field, "")) == (
        normalize_value(metric_name, predicted_record.get(predicted_field, ""))
    )


# ============================================================
# 3. CSV読み込み
# ============================================================

def read_by_id(path: Path) -> Dict[str, Dict[str, str]]:
    """CSVをrecord_idをキーとした辞書で読み込む。"""

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return {row["record_id"]: row for row in csv.DictReader(csv_file)}


# ============================================================
# 4. 正解率の計算
# ============================================================

def evaluate(
    expected_by_id: Dict[str, Dict[str, str]],
    predicted_by_id: Dict[str, Dict[str, str]],
) -> List[Dict[str, str]]:
    """項目ごとの完全一致率と全項目一致率を計算する。"""

    common_ids = sorted(set(expected_by_id) & set(predicted_by_id))
    if not common_ids:
        raise ValueError("比較可能なrecord_idがありません")

    summary: List[Dict[str, str]] = []
    for metric_name in METRIC_FIELDS:
        correct = sum(
            metric_matches(
                metric_name,
                expected_by_id[record_id],
                predicted_by_id[record_id],
            )
            for record_id in common_ids
        )
        summary.append(
            {
                "metric": metric_name,
                "correct": str(correct),
                "total": str(len(common_ids)),
                "accuracy": f"{correct / len(common_ids):.3f}",
            }
        )

    all_fields_exact = sum(
        all(
            metric_matches(
                metric_name,
                expected_by_id[record_id],
                predicted_by_id[record_id],
            )
            for metric_name in ALL_FIELDS_EXACT
        )
        for record_id in common_ids
    )
    summary.append(
        {
            "metric": "all_fields_exact",
            "correct": str(all_fields_exact),
            "total": str(len(common_ids)),
            "accuracy": f"{all_fields_exact / len(common_ids):.3f}",
        }
    )

    all_fields_normalized = sum(
        all(
            metric_matches(
                metric_name,
                expected_by_id[record_id],
                predicted_by_id[record_id],
            )
            for metric_name in ALL_FIELDS_NORMALIZED
        )
        for record_id in common_ids
    )
    summary.append(
        {
            "metric": "all_fields_normalized",
            "correct": str(all_fields_normalized),
            "total": str(len(common_ids)),
            "accuracy": f"{all_fields_normalized / len(common_ids):.3f}",
        }
    )
    return summary


def build_details(
    expected_by_id: Dict[str, Dict[str, str]],
    predicted_by_id: Dict[str, Dict[str, str]],
) -> List[Dict[str, str]]:
    """各レコード・各項目の一致／不一致を確認できる明細を作る。"""

    details: List[Dict[str, str]] = []
    common_ids = sorted(set(expected_by_id) & set(predicted_by_id))
    for record_id in common_ids:
        predicted = predicted_by_id[record_id]
        for metric_name, (expected_field, predicted_field) in METRIC_FIELDS.items():
            expected_value = expected_by_id[record_id][expected_field].strip()
            predicted_value = predicted.get(predicted_field, "").strip()
            is_match = metric_matches(
                metric_name,
                expected_by_id[record_id],
                predicted,
            )
            details.append(
                {
                    "record_id": record_id,
                    "message_text": predicted.get("message_text", ""),
                    "metric": metric_name,
                    "expected": expected_value,
                    "predicted": predicted_value,
                    "match": str(is_match).lower(),
                }
            )
    return details


# ============================================================
# 5. 評価結果の出力
# ============================================================

def write_summary(path: Path, summary: List[Dict[str, str]]) -> None:
    """評価結果をCSVへ保存する。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["metric", "correct", "total", "accuracy"],
        )
        writer.writeheader()
        writer.writerows(summary)


def write_details(path: Path, details: List[Dict[str, str]]) -> None:
    """項目別の比較明細をCSVへ保存する。"""

    if not details:
        raise ValueError("比較明細がありません")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(details[0].keys()))
        writer.writeheader()
        writer.writerows(details)


def run_evaluation(
    expected_path: Path,
    prediction_path: Path,
    summary_path: Path,
    detail_path: Path,
) -> List[Dict[str, str]]:
    """指定ファイルを比較し、集計と明細を書き出す。"""

    expected_by_id = read_by_id(expected_path)
    predicted_by_id = read_by_id(prediction_path)
    summary = evaluate(expected_by_id, predicted_by_id)
    details = build_details(expected_by_id, predicted_by_id)
    write_summary(summary_path, summary)
    write_details(detail_path, details)
    return summary


def main() -> None:
    """正解表とAI回答を比較する。"""

    parser = argparse.ArgumentParser(description="AI抽出結果を正解表と比較する")
    parser.add_argument("--expected", type=Path, default=EXPECTED_PATH)
    parser.add_argument("--predictions", type=Path, default=PREDICTION_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--details", type=Path, default=DETAIL_PATH)
    args = parser.parse_args()

    summary = run_evaluation(
        args.expected,
        args.predictions,
        args.summary,
        args.details,
    )

    print("評価完了")
    for row in summary:
        print(f"{row['metric']}: {row['correct']}/{row['total']} ({row['accuracy']})")
    print(f"集計: {args.summary.resolve()}")
    print(f"明細: {args.details.resolve()}")


if __name__ == "__main__":
    main()
