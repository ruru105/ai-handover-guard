"""API通信を行わずに安全機能を確認する。"""

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_extractor import (  # noqa: E402
    estimate_luna_cost_usd,
    make_output_row,
    make_user_message,
    normalize_assignee,
    normalize_extraction,
    read_trial_rows,
)


class LlmExtractorTest(unittest.TestCase):
    """入力変換・出力変換・料金計算・件数制限を確認する。"""

    def test_user_message_contains_only_required_context(self) -> None:
        record = {
            "record_id": "H001",
            "submitted_at": "2026-09-16 08:10",
            "source_department": "製造",
            "message_text": "在庫を確認してください",
        }

        message = make_user_message(record)

        self.assertIn("2026-09-16 08:10", message)
        self.assertIn("製造", message)
        self.assertIn("在庫を確認してください", message)
        self.assertNotIn("record_id", message)

    def test_output_row_uses_expected_csv_columns(self) -> None:
        record = {
            "record_id": "H001",
            "submitted_at": "2026-09-16 08:10",
            "source_department": "製造",
            "message_text": "在庫を確認してください",
        }
        parsed = {
            "action": "在庫を確認する",
            "assignee": "",
            "deadline": "",
            "priority": "中",
            "action_required": True,
        }

        output = make_output_row(record, parsed)

        self.assertEqual(output["extracted_action"], "在庫を確認する")
        self.assertEqual(output["action_required"], "true")

    def test_luna_cost_estimate(self) -> None:
        cost = estimate_luna_cost_usd(50_000, 15_000)
        self.assertAlmostEqual(cost, 0.028)

    def test_trial_limit_cannot_exceed_100(self) -> None:
        with self.assertRaises(ValueError):
            read_trial_rows(PROJECT_ROOT / "data" / "raw_handover.csv", 101)

    def test_trial_limit_allows_up_to_100(self) -> None:
        rows = read_trial_rows(PROJECT_ROOT / "data" / "raw_handover.csv", 100)
        self.assertEqual(len(rows), 100)

    def test_default_10_rows_match_original_trial_file(self) -> None:
        """既定の10件は、従来のtrial_10.csvと内容が変わっていないことを確認する。"""

        from_raw = read_trial_rows(PROJECT_ROOT / "data" / "raw_handover.csv", 10)
        from_trial_10 = read_trial_rows(PROJECT_ROOT / "data" / "trial_10.csv", 10)
        self.assertEqual(from_raw, from_trial_10)

    def test_removes_honorific_from_assignee(self) -> None:
        self.assertEqual(normalize_assignee("田中さん"), "田中")

    def test_info_only_clears_action_fields(self) -> None:
        parsed = {
            "action": "受診時間が変更",
            "assignee": "山本さん",
            "deadline": "",
            "priority": "低",
            "action_required": False,
        }

        normalized = normalize_extraction(parsed)

        self.assertEqual(normalized["action"], "")
        self.assertEqual(normalized["assignee"], "")


if __name__ == "__main__":
    unittest.main()
