"""Actual-time dispatch tests make no real GitHub or school requests."""

import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from http.client import BadStatusLine
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

DISPATCH_PATH = Path(__file__).resolve().parents[1] / "src" / "swu_checkin" / "dispatch.py"
SPEC = importlib.util.spec_from_file_location("dispatch_under_test", DISPATCH_PATH)
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)


def at(hour=21, minute=0, second=0, day=11):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=dispatch.BEIJING)


def run_record(
    *, run_id=1, created=None, status="completed", conclusion="success", title="自动查寝 · 2026-10-11", event="workflow_dispatch"
):
    created = at() if created is None else created
    return {
        "id": run_id,
        "created_at": created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "conclusion": conclusion,
        "display_title": title,
        "event": event,
    }


class FakeResponse:
    def __init__(self, payload=None, status=200, raw=None):
        self.status = status
        self.raw = json.dumps(payload).encode("utf-8") if raw is None else raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return self.raw


class DecisionTests(unittest.TestCase):
    def test_actual_time_window_and_explicit_inputs(self):
        for current in (at(), at(22, 54, 59)):
            with self.subTest(current=current):
                decision = dispatch.decide(current, [])
                self.assertEqual(decision.action, "checkin")
                self.assertEqual(
                    decision.inputs(), {"scheduled": True, "target_date": "2026-10-11", "notify_test": False}
                )

    def test_utc_clock_selects_beijing_window(self):
        decision = dispatch.decide(datetime(2026, 10, 11, 13, 0, tzinfo=timezone.utc), [])
        self.assertEqual(decision.action, "checkin")
        self.assertEqual(decision.target_date, "2026-10-11")

    def test_no_new_job_or_alert_during_cleanup(self):
        for current in (at(22, 55), at(22, 59, 59)):
            with self.subTest(current=current):
                self.assertEqual(dispatch.decide(current, []).action, "skip")

    def test_after_2300_checks_today_and_before_2100_checks_yesterday(self):
        self.assertEqual(dispatch.decide(at(23), []).inputs(), {"missed_date": "2026-10-11"})
        for current in (at(0, day=12), at(12, day=12), at(20, 59, 59, day=12)):
            with self.subTest(current=current):
                decision = dispatch.decide(current, [])
                self.assertEqual(decision.action, "missed")
                self.assertEqual(decision.target_date, "2026-10-11")

    def test_legacy_success_title_is_accepted_in_target_window(self):
        legacy = run_record(title="SWU自动签到01", created=at(21, 8, day=10))
        decision = dispatch.decide(at(12), [legacy])
        self.assertEqual(decision.action, "skip")
        self.assertIn("签到成功", decision.reason)

    def test_success_outside_target_window_does_not_confirm_tonight(self):
        for created in (at(20, 59, 59), at(22, 55), at(21, day=10)):
            with self.subTest(created=created):
                self.assertEqual(dispatch.decide(at(21, 20), [run_record(created=created)]).action, "checkin")

    def test_same_evening_active_automatic_run_prevents_duplicate_dispatch(self):
        for status in dispatch.ACTIVE_STATUSES:
            with self.subTest(status=status):
                active = run_record(status=status, conclusion=None)
                self.assertEqual(dispatch.decide(at(21, 10), [active]).action, "skip")

    def test_legacy_schedule_active_can_prevent_duplicate(self):
        active = run_record(status="in_progress", conclusion=None, title="SWU自动签到01", event="schedule", created=at(20, 43))
        self.assertEqual(dispatch.decide(at(21, 10), [active]).action, "skip")

    def test_other_date_automatic_active_does_not_block_tonight(self):
        old = run_record(status="queued", conclusion=None, title="自动查寝 · 2026-10-10", created=at(21, day=10))
        self.assertEqual(dispatch.decide(at(21, 10), [old]).action, "checkin")

    def test_completed_failure_allows_window_retry_and_suppresses_later_notice(self):
        failed = run_record(conclusion="failure")
        self.assertEqual(dispatch.decide(at(21, 10), [failed]).action, "checkin")
        self.assertEqual(dispatch.decide(at(23), [failed]).action, "skip")

    def test_mail_tests_do_not_count_as_sign_in_or_reported_failure(self):
        for conclusion in ("success", "failure"):
            with self.subTest(conclusion=conclusion):
                test = run_record(title="邮件通知测试", conclusion=conclusion)
                self.assertEqual(dispatch.decide(at(21, 10), [test]).action, "checkin")
                self.assertEqual(dispatch.decide(at(23), [test]).action, "missed")

    def test_existing_notice_for_target_date_is_never_duplicated(self):
        for status, conclusion in (("queued", None), ("completed", "success"), ("completed", "failure")):
            with self.subTest(status=status, conclusion=conclusion):
                notice = run_record(status=status, conclusion=conclusion, title="未签到告警 · 2026-10-11", created=at(23))
                self.assertEqual(dispatch.decide(at(12, day=12), [notice]).action, "skip")

    def test_active_manual_main_in_target_window_suppresses_missed_notice(self):
        active = run_record(status="in_progress", conclusion=None, title="手动签到")
        self.assertEqual(dispatch.decide(at(23), [active]).action, "skip")

    def test_active_manual_main_in_window_prevents_queued_duplicate_signin(self):
        for status in ("queued", "in_progress"):
            with self.subTest(status=status):
                active = run_record(status=status, conclusion=None, title="手动签到")
                self.assertEqual(dispatch.decide(at(21, 10), [active]).action, "skip")

    def test_cancelled_main_does_not_claim_failure_notification_was_sent(self):
        cancelled = run_record(conclusion="cancelled")
        self.assertEqual(dispatch.decide(at(23), [cancelled]).action, "missed")

    def test_malformed_run_inventory_fails_instead_of_assuming_absent(self):
        for invalid in ({"status": "completed"}, run_record(status="unknown")):
            with self.subTest(invalid=invalid):
                with self.assertRaises(dispatch.DispatchError):
                    dispatch.decide(at(23), [invalid])


