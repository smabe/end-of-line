"""Streaming projection of plan state events for AI-agent consumption
(Claude's Monitor tool). See plans/clu-watch.md."""
from __future__ import annotations

from typing import Any, Callable

from . import state as st

_DEFAULT_VISIBLE: frozenset[str] = frozenset(filter(None, {
    st.EVENT_PHASE_STARTED,
    st.EVENT_PHASE_COMPLETED,
    st.EVENT_PHASE_BLOCKED,
    st.EVENT_BLOCKER_ANSWERED,
    st.EVENT_BLOCKER_CONSUMED,
    st.EVENT_BLOCKER_SLA_EXCEEDED,
    st.EVENT_PHASE_MAX_ATTEMPTS,
    st.EVENT_PHASE_STALLED,
    st.EVENT_TASK_SPAWNED,
    st.EVENT_TASK_COMPLETED,
    st.EVENT_PLAN_COMPLETED,
    st.EVENT_DISPATCH_FAILED,
    st.EVENT_SYSTEMIC_FAILURE,
    st.EVENT_PAUSED,
    st.EVENT_RESUMED,
    st.EVENT_RETRY_REQUESTED,
    st.EVENT_QUEUE_POPPED,
    st.EVENT_WORKTREE_MISSING,
    st.EVENT_WORKTREE_CONFLICT_WARNING,
    # Queue v2 — present after queue-worker-callback merged
    getattr(st, "EVENT_QUEUE_APPENDED", None),
    getattr(st, "EVENT_QUEUE_REJECTED", None),
}))

_VERBOSE_ONLY: frozenset[str] = frozenset({
    st.EVENT_LEASE_EXPIRED,
    st.EVENT_LEASE_EXTENDED,
    st.EVENT_CLAIM_FORCE_RELEASED,
    st.EVENT_ATTEMPTS_RESET,
    st.EVENT_STUCK_BLOCKER_REPINGED,
    st.EVENT_STALLED_CLAIM_NOTIFIED,
    st.EVENT_WORKTREE_ATTACHED,
    st.EVENT_WORKTREE_CLEANED,
    st.EVENT_WORKTREE_RETAINED_AHEAD,
})


def _trunc(s: str | None, n: int = 100) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n - 1] + "…"


def _phase_prefix(slug: str, e: dict[str, Any]) -> str:
    phase = e.get("phase", "")
    if phase:
        return f"{slug}/{phase}"
    return slug


def _fmt_blocked(slug: str, e: dict[str, Any]) -> str:
    bid = e.get("blocker_id", "?")
    q = _trunc(e.get("question"))
    prefix = _phase_prefix(slug, e)
    if q:
        return f"{prefix}: BLOCKED {bid} — {q}"
    return f"{prefix}: BLOCKED {bid}"


