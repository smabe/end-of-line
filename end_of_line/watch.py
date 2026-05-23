"""Streaming projection of plan state events for AI-agent consumption
(Claude's Monitor tool). See plans/clu-watch.md."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, TextIO

from . import state as st
from .plan_parser import parse_sessions_index

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
    # Stuck-tool detection (#67) — actionable, not verbose. The operator
    # should see wedged subprocesses in the default stream.
    getattr(st, "EVENT_TOOL_STUCK", None),
    # Queue v2 — present after queue-worker-callback merged
    getattr(st, "EVENT_QUEUE_APPENDED", None),
    getattr(st, "EVENT_QUEUE_REJECTED", None),
    # Attestation gate refusal (#70) — actionable, the worker is wedged on
    # a missing/stale verify or simplify stamp.
    getattr(st, "EVENT_ATTESTATION_REFUSED", None),
}))

_VERBOSE_ONLY: frozenset[str] = frozenset({
    st.EVENT_LEASE_EXPIRED,
    st.EVENT_LEASE_EXTENDED,
    st.EVENT_PHASE_ORPHAN_REAPED,
    st.EVENT_CLAIM_FORCE_RELEASED,
    st.EVENT_ATTEMPTS_RESET,
    st.EVENT_STUCK_BLOCKER_REPINGED,
    st.EVENT_STALLED_CLAIM_NOTIFIED,
    st.EVENT_WORKTREE_ATTACHED,
    st.EVENT_WORKTREE_CLEANED,
    st.EVENT_WORKTREE_RETAINED_AHEAD,
})

# Operator-dashboard (#70) filter — the cross-plan-worth-interrupting set.
# Under `clu watch --operator`, only these events render; the _VERBOSE_ONLY
# gate is bypassed (stalled_claim_notified is operator-relevant even when
# the normal verbose check would hide it).
_OPERATOR_VISIBLE: frozenset[str] = frozenset(filter(None, {
    getattr(st, "EVENT_TOOL_STUCK", None),
    st.EVENT_PHASE_BLOCKED,
    getattr(st, "EVENT_ATTESTATION_REFUSED", None),
    st.EVENT_STALLED_CLAIM_NOTIFIED,
}))


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
    st.EVENT_PHASE_ORPHAN_REAPED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: orphan reaped "
        f"pid={e.get('pid', '?')} signaled={e.get('signaled', '?')}"
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

# Stuck-tool formatter (#67) — splice in if the constant is defined so older
# state files predating worker-watchdog don't trip up the dispatch table.
_TOOL_STUCK = getattr(st, "EVENT_TOOL_STUCK", None)
if _TOOL_STUCK:
    _FORMATTERS[_TOOL_STUCK] = lambda slug, e: (
        f"{_phase_prefix(slug, e)}: STUCK TOOL pid={e.get('descendant_pid', '?')} "
        f"elapsed={e.get('elapsed_seconds', '?')}s "
        f"cmd={_trunc(e.get('command'), 80)}"
    )


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


# Attestation-refused formatter (#70 dashboard) — splice in when defined.
_ATTEST_REFUSED = getattr(st, "EVENT_ATTESTATION_REFUSED", None)
if _ATTEST_REFUSED:
    def _fmt_attest_refused(slug: str, e: dict[str, Any]) -> str:
        gate = e.get("gate", "?")
        stamped = e.get("stamped_at") or "never"
        head = (e.get("head_sha") or "?")[:7]
        stamped_short = stamped[:7] if stamped != "never" else "never"
        return (
            f"{_phase_prefix(slug, e)}: ATTESTATION REFUSED ({gate} gate) "
            f"stamped={stamped_short} head={head}"
        )
    _FORMATTERS[_ATTEST_REFUSED] = _fmt_attest_refused


_TASK_STATUS_MAP: dict[str, str] = {
    st.EVENT_PHASE_STARTED: "in_progress",
    st.EVENT_PHASE_COMPLETED: "completed",
    st.EVENT_PHASE_BLOCKED: "in_progress",
    st.EVENT_PHASE_MAX_ATTEMPTS: "in_progress",
    st.EVENT_SYSTEMIC_FAILURE: "in_progress",
    st.EVENT_PLAN_COMPLETED: "completed",
    st.EVENT_PAUSED: "in_progress",
    st.EVENT_RESUMED: "in_progress",
    st.EVENT_PHASE_STALLED: "in_progress",
}
if _ATTEST_REFUSED:
    _TASK_STATUS_MAP[_ATTEST_REFUSED] = "in_progress"

_TASK_VERBOSE_STATUS_MAP: dict[str, str] = {
    st.EVENT_LEASE_EXTENDED: "in_progress",
    st.EVENT_LEASE_EXPIRED: "in_progress",
    st.EVENT_CLAIM_FORCE_RELEASED: "in_progress",
    st.EVENT_ATTEMPTS_RESET: "in_progress",
    st.EVENT_STUCK_BLOCKER_REPINGED: "in_progress",
    st.EVENT_STALLED_CLAIM_NOTIFIED: "in_progress",
    st.EVENT_WORKTREE_ATTACHED: "in_progress",
}

# Events where task_id is the plan slug alone (no /phase segment)
_PLAN_SCOPED_EVENTS: frozenset[str] = frozenset({
    st.EVENT_PLAN_COMPLETED, st.EVENT_PAUSED, st.EVENT_RESUMED,
})


def _escape_msg(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _task_line(
    verb: str,
    task_id: str,
    *,
    parent: str | None = None,
    status: str,
    msg: str | None = None,
) -> str:
    parent_field = f" parent={parent}" if parent else ""
    msg_field = f' msg="{msg}"' if msg is not None else ""
    return f"{verb} task={task_id}{parent_field} status={status}{msg_field}"


def _task_msg_for(event: dict[str, Any]) -> str:
    t = event.get("type")
    if t == st.EVENT_PHASE_STARTED:
        return f"started (attempt {event.get('attempts', 1)})"
    if t == st.EVENT_PHASE_COMPLETED:
        return "completed"
    if t == st.EVENT_PHASE_BLOCKED:
        bid = event.get("blocker_id", "?")
        q = _trunc(event.get("question") or "")
        return f"BLOCKED {bid} — {q}" if q else f"BLOCKED {bid}"
    if t == st.EVENT_PHASE_MAX_ATTEMPTS:
        return f"HALTED (max attempts on {event.get('phase', '?')})"
    if t == st.EVENT_SYSTEMIC_FAILURE:
        sig = _trunc(event.get("signature") or "")
        return f"SYSTEMIC FAILURE — {sig}"
    if t == st.EVENT_PLAN_COMPLETED:
        return "plan done"
    if t == st.EVENT_PAUSED:
        reason = _trunc(event.get("reason") or "")
        return f"paused — {reason}" if reason else "paused"
    if t == st.EVENT_RESUMED:
        return "resumed"
    if t == st.EVENT_PHASE_STALLED:
        return "stalled"
    return (t or "").replace("_", " ")


def project_event_task(
    event: dict[str, Any],
    plan_slug: str,
    *,
    verbose: bool = False,
) -> str | None:
    t = event.get("type")
    if t not in _TASK_STATUS_MAP:
        if not (verbose and t in _TASK_VERBOSE_STATUS_MAP):
            return None
        status = _TASK_VERBOSE_STATUS_MAP[t]
    else:
        status = _TASK_STATUS_MAP[t]

    if t in _PLAN_SCOPED_EVENTS:
        task_id = plan_slug
        parent = None
    else:
        phase = event.get("phase", "?")
        task_id = f"{plan_slug}/{phase}"
        parent = plan_slug

    msg = _escape_msg(_task_msg_for(event))
    return _task_line("TASK_UPDATE", task_id, parent=parent, status=status, msg=msg)


def _slug_for_path(path: Path) -> str:
    return path.stem.removesuffix(".state")


def _state_path_to_project(state_path: Path) -> Path:
    # <project>/plans/.orchestrator/<slug>.state.json — walk up 3 levels
    return state_path.parent.parent.parent


def bootstrap_task_list(
    state_paths: list[Path],
    cfg_loader: Callable[[Path], Any],
    sink: TextIO,
) -> None:
    """Emit TASK_CREATE per plan+phase; if current_claim is running, also emit TASK_UPDATE to reconcile."""
    for state_path in state_paths:
        if not state_path.exists():
            continue
        slug = _slug_for_path(state_path)
        if not slug:
            continue
        try:
            data: dict = json.loads(state_path.read_text())
        except Exception:
            data = {}
        cfg = cfg_loader(state_path)
        plan_path = cfg.project_root / cfg.plan_dir / f"{slug}.md"
        if not plan_path.exists():
            raise FileNotFoundError(f"no master plan at {plan_path}")
        print(_task_line("TASK_CREATE", slug, status="pending"),
              file=sink, flush=True)
        for phase in parse_sessions_index(plan_path):
            print(_task_line("TASK_CREATE", f"{slug}/{phase.id}",
                             parent=slug, status="pending"),
                  file=sink, flush=True)
        claim = data.get("current_claim")
        if claim and data.get("status") == "running":
            phase_id = claim["phase_id"]
            print(_task_line("TASK_UPDATE", slug, status="in_progress",
                             msg="bootstrap: plan running"),
                  file=sink, flush=True)
            print(_task_line("TASK_UPDATE", f"{slug}/{phase_id}",
                             parent=slug, status="in_progress",
                             msg="bootstrap: already active"),
                  file=sink, flush=True)


def _snapshot_line(slug: str, data: dict) -> str:
    claim = data.get("current_claim")
    active = f"active={claim['phase_id']}" if claim else "active=none"
    return f"[snapshot] {slug}: {data['status']}, {active}"


def stream_loop(
    state_paths: list[Path],
    *,
    json_mode: bool = False,
    task_list_mode: bool = False,
    verbose: bool = False,
    operator: bool = False,
    sink: TextIO | None = None,
    poll_interval: float = 1.0,
    max_ticks: int | None = None,
    _before_first_tick: Callable[[], None] | None = None,
    cfg_loader: Callable[[Path], Any] | None = None,
) -> int:
    """Poll state files, emit projected events. Returns ExitCode.OK (0).

    `_before_first_tick` is a test seam called once after the baseline
    snapshot and before the first poll tick — lets tests inject events
    without threading.

    `task_list_mode` routes events through `project_event_task` and
    emits a TASK_CREATE bootstrap before the snapshot baseline.
    Mutually exclusive with `json_mode` (CLI gates this).
    """
    if sink is None:
        sink = sys.stdout
    cursors: dict[Path, int] = {}
    baseline: list[tuple[str, dict]] = []

    for path in list(state_paths):
        try:
            data = st.load(path)
        except (FileNotFoundError, OSError, json.JSONDecodeError, st.SchemaVersionMismatch):
            continue
        slug = _slug_for_path(path)
        cursors[path] = len(data.get("events", []))
        baseline.append((slug, data))

    if task_list_mode:
        if cfg_loader is None:
            from .cli import load_project_config  # lazy — cli imports watch, avoid cycle
            cfg_loader = lambda sp: load_project_config(_state_path_to_project(sp))
        bootstrap_task_list(list(cursors.keys()), cfg_loader, sink)

    for slug, data in baseline:
        print(_snapshot_line(slug, data), file=sink, flush=True)

    if _before_first_tick is not None:
        _before_first_tick()

    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            for path in list(cursors.keys()):
                try:
                    data = st.load(path)
                except (FileNotFoundError, OSError, json.JSONDecodeError, st.SchemaVersionMismatch):
                    cursors.pop(path, None)
                    continue
                events = data.get("events", [])
                slug = _slug_for_path(path)
                for evt in events[cursors[path]:]:
                    if task_list_mode:
                        line_or_none = project_event_task(evt, slug, verbose=verbose)
                    else:
                        line_or_none = project_event(evt, slug, verbose=verbose,
                                                     operator=operator)
                    if line_or_none is None:
                        continue
                    if json_mode:
                        print(json.dumps({"ts": evt.get("ts"), "slug": slug, "event": evt}),
                              file=sink, flush=True)
                    else:
                        print(line_or_none, file=sink, flush=True)
                cursors[path] = len(events)
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("", file=sink, flush=True)
    return 0


def project_event(
    event: dict[str, Any],
    plan_slug: str,
    *,
    verbose: bool = False,
    operator: bool = False,
) -> str | None:
    t = event.get("type")
    if operator:
        if t not in _OPERATOR_VISIBLE:
            return None
        # operator mode bypasses the _VERBOSE_ONLY gate so wedge signals
        # like stalled_claim_notified render at default volume.
    elif t in _VERBOSE_ONLY and not verbose:
        return None
    fmt = _FORMATTERS.get(t)
    return fmt(plan_slug, event) if fmt else None
