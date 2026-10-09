"""Run the existing check-in command until the same evening's deadline.

This file deliberately uses only the standard library and runs directly:
``python src/swu_checkin/scheduled.py``. Importing the package would load OCR
dependencies before the scheduler can wait for the check-in window.
"""

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")
DEFAULT_POLL_INTERVAL = 300
CHILD_TIMEOUT = 600
MAX_LOG_CHARS = 20000
STATUS_MESSAGES = {
    0: "今日无签到记录",
    1: "签到成功",
    2: "已签到",
    3: "登录失败",
    4: "网络错误或数据异常",
    5: "请假期间无需签到",
}
RESULT_LINE = re.compile(r"^\[([0-5])\]\s+(.+)$")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def beijing_now() -> datetime:
    return datetime.now(BEIJING)


def _as_beijing(value: datetime) -> datetime:
    # Naive injected times are explicitly Beijing time, never the host timezone.
    if value.tzinfo is None:
        return value.replace(tzinfo=BEIJING)
    return value.astimezone(BEIJING)


def _poll_interval(value: object) -> int:
    if value is None or value == "":
        return DEFAULT_POLL_INTERVAL
    interval = int(value)
    if interval <= 0:
        raise ValueError("poll interval must be positive")
    return interval


def _decode_output(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def _forward_logs(output: str, env: Mapping[str, str], emit: Callable[[str], None]) -> None:
    # Redact before truncating so a cut through a secret cannot reveal its prefix.
    sensitive_values = {
        value
        for name, value in env.items()
        if value and name.upper().endswith(("PASSWORD", "TOKEN", "SECRET", "API_KEY", "USERNAME", "EMAIL"))
    }
    for value in sorted(sensitive_values, key=len, reverse=True):
        output = output.replace(value, "***")
    output = ANSI_ESCAPE.sub("", output)
    output = "".join(char for char in output if char in "\n\t" or ord(char) >= 32)
    truncated = len(output) > MAX_LOG_CHARS
    for line in output[:MAX_LOG_CHARS].splitlines():
        # Prefix each line so child text cannot act as a GitHub workflow command
        # or be mistaken for this wrapper's final machine-readable result.
        emit(f"  子进程 | {line}")
    if truncated:
        emit("  子进程 | 日志过长，后续内容已截断")


def _result_status(output: str) -> int | None:
    lines = output.splitlines()
    match = RESULT_LINE.fullmatch(lines[-1]) if lines else None
    return int(match.group(1)) if match else None


def _finish(status: int, message: str, emit: Callable[[str], None]) -> int:
    emit(f"[{status}] {message}")
    return 0 if status in {1, 2} else 1


def run_scheduled(
    *,
    now: Callable[[], datetime] = beijing_now,
    sleep: Callable[[float], None] = time.sleep,
    runner: Callable = subprocess.run,
    poll_interval: int | str | None = None,
    env: Mapping[str, str] | None = None,
    emit: Callable[[str], None] = print,
) -> int:
    """Wait for 21:01, then retry through 23:25 on the captured Beijing date.

    Both late starts and unconfirmed outcomes fail, allowing the workflow to
    send its existing failure email. Status 5 keeps the workflow's leave alert
    behavior and is terminal. Time, sleep and process execution are injectable
    so tests never contact the school or wait for a real evening.
    """
    child_env = dict(os.environ if env is None else env)
    try:
        interval = _poll_interval(
            child_env.get("SWUDK_POLL_INTERVAL") if poll_interval is None else poll_interval
        )
    except (TypeError, ValueError, OverflowError):
        return _finish(4, "SWUDK_POLL_INTERVAL 必须是正整数秒数", emit)

    started = _as_beijing(now())
    earliest = started.replace(hour=20, minute=40, second=0, microsecond=0)
    first_attempt = started.replace(hour=21, minute=1, second=0, microsecond=0)
    deadline = started.replace(hour=23, minute=25, second=0, microsecond=0)
    if started < earliest or started >= deadline:
        return _finish(4, "定时任务未在北京时间 20:40–23:25 启动，未提交签到", emit)

    emit(f"北京时间 {started:%Y-%m-%d %H:%M:%S} 启动，签到确认截止 {deadline:%H:%M}")
    if started < first_attempt:
        emit("等待北京时间 21:01 后开始签到")
        while True:
            current = _as_beijing(now())
            if current >= first_attempt:
                break
            sleep(min(60, (first_attempt - current).total_seconds()))

    child_env["SWUDK_ENFORCE_WINDOW"] = "1"
    child_env["SWUDK_TARGET_DATE"] = started.strftime("%Y-%m-%d")
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["SWUDK_DEBUG_CREDENTIALS"] = "0"
    attempt = 0
    last_reason = "未取得签到结果"
    while True:
        remaining = (deadline - _as_beijing(now())).total_seconds()
        if remaining <= 0:
            return _finish(4, f"截至北京时间 23:25 未确认签到成功（{last_reason}）", emit)

        attempt += 1
        emit(f"第 {attempt} 轮签到，北京时间 {_as_beijing(now()):%H:%M:%S}")
        status = None
        returncode = None
        try:
            result = runner(
                [sys.executable, "-m", "swu_checkin.check_in"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
                timeout=min(CHILD_TIMEOUT, remaining),
            )
            output = _decode_output(result.stdout)
            _forward_logs(output, child_env, emit)
            status = _result_status(output)
            returncode = result.returncode
            if status is None:
                last_reason = "签到进程未输出完整结果"
            elif returncode != 0 and status in {1, 2}:
                last_reason = "签到进程异常退出，未确认成功"
            else:
                last_reason = STATUS_MESSAGES[status]
        except subprocess.TimeoutExpired as error:
            _forward_logs(_decode_output(error.output), child_env, emit)
            last_reason = "签到进程超时"
            emit("本轮签到超时，等待下一轮")
        except OSError:
            last_reason = "签到进程启动失败"
            emit("本轮签到进程启动失败，等待下一轮")

        # The process can finish after a timeout due to teardown or a clock jump.
        # Never report success for a previous date or outside this deadline.
        current = _as_beijing(now())
        if current >= deadline:
            return _finish(4, f"截至北京时间 23:25 未及时确认签到成功（{last_reason}）", emit)
        if status in {1, 2} and returncode == 0:
            return _finish(status, STATUS_MESSAGES[status], emit)
        if status == 5:
            return _finish(5, STATUS_MESSAGES[5], emit)

        wait = min(interval, (deadline - current).total_seconds())
        emit(f"本轮未确认签到成功（{last_reason}），{wait:g} 秒后重试")
        sleep(wait)


def main() -> int:
    return run_scheduled()


if __name__ == "__main__":
    raise SystemExit(main())
