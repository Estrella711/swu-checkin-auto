"""Wait for the evening handoff without importing check-in or OCR code.

Run directly with ``python src/swu_checkin/prewarm.py``. This preparation
stage makes no school requests and performs no check-in submission.
"""

import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")


def beijing_now() -> datetime:
    return datetime.now(BEIJING)


def _as_beijing(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=BEIJING)
    return value.astimezone(BEIJING)


def _wait_for_window(
    started: datetime,
    *,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    emit: Callable[[str], None],
) -> int:
    earliest = started.replace(hour=15, minute=40, second=0, microsecond=0)
    ready_at = started.replace(hour=20, minute=50, second=0, microsecond=0)
    deadline = started.replace(hour=22, minute=55, second=0, microsecond=0)
    if started < earliest or started >= deadline:
        emit("[4] 准备任务未在北京时间 15:40–22:55 启动，未提交签到")
        return 1

    emit(f"北京时间 {started:%Y-%m-%d %H:%M:%S} 开始准备，等待当日 20:50 交接")
    emit("本步骤仅等待；自动查寝打卡时段为北京时间 21:00–23:00，当前未提交签到")
    while True:
        current = _as_beijing(now())
        if current.date() != started.date() or current >= deadline:
            emit("[4] 准备任务已跨日期或超过当日 22:55，未提交签到")
            return 1
        if current >= ready_at:
            emit(f"北京时间 {current:%Y-%m-%d %H:%M:%S} 准备完成，交由后续任务执行正式签到")
            return 0
        sleep(min(60, (ready_at - current).total_seconds()))


def wait_for_window(
    *,
    now: Callable[[], datetime] = beijing_now,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] = print,
) -> int:
    """Wait until 20:50 on the captured Beijing date, never submitting check-in.

    Starts before 15:40 or at/after 22:55 fail immediately. A date change or
    a clock jump past the deadline also fails so dependent jobs cannot treat
    preparation for an expired evening as a successful handoff.
    """
    started = _as_beijing(now())
    return _wait_for_window(started, now=now, sleep=sleep, emit=emit)


def main(
    *,
    now: Callable[[], datetime] = beijing_now,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] = print,
    env: Mapping[str, str] | None = None,
) -> int:
    # Capture once before waiting: the dependent job must receive this evening's
    # date, including if a runner delay occurs after preparation has finished.
    started = _as_beijing(now())
    result = _wait_for_window(started, now=now, sleep=sleep, emit=emit)
    if result != 0:
        return result

    output_path = (os.environ if env is None else env).get("GITHUB_OUTPUT")
    if output_path:
        try:
            with open(output_path, "a", encoding="utf-8") as output:
                output.write(f"target_date={started:%Y-%m-%d}\n")
        except OSError:
            emit("[4] 无法传递准备任务的目标日期，后续自动签到未启动")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
