"""Outbound notification router.

Routes notifications through configured backends (phase 1: iMessage only).
Quiet hours gate every kind defined here. If you add a kind that must bypass
quiet hours (halts, emergency stale escalations), include it in
QUIET_HOURS_BYPASS_KINDS.
"""
from __future__ import annotations

import datetime as _dt
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .notify_discord import DiscordNotifier
from .notify_imessage import IMessageNotifier
from .state_blocker import (
    BLOCKER_BODY_SOFT_LIMIT,
    KIND_STUCK_BLOCKER,
    render_blocker,
    render_halted,
    render_stalled,
    render_stuck_blocker,
)

if TYPE_CHECKING:
    from .config import NotifySpec

_NOTIFIER_REGISTRY: dict[str, type] = {
    "imessage": IMessageNotifier,
    "discord": DiscordNotifier,
}

_GLOBAL_SUPPRESS: bool = False


def set_global_suppress(v: bool) -> None:
    global _GLOBAL_SUPPRESS
    _GLOBAL_SUPPRESS = v

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
# KIND_STUCK_BLOCKER is imported from state_blocker above.
KIND_STALLED_CLAIM = "stalled_claim"
KIND_GATE_CLEAN = "gate_clean"
KIND_GATE_DIRTY = "gate_dirty"

QUIET_HOURS_BYPASS_KINDS: frozenset[str] = frozenset({
    KIND_HALTED,
    KIND_QUEUE_REPAIR_FAILED,
    KIND_QUEUE_CORRUPT,
})


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
    plan_slug: str | None = None,
    project_root: str | None = None,
    inbox_writer: Callable[..., str] | None = None,
) -> bool:
    """Send a notification if quiet hours / config permit. Returns True if sent.

    Stays best-effort — on backend failure we log to stderr and return False
    so a broken backend can't take down the supervisor.

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
    if _GLOBAL_SUPPRESS:
        return False
    # Quiet hours are user-facing wall-clock semantics — local time is the
    # whole point. Don't switch this to UTC to match state.py.
    now = now or _dt.datetime.now()
    if in_quiet_window(spec, now) and kind not in QUIET_HOURS_BYPASS_KINDS:
        return False
    sent_any = False
    for ch in spec.channels:
        notifier_cls = _NOTIFIER_REGISTRY.get(ch.kind)
        if notifier_cls is None:
            print(f"notify: unknown channel kind '{ch.kind}' — skipping", file=sys.stderr)
            continue
        if not ch.enabled:
            continue
        if ch.kinds is not None and kind not in ch.kinds:
            continue
        try:
            notifier = notifier_cls.from_spec(ch)
            notifier.send(kind, body, plan_slug=plan_slug or "", blocker_id=None)
            sent_any = True
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"notify: send failed ({kind} via {ch.kind}): {exc}", file=sys.stderr)
    return sent_any


def in_quiet_window(spec: "NotifySpec", now: _dt.datetime) -> bool:
    if not spec.quiet_hours:
        return False
    try:
        start = parse_hhmm(spec.quiet_hours[0])
        end = parse_hhmm(spec.quiet_hours[1])
    except (ValueError, IndexError):
        return False
    return is_quiet_hours(now, start, end)


# render_blocker, render_stalled, render_halted, render_stuck_blocker are
# defined in state_blocker and re-exported above. Back-compat: callers that
# import them from notify continue to work.

__all__ = [
    "BLOCKER_BODY_SOFT_LIMIT", "KIND_STUCK_BLOCKER",
    "render_blocker", "render_halted", "render_stalled", "render_stuck_blocker",
]


def render_completed(plan_slug: str, commit_count: int) -> str:
    return f"✅ {plan_slug} done — {commit_count} commit(s)."


def render_queue_skipped(slug: str, reason: str) -> str:
    return f"⏭️  queue skipped {slug} — {reason}."


def render_queue_corrupt(diagnosis: str, backup_path) -> str:
    return f"💀 queue corrupt: {diagnosis}. backup at {backup_path}."


def render_queue_repaired(slug_count: int, backup_path) -> str:
    entries = "entry" if slug_count == 1 else "entries"
    return f"🔧 queue repaired — {slug_count} {entries} preserved. backup at {backup_path}."


def render_queue_repair_failed(reason: str, backup_path) -> str:
    return f"💥 queue repair failed: {reason}. reverted from backup at {backup_path}."


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


def render_gate_clean(batch_id: str, slugs: list[str]) -> str:
    return f"Batch {batch_id} dry-merge clean: {', '.join(slugs)}"


def render_gate_dirty(batch_id: str, outcome: str, follow_up_path: str) -> str:
    return (
        f"Batch {batch_id} dry-merge DIRTY ({outcome}). "
        f"Follow-up: {follow_up_path}"
    )


def render_worktree_conflict(
    project_root: Path, slug_a: str, slug_b: str,
) -> str:
    return (
        f"🌳 {slug_a} ⟷ {slug_b} in {project_root.name} — both active "
        f"without a worktree. Concurrent edits will collide. Pause one "
        f"(`clu pause`) or rerun init with `--worktree`."
    )