class OrchestrationTests(unittest.TestCase):
    def make_client(self, runs=None):
        client = Mock()
        client.list_runs.return_value = [] if runs is None else runs
        return client

    def invoke(self, client, times=None, notify_test=False):
        self.logs = []
        self.summaries = []
        current = at(21, 10) if times is None else None
        now = (lambda: current) if times is None else Mock(side_effect=times)
        return dispatch.run_dispatch(
            client=client, now=now, emit=self.logs.append, summary=self.summaries.append, notify_test=notify_test
        )

    def test_dispatch_and_summary_use_actual_date_and_48h_inventory(self):
        client = self.make_client()
        self.assertEqual(self.invoke(client), 0)
        client.list_runs.assert_called_once_with(at(21, 10).astimezone(timezone.utc) - timedelta(hours=48))
        client.dispatch.assert_called_once_with({"scheduled": True, "target_date": "2026-10-11", "notify_test": False})
        self.assertEqual(self.logs, self.summaries)
        self.assertTrue(self.logs[-1].startswith("dispatch:"))

    def test_missed_summary_and_only_notification_inputs(self):
        client = self.make_client()
        self.assertEqual(self.invoke(client, times=[at(23)] * 3), 0)
        client.dispatch.assert_called_once_with({"missed_date": "2026-10-11"})
        self.assertTrue(self.logs[-1].startswith("missed:"))

    def test_notify_test_bypasses_clock_and_inventory(self):
        client = self.make_client()
        self.assertEqual(self.invoke(client, times=[], notify_test=True), 0)
        client.list_runs.assert_not_called()
        client.dispatch.assert_called_once_with({"scheduled": False, "notify_test": True})
        self.assertEqual(self.summaries, ["dispatch: 派发邮件测试（未调用学校接口）"])

    def test_cleanup_skip_needs_no_api_calls(self):
        client = self.make_client()
        self.assertEqual(self.invoke(client, times=[at(22, 55)]), 0)
        client.list_runs.assert_not_called()
        client.dispatch.assert_not_called()
        self.assertTrue(self.logs[-1].startswith("skip:"))

    def test_query_crossing_window_never_dispatches_signin(self):
        for after in (at(22, 55), at(23), at(21, day=12)):
            with self.subTest(after=after):
                client = self.make_client()
                self.assertEqual(self.invoke(client, times=[at(21, 10), after]), 0)
                client.dispatch.assert_not_called()

    def test_final_post_clock_check_prevents_late_dispatch(self):
        client = self.make_client()
        self.assertEqual(self.invoke(client, times=[at(22, 54, 50), at(22, 54, 59), at(22, 55)]), 0)
        client.dispatch.assert_not_called()

    def test_midnight_alert_keeps_correct_evening(self):
        client = self.make_client()
        self.assertEqual(self.invoke(client, times=[at(23, 59), at(0, day=12), at(0, day=12)]), 0)
        client.dispatch.assert_called_once_with({"missed_date": "2026-10-11"})

    def test_success_run_skips_without_post(self):
        client = self.make_client([run_record()])
        self.assertEqual(self.invoke(client, times=[at(21, 10)] * 2), 0)
        client.dispatch.assert_not_called()
        self.assertTrue(self.logs[-1].startswith("skip:"))

    def test_manual_active_run_skips_without_post(self):
        active = run_record(status="in_progress", conclusion=None, title="手动签到")
        client = self.make_client([active])
        self.assertEqual(self.invoke(client, times=[at(21, 10)] * 2), 0)
        client.dispatch.assert_not_called()
        self.assertTrue(self.logs[-1].startswith("skip:"))

    def test_incomplete_or_failed_get_is_failure_and_never_posts(self):
        client = self.make_client()
        client.list_runs.side_effect = dispatch.DispatchError("token secret body should not be logged", http_status=403)
        self.assertEqual(self.invoke(client), 1)
        client.dispatch.assert_not_called()
        self.assertNotIn("secret", "\n".join(self.logs + self.summaries))
        self.assertIn("HTTP 403", self.logs[-1])

    def test_failed_post_does_not_claim_dispatch_success(self):
        client = self.make_client()
        client.dispatch.side_effect = URLError("sensitive token")
        self.assertEqual(self.invoke(client, notify_test=True), 1)
        self.assertTrue(self.logs[-1].startswith("error:"))
        self.assertNotIn("sensitive", "\n".join(self.logs))


