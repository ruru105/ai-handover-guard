"""AIの抽出結果を人間が決めた正解表と比較する。"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, List, Tuple

from excel_report import excel_safe_text


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

# 回答がないrecord_idの明細に表示する言葉
MISSING_PREDICTION_LABEL = "(回答なし)"

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

    records: Dict[str, Dict[str, str]] = {}
    duplicates: List[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            record_id = row["record_id"]
            if record_id in records and record_id not in duplicates:
                duplicates.append(record_id)
            records[record_id] = row
    if duplicates:
        # 後の行で黙って上書きすると、どちらの行を採点したか分からなくなる
        raise ValueError(f"{path.name} に同じrecord_idが複数あります: {', '.join(duplicates)}")
    return records


# ============================================================
# 4. 正解率の計算
# ============================================================

def scored_ids(
    expected_by_id: Dict[str, Dict[str, str]],
    predicted_by_id: Dict[str, Dict[str, str]],
    allow_partial: bool = False,
) -> List[str]:
    """採点する対象のrecord_idを返す。

    ふだんは正解表の全件が対象で、AIの回答がないrecord_idは「不正解」として数える
    (回答が欠けた件だけを外すと、正解率が実際より高く見えてしまうため)。
    allow_partial=Trueのときだけ、回答のある件に限って採点する(10件試験など、一部だけ試すとき)。
    """

    if not expected_by_id:
        raise ValueError("正解表にrecord_idがありません")
    common_ids = sorted(set(expected_by_id) & set(predicted_by_id))
    if not common_ids:
        raise ValueError("比較可能なrecord_idがありません")
    return common_ids if allow_partial else sorted(expected_by_id)


def missing_and_extra_ids(
    expected_by_id: Dict[str, Dict[str, str]],
    predicted_by_id: Dict[str, Dict[str, str]],
) -> Tuple[List[str], List[str]]:
    """(回答がない正解表のID, 正解表にない回答のID)を返す。"""

    missing = sorted(set(expected_by_id) - set(predicted_by_id))
    extra = sorted(set(predicted_by_id) - set(expected_by_id))
    return missing, extra


def evaluate(
    expected_by_id: Dict[str, Dict[str, str]],
    predicted_by_id: Dict[str, Dict[str, str]],
    allow_partial: bool = False,
) -> List[Dict[str, str]]:
    """項目ごとの完全一致率と全項目一致率を計算する。

    回答がないrecord_idは、全項目「不一致」として分母に入れる(allow_partialのときを除く)。
    最後の行prediction_coverageは、正解表のうち回答があった件数を示す。
    """

    target_ids = scored_ids(expected_by_id, predicted_by_id, allow_partial)

    def matches(metric_name: str, record_id: str) -> bool:
        predicted = predicted_by_id.get(record_id)
        if predicted is None:
            return False  # 回答なしは、正解が空欄でも一致扱いにしない
        return metric_matches(metric_name, expected_by_id[record_id], predicted)

    summary: List[Dict[str, str]] = []
    for metric_name in METRIC_FIELDS:
        correct = sum(matches(metric_name, record_id) for record_id in target_ids)
        summary.append(
            {
                "metric": metric_name,
                "correct": str(correct),
                "total": str(len(target_ids)),
                "accuracy": f"{correct / len(target_ids):.3f}",
            }
        )

    for name, metrics in (
        ("all_fields_exact", ALL_FIELDS_EXACT),
        ("all_fields_normalized", ALL_FIELDS_NORMALIZED),
    ):
        correct = sum(
            all(matches(metric_name, record_id) for metric_name in metrics)
            for record_id in target_ids
        )
        summary.append(
            {
                "metric": name,
                "correct": str(correct),
                "total": str(len(target_ids)),
                "accuracy": f"{correct / len(target_ids):.3f}",
            }
        )

    answered = sum(record_id in predicted_by_id for record_id in expected_by_id)
    summary.append(
        {
            "metric": "prediction_coverage",
            "correct": str(answered),
            "total": str(len(expected_by_id)),
            "accuracy": f"{answered / len(expected_by_id):.3f}",
        }
    )
    return summary


def build_details(
    expected_by_id: Dict[str, Dict[str, str]],
    predicted_by_id: Dict[str, Dict[str, str]],
    allow_partial: bool = False,
) -> List[Dict[str, str]]:
    """各レコード・各項目の一致／不一致を確認できる明細を作る。回答がない件は「(回答なし)」で不一致。"""

    details: List[Dict[str, str]] = []
    for record_id in scored_ids(expected_by_id, predicted_by_id, allow_partial):
        predicted = predicted_by_id.get(record_id)
        for metric_name, (expected_field, predicted_field) in METRIC_FIELDS.items():
            expected_value = expected_by_id[record_id][expected_field].strip()
            if predicted is None:
                details.append(
                    {
                        "record_id": record_id,
                        "message_text": "",
                        "metric": metric_name,
                        "expected": expected_value,
                        "predicted": MISSING_PREDICTION_LABEL,
                        "match": "false",
                    }
                )
                continue
            details.append(
                {
                    "record_id": record_id,
                    "message_text": predicted.get("message_text", ""),
                    "metric": metric_name,
                    "expected": expected_value,
                    "predicted": predicted.get(predicted_field, "").strip(),
                    "match": str(
                        metric_matches(metric_name, expected_by_id[record_id], predicted)
                    ).lower(),
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
        # この明細は人がExcelで開いて確認するための出力で、プログラムは読み戻さない。
        # 申し送りの原文などが数式として実行されないよう、危険な文字で始まる文字の前に「'」を付ける。
        for detail in details:
            writer.writerow({key: excel_safe_text(value) for key, value in detail.items()})


def run_evaluation(
    expected_path: Path,
    prediction_path: Path,
    summary_path: Path,
    detail_path: Path,
    allow_partial: bool = False,
) -> List[Dict[str, str]]:
    """指定ファイルを比較し、集計と明細を書き出す。"""

    expected_by_id = read_by_id(expected_path)
    predicted_by_id = read_by_id(prediction_path)
    summary = evaluate(expected_by_id, predicted_by_id, allow_partial)
    details = build_details(expected_by_id, predicted_by_id, allow_partial)
    write_summary(summary_path, summary)
    write_details(detail_path, details)
    return summary


def print_id_report(expected_path: Path, prediction_path: Path, allow_partial: bool) -> None:
    """回答が欠けた件・正解表にない件を表示する。"""

    missing, extra = missing_and_extra_ids(read_by_id(expected_path), read_by_id(prediction_path))
    if missing:
        how = "採点から除外しました(--allow-partial)" if allow_partial else "不正解として数えました"
        print(f"回答がない正解表のID: {len(missing)}件を{how}")
    if extra:
        print(f"正解表にない回答のID: {len(extra)}件は採点しません")


def main() -> None:
    """正解表とAI回答を比較する。"""

    parser = argparse.ArgumentParser(description="AI抽出結果を正解表と比較する")
    parser.add_argument("--expected", type=Path, default=EXPECTED_PATH)
    parser.add_argument("--predictions", type=Path, default=PREDICTION_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--details", type=Path, default=DETAIL_PATH)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="回答のある件だけを採点する(10件試験など)。指定しない場合、回答がない件は不正解として数える",
    )
    args = parser.parse_args()

    summary = run_evaluation(
        args.expected,
        args.predictions,
        args.summary,
        args.details,
        args.allow_partial,
    )

    print("評価完了")
    print_id_report(args.expected, args.predictions, args.allow_partial)
    for row in summary:
        print(f"{row['metric']}: {row['correct']}/{row['total']} ({row['accuracy']})")
    print(f"集計: {args.summary.resolve()}")
    print(f"明細: {args.details.resolve()}")


if __name__ == "__main__":
    main()
