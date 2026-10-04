"""AI回答の採点機能テスト。"""

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import csv  # noqa: E402
import tempfile  # noqa: E402

from evaluate_predictions import (  # noqa: E402
    MISSING_PREDICTION_LABEL,
    build_details,
    evaluate,
    missing_and_extra_ids,
    read_by_id,
    run_evaluation,
    write_details,
)


class EvaluatePredictionsTest(unittest.TestCase):
    """正解・不正解が正しく集計されるか確認する。"""

    def test_calculates_field_and_all_field_accuracy(self) -> None:
        expected = {
            "H001": {
                "expected_action": "確認する",
                "expected_assignee": "田中",
                "expected_deadline": "17時",
                "expected_priority": "中",
                "expected_action_required": "true",
                "expected_audit_status": "READY",
            }
        }
        predicted = {
            "H001": {
                "extracted_action": "確認する",
                "extracted_assignee": "田中",
                "extracted_deadline": "",
                "extracted_priority": "中",
                "rule_priority": "中",
                "action_required": "true",
                "audit_status": "NEEDS_REVIEW",
            }
        }

        summary = {row["metric"]: row for row in evaluate(expected, predicted)}

        self.assertEqual(summary["action_exact"]["accuracy"], "1.000")
        self.assertEqual(summary["action_normalized"]["accuracy"], "1.000")
        self.assertEqual(summary["deadline"]["accuracy"], "0.000")
        self.assertEqual(summary["all_fields_exact"]["accuracy"], "0.000")

    def test_assignee_honorific_is_ignored(self) -> None:
        expected = {
            "H001": {
                "expected_action": "確認する",
                "expected_assignee": "田中",
                "expected_deadline": "17時",
                "expected_priority": "中",
                "expected_action_required": "true",
                "expected_audit_status": "READY",
            }
        }
        predicted = {
            "H001": {
                "message_text": "田中さんが確認する",
                "extracted_action": "確認する",
                "extracted_assignee": "田中さん",
                "extracted_deadline": "17時",
                "extracted_priority": "中",
                "rule_priority": "中",
                "action_required": "true",
                "audit_status": "READY",
            }
        }

        summary = {row["metric"]: row for row in evaluate(expected, predicted)}
        details = build_details(expected, predicted)

        self.assertEqual(summary["assignee"]["accuracy"], "1.000")
        self.assertEqual(summary["all_fields_exact"]["accuracy"], "1.000")
        assignee_detail = next(row for row in details if row["metric"] == "assignee")
        self.assertEqual(assignee_detail["match"], "true")

    def test_action_normalized_ignores_suru_ending(self) -> None:
        expected = {
            "H001": {
                "expected_action": "在庫を確認する",
                "expected_assignee": "田中",
                "expected_deadline": "17時",
                "expected_priority": "中",
                "expected_action_required": "true",
                "expected_audit_status": "READY",
            }
        }
        predicted = {
            "H001": {
                "extracted_action": "在庫を確認",
                "extracted_assignee": "田中",
                "extracted_deadline": "17時",
                "extracted_priority": "中",
                "rule_priority": "中",
                "action_required": "true",
                "audit_status": "READY",
            }
        }

        summary = {row["metric"]: row for row in evaluate(expected, predicted)}

        self.assertEqual(summary["action_exact"]["accuracy"], "0.000")
        self.assertEqual(summary["action_normalized"]["accuracy"], "1.000")
        self.assertEqual(summary["all_fields_normalized"]["accuracy"], "1.000")


def _expected(action_required: str = "true", deadline: str = "17時") -> dict:
    return {
        "expected_action": "確認する",
        "expected_assignee": "田中",
        "expected_deadline": deadline,
        "expected_priority": "中",
        "expected_action_required": action_required,
        "expected_audit_status": "READY",
    }


def _correct_prediction(deadline: str = "17時") -> dict:
    return {
        "extracted_action": "確認する",
        "extracted_assignee": "田中",
        "extracted_deadline": deadline,
        "extracted_priority": "中",
        "rule_priority": "中",
        "action_required": "true",
        "audit_status": "READY",
    }


