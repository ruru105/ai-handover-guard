"""保存済みのAI回答を、追加課金なしで監査・評価する。"""

from __future__ import annotations

import argparse
from pathlib import Path

from audit_rules import run_audit
from evaluate_predictions import run_evaluation
from excel_report import write_audit_xlsx


# ============================================================
# 1. 入出力パス
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AI_PREDICTIONS_PATH = PROJECT_ROOT / "output" / "ai_predictions.csv"
AUDIT_RESULT_PATH = PROJECT_ROOT / "output" / "audit_result.csv"
EXPECTED_PATH = PROJECT_ROOT / "data" / "expected_labels.csv"
SUMMARY_PATH = PROJECT_ROOT / "output" / "evaluation_summary.csv"
DETAIL_PATH = PROJECT_ROOT / "output" / "evaluation_details.csv"


# ============================================================
# 2. 監査・評価の一括実行
# ============================================================

def main() -> None:
    """AI出力を監査し、正解率と不一致明細を作る。"""

    parser = argparse.ArgumentParser(description="保存済みのAI回答を監査・評価する")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="回答のある件だけを採点する(10件試験など)。指定しない場合、回答がない件は不正解として数える",
    )
    args = parser.parse_args()

    if not AI_PREDICTIONS_PATH.exists():
        raise FileNotFoundError(
            "output/ai_predictions.csv がありません。先にAI試験を実行してください"
        )

    audited_records = run_audit(AI_PREDICTIONS_PATH, AUDIT_RESULT_PATH)
    write_audit_xlsx(AUDIT_RESULT_PATH.with_suffix(".xlsx"), audited_records)
    summary = run_evaluation(
        EXPECTED_PATH,
        AUDIT_RESULT_PATH,
        SUMMARY_PATH,
        DETAIL_PATH,
        args.allow_partial,
    )

    needs_review_count = sum(
        record["audit_status"] == "NEEDS_REVIEW" for record in audited_records
    )
    print("追加のAPI通信なしで監査・評価が完了しました")
    print(f"監査件数: {len(audited_records)}件")
    print(f"要確認: {needs_review_count}件")
    for row in summary:
        print(f"{row['metric']}: {row['correct']}/{row['total']} ({row['accuracy']})")
    print(f"不一致明細: {DETAIL_PATH}")
    print(f"Excel版の監査結果: {AUDIT_RESULT_PATH.with_suffix('.xlsx')}")


if __name__ == "__main__":
    main()
