"""Scheduler tests run without importing requests, OCR or the school APIs."""

import importlib.util
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

SCHEDULED_PATH = Path(__file__).resolve().parents[1] / "src" / "swu_checkin" / "scheduled.py"
SPEC = importlib.util.spec_from_file_location("scheduled_under_test", SCHEDULED_PATH)
scheduled = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduled)


class Clock:
    def __init__(self, hour=21, minute=1, second=0):
        self.current = datetime(2026, 10, 9, hour, minute, second, tzinfo=scheduled.BEIJING)
        self.sleeps = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


def child(status=1, returncode=0, stdout=None):
    output = f"[{status}] {scheduled.STATUS_MESSAGES[status]}\n" if stdout is None else stdout
    return subprocess.CompletedProcess([], returncode, stdout=output)


class ScheduledTests(unittest.TestCase):
    def run_scheduler(self, clock, runner, **kwargs):
        self.logs = []
        return scheduled.run_scheduled(
            now=clock.now, sleep=clock.sleep, runner=runner, env={}, emit=self.logs.append, **kwargs
        )

    def test_warm_start_waits_until_2101_and_forces_window_guard(self):
        clock = Clock(20, 43)
        execution_times = []

        def runner(command, **kwargs):
            execution_times.append(clock.current)
            self.assertEqual(command, [sys.executable, "-m", "swu_checkin.check_in"])
            self.assertEqual(kwargs["env"]["SWUDK_ENFORCE_WINDOW"], "1")
            self.assertEqual(kwargs["env"]["SWUDK_TARGET_DATE"], "2026-10-09")
            self.assertEqual(kwargs["env"]["PYTHONUNBUFFERED"], "1")
            self.assertEqual(kwargs["env"]["SWUDK_DEBUG_CREDENTIALS"], "0")
            self.assertEqual(kwargs["timeout"], 600)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
            self.assertTrue(kwargs["text"])
            self.assertEqual(kwargs["encoding"], "utf-8")
            self.assertEqual(kwargs["errors"], "replace")
            return child()

        self.assertEqual(self.run_scheduler(clock, runner), 0)
        self.assertEqual(execution_times, [datetime(2026, 10, 9, 21, 1, tzinfo=scheduled.BEIJING)])
        self.assertEqual(sum(clock.sleeps), 18 * 60)
        self.assertEqual(self.logs[-1], "[1] 签到成功")

    def test_no_record_and_network_failure_retry_until_confirmed_success(self):
        clock = Clock()
        runner = Mock(side_effect=[child(0, 1), child(4, 1), child(1)])
        self.assertEqual(self.run_scheduler(clock, runner), 0)
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(clock.sleeps, [300, 300])
        self.assertEqual(clock.current.hour, 21)
        self.assertEqual(clock.current.minute, 11)

    def test_configured_interval_and_already_checked_in(self):
        clock = Clock()
        runner = Mock(side_effect=[child(3, 1), child(2)])
        self.assertEqual(self.run_scheduler(clock, runner, poll_interval="45"), 0)
        self.assertEqual(clock.sleeps, [45])
        self.assertEqual(self.logs[-1], "[2] 已签到")

    def test_invalid_intervals_fail_without_running_or_sleeping(self):
        for interval in ("invalid", "1.5", "-1", "0", 0, -5):
            with self.subTest(interval=interval):
                clock = Clock()
                runner = Mock()
                self.assertEqual(self.run_scheduler(clock, runner, poll_interval=interval), 1)
                runner.assert_not_called()
                self.assertEqual(clock.sleeps, [])
                self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_early_and_late_starts_fail_instead_of_skipping_green(self):
        for hour, minute in ((20, 39), (23, 25), (23, 30), (0, 0), (12, 0)):
            with self.subTest(hour=hour, minute=minute):
                runner = Mock()
                self.assertEqual(self.run_scheduler(Clock(hour, minute), runner), 1)
                runner.assert_not_called()
                self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_deadline_caps_timeout_and_wait_and_never_starts_new_child(self):
        clock = Clock(23, 24, 30)
        runner = Mock(return_value=child(0, 1))
        self.assertEqual(self.run_scheduler(clock, runner), 1)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(runner.call_args.kwargs["timeout"], 30)
        self.assertEqual(clock.sleeps, [30])
        self.assertEqual(clock.current.minute, 25)
        self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_success_after_deadline_does_not_return_green(self):
        clock = Clock(23, 24, 59)

        def runner(*args, **kwargs):
            clock.current += timedelta(seconds=2)
            return child(1)

        self.assertEqual(self.run_scheduler(clock, runner), 1)
        self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_warm_wait_crossing_midnight_keeps_original_deadline(self):
        clock = Clock(20, 43)
        runner = Mock()

        def sleep(_seconds):
            clock.current += timedelta(days=1)

        self.logs = []
        code = scheduled.run_scheduled(
            now=clock.now, sleep=sleep, runner=runner, env={}, emit=self.logs.append
        )
        self.assertEqual(code, 1)
        runner.assert_not_called()
        self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_utc_clock_converts_to_beijing_without_host_timezone(self):
        clock = Clock()
        clock.current = datetime(2026, 10, 9, 13, 1, tzinfo=timezone.utc)
        runner = Mock(return_value=child(2))
        self.assertEqual(self.run_scheduler(clock, runner), 0)
        self.assertEqual(clock.sleeps, [])

    def test_timeout_partial_output_and_missing_results_retry(self):
        clock = Clock()
        runner = Mock(
            side_effect=[
                subprocess.TimeoutExpired(["python"], 180, output=b"[1] partial"),
                child(stdout="[1]"),
                child(stdout="diagnostic without result\n"),
                child(1),
            ]
        )
        self.assertEqual(self.run_scheduler(clock, runner), 0)
        self.assertEqual(runner.call_count, 4)
        self.assertEqual(clock.sleeps, [300, 300, 300])
        self.assertIn("本轮签到超时，等待下一轮", self.logs)

    def test_missing_final_result_cannot_use_an_earlier_success_line(self):
        clock = Clock(23, 24, 59)
        runner = Mock(return_value=child(stdout="[1] 签到成功\nTraceback: failed\n"))
        self.assertEqual(self.run_scheduler(clock, runner), 1)
        self.assertEqual(runner.call_count, 1)
        self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_success_status_with_nonzero_exit_is_not_confirmed(self):
        clock = Clock()
        runner = Mock(side_effect=[child(1, 1), child(2, 0)])
        self.assertEqual(self.run_scheduler(clock, runner), 0)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(clock.sleeps, [300])

    def test_leave_is_terminal_and_preserves_failure_email_behavior(self):
        clock = Clock()
        runner = Mock(return_value=child(5, 1))
        self.assertEqual(self.run_scheduler(clock, runner), 1)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(self.logs[-1], "[5] 请假期间无需签到")

    def test_spawn_failure_retries_then_can_succeed(self):
        clock = Clock()
        runner = Mock(side_effect=[OSError("secret should not be logged"), child(1)])
        self.assertEqual(self.run_scheduler(clock, runner), 0)
        self.assertEqual(runner.call_count, 2)
        self.assertNotIn("secret should not be logged", "\n".join(self.logs))

    def test_logs_redact_credentials_and_prefix_workflow_commands(self):
        clock = Clock()
        logs = []
        env = {"SWUDK_USERNAME": "student123", "SWUDK_PASSWORD": "longpassword", "GITHUB_TOKEN": "secret_token"}
        output = "::error:: student123 longpassword secret_token\n\x1b[31mdebug\x1b[0m\n[1] 签到成功\n"
        runner = Mock(return_value=child(stdout=output))
        self.assertEqual(
            scheduled.run_scheduled(now=clock.now, sleep=clock.sleep, runner=runner, env=env, emit=logs.append), 0
        )
        joined = "\n".join(logs)
        for secret in env.values():
            self.assertNotIn(secret, joined)
        self.assertIn("  子进程 | ::error:: *** *** ***", joined)
        self.assertNotIn("\x1b", joined)
        self.assertEqual(logs[-1], "[1] 签到成功")
        self.assertNotIn("SWUDK_ENFORCE_WINDOW", env)

    def test_long_logs_are_truncated_but_final_status_remains_parseable(self):
        clock = Clock()
        runner = Mock(return_value=child(stdout="x" * 25000 + "\n[2] 已签到\n"))
        self.assertEqual(self.run_scheduler(clock, runner), 0)
        self.assertIn("  子进程 | 日志过长，后续内容已截断", self.logs)
        self.assertEqual(self.logs[-1], "[2] 已签到")


if __name__ == "__main__":
    unittest.main()
