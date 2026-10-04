"""やること抜けチェッカー V0.1〜V0.4 の監査ルールテスト。"""

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

    def test_unreadable_deadline_is_unknown_and_needs_review_overall(self) -> None:
        # 期限が書かれているのに読めない場合は、「判定対象外」ではなく「判定不能」にして要確認へ回す。
        for bad in ("来週中", "2026/09/18", "2026-09-18 25:00"):
            result = audit_record(self._date_only_record(bad), as_of=datetime(2026, 9, 20, 9, 0))
            self.assertEqual(result["sla_audit_status"], "UNKNOWN", bad)
            self.assertEqual(result["overall_status"], "NEEDS_REVIEW", bad)
            self.assertIn(bad, result["sla_audit_message"])

    def test_empty_deadline_is_still_not_applicable(self) -> None:
        result = audit_record(self._date_only_record(""), as_of=datetime(2026, 9, 20, 9, 0))
        self.assertEqual(result["sla_audit_status"], "NOT_APPLICABLE")

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


class OverallStatusTest(unittest.TestCase):
    """総合判定(overall_status)。項目の充足(audit_status)とは別に、期限超過・重複なども見る。"""

    AS_OF = datetime(2026, 9, 20, 9, 0)

    @staticmethod
    def _record(record_id: str = "O1", **overrides) -> dict:
        record = {
            "record_id": record_id,
            "submitted_at": "2026-09-19 08:00",
            "source_department": "製造",
            "message_text": "田中さん、部品Aの在庫を確認してください",
            "extracted_action": "部品Aの在庫を確認する",
            "extracted_assignee": "田中",
            "extracted_deadline": "2026-09-25 15:00",
            "extracted_priority": "中",
            "action_required": "true",
        }
        record.update(overrides)
        return record

    def test_h01_overdue_is_not_overall_ready(self) -> None:
        result = audit_records([self._record(extracted_deadline="2026-09-19 17:00")], self.AS_OF)[0]
        self.assertEqual(result["audit_status"], "READY")  # 項目の充足は従来どおり
        self.assertEqual(result["sla_audit_status"], "NEEDS_REVIEW")
        self.assertEqual(result["overall_status"], "NEEDS_REVIEW")
        self.assertIn("期限超過", result["overall_message"])

    def test_clean_record_is_overall_ready(self) -> None:
        result = audit_records([self._record()], self.AS_OF)[0]
        self.assertEqual(result["audit_status"], "READY")
        self.assertEqual(result["overall_status"], "READY")

    def test_duplicate_candidate_is_overall_needs_review(self) -> None:
        results = audit_records(
            [self._record("D1"), self._record("D2", submitted_at="2026-09-19 10:00")], self.AS_OF
        )
        for result in results:
            self.assertEqual(result["audit_status"], "READY")
            self.assertEqual(result["overall_status"], "NEEDS_REVIEW")
            self.assertIn("重複候補", result["overall_message"])

    def test_priority_mismatch_is_overall_needs_review(self) -> None:
        result = audit_records([self._record(extracted_priority="高")], self.AS_OF)[0]
        self.assertEqual(result["priority_audit_status"], "NEEDS_REVIEW")
        self.assertEqual(result["overall_status"], "NEEDS_REVIEW")
        self.assertIn("優先度の不一致", result["overall_message"])

    def test_missing_field_is_overall_needs_review(self) -> None:
        result = audit_records([self._record(extracted_assignee="")], self.AS_OF)[0]
        self.assertEqual(result["audit_status"], "NEEDS_REVIEW")
        self.assertEqual(result["overall_status"], "NEEDS_REVIEW")
        self.assertIn("項目不足", result["overall_message"])

    def test_h02_unreadable_submitted_at_is_not_overall_ready(self) -> None:
        result = audit_records([self._record(submitted_at="昨日")], self.AS_OF)[0]
        self.assertEqual(result["audit_status"], "READY")
        self.assertEqual(result["sla_audit_status"], "UNKNOWN")
        self.assertEqual(result["overall_status"], "NEEDS_REVIEW")
        self.assertIn("判定不能", result["overall_message"])

    def test_info_only_stays_info_only(self) -> None:
        result = audit_records([self._record(action_required="false")], self.AS_OF)[0]
        self.assertEqual(result["audit_status"], "INFO_ONLY")
        self.assertEqual(result["overall_status"], "INFO_ONLY")

    def test_h03_misspelled_action_required_is_not_info_only(self) -> None:
        for bad in ("tru", "maybe", "", "  "):
            result = audit_records([self._record(action_required=bad)], self.AS_OF)[0]
            self.assertEqual(result["audit_status"], "NEEDS_REVIEW", repr(bad))
            self.assertEqual(result["overall_status"], "NEEDS_REVIEW", repr(bad))
            self.assertEqual(result["missing_fields"], "対応要否", repr(bad))
            self.assertEqual(result["sla_audit_status"], "UNKNOWN", repr(bad))
            self.assertIn("対応要否", result["overall_message"], repr(bad))

    def test_h03_accepted_spellings_still_work(self) -> None:
        for good in ("true", "TRUE", " yes ", "1", "Y"):
            result = audit_records([self._record(action_required=good)], self.AS_OF)[0]
            self.assertNotEqual(result["audit_status"], "INFO_ONLY", good)
        for good in ("false", "FALSE", " no ", "0", "N"):
            result = audit_records([self._record(action_required=good)], self.AS_OF)[0]
            self.assertEqual(result["audit_status"], "INFO_ONLY", good)

    def test_all_records_have_the_same_columns(self) -> None:
        """CSV・Excelは先頭行の項目名で列を決めるため、どの種類の結果も同じ項目を持つこと。"""

        results = audit_records(
            [
                self._record("K1"),
                self._record("K2", action_required="false"),
                self._record("K3", action_required="tru"),
                self._record("K4", extracted_assignee=""),
            ],
            self.AS_OF,
        )
        key_sets = {frozenset(result.keys()) for result in results}
        self.assertEqual(len(key_sets), 1)
        self.assertIn("overall_status", results[0])
        self.assertIn("overall_message", results[0])