class MissingPredictionTest(unittest.TestCase):
    """回答が欠けたrecord_idを分母から外さず、不正解として数えること(H06)。"""

    def setUp(self) -> None:
        self.expected = {f"H00{i}": _expected() for i in range(1, 5)}
        self.predicted = {f"H00{i}": _correct_prediction() for i in range(1, 3)}  # H003・H004が欠落

    def test_missing_predictions_count_as_wrong(self) -> None:
        summary = {row["metric"]: row for row in evaluate(self.expected, self.predicted)}
        self.assertEqual(summary["all_fields_exact"]["correct"], "2")
        self.assertEqual(summary["all_fields_exact"]["total"], "4")
        self.assertEqual(summary["all_fields_exact"]["accuracy"], "0.500")
        self.assertEqual(summary["assignee"]["total"], "4")

    def test_prediction_coverage_row(self) -> None:
        summary = {row["metric"]: row for row in evaluate(self.expected, self.predicted)}
        self.assertEqual(summary["prediction_coverage"]["correct"], "2")
        self.assertEqual(summary["prediction_coverage"]["total"], "4")

    def test_missing_prediction_is_wrong_even_when_expected_is_blank(self) -> None:
        # 正解が空欄の項目で、回答なし(空)と偶然一致しないこと
        expected = {"H001": _expected(deadline=""), "H002": _expected(deadline="")}
        predicted = {"H001": _correct_prediction(deadline="")}
        summary = {row["metric"]: row for row in evaluate(expected, predicted)}
        self.assertEqual(summary["deadline"]["correct"], "1")
        self.assertEqual(summary["deadline"]["total"], "2")

    def test_allow_partial_scores_only_answered_records(self) -> None:
        summary = {
            row["metric"]: row
            for row in evaluate(self.expected, self.predicted, allow_partial=True)
        }
        self.assertEqual(summary["all_fields_exact"]["total"], "2")
        self.assertEqual(summary["all_fields_exact"]["accuracy"], "1.000")
        # 一部だけの採点であることは、coverageで分かる
        self.assertEqual(summary["prediction_coverage"]["total"], "4")
        self.assertEqual(summary["prediction_coverage"]["correct"], "2")

    def test_details_list_missing_records_as_mismatch(self) -> None:
        details = build_details(self.expected, self.predicted)
        missing_rows = [row for row in details if row["record_id"] == "H003"]
        self.assertEqual(len(missing_rows), 8)
        self.assertTrue(all(row["match"] == "false" for row in missing_rows))
        self.assertTrue(all(row["predicted"] == MISSING_PREDICTION_LABEL for row in missing_rows))
        self.assertFalse(any(row["record_id"] == "H003" for row in build_details(
            self.expected, self.predicted, allow_partial=True)))

    def test_missing_and_extra_ids(self) -> None:
        predicted = dict(self.predicted)
        predicted["X999"] = _correct_prediction()
        missing, extra = missing_and_extra_ids(self.expected, predicted)
        self.assertEqual(missing, ["H003", "H004"])
        self.assertEqual(extra, ["X999"])

    def test_extra_predictions_are_not_scored(self) -> None:
        predicted = {f"H00{i}": _correct_prediction() for i in range(1, 5)}
        predicted["X999"] = _correct_prediction()
        summary = {row["metric"]: row for row in evaluate(self.expected, predicted)}
        self.assertEqual(summary["all_fields_exact"]["total"], "4")

    def test_no_overlap_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            evaluate(self.expected, {"X999": _correct_prediction()})


class DuplicateIdTest(unittest.TestCase):
    def test_duplicate_record_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pred.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["record_id", "extracted_action"])
                writer.writeheader()
                writer.writerow({"record_id": "A1", "extracted_action": "x"})
                writer.writerow({"record_id": "A1", "extracted_action": "y"})
            with self.assertRaises(ValueError) as caught:
                read_by_id(path)
            self.assertIn("A1", str(caught.exception))


class RunEvaluationTest(unittest.TestCase):
    def test_summary_file_counts_missing_as_wrong_by_default(self) -> None:
        fields_expected = ["record_id"] + list(_expected())
        fields_pred = ["record_id", "message_text"] + list(_correct_prediction())
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with (tmp_path / "expected.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields_expected)
                writer.writeheader()
                for record_id in ("A1", "A2"):
                    writer.writerow({"record_id": record_id, **_expected()})
            with (tmp_path / "pred.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields_pred)
                writer.writeheader()
                writer.writerow({"record_id": "A1", "message_text": "m", **_correct_prediction()})

            summary = run_evaluation(
                tmp_path / "expected.csv", tmp_path / "pred.csv",
                tmp_path / "s.csv", tmp_path / "d.csv",
            )
            partial = run_evaluation(
                tmp_path / "expected.csv", tmp_path / "pred.csv",
                tmp_path / "s2.csv", tmp_path / "d2.csv", allow_partial=True,
            )

        strict = {row["metric"]: row for row in summary}
        lenient = {row["metric"]: row for row in partial}
        self.assertEqual((strict["all_fields_exact"]["correct"], strict["all_fields_exact"]["total"]), ("1", "2"))
        self.assertEqual((lenient["all_fields_exact"]["correct"], lenient["all_fields_exact"]["total"]), ("1", "1"))


class DetailsCsvSafetyTest(unittest.TestCase):
    def test_details_csv_neutralizes_formulas_but_summary_keeps_numbers(self) -> None:
        details = [
            {"record_id": "A1", "message_text": "=1+1", "metric": "assignee",
             "expected": "田中", "predicted": "@SUM(1)", "match": "false"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "details.csv"
            write_details(path, details)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
        self.assertEqual(row["message_text"], "'=1+1")
        self.assertEqual(row["predicted"], "'@SUM(1)")
        self.assertEqual(row["expected"], "田中")
        self.assertEqual(row["match"], "false")


if __name__ == "__main__":
    unittest.main()
