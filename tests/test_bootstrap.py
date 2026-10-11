"""Bootstrap tests use a simulated clock and never wait or call school APIs."""

import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

BOOTSTRAP_PATH = Path(__file__).resolve().parents[1] / "src" / "swu_checkin" / "bootstrap.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_under_test", BOOTSTRAP_PATH)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class Clock:
    def __init__(self, hour=16, minute=50):
        self.current = datetime(2026, 10, 11, hour, minute, tzinfo=bootstrap.BEIJING)
        self.sleeps = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=BOOTSTRAP_PATH.parents[2])
        self.addCleanup(self.directory.cleanup)
        self.output_path = Path(self.directory.name) / "github-output"
        self.logs = []

    def wait(self, clock, **kwargs):
        return bootstrap.wait_until_evening(
            now=clock.now,
            sleep=kwargs.pop("sleep", clock.sleep),
            emit=self.logs.append,
            env=kwargs.pop("env", {"GITHUB_OUTPUT": str(self.output_path)}),
            **kwargs,
        )

    def test_first_deployment_1650_waits_until_same_day_2100(self):
        clock = Clock()
        self.assertEqual(self.wait(clock), 0)
        self.assertEqual(clock.current, datetime(2026, 10, 11, 21, 0, tzinfo=bootstrap.BEIJING))
        self.assertEqual(sum(clock.sleeps), 250 * 60)
        self.assertTrue(all(0 < seconds <= 60 for seconds in clock.sleeps))
        self.assertEqual(self.output_path.read_text(encoding="utf-8"), "target_date=2026-10-11\n")
        self.assertIn("首次启用等待", "\n".join(self.logs))
        self.assertIn("本步骤未调用学校接口", self.logs[-1])

    def test_1530_boundary_waits_at_most_330_minutes(self):
        clock = Clock(15, 30)
        self.assertEqual(self.wait(clock), 0)
        self.assertEqual(sum(clock.sleeps), 330 * 60)
        self.assertEqual(max(clock.sleeps), 60)

    def test_1529_is_rejected_without_wait_or_output(self):
        clock = Clock(15, 29)
        self.assertEqual(self.wait(clock), 1)
        self.assertEqual(clock.sleeps, [])
        self.assertFalse(self.output_path.exists())

    def test_2100_or_later_hands_off_immediately(self):
        for hour, minute in ((21, 0), (21, 1), (22, 55), (23, 59)):
            with self.subTest(hour=hour, minute=minute):
                clock = Clock(hour, minute)
                self.assertEqual(self.wait(clock), 0)
                self.assertEqual(clock.sleeps, [])

    def test_utc_clock_is_converted_to_beijing_before_wait(self):
        clock = Clock()
        clock.current = datetime(2026, 10, 11, 8, 50, tzinfo=timezone.utc)
        self.assertEqual(self.wait(clock), 0)
        self.assertEqual(sum(clock.sleeps), 250 * 60)
        self.assertEqual(self.output_path.read_text(encoding="utf-8"), "target_date=2026-10-11\n")

    def test_naive_clock_explicitly_means_beijing(self):
        clock = Clock()
        clock.current = datetime(2026, 10, 11, 16, 50)
        self.assertEqual(self.wait(clock), 0)
        self.assertEqual(sum(clock.sleeps), 250 * 60)
        self.assertEqual(self.output_path.read_text(encoding="utf-8"), "target_date=2026-10-11\n")

    def test_wait_crossing_midnight_refuses_handoff(self):
        clock = Clock()

        def jump_to_next_day(_seconds):
            clock.current += timedelta(days=1)

        self.assertEqual(self.wait(clock, sleep=jump_to_next_day), 1)
        self.assertFalse(self.output_path.exists())
        self.assertIn("日期已变化", self.logs[-1])

    def test_clock_changes_date_before_output_refuses_handoff(self):
        now = Mock(side_effect=[
            datetime(2026, 10, 11, 23, 59, tzinfo=bootstrap.BEIJING),
            datetime(2026, 10, 12, 0, 0, tzinfo=bootstrap.BEIJING),
        ])
        sleep = Mock()
        code = bootstrap.wait_until_evening(
            now=now, sleep=sleep, emit=self.logs.append, env={"GITHUB_OUTPUT": str(self.output_path)}
        )
        self.assertEqual(code, 1)
        sleep.assert_not_called()
        self.assertFalse(self.output_path.exists())

    def test_output_appends_captured_date_and_preserves_existing_values(self):
        self.output_path.write_text("existing=value\n", encoding="utf-8")
        self.assertEqual(self.wait(Clock(21, 0)), 0)
        self.assertEqual(
            self.output_path.read_text(encoding="utf-8"), "existing=value\ntarget_date=2026-10-11\n"
        )

    def test_missing_output_variable_fails_without_waiting(self):
        clock = Clock()
        self.assertEqual(self.wait(clock, env={}), 1)
        self.assertEqual(clock.sleeps, [])
        self.assertFalse(self.output_path.exists())

    def test_output_io_failure_returns_failure(self):
        with patch("builtins.open", side_effect=OSError("private path must not be logged")):
            self.assertEqual(self.wait(Clock(21, 0)), 1)
        self.assertNotIn("private path", "\n".join(self.logs))
        self.assertFalse(self.output_path.exists())

    def test_output_write_failure_returns_failure(self):
        writer = mock_open()
        writer.return_value.write.side_effect = OSError("write failed")
        with patch("builtins.open", writer):
            self.assertEqual(self.wait(Clock(21, 0)), 1)
        self.assertFalse(self.output_path.exists())

    def test_clock_read_failure_returns_failure(self):
        with patch.object(Clock, "now", side_effect=OSError("clock read failed")):
            self.assertEqual(self.wait(Clock()), 1)
        self.assertFalse(self.output_path.exists())


if __name__ == "__main__":
    unittest.main()