_FORMATTERS: dict[str, Callable[[str, dict[str, Any]], str]] = {
    st.EVENT_PHASE_STARTED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: started (attempt {e.get('attempts', 1)})"
    ),
    st.EVENT_PHASE_COMPLETED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: completed"
    ),
    st.EVENT_PHASE_BLOCKED: _fmt_blocked,
    st.EVENT_BLOCKER_ANSWERED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: answer received for "
        f"{e.get('blocker_id', '?')}: {_trunc(e.get('answer'))}"
    ),
    st.EVENT_BLOCKER_CONSUMED: lambda slug, e: (
        f"{slug}: blocker {e.get('blocker_id', '?')} consumed — phase resuming"
    ),
    st.EVENT_BLOCKER_SLA_EXCEEDED: lambda slug, e: (
        f"{slug}: blocker {e.get('blocker_id', '?')} SLA exceeded "
        f"({e.get('age_hours', '?')}h)"
    ),
    st.EVENT_PHASE_MAX_ATTEMPTS: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: HALTED max attempts "
        f"({e.get('attempts', '?')})"
    ),
    st.EVENT_PHASE_STALLED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: stalled "
        f"({e.get('age_seconds', '?')}s since last heartbeat)"
    ),
    st.EVENT_TASK_SPAWNED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: spawned task {e.get('task', '?')}"
    ),
    st.EVENT_TASK_COMPLETED: lambda slug, e: (
        f"{slug}: task {e.get('task', '?')} done"
    ),
    st.EVENT_PLAN_COMPLETED: lambda slug, e: f"{slug}: PLAN DONE",
    st.EVENT_DISPATCH_FAILED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: dispatch failed — "
        f"{_trunc(e.get('reason'))}"
    ),
    st.EVENT_SYSTEMIC_FAILURE: lambda slug, e: (
        f"{slug}: SYSTEMIC FAILURE — {_trunc(e.get('signature'))}"
    ),
    st.EVENT_PAUSED: lambda slug, e: (
        f"{slug}: paused" + (f" ({_trunc(e.get('reason'))})" if e.get("reason") else "")
    ),
    st.EVENT_RESUMED: lambda slug, e: f"{slug}: resumed",
    st.EVENT_RETRY_REQUESTED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: retry requested"
    ),
    st.EVENT_QUEUE_POPPED: lambda slug, e: (
        f"{slug}: popped {e.get('slug', '?')} from queue "
        f"(by {e.get('added_by', '?')})"
    ),
    st.EVENT_WORKTREE_MISSING: lambda slug, e: (
        f"{slug}: WORKTREE MISSING — {e.get('worktree_path', '?')}"
    ),
    st.EVENT_WORKTREE_CONFLICT_WARNING: lambda slug, e: (
        f"{slug}: worktree conflict with {e.get('other_slug', '?')} "
        f"— both plans share project without isolated worktrees"
    ),
    # Verbose-only
    st.EVENT_LEASE_EXPIRED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: lease expired"
    ),
    st.EVENT_LEASE_EXTENDED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: lease extended by "
        f"{e.get('extended_by_minutes', '?')}min → {e.get('new_expires', '?')}"
    ),
    st.EVENT_CLAIM_FORCE_RELEASED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: claim force-released"
        + (" (forced)" if e.get("forced") else "")
    ),
    st.EVENT_ATTEMPTS_RESET: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: attempts reset"
    ),
    st.EVENT_STUCK_BLOCKER_REPINGED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: blocker {e.get('blocker_id', '?')} "
        f"re-pinged ({e.get('age_min', '?')}min open)"
    ),
    st.EVENT_STALLED_CLAIM_NOTIFIED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: stalled claim notification sent "
        f"({e.get('stalled_min', '?')}min past lease)"
    ),
    st.EVENT_WORKTREE_ATTACHED: lambda slug, e: (
        f"{slug}: worktree attached at {e.get('path', '?')} "
        f"(branch {e.get('branch', '?')})"
    ),
    st.EVENT_WORKTREE_CLEANED: lambda slug, e: (
        f"{slug}: worktree cleaned — {e.get('path', '?')} "
        f"(trigger={e.get('trigger', '?')})"
    ),
    st.EVENT_WORKTREE_RETAINED_AHEAD: lambda slug, e: (
        f"{slug}: worktree retained (branch ahead) — {e.get('path', '?')}"
    ),
}

# Queue v2 formatters — splice in only when constants are defined
_Q_APPENDED = getattr(st, "EVENT_QUEUE_APPENDED", None)
_Q_REJECTED = getattr(st, "EVENT_QUEUE_REJECTED", None)
if _Q_APPENDED:
    _FORMATTERS[_Q_APPENDED] = lambda slug, e: (
        f"{slug}: queued {e.get('slug', '?')} from phase "
        f"{e.get('source_phase', '?')}"
    )
if _Q_REJECTED:
    _FORMATTERS[_Q_REJECTED] = lambda slug, e: (
        f"{slug}: queue rejected {e.get('slug', '?')} from phase "
        f"{e.get('source_phase', '?')} ({e.get('reason', '?')})"
    )


def project_event(
    event: dict[str, Any],
    plan_slug: str,
    *,
    verbose: bool = False,
) -> str | None:
    t = event.get("type")
    if t in _VERBOSE_ONLY and not verbose:
        return None
    fmt = _FORMATTERS.get(t)
    return fmt(plan_slug, event) if fmt else None
