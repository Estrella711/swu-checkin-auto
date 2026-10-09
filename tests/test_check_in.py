"""Exercise check-in behavior with an isolated transport; never call school APIs."""

import importlib.util
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "swu_checkin" / "check_in.py"
PACKAGE_NAME = "_check_in_test_package"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = []
info = types.ModuleType(f"{PACKAGE_NAME}.get_info")
for helper in ("get_dormitory", "get_student_id", "get_transition_today", "get_token"):
    setattr(info, helper, Mock())
transport = types.ModuleType("requests")
transport.exceptions = types.SimpleNamespace(RequestException=type("RequestException", (Exception,), {}))
transport.get = Mock()
transport.post = Mock()
spec = importlib.util.spec_from_file_location(f"{PACKAGE_NAME}.check_in", MODULE_PATH)
checkin = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {PACKAGE_NAME: package, info.__name__: info, "requests": transport}):
    spec.loader.exec_module(checkin)
BEIJING_NOW = checkin._beijing_now


class FrozenDateTime(datetime):
    """An execution host whose local clock is UTC, including a date boundary."""

    utc_now = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.utc_now.replace(tzinfo=None)
        return cls.utc_now.astimezone(tz)


class CheckInTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        self.current = datetime(2026, 10, 9, 21, 0, tzinfo=checkin.CHINA)
        self.clock = patch.object(checkin, "_beijing_now", side_effect=lambda: self.current).start()
        self.token = patch.object(checkin, "get_token", return_value="token").start()
        self.student = patch.object(checkin, "get_student_id", return_value="student").start()
        self.dorm = patch.object(
            checkin,
            "get_dormitory",
            return_value={
                "data": {
                    "columnList": [
                        {"prop": "qddz", "latitude": 29.8, "longitude": 106.4},
                        {"prop": "qsqddd", "value": "Dormitory"},
                        {"prop": "qdbj", "value": "101"},
                    ]
                }
            },
        ).start()
        unsigned = {"formId": "form", "id": "record", "qdzt": "未签到"}
        signed = {**unsigned, "qdzt": "已签到"}
        self.transition = patch.object(
            checkin, "get_transition_today", side_effect=[unsigned, unsigned, signed]
        ).start()
        self.response = Mock()
        self.response.json.return_value = {"code": 200}
        self.post = patch.object(checkin.requests, "post", return_value=self.response).start()
        self.get = patch.object(checkin.requests, "get", return_value=Mock()).start()
        self.get.return_value.json.return_value = {"data": {"records": []}}

    def enforce_window(self, target_date="2026-10-09"):
        os.environ["SWUDK_ENFORCE_WINDOW"] = "1"
        os.environ["SWUDK_TARGET_DATE"] = target_date

    def run_checkin(self, timeout=10):
        return checkin.check_in("username", "password", timeout)

    def test_beijing_clock_and_payload_date_ignore_utc_host(self):
        with patch.object(checkin, "datetime", FrozenDateTime):
            self.assertEqual(BEIJING_NOW(), datetime(2026, 10, 10, 2, tzinfo=checkin.CHINA))
            self.assertEqual(BEIJING_NOW().utcoffset(), timedelta(hours=8))

    def test_payload_uses_beijing_date_across_utc_date_boundary(self):
        self.current = FrozenDateTime.utc_now.astimezone(checkin.CHINA)
        self.assertEqual(self.run_checkin(), 1)
        payload = json.loads(self.post.call_args.kwargs["data"])
        self.assertEqual(payload["tsrq"], "2026-10-10")

    def test_leave_dates_are_compared_in_beijing_time(self):
        self.current = datetime(2026, 10, 10, 2, 0, tzinfo=checkin.CHINA)
        self.get.return_value.json.return_value = {
            "data": {
                "records": [{"lcztmc": "已同意", "kssj": "2026-10-10 01:00", "jssj": "2026-10-10 03:00"}]
            }
        }
        self.assertEqual(self.run_checkin(), 5)
        self.transition.assert_not_called()
        self.post.assert_not_called()

    def test_window_start_is_inclusive_and_stop_is_exclusive(self):
        self.enforce_window()
        self.assertEqual(checkin._request_timeout_in_window(10, self.current), 10)
        last_moment = self.current.replace(hour=23, minute=24, second=59, microsecond=500000)
        self.assertEqual(checkin._request_timeout_in_window(10, last_moment), 0.5)
        for hour, minute, second in ((20, 59, 59), (23, 25, 0), (23, 30, 0), (0, 0, 0)):
            with self.subTest(hour=hour, minute=minute, second=second):
                self.current = self.current.replace(hour=hour, minute=minute, second=second)
                self.assertEqual(self.run_checkin(), 4)
                self.token.assert_not_called()
                self.get.assert_not_called()
                self.transition.assert_not_called()
                self.post.assert_not_called()

    def test_target_date_prevents_next_day_submission(self):
        self.enforce_window()
        self.current += timedelta(days=1)
        self.assertEqual(self.run_checkin(), 4)
        self.token.assert_not_called()
        self.post.assert_not_called()

    def test_login_crossing_cutoff_never_saves(self):
        self.enforce_window()
        self.current = self.current.replace(hour=23, minute=24, second=59)

        def slow_login(*_args):
            self.current += timedelta(seconds=2)
            return "token"

        self.token.side_effect = slow_login
        self.assertEqual(self.run_checkin(), 4)
        self.token.assert_called_once()
        self.post.assert_not_called()

    def test_slow_student_lookup_crossing_midnight_never_saves(self):
        self.enforce_window()

        def slow_student(*_args):
            self.current += timedelta(days=1)
            return "student"

        self.student.side_effect = slow_student
        self.assertEqual(self.run_checkin(), 4)
        self.student.assert_called_once()
        self.post.assert_not_called()

    def test_slow_dormitory_lookup_crossing_cutoff_never_saves(self):
        self.enforce_window()
        self.current = self.current.replace(hour=23, minute=24, second=59)
        dormitory = self.dorm.return_value

        def slow_dormitory(*_args):
            self.current += timedelta(seconds=2)
            return dormitory

        self.dorm.side_effect = slow_dormitory
        self.assertEqual(self.run_checkin(), 4)
        self.dorm.assert_called_once()
        self.post.assert_not_called()

    def test_post_timeout_is_capped_by_remaining_window(self):
        self.enforce_window()
        self.current = self.current.replace(hour=23, minute=24, second=58, microsecond=500000)
        self.assertEqual(self.run_checkin(), 1)
        self.assertEqual(self.post.call_args.kwargs["timeout"], 1.5)

    def test_verified_post_returns_success(self):
        self.enforce_window()
        self.assertEqual(self.run_checkin(), 1)
        self.post.assert_called_once()
        self.response.raise_for_status.assert_called_once()
        self.assertEqual(self.transition.call_count, 3)

    def test_http_200_without_signed_state_remains_retryable(self):
        self.response.json.return_value = {"code": 500, "message": "Submission rejected"}
        unsigned = {"formId": "form", "id": "record", "qdzt": "未签到"}
        self.transition.side_effect = [unsigned, unsigned, unsigned]
        self.assertEqual(self.run_checkin(), 4)
        self.post.assert_called_once()
        self.assertIn(4, checkin.RETRYABLE_STATUS)

    def test_missing_confirmation_remains_retryable(self):
        unsigned = {"formId": "form", "id": "record", "qdzt": "未签到"}
        self.transition.side_effect = [unsigned, unsigned, None]
        self.assertEqual(self.run_checkin(), 4)
        self.post.assert_called_once()

    def test_delayed_confirmation_is_seen_on_retry_without_duplicate_save(self):
        unsigned = {"formId": "form", "id": "record", "qdzt": "未签到"}
        signed = {**unsigned, "qdzt": "已签到"}
        self.transition.side_effect = [unsigned, unsigned, unsigned, signed]
        with patch.object(checkin.time, "sleep") as sleep:
            result = checkin.check_in_with_retry("username", "password", max_attempts=2, retry_delay=1)
        self.assertEqual(result, 2)
        self.post.assert_called_once()
        sleep.assert_called_once_with(1)

    def test_already_signed_does_not_save(self):
        self.transition.side_effect = None
        self.transition.return_value = {"formId": "form", "id": "record", "qdzt": "已签到"}
        self.assertEqual(self.run_checkin(), 2)
        self.dorm.assert_not_called()
        self.student.assert_not_called()
        self.post.assert_not_called()

    def test_second_lookup_already_signed_does_not_save(self):
        self.transition.side_effect = [
            {"formId": "form", "id": "record", "qdzt": "未签到"},
            {"formId": "form", "id": "record", "qdzt": "已签到"},
        ]
        self.assertEqual(self.run_checkin(), 2)
        self.dorm.assert_not_called()
        self.post.assert_not_called()

    def test_manual_checkin_keeps_existing_time_behavior(self):
        os.environ["SWUDK_TARGET_DATE"] = "2026-10-01"
        self.current = self.current.replace(hour=23, minute=29)
        self.assertEqual(self.run_checkin(timeout=7), 1)
        self.assertEqual(self.post.call_args.kwargs["timeout"], 7)


if __name__ == "__main__":
    unittest.main()
