"""`/extract`の100件確認スクリプトのテスト。本物のAPI通信は一切行わない。"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import api  # noqa: E402
import verify_extract_api as verify  # noqa: E402
from llm_extractor import read_trial_rows  # noqa: E402

client = TestClient(api.app)


class FakeParsed:
    def model_dump(self) -> dict:
        return {
            "action": "部品Aの在庫を確認する",
            "assignee": "田中さん",
            "deadline": "2026-09-16 15:00",
            "priority": "中",
            "action_required": True,
        }


class FakeOpenAI:
    """本物のクライアントの代わり。呼ばれた回数を数え、指定した回で失敗できる。"""

    def __init__(self, fail_on_call: int = 0) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise Exception("secret sk-12345")
        return SimpleNamespace(
            output_parsed=FakeParsed(),
            usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
        )


def sample_rows(count: int = 100) -> list:
    return read_trial_rows(PROJECT_ROOT / "data" / "raw_handover.csv", count)


def quiet(*_args, **_kwargs) -> None:
    return None


class SplitBatchesTest(unittest.TestCase):
    def test_100_rows_make_ten_batches_of_ten(self) -> None:
        batches = verify.split_batches(sample_rows(100))
        self.assertEqual([len(b) for b in batches], [10] * 10)

    def test_remainder_goes_to_last_batch(self) -> None:
        batches = verify.split_batches(sample_rows(25))
        self.assertEqual([len(b) for b in batches], [10, 10, 5])

    def test_invalid_size_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            verify.split_batches(sample_rows(5), 0)

    def test_batch_size_matches_the_api_limit(self) -> None:
        self.assertEqual(verify.BATCH_SIZE, api.MAX_EXTRACT_RECORDS)

    def test_request_record_has_only_input_fields(self) -> None:
        row = dict(sample_rows(1)[0], extracted_action="これは送らない")
        sent = verify.raw_request_record(row)
        self.assertEqual(
            set(sent), {"record_id", "submitted_at", "source_department", "message_text"}
        )


class RunBatchesTest(unittest.TestCase):
    def test_100_rows_are_sent_in_ten_requests_and_all_come_back(self) -> None:
        fake = FakeOpenAI()
        with mock.patch.object(api, "create_openai_client", return_value=fake):
            predictions, usage, error = verify.run_batches(client.post, sample_rows(100), quiet)
        self.assertIsNone(error)
        self.assertEqual(usage["requests"], 10)
        self.assertEqual(len(predictions), 100)
        self.assertEqual(fake.calls, 100)
        self.assertEqual(usage["input_tokens"], 100_000)
        self.assertEqual(usage["output_tokens"], 5_000)
        self.assertGreater(usage["estimated_cost_usd"], 0)
        expected_ids = [row["record_id"] for row in sample_rows(100)]
        self.assertEqual([p["record_id"] for p in predictions], expected_ids)

    def test_every_request_carries_the_paid_confirmation(self) -> None:
        sent = []

        def recording_post(url, json):
            sent.append(json)
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "count": len(json["records"]),
                    "records": [{"record_id": r["record_id"]} for r in json["records"]],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "estimated_cost_usd": 0.0},
                },
            )

        verify.run_batches(recording_post, sample_rows(25), quiet)
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(body["confirm_paid_api"] is True for body in sent))
        self.assertTrue(all(len(body["records"]) <= 10 for body in sent))

    def test_stops_at_first_failure_and_does_not_retry(self) -> None:
        fake = FakeOpenAI(fail_on_call=25)  # 3回目の5件目で失敗
        with mock.patch.object(api, "create_openai_client", return_value=fake):
            predictions, usage, error = verify.run_batches(client.post, sample_rows(100), quiet)
        self.assertIn("3回目", error)
        self.assertIn("502", error)
        self.assertNotIn("sk-12345", error)
        self.assertEqual(len(predictions), 20)  # 成功した2回分だけ
        self.assertEqual(usage["requests"], 2)
        self.assertEqual(fake.calls, 25)  # 失敗のあとに、追加の通信をしない

    def test_missing_api_key_stops_with_status_in_message(self) -> None:
        with mock.patch.object(
            api, "create_openai_client", side_effect=RuntimeError("OPENAI_API_KEYが設定されていません")
        ):
            predictions, _usage, error = verify.run_batches(client.post, sample_rows(10), quiet)
        self.assertEqual(predictions, [])
        self.assertIn("503", error)

    def test_exception_in_post_is_reported_without_details(self) -> None:
        def broken_post(url, json):
            raise ConnectionError("secret sk-12345")

        _p, _u, error = verify.run_batches(broken_post, sample_rows(10), quiet)
        self.assertIn("1回目", error)
        self.assertIn("ConnectionError", error)
        self.assertNotIn("sk-12345", error)


class CheckResponseTest(unittest.TestCase):
    def make_response(self, ids, count=None, status=200):
        return SimpleNamespace(
            status_code=status,
            json=lambda: {
                "count": len(ids) if count is None else count,
                "records": [{"record_id": i} for i in ids],
            },
        )

    def test_count_mismatch_is_an_error(self) -> None:
        batch = sample_rows(3)
        ids = [r["record_id"] for r in batch][:2]
        with self.assertRaises(verify.VerificationError):
            verify.check_response(1, batch, self.make_response(ids))

    def test_id_order_mismatch_is_an_error(self) -> None:
        batch = sample_rows(3)
        ids = [r["record_id"] for r in batch][::-1]
        with self.assertRaises(verify.VerificationError):
            verify.check_response(1, batch, self.make_response(ids))

    def test_non_200_is_an_error(self) -> None:
        batch = sample_rows(1)
        with self.assertRaises(verify.VerificationError):
            verify.check_response(1, batch, self.make_response([], status=422))


class ComparePredictionsTest(unittest.TestCase):
    def test_counts_matching_rows_and_fields(self) -> None:
        base = {
            "extracted_action": "A", "extracted_assignee": "田中", "extracted_deadline": "d",
            "extracted_priority": "中", "action_required": "true",
        }
        api_rows = [dict(base, record_id="1"), dict(base, record_id="2", extracted_assignee="")]
        cli_rows = [dict(base, record_id="1"), dict(base, record_id="2"), dict(base, record_id="3")]
        result = verify.compare_predictions(api_rows, cli_rows)
        self.assertEqual(result["common"], 2)
        self.assertEqual(result["same_all"], 1)
        self.assertEqual(result["per_field"]["extracted_assignee"], 1)
        self.assertEqual(result["per_field"]["extracted_action"], 2)


class OutputPathsTest(unittest.TestCase):
    def test_default_outputs_never_overwrite_the_cli_results(self) -> None:
        cli_files = {
            verify.CLI_PREDICTIONS_PATH,
            PROJECT_ROOT / "output" / "evaluation_summary.csv",
            PROJECT_ROOT / "output" / "evaluation_details.csv",
        }
        api_files = {
            verify.API_PREDICTIONS_PATH,
            verify.API_SUMMARY_PATH,
            verify.API_DETAILS_PATH,
        }
        self.assertTrue(cli_files.isdisjoint(api_files))


class MainTest(unittest.TestCase):
    def run_main(self, argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["verify_extract_api.py"] + argv):
            with contextlib.redirect_stdout(out):
                code = verify.main()
        return code, out.getvalue()

    def test_without_yes_no_communication_happens(self) -> None:
        fake = FakeOpenAI()
        with mock.patch.object(api, "create_openai_client", return_value=fake) as creator:
            code, text = self.run_main(["--limit", "20"])
        self.assertEqual(code, 0)
        self.assertIn("まだ通信していません", text)
        self.assertEqual(fake.calls, 0)
        creator.assert_not_called()

    def test_limit_over_100_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.run_main(["--limit", "101", "--yes"])

    def test_with_yes_runs_all_batches_saves_and_scores(self) -> None:
        fake = FakeOpenAI()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch.object(api, "create_openai_client", return_value=fake), \
                 mock.patch.object(verify, "API_SUMMARY_PATH", tmp_path / "s.csv"), \
                 mock.patch.object(verify, "API_DETAILS_PATH", tmp_path / "d.csv"), \
                 mock.patch.object(verify, "CLI_PREDICTIONS_PATH", tmp_path / "none.csv"):
                code, text = self.run_main(["--yes", "--output", str(tmp_path / "p.csv")])
            self.assertEqual(code, 0)
            self.assertEqual(fake.calls, 100)
            self.assertIn("送信 10回 / 受け取り 100件", text)
            self.assertIn("採点(APIの結果)", text)
            saved = verify.read_rows(tmp_path / "p.csv")
            self.assertEqual(len(saved), 100)
            self.assertTrue((tmp_path / "s.csv").exists())

    def test_failure_returns_1_saves_partial_and_skips_scoring(self) -> None:
        fake = FakeOpenAI(fail_on_call=15)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch.object(api, "create_openai_client", return_value=fake), \
                 mock.patch.object(verify, "API_SUMMARY_PATH", tmp_path / "s.csv"), \
                 mock.patch.object(verify, "API_DETAILS_PATH", tmp_path / "d.csv"):
                code, text = self.run_main(["--yes", "--output", str(tmp_path / "p.csv")])
            self.assertEqual(code, 1)
            self.assertIn("エラー:", text)
            self.assertEqual(len(verify.read_rows(tmp_path / "p.csv")), 10)
            self.assertFalse((tmp_path / "s.csv").exists())
            self.assertEqual(fake.calls, 15)


if __name__ == "__main__":
    unittest.main()
