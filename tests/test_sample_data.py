"""試験用申し送りデータの品質確認。"""

import csv
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PATH = PROJECT_ROOT / "data" / "mock_ai_output.csv"
RAW_INPUT_PATH = PROJECT_ROOT / "data" / "raw_handover.csv"
EXPECTED_LABELS_PATH = PROJECT_ROOT / "data" / "expected_labels.csv"


class SampleDataTest(unittest.TestCase):
    """100件の試験データが正しく用意されているか確認する。"""

    @classmethod
    def setUpClass(cls) -> None:
        with SAMPLE_PATH.open("r", encoding="utf-8-sig", newline="") as csv_file:
            cls.rows = list(csv.DictReader(csv_file))

    def test_contains_exactly_100_records(self) -> None:
        self.assertEqual(len(self.rows), 100)

    def test_record_ids_are_unique(self) -> None:
        record_ids = [row["record_id"] for row in self.rows]
        self.assertEqual(len(record_ids), len(set(record_ids)))

    def test_contains_action_and_information_records(self) -> None:
        action_values = {row["action_required"] for row in self.rows}
        self.assertEqual(action_values, {"true", "false"})

    def test_contains_all_audit_patterns(self) -> None:
        has_complete = any(
            row["action_required"] == "true"
            and row["extracted_action"]
            and row["extracted_assignee"]
            and row["extracted_deadline"]
            for row in self.rows
        )
        has_missing = any(
            row["action_required"] == "true"
            and (
                not row["extracted_action"]
                or not row["extracted_assignee"]
                or not row["extracted_deadline"]
            )
            for row in self.rows
        )
        self.assertTrue(has_complete)
        self.assertTrue(has_missing)

    def test_raw_input_does_not_contain_answer_columns(self) -> None:
        with RAW_INPUT_PATH.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            raw_rows = list(reader)
            raw_columns = set(reader.fieldnames or [])

        self.assertEqual(len(raw_rows), 100)
        self.assertNotIn("extracted_action", raw_columns)
        self.assertNotIn("expected_action", raw_columns)

    def test_expected_labels_match_record_ids(self) -> None:
        with EXPECTED_LABELS_PATH.open(
            "r", encoding="utf-8-sig", newline=""
        ) as csv_file:
            expected_rows = list(csv.DictReader(csv_file))

        sample_ids = {row["record_id"] for row in self.rows}
        expected_ids = {row["record_id"] for row in expected_rows}
        self.assertEqual(len(expected_rows), 100)
        self.assertEqual(expected_ids, sample_ids)


if __name__ == "__main__":
    unittest.main()
