"""AI Handover Guard V0.1 の監査ルールテスト。"""

import csv
import sys
import unittest
from pathlib import Path


# src内のモジュールを読み込めるようにする
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from audit_rules import audit_record, determine_rule_priority  # noqa: E402


class AuditRecordTest(unittest.TestCase):
    """1件分の監査判定を確認する。"""

    def test_ready_when_required_fields_exist(self) -> None:
        record = {
            "action_required": "true",
            "extracted_action": "在庫を確認する",
            "extracted_assignee": "田中",
            "extracted_deadline": "2026-09-16 15:00",
        }

        result = audit_record(record)

        self.assertEqual(result["audit_status"], "READY")
        self.assertEqual(result["missing_fields"], "")

    def test_needs_review_when_assignee_is_missing(self) -> None:
        record = {
            "action_required": "true",
            "extracted_action": "取引先へ確認する",
            "extracted_assignee": "",
            "extracted_deadline": "2026-09-17 12:00",
        }

        result = audit_record(record)

        self.assertEqual(result["audit_status"], "NEEDS_REVIEW")
        self.assertIn("担当者", result["missing_fields"])

    def test_info_only_does_not_require_action_fields(self) -> None:
        record = {
            "action_required": "false",
            "extracted_action": "",
            "extracted_assignee": "",
            "extracted_deadline": "",
        }

        result = audit_record(record)

        self.assertEqual(result["audit_status"], "INFO_ONLY")

    def test_high_priority_for_equipment_abnormal_noise(self) -> None:
        record = {
            "message_text": "設備3号機から異音があります",
            "action_required": "true",
            "extracted_action": "設備を確認する",
            "extracted_assignee": "鈴木",
            "extracted_deadline": "2026-09-16 11:00",
            "extracted_priority": "中",
        }

        result = audit_record(record)

        self.assertEqual(determine_rule_priority(record), "高")
        self.assertEqual(result["rule_priority"], "高")
        self.assertEqual(result["priority_audit_status"], "NEEDS_REVIEW")

    def test_low_priority_for_routine_contact_list_update(self) -> None:
        record = {
            "message_text": "連絡網の更新内容を確認してください",
            "action_required": "true",
            "extracted_action": "連絡網を確認する",
            "extracted_assignee": "加藤",
            "extracted_deadline": "2026-09-16 17:00",
            "extracted_priority": "低",
        }

        result = audit_record(record)

        self.assertEqual(result["rule_priority"], "低")
        self.assertEqual(result["priority_audit_status"], "MATCH")

    def test_rule_priority_matches_all_100_expected_labels(self) -> None:
        with (PROJECT_ROOT / "data" / "mock_ai_output.csv").open(
            encoding="utf-8-sig", newline=""
        ) as csv_file:
            predictions = {
                row["record_id"]: row for row in csv.DictReader(csv_file)
            }
        with (PROJECT_ROOT / "data" / "expected_labels.csv").open(
            encoding="utf-8-sig", newline=""
        ) as csv_file:
            expected = {
                row["record_id"]: row for row in csv.DictReader(csv_file)
            }

        for record_id, record in predictions.items():
            self.assertEqual(
                determine_rule_priority(record),
                expected[record_id]["expected_priority"],
                record_id,
            )


if __name__ == "__main__":
    unittest.main()
