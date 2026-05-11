"""Outbound notification adapter — iMessage via osascript.

clu runs from cron and the worker runs in a separate process, so the
notification path must be self-contained: no MCP, no long-running daemon,
no network. Calling Messages.app via osascript is the cheapest thing that
delivers to the user's phone without standing up new infra.

Quiet hours gate every kind defined here. If you add a kind that must
bypass quiet hours (halts, emergency stale escalations), include it in
QUIET_HOURS_BYPASS_KINDS.
"""
from __future__ import annotations

import datetime as _dt
import subprocess
import sys
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .config import NotifySpec

KIND_BLOCKER = "blocker"
KIND_STALLED = "stalled"
KIND_COMPLETED = "completed"

QUIET_HOURS_BYPASS_KINDS: frozenset[str] = frozenset()

# osascript-friendly AppleScript: argv carries the handle + body so we
# don't have to escape user-controlled text into the script source.
_APPLESCRIPT = """
on run argv
    set toHandle to item 1 of argv
    set body to item 2 of argv
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy toHandle of targetService
        send body to targetBuddy
    end tell
end run
""".strip()

Sender = Callable[[str, str], None]


def _osascript_send(to: str, body: str) -> None:
    """Fire-and-forget — don't block the cron tick on a hung Messages.app."""
    subprocess.Popen(
        ["osascript", "-e", _APPLESCRIPT, "--", to, body],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def parse_hhmm(s: str) -> _dt.time:
    hh, mm = s.split(":", 1)
    return _dt.time(int(hh), int(mm))


def is_quiet_hours(
    now: _dt.datetime, start: _dt.time, end: _dt.time,
) -> bool:
    """True if `now` falls inside the [start, end) quiet window.

    Wraps overnight when end < start (e.g. 22:00–08:00 means quiet through
    midnight). end == start collapses to "never quiet".
    """
    if start == end:
        return False
    t = now.time()
    if start < end:
        return start <= t < end
    return t >= start or t < end


def notify(
    spec: "NotifySpec",
    kind: str,
    body: str,
    *,
    now: _dt.datetime | None = None,
    sender: Sender | None = None,
) -> bool:
    """Send an iMessage if quiet hours / config permit. Returns True if sent.

    Stays best-effort — on osascript failure we log to stderr and return False
    so a broken Messages.app can't take down the supervisor.
    """
    # Quiet hours are user-facing wall-clock semantics — local time is the
    # whole point. Don't switch this to UTC to match state.py.
    now = now or _dt.datetime.now()
    if _in_quiet_window(spec, now) and kind not in QUIET_HOURS_BYPASS_KINDS:
        return False
    if not spec.imessage_to:
        return False
    try:
        (sender or _osascript_send)(spec.imessage_to, body)
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"notify: send failed ({kind}): {exc}", file=sys.stderr)
        return False


def _in_quiet_window(spec: "NotifySpec", now: _dt.datetime) -> bool:
    if not spec.quiet_hours:
        return False
    try:
        start = parse_hhmm(spec.quiet_hours[0])
        end = parse_hhmm(spec.quiet_hours[1])
    except (ValueError, IndexError):
        return False
    return is_quiet_hours(now, start, end)


def render_blocker(
    plan_slug: str, blocker_id: str, phase: str, question: str, options: list[str],
) -> str:
    opts = "\n".join(f"[{i}] {o}" for i, o in enumerate(options))
    return (
        f"❓ {plan_slug}/{blocker_id} [{phase}]\n{question}\n{opts}\n\n"
        f"Reply: `{plan_slug} <number>` or just the number if this is the "
        f"only open question."
    )


def render_stalled(plan_slug: str, phase: str, age_seconds: float) -> str:
    minutes = int(age_seconds // 60)
    return f"⚠️  {plan_slug}/{phase} stalled — no heartbeat for {minutes} min."


def render_completed(plan_slug: str, commit_count: int) -> str:
    return f"✅ {plan_slug} done — {commit_count} commit(s)."
