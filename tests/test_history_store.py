"""SQLite履歴と、過去分にまたがる重複検出のテスト。"""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from audit_rules import audit_records  # noqa: E402
from history_store import HistoryStore, audit_with_history, validate_record_ids  # noqa: E402


def make_record(record_id: str, submitted_at: str, assignee: str = "田中",
                action: str = "部品Aの在庫を確認する", **overrides) -> dict:
    record = {
        "record_id": record_id,
        "submitted_at": submitted_at,
        "source_department": "製造",
        "message_text": "原文",
        "extracted_action": action,
        "extracted_assignee": assignee,
        "extracted_deadline": "2026-09-30 17:00",
        "extracted_priority": "中",
        "action_required": "true",
    }
    record.update(overrides)
    return record


AS_OF = datetime(2026, 9, 16, 12, 0)


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "sub" / "history.db"
        self.store = HistoryStore(self.db_path)


class HistoryStoreTest(StoreTestCase):
    def test_creates_file_in_missing_folder(self) -> None:
        self.assertTrue(self.db_path.exists())
        self.assertEqual(self.store.count(), 0)

    def test_upsert_saves_and_counts(self) -> None:
        saved = self.store.upsert([make_record("A", "2026-09-16 08:00"), make_record("B", "2026-09-16 09:00")])
        self.assertEqual(saved, 2)
        self.assertEqual(self.store.count(), 2)

    def test_same_record_id_is_replaced_not_duplicated(self) -> None:
        self.store.upsert([make_record("A", "2026-09-16 08:00", assignee="田中")])
        self.store.upsert([make_record("A", "2026-09-16 08:00", assignee="佐藤")])
        rows = self.store.fetch_between(datetime(2026, 9, 16, 0, 0), datetime(2026, 9, 17, 0, 0))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["extracted_assignee"], "佐藤")

    def test_data_persists_across_instances(self) -> None:
        self.store.upsert([make_record("A", "2026-09-16 08:00")])
        self.assertEqual(HistoryStore(self.db_path).count(), 1)

    def test_fetch_between_filters_and_orders_by_time(self) -> None:
        self.store.upsert([
            make_record("LATE", "2026-09-16 12:00"),
            make_record("EARLY", "2026-09-16 08:00"),
            make_record("OUT", "2026-09-20 08:00"),
        ])
        rows = self.store.fetch_between(datetime(2026, 9, 16, 0, 0), datetime(2026, 9, 17, 0, 0))
        self.assertEqual([row["record_id"] for row in rows], ["EARLY", "LATE"])

    def test_fetch_between_includes_boundaries(self) -> None:
        self.store.upsert([make_record("EDGE", "2026-09-16 08:00")])
        rows = self.store.fetch_between(datetime(2026, 9, 16, 8, 0), datetime(2026, 9, 16, 8, 0))
        self.assertEqual(len(rows), 1)

    def test_values_are_stored_as_text_and_returned_unchanged(self) -> None:
        self.store.upsert([make_record("A", "2026-09-16 08:00", action_required=True)])
        row = self.store.fetch_between(datetime(2026, 9, 16, 0, 0), datetime(2026, 9, 17, 0, 0))[0]
        self.assertEqual(row["action_required"], "True")