class GitHubClientTests(unittest.TestCase):
    def client(self, opener):
        return dispatch.GitHubClient("Estrella711/swu-checkin-auto", "test-sensitive-token", opener=opener)

    def test_get_uses_fixed_workflow_headers_filter_and_30s_timeout(self):
        opener = Mock(return_value=FakeResponse({"total_count": 0, "workflow_runs": []}))
        self.assertEqual(self.client(opener).list_runs(at() - timedelta(hours=48)), [])
        request = opener.call_args.args[0]
        parsed = urlparse(request.full_url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "api.github.com")
        self.assertEqual(parsed.path, "/repos/Estrella711/swu-checkin-auto/actions/workflows/checkin.yml/runs")
        self.assertEqual(parse_qs(parsed.query), {"per_page": ["100"], "page": ["1"], "created": [">=2026-10-09T13:00:00Z"]})
        self.assertEqual(opener.call_args.kwargs, {"timeout": 30})
        self.assertEqual(request.get_header("Authorization"), "Bearer test-sensitive-token")
        self.assertNotIn("test-sensitive-token", request.full_url)

    def test_more_than_100_runs_are_fully_paginated(self):
        records = [run_record(run_id=index) for index in range(1, 151)]
        opener = Mock(side_effect=[
            FakeResponse({"total_count": 150, "workflow_runs": records[:100]}),
            FakeResponse({"total_count": 150, "workflow_runs": records[100:]}),
        ])
        result = self.client(opener).list_runs(at() - timedelta(hours=48))
        self.assertEqual(len(result), 150)
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(parse_qs(urlparse(opener.call_args.args[0].full_url).query)["page"], ["2"])

    def test_partial_inventory_or_over_1000_runs_fails(self):
        for payload in (
            {"total_count": 101, "workflow_runs": [run_record()]},
            {"total_count": 1001, "workflow_runs": []},
            {"total_count": None, "workflow_runs": []},
            {"total_count": 1, "workflow_runs": [{"id": 1, "status": "completed"}]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(dispatch.DispatchError):
                    self.client(Mock(return_value=FakeResponse(payload))).list_runs(at() - timedelta(hours=48))

    def test_duplicate_page_records_cannot_fake_complete_coverage(self):
        records = [run_record(run_id=index) for index in range(1, 101)]
        opener = Mock(side_effect=[
            FakeResponse({"total_count": 101, "workflow_runs": records}),
            FakeResponse({"total_count": 101, "workflow_runs": [records[-1]]}),
        ])
        with self.assertRaises(dispatch.DispatchError):
            self.client(opener).list_runs(at() - timedelta(hours=48))

    def test_post_accepts_current_200_and_legacy_204(self):
        for status in (200, 204):
            with self.subTest(status=status):
                opener = Mock(return_value=FakeResponse(status=status, raw=b""))
                self.client(opener).dispatch({"scheduled": False, "notify_test": True})
                request = opener.call_args.args[0]
                self.assertTrue(request.full_url.endswith("/actions/workflows/checkin.yml/dispatches"))
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(json.loads(request.data), {"ref": "main", "inputs": {"scheduled": False, "notify_test": True}})
                self.assertEqual(opener.call_args.kwargs, {"timeout": 30})

    def test_request_exceptions_never_include_server_body_token_or_url(self):
        errors = (
            HTTPError("https://secret.invalid/?token=secret", 403, "secret body", {}, None),
            URLError("secret token"),
            BadStatusLine("secret response body"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(dispatch.DispatchError) as captured:
                    self.client(Mock(side_effect=error)).dispatch({"notify_test": True})
                self.assertNotIn("secret", str(captured.exception))
                self.assertNotIn("https", str(captured.exception))

    def test_invalid_repository_cannot_change_api_host_or_path(self):
        for repository in ("https://evil.invalid/repo", "owner/repo/extra", "owner/repo?token=x", ""):
            with self.subTest(repository=repository):
                with self.assertRaises(dispatch.DispatchError):
                    dispatch.GitHubClient(repository, "token")

    def test_main_reads_only_github_token_and_notify_test_flag(self):
        environment = {
            "GITHUB_REPOSITORY": "Estrella711/swu-checkin-auto",
            "GITHUB_TOKEN": "token-from-workflow",
            "GITHUB_REF_NAME": "feature/watchdog-test",
            "CHECKIN_REF": "main",
            "DISPATCH_NOTIFY_TEST": "true",
        }
        with patch.dict(dispatch.os.environ, environment, clear=True):
            with patch.object(dispatch, "GitHubClient") as client_class:
                with patch.object(dispatch, "run_dispatch", return_value=0) as run_dispatch:
                    self.assertEqual(dispatch.main(), 0)
        client_class.assert_called_once_with("Estrella711/swu-checkin-auto", "token-from-workflow", ref="main")
        self.assertTrue(run_dispatch.call_args.kwargs["notify_test"])

    def test_main_dispatches_configured_default_branch_instead_of_triggering_branch(self):
        environment = {
            "GITHUB_REPOSITORY": "Estrella711/swu-checkin-auto",
            "GITHUB_TOKEN": "token-from-workflow",
            "GITHUB_REF_NAME": "feature/watchdog-test",
            "CHECKIN_REF": "stable-default",
        }
        with patch.dict(dispatch.os.environ, environment, clear=True):
            with patch.object(dispatch, "GitHubClient") as client_class:
                with patch.object(dispatch, "run_dispatch", return_value=0):
                    self.assertEqual(dispatch.main(), 0)
        client_class.assert_called_once_with("Estrella711/swu-checkin-auto", "token-from-workflow", ref="stable-default")

    def test_main_defaults_to_main_when_only_triggering_feature_branch_is_present(self):
        environment = {
            "GITHUB_REPOSITORY": "Estrella711/swu-checkin-auto",
            "GITHUB_TOKEN": "token-from-workflow",
            "GITHUB_REF_NAME": "feature/watchdog-test",
        }
        with patch.dict(dispatch.os.environ, environment, clear=True):
            with patch.object(dispatch, "GitHubClient") as client_class:
                with patch.object(dispatch, "run_dispatch", return_value=0):
                    self.assertEqual(dispatch.main(), 0)
        client_class.assert_called_once_with("Estrella711/swu-checkin-auto", "token-from-workflow", ref="main")


if __name__ == "__main__":
    unittest.main()
