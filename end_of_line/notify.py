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
KIND_HALTED = "halted"
# Queue-pop skipped a head (plan file missing). Defers during quiet hours
# — the operator finds out next loud window, no 3am ping.
KIND_QUEUE_SKIPPED = "queue_skipped"
KIND_QUEUE_REPAIRED = "queue_repaired"
KIND_QUEUE_REPAIR_FAILED = "queue_repair_failed"
KIND_QUEUE_CORRUPT = "queue_corrupt"
# Gap-fill kinds — escalations, not emergencies, so NOT in
# QUIET_HOURS_BYPASS_KINDS. The inbox path surfaces them on next Claude
# turn regardless of quiet hours.
KIND_STUCK_BLOCKER = "stuck_blocker"
KIND_STALLED_CLAIM = "stalled_claim"

QUIET_HOURS_BYPASS_KINDS: frozenset[str] = frozenset({
    KIND_HALTED,
    KIND_QUEUE_REPAIR_FAILED,
    KIND_QUEUE_CORRUPT,
})

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
    plan_slug: str | None = None,
    project_root: str | None = None,
    inbox_writer: Callable[..., str] | None = None,
) -> bool:
    """Send an iMessage if quiet hours / config permit. Returns True if sent.

    Stays best-effort — on osascript failure we log to stderr and return False
    so a broken Messages.app can't take down the supervisor.

    When `plan_slug` and `project_root` are both provided, also drops an
    inbox event so the next Claude turn sees the same signal — independent
    of quiet hours, since the inbox is for in-session pickup, not waking
    the operator.
    """
    if plan_slug is not None and project_root is not None:
        writer = inbox_writer
        if writer is None:
            from . import inbox as _inbox
            writer = _inbox.write_event
        try:
            writer(
                type=kind,
                plan_slug=plan_slug,
                project_root=project_root,
                summary=body.splitlines()[0][:200] if body else kind,
                details={"full_body": body},
            )
        except OSError as exc:
            # Never let a broken inbox dir block the iMessage path.
            print(f"notify: inbox write failed ({kind}): {exc}", file=sys.stderr)
    # Quiet hours are user-facing wall-clock semantics — local time is the
    # whole point. Don't switch this to UTC to match state.py.
    now = now or _dt.datetime.now()
    if in_quiet_window(spec, now) and kind not in QUIET_HOURS_BYPASS_KINDS:
        return False
    if not spec.imessage_to:
        return False
    try:
        (sender or _osascript_send)(spec.imessage_to, body)
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"notify: send failed ({kind}): {exc}", file=sys.stderr)
        return False


def in_quiet_window(spec: "NotifySpec", now: _dt.datetime) -> bool:
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


def render_halted(plan_slug: str, phase: str, attempts: int) -> str:
    return (
        f"🛑 {plan_slug}/{phase} halted — {attempts} attempts. "
        f"`clu retry --plan {plan_slug}` to resume."
    )


def render_queue_skipped(slug: str, reason: str) -> str:
    return f"⏭️  queue skipped {slug} — {reason}."


def render_queue_corrupt(diagnosis: str, backup_path) -> str:
    return f"💀 queue corrupt: {diagnosis}. backup at {backup_path}."


def render_queue_repaired(slug_count: int, backup_path) -> str:
    entries = "entry" if slug_count == 1 else "entries"
    return f"🔧 queue repaired — {slug_count} {entries} preserved. backup at {backup_path}."


def render_queue_repair_failed(reason: str, backup_path) -> str:
    return f"💥 queue repair failed: {reason}. reverted from backup at {backup_path}."


def render_stuck_blocker(
    plan_slug: str, blocker_id: str, phase: str,
    question: str, options: list[str], age_min: int,
) -> str:
    opts = "\n".join(f"[{i}] {o}" for i, o in enumerate(options))
    return (
        f"⏰ {plan_slug}/{blocker_id} still open ({age_min}min) [{phase}]\n"
        f"{question}\n{opts}\n\n"
        f"Reply: `{plan_slug} <number>`."
    )


def render_stalled_claim(plan_slug: str, phase: str, age_min: int) -> str:
    return (
        f"🐌 {plan_slug}/{phase} claim stalled ({age_min}min past lease).\n"
        f"Worker is unresponsive. Run `clu release-claim --plan {plan_slug} "
        f"--phase {phase}` to free it, or `clu retry` if you've fixed the "
        f"underlying cause."
    )


def render_systemic_failure(plan_slug: str, phase: str, signature: str) -> str:
    return (
        f"🚨 {plan_slug}/{phase} paused — systemic failure: {signature}. "
        f"Run `clu resume --plan {plan_slug}` once cleared."
    )


def render_worktree_missing(plan_slug: str, worktree_path: str) -> str:
    return (
        f"🌳 {plan_slug} paused — worktree missing at {worktree_path}. "
        f"Restore the dir (e.g. `git worktree add`) or edit state.worktree, "
        f"then `clu resume --plan {plan_slug}`."
    )
