"""Preparation timing tests need only the Python standard library."""

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

PREWARM_PATH = Path(__file__).resolve().parents[1] / "src" / "swu_checkin" / "prewarm.py"
SPEC = importlib.util.spec_from_file_location("prewarm_under_test", PREWARM_PATH)
prewarm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prewarm)


class Clock:
    def __init__(self, hour=15, minute=43, second=0):
        self.current = datetime(2026, 10, 10, hour, minute, second, tzinfo=prewarm.BEIJING)
        self.sleeps = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class PrewarmTests(unittest.TestCase):
    def run_prewarm(self, clock, sleep=None):
        self.logs = []
        return prewarm.wait_for_window(
            now=clock.now, sleep=clock.sleep if sleep is None else sleep, emit=self.logs.append
        )

    def test_primary_1543_trigger_waits_five_hours_seven_minutes(self):
        clock = Clock()
        self.assertEqual(self.run_prewarm(clock), 0)
        self.assertEqual(clock.current, datetime(2026, 10, 10, 20, 50, tzinfo=prewarm.BEIJING))
        self.assertEqual(sum(clock.sleeps), (5 * 60 + 7) * 60)
        self.assertEqual(len(clock.sleeps), 307)
        self.assertTrue(all(0 < seconds <= 60 for seconds in clock.sleeps))
        self.assertIn("当前未提交签到", "\n".join(self.logs))
        self.assertIn("自动查寝打卡时段", "\n".join(self.logs))

    def test_last_sleep_is_shortened_to_exact_handoff(self):
        clock = Clock(20, 49, 45)
        self.assertEqual(self.run_prewarm(clock), 0)
        self.assertEqual(clock.sleeps, [15])
        self.assertEqual(clock.current.minute, 50)
        self.assertEqual(clock.current.second, 0)

    def test_utc_clock_uses_beijing_date_and_handoff(self):
        clock = Clock()
        clock.current = datetime(2026, 10, 10, 7, 43, tzinfo=timezone.utc)
        self.assertEqual(self.run_prewarm(clock), 0)
        self.assertEqual(sum(clock.sleeps), (5 * 60 + 7) * 60)
        self.assertEqual(clock.current.hour, 12)
        self.assertEqual(clock.current.minute, 50)

    def test_naive_injected_clock_is_explicitly_beijing_time(self):
        clock = Clock(20, 50)
        clock.current = clock.current.replace(tzinfo=None)
        self.assertEqual(self.run_prewarm(clock), 0)
        self.assertEqual(clock.sleeps, [])

    def test_ready_or_delayed_start_returns_without_sleep(self):
        for hour, minute, second in ((20, 50, 0), (21, 1, 0), (22, 54, 59)):
            with self.subTest(hour=hour, minute=minute, second=second):
                clock = Clock(hour, minute, second)
                self.assertEqual(self.run_prewarm(clock), 0)
                self.assertEqual(clock.sleeps, [])

    def test_early_or_expired_start_fails_without_sleep(self):
        for hour, minute, second in ((15, 39, 59), (0, 0, 0), (22, 55, 0), (23, 0, 0)):
            with self.subTest(hour=hour, minute=minute, second=second):
                clock = Clock(hour, minute, second)
                self.assertEqual(self.run_prewarm(clock), 1)
                self.assertEqual(clock.sleeps, [])
                self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_start_exactly_1540_is_allowed(self):
        clock = Clock(15, 40)
        self.assertEqual(self.run_prewarm(clock), 0)
        self.assertEqual(sum(clock.sleeps), 310 * 60)

    def test_clock_jump_to_deadline_fails_on_original_date(self):
        clock = Clock()

        def jump(_seconds):
            clock.current = clock.current.replace(hour=22, minute=55)

        self.assertEqual(self.run_prewarm(clock, sleep=jump), 1)
        self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_midnight_clock_jump_never_hands_off_another_date(self):
        clock = Clock()

        def jump(_seconds):
            clock.current = clock.current.replace(hour=0, minute=1) + timedelta(days=1)

        self.assertEqual(self.run_prewarm(clock, sleep=jump), 1)
        self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_jump_to_next_evening_fails_even_within_allowed_hours(self):
        clock = Clock()

        def jump(_seconds):
            clock.current = clock.current.replace(hour=20, minute=50) + timedelta(days=1)

        self.assertEqual(self.run_prewarm(clock, sleep=jump), 1)
        self.assertTrue(self.logs[-1].startswith("[4] "))

    def test_main_appends_original_date_only_after_success(self):
        clock = Clock()
        logs = []
        output = mock_open()
        with patch("builtins.open", output):
            result = prewarm.main(
                now=clock.now, sleep=clock.sleep, emit=logs.append, env={"GITHUB_OUTPUT": "workflow-output"}
            )
        self.assertEqual(result, 0)
        output.assert_called_once_with("workflow-output", "a", encoding="utf-8")
        output().write.assert_called_once_with("target_date=2026-10-10\n")

    def test_main_never_recomputes_target_date_after_wait(self):
        clock = Clock(20, 50)
        output = mock_open()

        def finish_then_delay(started, **kwargs):
            self.assertEqual(started.date(), datetime(2026, 10, 10).date())
            clock.current += timedelta(days=1)
            return 0

        with patch.object(prewarm, "_wait_for_window", side_effect=finish_then_delay):
            with patch("builtins.open", output):
                result = prewarm.main(now=clock.now, env={"GITHUB_OUTPUT": "workflow-output"}, emit=Mock())
        self.assertEqual(result, 0)
        output().write.assert_called_once_with("target_date=2026-10-10\n")

    def test_failed_wait_writes_no_target_date(self):
        clock = Clock(23, 0)
        with patch("builtins.open") as output:
            result = prewarm.main(now=clock.now, sleep=clock.sleep, env={"GITHUB_OUTPUT": "workflow-output"}, emit=Mock())
        self.assertEqual(result, 1)
        output.assert_not_called()

    def test_output_write_failure_fails_so_dependent_job_cannot_continue(self):
        clock = Clock(20, 50)
        logs = []
        with patch("builtins.open", side_effect=OSError("sensitive path")):
            result = prewarm.main(now=clock.now, env={"GITHUB_OUTPUT": "workflow-output"}, emit=logs.append)
        self.assertEqual(result, 1)
        self.assertTrue(logs[-1].startswith("[4] "))
        self.assertNotIn("sensitive path", "\n".join(logs))

    def test_local_success_does_not_need_a_github_output_file(self):
        clock = Clock(20, 50)
        with patch("builtins.open") as output:
            result = prewarm.main(now=clock.now, env={}, emit=Mock())
        self.assertEqual(result, 0)
        output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