class AuditWithHistoryTest(StoreTestCase):
    def test_duplicate_across_batches_is_detected(self) -> None:
        audit_with_history(self.store, [make_record("OLD", "2026-09-16 08:00")], AS_OF)
        results, compared = audit_with_history(self.store, [make_record("NEW", "2026-09-16 10:00")], AS_OF)
        self.assertEqual(compared, 1)
        self.assertEqual(results[0]["duplicate_audit_status"], "NEEDS_REVIEW")
        self.assertIn("OLD", results[0]["duplicate_audit_message"])

    def test_no_history_means_unique(self) -> None:
        results, compared = audit_with_history(self.store, [make_record("NEW", "2026-09-16 10:00")], AS_OF)
        self.assertEqual(compared, 0)
        self.assertEqual(results[0]["duplicate_audit_status"], "UNIQUE")

    def test_history_outside_window_is_not_a_duplicate(self) -> None:
        audit_with_history(self.store, [make_record("OLD", "2026-09-15 08:00")], AS_OF)
        results, _ = audit_with_history(self.store, [make_record("NEW", "2026-09-16 10:00")], AS_OF)
        self.assertEqual(results[0]["duplicate_audit_status"], "UNIQUE")

    def test_window_hours_setting_is_respected(self) -> None:
        audit_with_history(self.store, [make_record("OLD", "2026-09-16 08:00")], AS_OF)
        results, _ = audit_with_history(
            self.store, [make_record("NEW", "2026-09-16 10:00")], AS_OF, duplicate_window_hours=1
        )
        self.assertEqual(results[0]["duplicate_audit_status"], "UNIQUE")

    def test_different_assignee_or_action_is_not_a_duplicate(self) -> None:
        audit_with_history(self.store, [make_record("OLD", "2026-09-16 08:00")], AS_OF)
        results, _ = audit_with_history(
            self.store,
            [make_record("N1", "2026-09-16 10:00", assignee="佐藤"),
             make_record("N2", "2026-09-16 10:05", action="棚卸しをする")],
            AS_OF,
        )
        self.assertEqual([r["duplicate_audit_status"] for r in results], ["UNIQUE", "UNIQUE"])

    def test_returns_only_new_records_in_input_order(self) -> None:
        audit_with_history(self.store, [make_record("OLD", "2026-09-16 08:00")], AS_OF)
        new = [make_record("N2", "2026-09-16 11:00", assignee="佐藤"), make_record("N1", "2026-09-16 09:00", assignee="鈴木")]
        results, _ = audit_with_history(self.store, new, AS_OF)
        self.assertEqual([r["record_id"] for r in results], ["N2", "N1"])

    def test_save_false_neither_saves_nor_changes_history(self) -> None:
        audit_with_history(self.store, [make_record("NEW", "2026-09-16 10:00")], AS_OF, save=False)
        self.assertEqual(self.store.count(), 0)

    def test_save_true_stores_new_records(self) -> None:
        audit_with_history(self.store, [make_record("A", "2026-09-16 10:00"), make_record("B", "2026-09-16 11:00", assignee="佐藤")], AS_OF)
        self.assertEqual(self.store.count(), 2)

    def test_resubmitting_same_record_id_is_not_flagged_against_itself(self) -> None:
        audit_with_history(self.store, [make_record("A", "2026-09-16 08:00")], AS_OF)
        results, compared = audit_with_history(self.store, [make_record("A", "2026-09-16 08:00")], AS_OF)
        self.assertEqual(compared, 0)
        self.assertEqual(results[0]["duplicate_audit_status"], "UNIQUE")
        self.assertEqual(self.store.count(), 1)

    def test_duplicate_within_the_same_batch_still_detected(self) -> None:
        results, _ = audit_with_history(
            self.store,
            [make_record("A", "2026-09-16 08:00"), make_record("B", "2026-09-16 09:00")],
            AS_OF,
        )
        self.assertEqual([r["duplicate_audit_status"] for r in results], ["NEEDS_REVIEW", "NEEDS_REVIEW"])

    def test_unreadable_submitted_at_does_not_crash(self) -> None:
        audit_with_history(self.store, [make_record("OLD", "2026-09-16 08:00")], AS_OF)
        results, compared = audit_with_history(self.store, [make_record("BAD", "昨日の朝")], AS_OF)
        self.assertEqual(compared, 0)
        self.assertEqual(results[0]["duplicate_audit_status"], "UNKNOWN")

    def test_split_batches_match_all_at_once_for_later_batch(self) -> None:
        """2回に分けて監査しても、後半の判定は、全部まとめて監査した場合と同じになる。"""

        records = [
            make_record("R1", "2026-09-16 08:00"),
            make_record("R2", "2026-09-16 09:00", assignee="佐藤", action="棚卸しをする"),
            make_record("R3", "2026-09-16 09:30"),
            make_record("R4", "2026-09-16 20:00", assignee="佐藤", action="棚卸しをする"),
            make_record("R5", "2026-09-18 08:00"),
            make_record("R6", "2026-09-18 09:00", assignee="鈴木", action="請求書を確認する"),
        ]
        everything = {r["record_id"]: r for r in audit_records(records, AS_OF)}
        audit_with_history(self.store, records[:3], AS_OF)
        later, _ = audit_with_history(self.store, records[3:], AS_OF)
        for result in later:
            whole = everything[result["record_id"]]
            self.assertEqual(result["duplicate_audit_status"], whole["duplicate_audit_status"], result["record_id"])
            self.assertEqual(result["duplicate_audit_message"], whole["duplicate_audit_message"], result["record_id"])


class RecordIdValidationTest(StoreTestCase):
    def test_duplicate_ids_in_one_batch_are_rejected_and_nothing_saved(self) -> None:
        batch = [make_record("R1", "2026-09-16 08:00"), make_record("R1", "2026-09-16 09:00", action="別の作業")]
        with self.assertRaises(ValueError):
            self.store.upsert(batch)
        with self.assertRaises(ValueError):
            audit_with_history(self.store, batch, AS_OF)
        self.assertEqual(self.store.count(), 0)

    def test_empty_or_blank_id_is_rejected(self) -> None:
        for bad in ("", "   "):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_record_ids([make_record(bad, "2026-09-16 08:00")])

    def test_unique_ids_pass(self) -> None:
        validate_record_ids([make_record("A", "2026-09-16 08:00"), make_record("B", "2026-09-16 09:00")])

    def test_updating_an_existing_id_in_a_later_batch_is_still_allowed(self) -> None:
        self.store.upsert([make_record("A", "2026-09-16 08:00", assignee="田中")])
        self.store.upsert([make_record("A", "2026-09-16 08:00", assignee="佐藤")])
        self.assertEqual(self.store.count(), 1)


if __name__ == "__main__":
    unittest.main()
