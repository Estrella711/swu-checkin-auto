"""Dispatch the check-in workflow using actual Beijing time, never school APIs.

Run this standard-library-only file directly so importing the package cannot
load its OCR dependencies. Tokens are sent only in GitHub authorization headers.
"""

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")
WORKFLOW = "checkin.yml"
API_VERSION = "2026-03-10"
REQUEST_TIMEOUT = 30
PAGE_SIZE = 100
MAX_PAGES = 10  # GitHub limits searches with a created filter to 1,000 runs.
ACTIVE_STATUSES = {"queued", "in_progress", "requested", "waiting", "pending"}
KNOWN_STATUSES = ACTIVE_STATUSES | {"completed"}


class DispatchError(RuntimeError):
    def __init__(self, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


class Decision(NamedTuple):
    action: str
    target_date: str | None
    reason: str

    def inputs(self) -> dict:
        if self.action == "checkin":
            return {"scheduled": True, "target_date": self.target_date, "notify_test": False}
        if self.action == "missed":
            return {"missed_date": self.target_date}
        raise DispatchError("A skipped decision cannot be dispatched")


def beijing_now() -> datetime:
    return datetime.now(BEIJING)


def _as_beijing(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=BEIJING)
    return value.astimezone(BEIJING)


def _created_at(run: Mapping) -> datetime:
    value = run.get("created_at")
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise DispatchError("A workflow run has no valid creation time") from None
    if created.tzinfo is None:
        raise DispatchError("A workflow run creation time has no timezone")
    return created.astimezone(BEIJING)


def _title(run: Mapping) -> str:
    title = run.get("display_title") or run.get("name") or ""
    return title.strip() if isinstance(title, str) else ""


def _notification_only(run: Mapping) -> bool:
    title = _title(run)
    return title == "邮件通知测试" or title.startswith("未签到告警 · ")


def _automatic_for_date(run: Mapping, target_date: str, created: datetime) -> bool:
    title = _title(run)
    if title == f"自动查寝 · {target_date}":
        return True
    if created.date().isoformat() != target_date:
        return False
    return run.get("event") == "schedule" or title in {
        "自动查寝 · 外部触发",
        "自动查寝 · 手动指定",
    }


def decide(now: datetime, runs: Sequence[Mapping]) -> Decision:
    """Decide from actual time and a complete recent checkin.yml run inventory."""
    current = _as_beijing(now)
    window_start = current.replace(hour=21, minute=0, second=0, microsecond=0)
    window_end = current.replace(hour=22, minute=55, second=0, microsecond=0)
    alert_start = current.replace(hour=23, minute=0, second=0, microsecond=0)
    if window_end <= current < alert_start:
        return Decision("skip", current.date().isoformat(), "22:55–23:00 保留收尾时间，不派发新签到或告警")

    in_window = window_start <= current < window_end
    target_day = current.date() if current >= window_start else (current - timedelta(days=1)).date()
    target_date = target_day.isoformat()
    target_start = datetime.combine(target_day, datetime.min.time(), BEIJING).replace(hour=21)
    target_end = target_start.replace(hour=22, minute=55)
    confirmed = False
    failed_and_notified = False
    related_active = False
    notice_exists = False
    for run in runs:
        if not isinstance(run, Mapping) or run.get("status") not in KNOWN_STATUSES:
            raise DispatchError("Workflow run inventory contains an invalid status")
        created = _created_at(run)
        title = _title(run)
        if title == f"未签到告警 · {target_date}":
            notice_exists = True
        if _notification_only(run):
            continue

        created_in_window = target_start <= created < target_end
        active = run["status"] in ACTIVE_STATUSES
        automatic = _automatic_for_date(run, target_date, created)
        if active and (automatic or created_in_window):
            related_active = True
        if created_in_window and run["status"] == "completed":
            confirmed = confirmed or run.get("conclusion") == "success"
            failed_and_notified = failed_and_notified or run.get("conclusion") == "failure"

    if confirmed:
        return Decision("skip", target_date, "目标晚间已有签到成功或已签到的主流程")
    if in_window:
        if related_active:
            return Decision("skip", target_date, "目标晚间相关签到已在排队或执行")
        return Decision("checkin", target_date, "实际北京时间处于 21:00–22:55，派发正式自动签到")
    if failed_and_notified:
        return Decision("skip", target_date, "目标晚间已有失败主流程，已进入原有失败通知流程")
    if notice_exists:
        return Decision("skip", target_date, "目标晚间已有未签到告警，避免重复邮件")
    if related_active:
        return Decision("skip", target_date, "目标晚间相关签到仍在排队或执行，等待结果")
    return Decision("missed", target_date, "目标晚间没有确认签到结果或既有失败通知，派发仅通知告警")


class GitHubClient:
    """Call one fixed workflow; reject incomplete inventories before dispatching."""

    def __init__(self, repository: str, token: str, *, ref: str = "main", opener: Callable | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or "") or not token:
            raise DispatchError("GitHub repository or token is missing or invalid")
        self._base = f"https://api.github.com/repos/{repository}/actions/workflows/{WORKFLOW}"
        self._token = token
        self._ref = ref or "main"
        self._opener = urlopen if opener is None else opener

    def _request(self, method: str, url: str, payload: Mapping | None = None) -> bytes:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "swu-checkin-time-dispatcher",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=REQUEST_TIMEOUT) as response:
                status = response.getcode()
                expected = {200} if method == "GET" else {200, 204}
                if status not in expected:
                    raise DispatchError("Unexpected GitHub response", http_status=status)
                return response.read()
        except HTTPError as error:
            raise DispatchError("GitHub request failed", http_status=error.code) from None
        except (URLError, OSError, ValueError, HTTPException):
            # Never include exception text, URLs, headers or a response body.
            raise DispatchError("GitHub request could not be completed") from None

    def list_runs(self, since: datetime) -> list[Mapping]:
        created_filter = ">=" + since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        collected = {}
        expected_count = 0
        for page in range(1, MAX_PAGES + 1):
            query = urlencode({"per_page": PAGE_SIZE, "page": page, "created": created_filter})
            raw = self._request("GET", f"{self._base}/runs?{query}")
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise DispatchError("GitHub returned no valid run inventory") from None
            if not isinstance(payload, dict):
                raise DispatchError("GitHub returned an invalid run inventory")
            count = payload.get("total_count")
            batch = payload.get("workflow_runs")
            if type(count) is not int or count < 0 or not isinstance(batch, list) or len(batch) > PAGE_SIZE:
                raise DispatchError("GitHub returned an invalid run inventory")
            if count > PAGE_SIZE * MAX_PAGES:
                raise DispatchError("GitHub run search exceeds its pagination limit")
            expected_count = max(expected_count, count)
            for run in batch:
                if not isinstance(run, dict) or type(run.get("id")) is not int:
                    raise DispatchError("GitHub returned an invalid run inventory")
                _created_at(run)
                if run.get("status") not in KNOWN_STATUSES:
                    raise DispatchError("GitHub returned an invalid run inventory")
                collected.setdefault(run["id"], run)
            if len(collected) >= expected_count:
                return list(collected.values())
            if len(batch) < PAGE_SIZE:
                raise DispatchError("GitHub run inventory ended before all records were retrieved")
        raise DispatchError("GitHub run inventory could not be fully retrieved")

    def dispatch(self, inputs: Mapping) -> None:
        self._request("POST", f"{self._base}/dispatches", {"ref": self._ref, "inputs": dict(inputs)})


