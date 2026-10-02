"""AI Handover Guard V0.1〜V0.4 の監査ルールテスト。"""

import csv
import sys
import unittest
from datetime import datetime
from pathlib import Path


# src内のモジュールを読み込めるようにする
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from audit_rules import (  # noqa: E402
    audit_record,
    audit_records,
    determine_rule_priority,
    parse_deadline,
)


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

    def test_claim_keyword_makes_priority_high(self) -> None:
        record = {
            "message_text": "取引先からクレームがありました",
            "action_required": "true",
        }

        self.assertEqual(determine_rule_priority(record), "高")


class SlaAuditTest(unittest.TestCase):
    """V0.4: 緊急度に応じた期限超過（着手遅延）判定を確認する。"""

    def test_high_priority_overdue_after_sla_hours(self) -> None:
        record = {
            "message_text": "設備3号機から異音があります",
            "submitted_at": "2026-09-16 08:00",
            "action_required": "true",
            "extracted_action": "設備を確認する",
            "extracted_assignee": "鈴木",
            "extracted_deadline": "2026-09-20 17:00",
            "extracted_priority": "高",
        }

        result = audit_record(
            record,
            as_of=datetime(2026, 9, 16, 13, 0),
            sla_hours=4,
        )

        self.assertEqual(result["rule_priority"], "高")
        self.assertEqual(result["sla_audit_status"], "NEEDS_REVIEW")

    def test_high_priority_within_sla_hours(self) -> None:
        record = {
            "message_text": "設備3号機から異音があります",
            "submitted_at": "2026-09-16 08:00",
            "action_required": "true",
            "extracted_action": "設備を確認する",
            "extracted_assignee": "鈴木",
            "extracted_deadline": "2026-09-20 17:00",
            "extracted_priority": "高",
        }

        result = audit_record(
            record,
            as_of=datetime(2026, 9, 16, 10, 0),
            sla_hours=4,
        )

        self.assertEqual(result["sla_audit_status"], "ON_TIME")

    def test_high_priority_ignores_far_extracted_deadline(self) -> None:
        """文中の期限が先でも、緊急度「高」はSLA時間を優先して超過扱いにする。"""

        record = {
            "message_text": "クレームがありました",
            "submitted_at": "2026-09-16 08:00",
            "action_required": "true",
            "extracted_action": "確認する",
            "extracted_assignee": "田中",
            "extracted_deadline": "2026-09-30 17:00",
            "extracted_priority": "高",
        }

        result = audit_record(
            record,
            as_of=datetime(2026, 9, 16, 13, 0),
            sla_hours=4,
        )

        self.assertEqual(result["sla_audit_status"], "NEEDS_REVIEW")

    def test_medium_priority_uses_extracted_deadline(self) -> None:
        record = {
            "message_text": "在庫を確認してください",
            "submitted_at": "2026-09-16 08:00",
            "action_required": "true",
            "extracted_action": "在庫を確認する",
            "extracted_assignee": "田中",
            "extracted_deadline": "2026-09-16 15:00",
            "extracted_priority": "中",
        }

        overdue = audit_record(record, as_of=datetime(2026, 9, 16, 16, 0))
        on_time = audit_record(record, as_of=datetime(2026, 9, 16, 14, 0))

        self.assertEqual(overdue["sla_audit_status"], "NEEDS_REVIEW")
        self.assertEqual(on_time["sla_audit_status"], "ON_TIME")

    def test_info_only_is_not_applicable(self) -> None:
        record = {
            "message_text": "共有のみです",
            "submitted_at": "2026-09-16 08:00",
            "action_required": "false",
        }

        result = audit_record(record, as_of=datetime(2026, 9, 20, 8, 0))

        self.assertEqual(result["sla_audit_status"], "NOT_APPLICABLE")

    def test_missing_submitted_at_is_unknown(self) -> None:
        record = {
            "message_text": "設備を確認してください",
            "submitted_at": "",
            "action_required": "true",
            "extracted_priority": "中",
        }

        result = audit_record(record, as_of=datetime(2026, 9, 16, 8, 0))

        self.assertEqual(result["sla_audit_status"], "UNKNOWN")

    def test_missing_extracted_deadline_for_medium_priority_is_not_applicable(
        self,
    ) -> None:
        record = {
            "message_text": "確認してください",
            "submitted_at": "2026-09-16 08:00",
            "action_required": "true",
            "extracted_deadline": "",
            "extracted_priority": "中",
        }

        result = audit_record(record, as_of=datetime(2026, 9, 20, 8, 0))

        self.assertEqual(result["sla_audit_status"], "NOT_APPLICABLE")

    def _date_only_record(self, deadline: str = "2026-09-18") -> dict:
        return {
            "message_text": "発注番号の数量を確認してください",
            "submitted_at": "2026-09-16 08:00",
            "action_required": "true",
            "extracted_action": "仕入先へ確認する",
            "extracted_assignee": "",
            "extracted_deadline": deadline,
            "extracted_priority": "中",
        }

    def test_date_only_deadline_is_overdue_after_that_day(self) -> None:
        result = audit_record(self._date_only_record(), as_of=datetime(2026, 9, 20, 9, 0))

        self.assertEqual(result["sla_audit_status"], "NEEDS_REVIEW")
        self.assertIn("日付のみ", result["sla_audit_message"])

    def test_date_only_deadline_is_valid_until_end_of_that_day(self) -> None:
        result = audit_record(self._date_only_record(), as_of=datetime(2026, 9, 18, 23, 59))

        self.assertEqual(result["sla_audit_status"], "ON_TIME")

    def test_date_only_deadline_is_overdue_from_next_midnight(self) -> None:
        result = audit_record(self._date_only_record(), as_of=datetime(2026, 9, 19, 0, 0))

        self.assertEqual(result["sla_audit_status"], "NEEDS_REVIEW")

    def test_unreadable_deadline_is_still_not_applicable(self) -> None:
        for bad in ("来週中", "2026/09/18", "2026-09-18 25:00"):
            result = audit_record(self._date_only_record(bad), as_of=datetime(2026, 9, 20, 9, 0))
            self.assertEqual(result["sla_audit_status"], "NOT_APPLICABLE", bad)

    def test_deadline_with_time_keeps_existing_behavior(self) -> None:
        record = self._date_only_record("2026-09-18 17:00")

        before = audit_record(record, as_of=datetime(2026, 9, 18, 16, 59))
        after = audit_record(record, as_of=datetime(2026, 9, 18, 17, 1))

        self.assertEqual(before["sla_audit_status"], "ON_TIME")
        self.assertEqual(after["sla_audit_status"], "NEEDS_REVIEW")
        self.assertNotIn("日付のみ", after["sla_audit_message"])

    def test_parse_deadline_distinguishes_date_only(self) -> None:
        self.assertEqual(parse_deadline("2026-09-18 17:00"), (datetime(2026, 9, 18, 17, 0), False))
        deadline, date_only = parse_deadline("2026-09-18")
        self.assertTrue(date_only)
        self.assertEqual(deadline.date(), datetime(2026, 9, 18).date())
        self.assertEqual(parse_deadline(""), (None, False))