class ExcelSafeCsvOutputTest(unittest.TestCase):
    """コマンドで監査したとき、採点用CSVは元のまま、Excelで開く用のCSVは数式が無効になること。"""

    def test_cli_writes_machine_csv_unchanged_and_excel_safe_csv(self) -> None:
        import subprocess
        import tempfile

        header = (
            "record_id,submitted_at,source_department,message_text,extracted_action,"
            "extracted_assignee,extracted_deadline,extracted_priority,action_required\n"
        )
        row = "R1,2026-09-19 08:00,製造,=1+1,=HYPERLINK(1),田中,2026-09-25 15:00,中,true\n"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "in.csv"
            source.write_text(header + row, encoding="utf-8-sig")
            output = tmp_path / "audit_result.csv"
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "src" / "audit_rules.py"),
                 "--input", str(source), "--output", str(output), "--as-of", "2026-09-20 09:00"],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            machine = list(csv.DictReader(output.open(encoding="utf-8-sig", newline="")))
            safe = list(csv.DictReader(
                (tmp_path / "audit_result_for_excel.csv").open(encoding="utf-8-sig", newline="")
            ))
        # 採点・再読み込みに使う元のCSVは、原文のまま(書き換えない)
        self.assertEqual(machine[0]["message_text"], "=1+1")
        self.assertEqual(machine[0]["extracted_action"], "=HYPERLINK(1)")
        # Excelで開く用は、先頭に「'」が付く
        self.assertEqual(safe[0]["message_text"], "'=1+1")
        self.assertEqual(safe[0]["extracted_action"], "'=HYPERLINK(1)")
        self.assertEqual(safe[0]["record_id"], "R1")


if __name__ == "__main__":
    unittest.main()
