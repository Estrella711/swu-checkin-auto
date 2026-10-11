"""Bridge the first manual deployment to tonight without calling school APIs."""

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


def wait_until_evening(
    *,
    now: Callable[[], datetime] = beijing_now,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] = print,
    env: Mapping[str, str] | None = None,
) -> int:
    """Wait in at most 60-second slices, then hand off the captured Beijing date."""
    emit("首次启用等待：本步骤未调用学校接口，仅等待当晚巡检交接。")
    output_path = (os.environ if env is None else env).get("GITHUB_OUTPUT", "")
    if not isinstance(output_path, str) or not output_path.strip():
        emit("首次启用等待失败：缺少 GITHUB_OUTPUT，无法交接目标日期。")
        return 1

    try:
        started = _as_beijing(now())
        target_date = started.date()
        earliest = started.replace(hour=15, minute=30, second=0, microsecond=0)
        evening = started.replace(hour=21, minute=0, second=0, microsecond=0)
        if started < earliest:
            emit("首次启用等待失败：须在北京时间 15:30 后启动，未交接巡检。")
            return 1

        emit(f"首次启用等待启动于北京时间 {started:%Y-%m-%d %H:%M:%S}。")
        if started < evening:
            emit("等待当日北京时间 21:00，随后由独立巡检步骤判断签到窗口。")

        current = started
        while True:
            if current.date() != target_date:
                emit("首次启用等待失败：北京时间日期已变化，禁止交接到另一天。")
                return 1
            if current >= evening:
                break
            sleep(min(60, (evening - current).total_seconds()))
            current = _as_beijing(now())

        if _as_beijing(now()).date() != target_date:
            emit("首次启用等待失败：北京时间日期已变化，禁止交接到另一天。")
            return 1
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"target_date={target_date.isoformat()}\n")
        emit(f"首次启用等待完成：交接目标日期 {target_date.isoformat()}；本步骤未调用学校接口。")
        return 0
    except (OSError, ValueError, TypeError):
        emit("首次启用等待失败：读取时钟或写入交接输出失败，未完成巡检交接。")
        return 1


def main() -> int:
    return wait_until_evening()


if __name__ == "__main__":
    raise SystemExit(main())