class DuplicateAuditTest(unittest.TestCase):
    """V0.4: 同じ担当者・同じ作業内容の重複候補判定を確認する。"""

    def test_duplicate_detected_within_window(self) -> None:
        records = [
            {
                "record_id": "R1",
                "message_text": "設備を確認してください",
                "submitted_at": "2026-09-16 08:00",
                "action_required": "true",
                "extracted_action": "設備を確認する",
                "extracted_assignee": "鈴木",
                "extracted_deadline": "2026-09-16 17:00",
                "extracted_priority": "中",
            },
            {
                "record_id": "R2",
                "message_text": "設備を確認してください",
                "submitted_at": "2026-09-16 10:00",
                "action_required": "true",
                "extracted_action": "設備を確認する",
                "extracted_assignee": "鈴木",
                "extracted_deadline": "2026-09-16 17:00",
                "extracted_priority": "中",
            },
        ]

        results = audit_records(records, duplicate_window_hours=24)

        self.assertEqual(results[0]["duplicate_audit_status"], "NEEDS_REVIEW")
        self.assertEqual(results[1]["duplicate_audit_status"], "NEEDS_REVIEW")
        self.assertIn("R2", results[0]["duplicate_audit_message"])
        self.assertIn("R1", results[1]["duplicate_audit_message"])

    def test_no_duplicate_outside_window(self) -> None:
        records = [
            {
                "record_id": "R1",
                "submitted_at": "2026-09-16 08:00",
                "action_required": "true",
                "extracted_action": "設備を確認する",
                "extracted_assignee": "鈴木",
                "extracted_deadline": "2026-09-16 17:00",
                "extracted_priority": "中",
            },
            {
                "record_id": "R2",
                "submitted_at": "2026-09-18 08:00",
                "action_required": "true",
                "extracted_action": "設備を確認する",
                "extracted_assignee": "鈴木",
                "extracted_deadline": "2026-09-18 17:00",
                "extracted_priority": "中",
            },
        ]

        results = audit_records(records, duplicate_window_hours=24)

        self.assertEqual(results[0]["duplicate_audit_status"], "UNIQUE")
        self.assertEqual(results[1]["duplicate_audit_status"], "UNIQUE")

    def test_different_assignee_is_not_grouped(self) -> None:
        records = [
            {
                "record_id": "R1",
                "submitted_at": "2026-09-16 08:00",
                "action_required": "true",
                "extracted_action": "設備を確認する",
                "extracted_assignee": "鈴木",
                "extracted_deadline": "2026-09-16 17:00",
                "extracted_priority": "中",
            },
            {
                "record_id": "R2",
                "submitted_at": "2026-09-16 09:00",
                "action_required": "true",
                "extracted_action": "設備を確認する",
                "extracted_assignee": "田中",
                "extracted_deadline": "2026-09-16 17:00",
                "extracted_priority": "中",
            },
        ]

        results = audit_records(records, duplicate_window_hours=24)

        self.assertEqual(results[0]["duplicate_audit_status"], "UNIQUE")
        self.assertEqual(results[1]["duplicate_audit_status"], "UNIQUE")

    def test_normalization_ignores_whitespace_and_punctuation(self) -> None:
        records = [
            {
                "record_id": "R1",
                "submitted_at": "2026-09-16 08:00",
                "action_required": "true",
                "extracted_action": "設備を確認する",
                "extracted_assignee": "鈴木",
                "extracted_deadline": "2026-09-16 17:00",
                "extracted_priority": "中",
            },
            {
                "record_id": "R2",
                "submitted_at": "2026-09-16 09:00",
                "action_required": "true",
                "extracted_action": "設備を　確認する。",
                "extracted_assignee": "鈴木",
                "extracted_deadline": "2026-09-16 17:00",
                "extracted_priority": "中",
            },
        ]

        results = audit_records(records, duplicate_window_hours=24)

        self.assertEqual(results[0]["duplicate_audit_status"], "NEEDS_REVIEW")
        self.assertEqual(results[1]["duplicate_audit_status"], "NEEDS_REVIEW")

    def test_action_required_false_is_not_applicable(self) -> None:
        record = {
            "record_id": "R1",
            "submitted_at": "2026-09-16 08:00",
            "action_required": "false",
        }

        result = audit_record(record)

        self.assertEqual(result["duplicate_audit_status"], "NOT_APPLICABLE")

    def test_missing_assignee_is_not_applicable(self) -> None:
        record = {
            "record_id": "R1",
            "submitted_at": "2026-09-16 08:00",
            "action_required": "true",
            "extracted_action": "設備を確認する",
            "extracted_assignee": "",
            "extracted_deadline": "2026-09-16 17:00",
        }

        result = audit_record(record)

        self.assertEqual(result["duplicate_audit_status"], "NOT_APPLICABLE")

    def test_unparseable_submitted_at_is_unknown(self) -> None:
        record = {
            "record_id": "R1",
            "submitted_at": "",
            "action_required": "true",
            "extracted_action": "設備を確認する",
            "extracted_assignee": "鈴木",
            "extracted_deadline": "2026-09-16 17:00",
        }

        result = audit_record(record)

        self.assertEqual(result["duplicate_audit_status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
