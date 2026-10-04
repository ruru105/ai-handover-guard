"""やること抜けチェッカー V0.6 のAPIテスト。本物のAPI通信は一切行わない。"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import api  # noqa: E402
import history_store  # noqa: E402

client = TestClient(api.app)


def make_record(record_id: str = "T001", **overrides) -> dict:
    """監査リクエスト用の1件(既定は、必要項目がそろった中優先度の案件)。"""

    record = {
        "record_id": record_id,
        "submitted_at": "2026-09-16 08:10",
        "source_department": "製造",
        "message_text": "田中さん、部品Aの在庫を本日15時までに確認してください",
        "extracted_action": "部品Aの在庫を確認する",
        "extracted_assignee": "田中",
        "extracted_deadline": "2026-09-16 15:00",
        "extracted_priority": "中",
        "action_required": True,
    }
    record.update(overrides)
    return record


class FakeParsed:
    """AIの構造化結果の代わり。"""

    def model_dump(self) -> dict:
        return {
            "action": "部品Aの在庫を確認する",
            "assignee": "田中さん",
            "deadline": "2026-09-16 15:00",
            "priority": "中",
            "action_required": True,
        }


class FakeOpenAI:
    """本物のOpenAIクライアントの代わり。呼ばれた回数だけ記録する。"""

    def __init__(self) -> None:
        self.calls = 0
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            output_parsed=FakeParsed(),
            usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
        )


def raw_record(record_id: str = "R001") -> dict:
    return {
        "record_id": record_id,
        "submitted_at": "2026-09-16 08:10",
        "source_department": "製造",
        "message_text": "田中さん、部品Aの在庫を本日15時までに確認してください",
    }


class HealthTest(unittest.TestCase):
    def test_health_returns_ok(self) -> None:
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


class AuditEndpointTest(unittest.TestCase):
    def test_complete_record_is_ready_and_summary_counts(self) -> None:
        body = {"records": [make_record()], "as_of": "2026-09-16 12:00"}
        response = client.post("/audit", json=body)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["as_of"], "2026-09-16 12:00")
        self.assertEqual(data["results"][0]["audit_status"], "READY")
        self.assertEqual(data["summary"]["ready"], 1)
        self.assertEqual(data["summary"]["overdue"], 0)

    def test_missing_fields_are_needs_review(self) -> None:
        record = make_record(extracted_assignee="", extracted_deadline="")
        response = client.post("/audit", json={"records": [record], "as_of": "2026-09-16 12:00"})
        data = response.json()
        self.assertEqual(data["results"][0]["audit_status"], "NEEDS_REVIEW")
        self.assertEqual(data["summary"]["needs_review"], 1)

    def test_info_only_when_action_not_required(self) -> None:
        record = make_record(action_required="false")
        response = client.post("/audit", json={"records": [record], "as_of": "2026-09-16 12:00"})
        self.assertEqual(response.json()["results"][0]["audit_status"], "INFO_ONLY")
        self.assertEqual(response.json()["summary"]["info_only"], 1)

    def test_duplicate_candidates_are_counted(self) -> None:
        body = {
            "records": [
                make_record("D1"),
                make_record("D2", submitted_at="2026-09-16 09:10"),
            ],
            "as_of": "2026-09-16 10:00",
        }
        summary = client.post("/audit", json=body).json()["summary"]
        self.assertEqual(summary["duplicate_candidates"], 2)

    def test_duplicate_window_hours_is_applied(self) -> None:
        body = {
            "records": [
                make_record("D1"),
                make_record("D2", submitted_at="2026-09-16 09:10"),
            ],
            "as_of": "2026-09-16 10:00",
            "duplicate_window_hours": 0.5,
        }
        summary = client.post("/audit", json=body).json()["summary"]
        self.assertEqual(summary["duplicate_candidates"], 0)

    def test_bad_as_of_is_rejected(self) -> None:
        body = {"records": [make_record()], "as_of": "2026/09/16 12:00"}
        self.assertEqual(client.post("/audit", json=body).status_code, 422)

    def test_empty_records_are_rejected(self) -> None:
        self.assertEqual(client.post("/audit", json={"records": []}).status_code, 422)

    def test_too_many_records_are_rejected(self) -> None:
        records = [make_record(f"R{i}") for i in range(api.MAX_AUDIT_RECORDS + 1)]
        self.assertEqual(client.post("/audit", json={"records": records}).status_code, 422)

    def test_non_positive_sla_hours_is_rejected(self) -> None:
        body = {"records": [make_record()], "sla_hours": 0}
        self.assertEqual(client.post("/audit", json=body).status_code, 422)


class OverallStatusEndpointTest(unittest.TestCase):
    """総合判定(overall_status)と、対応要否の誤記の扱い。"""

    AS_OF = "2026-09-16 12:00"

    def test_overdue_record_is_overall_needs_review_even_if_fields_complete(self) -> None:
        record = make_record(extracted_deadline="2026-09-16 09:00")
        data = client.post("/audit", json={"records": [record], "as_of": self.AS_OF}).json()
        self.assertEqual(data["results"][0]["audit_status"], "READY")
        self.assertEqual(data["results"][0]["overall_status"], "NEEDS_REVIEW")
        self.assertEqual(data["summary"]["ready"], 1)
        self.assertEqual(data["summary"]["overall_ready"], 0)
        self.assertEqual(data["summary"]["overall_needs_review"], 1)

    def test_overall_reasons_count_each_reason(self) -> None:
        overdue = make_record(record_id="A1", extracted_action="部品Bを発注する", extracted_deadline="2026-09-16 09:00")
        missing = make_record(record_id="A2", extracted_action="床を清掃する", extracted_assignee="")
        clean = make_record(record_id="A3", extracted_action="伝票を提出する")
        data = client.post("/audit", json={"records": [overdue, missing, clean], "as_of": self.AS_OF}).json()
        reasons = data["summary"]["overall_reasons"]
        self.assertEqual(reasons["overdue"], 1)
        self.assertEqual(reasons["missing_fields"], 1)
        self.assertEqual(reasons["duplicate"], 0)
        self.assertEqual(data["summary"]["overall_needs_review"], 2)

    def test_vague_action_is_counted_as_its_own_reason(self) -> None:
        vague = make_record(record_id="B1", extracted_action="例の件")
        concrete = make_record(record_id="B2", extracted_action="伝票を提出する")
        data = client.post("/audit", json={"records": [vague, concrete], "as_of": self.AS_OF}).json()
        self.assertEqual(data["summary"]["overall_reasons"]["vague_action"], 1)
        self.assertEqual(data["results"][0]["vague_audit_status"], "NEEDS_REVIEW")
        self.assertEqual(data["results"][1]["vague_audit_status"], "CLEAR")
        self.assertEqual(data["summary"]["overall_needs_review"], 1)

    def test_overall_reasons_are_zero_for_clean_record(self) -> None:
        data = client.post("/audit", json={"records": [make_record()], "as_of": self.AS_OF}).json()
        self.assertEqual(sum(data["summary"]["overall_reasons"].values()), 0)

    def test_clean_record_is_overall_ready(self) -> None:
        data = client.post("/audit", json={"records": [make_record()], "as_of": self.AS_OF}).json()
        self.assertEqual(data["results"][0]["overall_status"], "READY")
        self.assertEqual(data["summary"]["overall_ready"], 1)
        self.assertEqual(data["summary"]["overall_needs_review"], 0)

    def test_misspelled_action_required_is_rejected(self) -> None:
        for bad in ("tru", "maybe", ""):
            record = make_record(action_required=bad)
            response = client.post("/audit", json={"records": [record], "as_of": self.AS_OF})
            self.assertEqual(response.status_code, 422, repr(bad))
            self.assertIn("action_required", response.text)

    def test_misspelled_action_required_is_rejected_by_other_endpoints_too(self) -> None:
        record = make_record(action_required="tru")
        for path in ("/overdue", "/history/audit"):
            response = client.post(path, json={"records": [record], "as_of": self.AS_OF})
            self.assertEqual(response.status_code, 422, path)

    def test_accepted_spellings_of_action_required(self) -> None:
        for good in (True, False, "true", "FALSE", " yes ", "0"):
            record = make_record(action_required=good)
            response = client.post("/audit", json={"records": [record], "as_of": self.AS_OF})
            self.assertEqual(response.status_code, 200, repr(good))


class OverdueEndpointTest(unittest.TestCase):
    def test_returns_only_overdue_records(self) -> None:
        body = {
            "records": [
                make_record("LATE", extracted_deadline="2026-09-16 15:00"),
                make_record(
                    "OK", extracted_deadline="2026-09-17 15:00",
                    extracted_action="棚卸しをする", extracted_assignee="佐藤",
                ),
            ],
            "as_of": "2026-09-16 16:00",
        }
        data = client.post("/overdue", json=body).json()
        self.assertEqual(data["total_records"], 2)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["overdue"][0]["record_id"], "LATE")
        self.assertIn("期限", data["overdue"][0]["sla_audit_message"])

    def test_date_only_deadline_is_valid_until_end_of_day(self) -> None:
        record = make_record("DATE", extracted_deadline="2026-09-18")
        on_day = client.post("/overdue", json={"records": [record], "as_of": "2026-09-18 23:59"})
        next_day = client.post("/overdue", json={"records": [record], "as_of": "2026-09-19 00:00"})
        self.assertEqual(on_day.json()["count"], 0)
        self.assertEqual(next_day.json()["count"], 1)

    def test_none_overdue_returns_empty_list(self) -> None:
        body = {"records": [make_record()], "as_of": "2026-09-16 09:00"}
        data = client.post("/overdue", json=body).json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["overdue"], [])


class ExtractEndpointTest(unittest.TestCase):
    def test_without_confirmation_no_api_call_is_made(self) -> None:
        fake = FakeOpenAI()
        with mock.patch.object(api, "create_openai_client", return_value=fake) as creator:
            response = client.post("/extract", json={"records": [raw_record()]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake.calls, 0)
        creator.assert_not_called()

    def test_confirmed_request_returns_normalized_rows_and_usage(self) -> None:
        fake = FakeOpenAI()
        body = {"records": [raw_record("R1"), raw_record("R2")], "confirm_paid_api": True}
        with mock.patch.object(api, "create_openai_client", return_value=fake):
            response = client.post("/extract", json=body)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(fake.calls, 2)
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["records"][0]["extracted_assignee"], "田中")  # 敬称は除かれる
        self.assertEqual(data["records"][0]["action_required"], "true")
        self.assertEqual(data["usage"]["input_tokens"], 2000)
        self.assertEqual(data["usage"]["output_tokens"], 100)
        self.assertGreater(data["usage"]["estimated_cost_usd"], 0)

    def test_other_model_has_no_cost_estimate(self) -> None:
        body = {"records": [raw_record()], "model": "other-model", "confirm_paid_api": True}
        with mock.patch.object(api, "create_openai_client", return_value=FakeOpenAI()):
            data = client.post("/extract", json=body).json()
        self.assertIsNone(data["usage"]["estimated_cost_usd"])

    def test_record_limit_is_enforced_before_any_call(self) -> None:
        fake = FakeOpenAI()
        records = [raw_record(f"R{i}") for i in range(api.MAX_EXTRACT_RECORDS + 1)]
        with mock.patch.object(api, "create_openai_client", return_value=fake):
            response = client.post("/extract", json={"records": records, "confirm_paid_api": True})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(fake.calls, 0)

    def test_missing_api_key_returns_503_without_leaking(self) -> None:
        with mock.patch.object(
            api, "create_openai_client", side_effect=RuntimeError("OPENAI_API_KEYが設定されていません")
        ):
            response = client.post(
                "/extract", json={"records": [raw_record()], "confirm_paid_api": True}
            )
        self.assertEqual(response.status_code, 503)

    def test_upstream_failure_returns_502_without_details(self) -> None:
        broken = SimpleNamespace(
            responses=SimpleNamespace(parse=mock.Mock(side_effect=Exception("secret sk-12345")))
        )
        with mock.patch.object(api, "create_openai_client", return_value=broken):
            response = client.post(
                "/extract", json={"records": [raw_record()], "confirm_paid_api": True}
            )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("sk-12345", response.text)


class HistoryEndpointTest(unittest.TestCase):
    """履歴(SQLite)を使うAPI。テストごとに一時フォルダのデータベースを使う。"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(history_store, "DEFAULT_DB_PATH", Path(tmp.name) / "history.db")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_history_starts_empty(self) -> None:
        self.assertEqual(client.get("/history").json(), {"count": 0})

    def test_audit_saves_records_and_reports_counts(self) -> None:
        body = {"records": [make_record("H1")], "as_of": "2026-09-16 12:00"}
        data = client.post("/history/audit", json=body).json()
        self.assertTrue(data["saved"])
        self.assertEqual(data["stored_total"], 1)
        self.assertEqual(data["history_compared"], 0)
        self.assertEqual(client.get("/history").json(), {"count": 1})

    def test_duplicate_across_requests_is_detected(self) -> None:
        first = {"records": [make_record("H1")], "as_of": "2026-09-16 12:00"}
        second = {"records": [make_record("H2", submitted_at="2026-09-16 09:10")], "as_of": "2026-09-16 12:00"}
        client.post("/history/audit", json=first)
        data = client.post("/history/audit", json=second).json()
        self.assertEqual(data["history_compared"], 1)
        self.assertEqual(data["summary"]["duplicate_candidates"], 1)
        self.assertIn("H1", data["results"][0]["duplicate_audit_message"])

    def test_plain_audit_ignores_saved_history(self) -> None:
        body = {"records": [make_record("H1")], "as_of": "2026-09-16 12:00"}
        client.post("/history/audit", json=body)
        other = {"records": [make_record("H2", submitted_at="2026-09-16 09:10")], "as_of": "2026-09-16 12:00"}
        plain = client.post("/audit", json=other).json()
        self.assertEqual(plain["summary"]["duplicate_candidates"], 0)
        self.assertEqual(client.get("/history").json(), {"count": 1})

    def test_save_false_compares_without_saving(self) -> None:
        client.post("/history/audit", json={"records": [make_record("H1")], "as_of": "2026-09-16 12:00"})
        body = {
            "records": [make_record("H2", submitted_at="2026-09-16 09:10")],
            "as_of": "2026-09-16 12:00",
            "save": False,
        }
        data = client.post("/history/audit", json=body).json()
        self.assertFalse(data["saved"])
        self.assertEqual(data["summary"]["duplicate_candidates"], 1)
        self.assertEqual(client.get("/history").json(), {"count": 1})

    def test_validation_errors_do_not_save(self) -> None:
        body = {"records": [make_record("H1")], "as_of": "bad"}
        self.assertEqual(client.post("/history/audit", json=body).status_code, 422)
        self.assertEqual(client.get("/history").json(), {"count": 0})


class RecordIdRulesTest(unittest.TestCase):
    """IDの空欄・重複は、どの監査入口でも入力エラー(422)にする。"""

    def test_duplicate_ids_are_rejected_by_all_audit_endpoints(self) -> None:
        records = [make_record("R1"), make_record("R1", extracted_action="別の作業")]
        for path in ("/audit", "/overdue", "/history/audit"):
            with self.subTest(path=path):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
                    history_store, "DEFAULT_DB_PATH", Path(tmp) / "h.db"
                ):
                    response = client.post(path, json={"records": records, "as_of": "2026-09-16 12:00"})
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(client.get("/history").json(), {"count": 0})

    def test_empty_id_is_rejected(self) -> None:
        body = {"records": [make_record("")], "as_of": "2026-09-16 12:00"}
        self.assertEqual(client.post("/audit", json=body).status_code, 422)

    def test_empty_id_is_rejected_for_extract_too(self) -> None:
        body = {"records": [dict(raw_record(), record_id="")], "confirm_paid_api": True}
        self.assertEqual(client.post("/extract", json=body).status_code, 422)


if __name__ == "__main__":
    unittest.main()
