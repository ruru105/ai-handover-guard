"""AI回答の採点機能テスト。"""

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evaluate_predictions import build_details, evaluate  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