def run_dispatch(
    *,
    client,
    now: Callable[[], datetime] = beijing_now,
    emit: Callable[[str], None] = print,
    summary: Callable[[str], None] | None = None,
    notify_test: bool = False,
) -> int:
    def report(message: str) -> None:
        emit(message)
        if summary is not None:
            summary(message)

    try:
        if notify_test:
            client.dispatch({"scheduled": False, "notify_test": True})
            report("dispatch: 派发邮件测试（未调用学校接口）")
            return 0

        started = _as_beijing(now())
        initial = decide(started, [])
        if initial.action == "skip":
            report(f"skip: {initial.reason}（{initial.target_date}）")
            return 0
        runs = client.list_runs(started.astimezone(timezone.utc) - timedelta(hours=48))
        decision = decide(now(), runs)
        if (decision.action, decision.target_date) != (initial.action, initial.target_date):
            if decision.action == "skip":
                report(f"skip: {decision.reason}（{decision.target_date}）")
            else:
                report("skip: 查询期间实际时窗或目标日期已改变，由下一轮巡检重新判断")
            return 0
        # Read the clock again immediately before POST. Keep the date and action
        # bound to the completed query; a late response cannot start a new night.
        final = decide(now(), runs)
        if (final.action, final.target_date) != (decision.action, decision.target_date):
            report("skip: 派发前实际时窗或目标日期已改变，由下一轮巡检重新判断")
            return 0
        client.dispatch(final.inputs())
        category = "dispatch" if final.action == "checkin" else "missed"
        report(f"{category}: {final.reason}（{final.target_date}）")
        return 0
    except (DispatchError, OSError, ValueError, TypeError) as error:
        suffix = ""
        if isinstance(error, DispatchError) and type(error.http_status) is int:
            suffix = f"（HTTP {error.http_status}）"
        emit(f"error: 巡检失败，GitHub 请求或完整运行记录未确认{suffix}；未确认派发成功")
        if summary is not None:
            try:
                summary(f"error: 巡检失败，GitHub 请求或完整运行记录未确认{suffix}")
            except (OSError, ValueError):
                pass
        return 1


def main() -> int:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    def summary(message: str) -> None:
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as output:
                output.write(f"### SWU 实际时间巡检\n\n- {message}\n")

    try:
        client = GitHubClient(
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GITHUB_TOKEN", ""),
            ref=os.environ.get("CHECKIN_REF") or "main",
        )
    except DispatchError:
        message = "error: GitHub 巡检配置缺失或无效，未派发任务"
        print(message)
        try:
            summary(message)
        except (OSError, ValueError):
            pass
        return 1
    return run_dispatch(
        client=client,
        summary=summary,
        notify_test=os.environ.get("DISPATCH_NOTIFY_TEST", "").lower() == "true",
    )


if __name__ == "__main__":
    raise SystemExit(main())
