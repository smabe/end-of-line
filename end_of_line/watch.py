"""Streaming projection of plan state events for AI-agent consumption
(Claude's Monitor tool). See plans/clu-watch.md."""

from __future__ import annotations

import errno
import json
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from . import db, plan_store
from . import state as st
from .plan_parser import parse_sessions_index

_DEFAULT_VISIBLE: frozenset[str] = frozenset(
    filter(
        None,
        {
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
            # Dead-PID detection (#72) — operator-actionable: a worker died, the
            # claim got released, next tick re-dispatches.
            getattr(st, "EVENT_PHASE_WORKER_DEAD", None),
            # Heartbeat-daemon death report (#104) — DEFAULT-visible, not
            # verbose-only: #104's complaint is that live watch streams saw
            # zero lines when the worker died, so an event only --verbose shows
            # would reproduce the incident.
            getattr(st, "EVENT_PHASE_WORKER_DEAD_REPORTED", None),
        },
    )
)

_VERBOSE_ONLY: frozenset[str] = frozenset(
    {
        st.EVENT_LEASE_EXPIRED,
        st.EVENT_LEASE_EXTENDED,
        st.EVENT_PHASE_ORPHAN_REAPED,
        st.EVENT_CLAIM_FORCE_RELEASED,
        st.EVENT_ATTEMPTS_RESET,
        st.EVENT_STUCK_BLOCKER_REPINGED,
        st.EVENT_STALLED_CLAIM_NOTIFIED,
        st.EVENT_HEARTBEAT_LOOP_FAILING,
        st.EVENT_WORKTREE_ATTACHED,
        st.EVENT_WORKTREE_CLEANED,
        st.EVENT_WORKTREE_RETAINED_AHEAD,
    }
)

# Operator-dashboard (#70) filter — the cross-plan-worth-interrupting set.
# Under `clu watch --operator`, only these events render; the _VERBOSE_ONLY
# gate is bypassed (stalled_claim_notified is operator-relevant even when
# the normal verbose check would hide it).
_OPERATOR_VISIBLE: frozenset[str] = frozenset(
    filter(
        None,
        {
            getattr(st, "EVENT_TOOL_STUCK", None),
            st.EVENT_PHASE_BLOCKED,
            getattr(st, "EVENT_ATTESTATION_REFUSED", None),
            st.EVENT_STALLED_CLAIM_NOTIFIED,
            st.EVENT_HEARTBEAT_LOOP_FAILING,
            getattr(st, "EVENT_PHASE_WORKER_DEAD", None),
            getattr(st, "EVENT_WORKER_IDLE", None),
            getattr(st, "EVENT_PHASE_WORKER_DEAD_REPORTED", None),
        },
    )
)


def _trunc(s: str | None, n: int = 100) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


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
    st.EVENT_PHASE_COMPLETED: lambda slug, e: f"{_phase_prefix(slug, e)}: completed",
    st.EVENT_PHASE_BLOCKED: _fmt_blocked,
    st.EVENT_BLOCKER_ANSWERED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: answer received for "
        f"{e.get('blocker_id', '?')}: {_trunc(e.get('answer'))}"
    ),
    st.EVENT_BLOCKER_CONSUMED: lambda slug, e: (
        f"{slug}: blocker {e.get('blocker_id', '?')} consumed — phase resuming"
    ),
    st.EVENT_BLOCKER_SLA_EXCEEDED: lambda slug, e: (
        f"{slug}: blocker {e.get('blocker_id', '?')} SLA exceeded ({e.get('age_hours', '?')}h)"
    ),
    st.EVENT_PHASE_MAX_ATTEMPTS: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: HALTED max attempts ({e.get('attempts', '?')})"
    ),
    st.EVENT_PHASE_STALLED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: stalled ({e.get('age_seconds', '?')}s since last heartbeat)"
    ),
    st.EVENT_TASK_SPAWNED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: spawned task {e.get('task', '?')}"
    ),
    st.EVENT_TASK_COMPLETED: lambda slug, e: f"{slug}: task {e.get('task', '?')} done",
    st.EVENT_PLAN_COMPLETED: lambda slug, e: f"{slug}: PLAN DONE",
    st.EVENT_PLAN_ABANDONED: lambda slug, e: (
        f"{slug}: ABANDONED" + (f" ({e.get('reason')})" if e.get("reason") else "")
    ),
    st.EVENT_DISPATCH_FAILED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: dispatch failed — {_trunc(e.get('reason'))}"
    ),
    st.EVENT_SYSTEMIC_FAILURE: lambda slug, e: (
        f"{slug}: SYSTEMIC FAILURE — {_trunc(e.get('signature'))}"
    ),
    st.EVENT_PAUSED: lambda slug, e: (
        f"{slug}: paused" + (f" ({_trunc(e.get('reason'))})" if e.get("reason") else "")
    ),
    st.EVENT_RESUMED: lambda slug, e: f"{slug}: resumed",
    st.EVENT_RETRY_REQUESTED: lambda slug, e: f"{_phase_prefix(slug, e)}: retry requested",
    st.EVENT_QUEUE_POPPED: lambda slug, e: (
        f"{slug}: popped {e.get('slug', '?')} from queue (by {e.get('added_by', '?')})"
    ),
    st.EVENT_WORKTREE_MISSING: lambda slug, e: (
        f"{slug}: WORKTREE MISSING — {e.get('worktree_path', '?')}"
    ),
    st.EVENT_WORKTREE_CONFLICT_WARNING: lambda slug, e: (
        f"{slug}: worktree conflict with {e.get('other_slug', '?')} "
        f"— both plans share project without isolated worktrees"
    ),
    # Verbose-only
    st.EVENT_LEASE_EXPIRED: lambda slug, e: f"{_phase_prefix(slug, e)}: lease expired",
    st.EVENT_PHASE_ORPHAN_REAPED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: orphan reaped "
        f"pid={e.get('pid', '?')} signaled={e.get('signaled', '?')}"
    ),
    st.EVENT_LEASE_EXTENDED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: lease extended by "
        f"{e.get('extended_by_minutes', '?')}min → {e.get('new_expires', '?')}"
    ),
    st.EVENT_CLAIM_FORCE_RELEASED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: claim force-released" + (" (forced)" if e.get("forced") else "")
    ),
    st.EVENT_ATTEMPTS_RESET: lambda slug, e: f"{_phase_prefix(slug, e)}: attempts reset",
    st.EVENT_STUCK_BLOCKER_REPINGED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: blocker {e.get('blocker_id', '?')} "
        f"re-pinged ({e.get('age_min', '?')}min open)"
    ),
    st.EVENT_STALLED_CLAIM_NOTIFIED: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: stalled claim notification sent "
        f"({e.get('stalled_min', '?')}min past lease)"
    ),
    st.EVENT_HEARTBEAT_LOOP_FAILING: lambda slug, e: (
        f"{_phase_prefix(slug, e)}: HEARTBEAT LOOP FAILING "
        f"log={_trunc(e.get('log_path', '?'), 60)}"
    ),
    st.EVENT_WORKTREE_ATTACHED: lambda slug, e: (
        f"{slug}: worktree attached at {e.get('path', '?')} (branch {e.get('branch', '?')})"
    ),
    st.EVENT_WORKTREE_CLEANED: lambda slug, e: (
        f"{slug}: worktree cleaned — {e.get('path', '?')} (trigger={e.get('trigger', '?')})"
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
        f"{slug}: queued {e.get('slug', '?')} from phase {e.get('source_phase', '?')}"
    )
if _Q_REJECTED:
    _FORMATTERS[_Q_REJECTED] = lambda slug, e: (
        f"{slug}: queue rejected {e.get('slug', '?')} from phase "
        f"{e.get('source_phase', '?')} ({e.get('reason', '?')})"
    )


# Dead-PID formatter (#72) — splice in only when constant is defined so
# older state files predating worker-pid-liveness don't trip up the dispatch.
_WORKER_DEAD = getattr(st, "EVENT_PHASE_WORKER_DEAD", None)
if _WORKER_DEAD:
    _FORMATTERS[_WORKER_DEAD] = lambda slug, e: (
        f"{_phase_prefix(slug, e)}: WORKER DEAD pid={e.get('pid', '?')}"
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


# Worker-idle formatter (wedge-watchdogs P2) — splice in when defined.
_WORKER_IDLE = getattr(st, "EVENT_WORKER_IDLE", None)
if _WORKER_IDLE:
    _FORMATTERS[_WORKER_IDLE] = lambda slug, e: (
        f"{_phase_prefix(slug, e)}: WORKER IDLE pid={e.get('pid', '?')} "
        f"low_cpu={e.get('low_cpu_minutes', '?')}min"
    )


# Heartbeat-daemon death-report formatter (#104) — splice in when defined.
# "(daemon)" distinguishes it from the supervisor's own WORKER DEAD line.
_WORKER_DEAD_REPORTED = getattr(st, "EVENT_PHASE_WORKER_DEAD_REPORTED", None)
if _WORKER_DEAD_REPORTED:
    _FORMATTERS[_WORKER_DEAD_REPORTED] = lambda slug, e: (
        f"{_phase_prefix(slug, e)}: WORKER DEAD (daemon) pid={e.get('pid', '?')}"
    )


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
if _WORKER_DEAD:
    _TASK_STATUS_MAP[_WORKER_DEAD] = "in_progress"
if _WORKER_DEAD_REPORTED:
    _TASK_STATUS_MAP[_WORKER_DEAD_REPORTED] = "in_progress"

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
_PLAN_SCOPED_EVENTS: frozenset[str] = frozenset(
    {
        st.EVENT_PLAN_COMPLETED,
        st.EVENT_PAUSED,
        st.EVENT_RESUMED,
    }
)


def _escape_msg(s: str) -> str:
    # Backslash pass MUST stay first: the \n / \r passes emit backslashes, and a
    # later backslash pass would double-escape them. Newlines are escaped, not
    # stripped, so the operator's wording survives on the one-line msg="…" record.
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


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
    """Emit TASK_CREATE per plan+phase.

    If current_claim is running, also emit TASK_UPDATE to reconcile.
    """
    for state_path in state_paths:
        if not plan_store.exists_for_path(state_path):
            continue
        slug = _slug_for_path(state_path)
        if not slug:
            continue
        try:
            data: dict = plan_store.snapshot(*plan_store.key_for_state_path(state_path))
        except Exception:
            # Deliberately the widest clause in this module: the bootstrap's
            # only job is to name the plan's phases, which comes from the plan
            # MARKDOWN. The claim it reads here is a nicety — a plan whose
            # store is missing, busy, broken or too new still gets its task
            # tree, just without the "already running" reconciliation.
            data = {}
        cfg = cfg_loader(state_path)
        plan_path = cfg.project_root / cfg.plan_dir / f"{slug}.md"
        if not plan_path.exists():
            continue
        print(_task_line("TASK_CREATE", slug, status="pending"), file=sink, flush=True)
        for phase in parse_sessions_index(plan_path):
            print(
                _task_line("TASK_CREATE", f"{slug}/{phase.id}", parent=slug, status="pending"),
                file=sink,
                flush=True,
            )
        claim = data.get("current_claim")
        if claim and data.get("status") == "running":
            phase_id = claim["phase_id"]
            print(
                _task_line(
                    "TASK_UPDATE", slug, status="in_progress", msg="bootstrap: plan running"
                ),
                file=sink,
                flush=True,
            )
            print(
                _task_line(
                    "TASK_UPDATE",
                    f"{slug}/{phase_id}",
                    parent=slug,
                    status="in_progress",
                    msg="bootstrap: already active",
                ),
                file=sink,
                flush=True,
            )


def _event_id(event: dict) -> int:
    """An event's monotonic row id, or 0 for an event that has none.

    Events appended inside a mutate window carry no id until the store writes
    them, and a hand-built fixture may carry none at all; 0 sorts them before
    every stored event, which is where an un-persisted event belongs.
    """
    raw = event.get("id")
    return int(raw) if isinstance(raw, int) else 0


def _max_event_id(events: list[dict]) -> int:
    return max((_event_id(e) for e in events), default=0)


def _snapshot_line(slug: str, data: dict) -> str:
    claim = data.get("current_claim")
    active = f"active={claim['phase_id']}" if claim else "active=none"
    return f"[snapshot] {slug}: {data['status']}, {active}"


# What a poll degrades on, split by whether waiting helps.
#
# Retry: the store is fine, somebody else is writing. `db.DbBusy` is what
# `db.read_txn` raises when the BEGIN itself is refused; `sqlite3.OperationalError`
# is what the FIRST statement inside a deferred transaction raises when the
# refusal lands there instead (WAL's last-connection-close cleanup, which
# `db.connect` warns readers about). A stream that dropped a plan for either
# would go quiet for the rest of its life over a lock held for milliseconds.
_RETRY_PLAN_ERRORS = (db.DbBusy, sqlite3.OperationalError)

# Skip: no database, no such plan, a store this clu must not read, or one that
# cannot be read at all. The file-era clause named `FileNotFoundError`/`OSError`
# and `ValueError`; a database adds `sqlite3.Error` for a broken store and
# `db.SchemaTooNew` for one from a newer clu, neither of which any of those
# names catches.
_SKIP_PLAN_ERRORS = (
    *db.DEGRADABLE_ERRORS,
    st.SchemaVersionMismatch,
    st.InvalidSlug,
    ValueError,
)


class _ProjectReader:
    """One project database, one connection, held across frames.

    The connection is held so `PRAGMA data_version` means something: it moves
    only when ANOTHER connection commits, so a poller that reconnects every
    frame reads a counter with no history and can never gate on it. What it
    never holds is a TRANSACTION — a reader with one open pins the WAL past its
    autocheckpoint and the file grows without bound until it lets go — so every
    read is its own short `read_txn`.
    """

    def __init__(self, orch_dir: Path) -> None:
        path = db.project_db_path(orch_dir)
        if not path.exists():
            raise FileNotFoundError(errno.ENOENT, "no clu database", str(path))
        self.conn = db.connect(path, readonly=True)
        self.conn.row_factory = sqlite3.Row
        try:
            db.ensure_project_schema(self.conn)
            # Primed HERE, before the caller takes its baseline snapshot, so
            # the first poll is already a real comparison. Priming afterwards
            # would make every stream's first tick an unconditional query, and
            # priming lazily on the first poll would risk the opposite: a
            # commit landing between the baseline and the first reading would
            # be invisible until the next unrelated write.
            self._data_version: int | None = self._read_data_version()
        except BaseException:
            self.conn.close()
            raise

    def _read_data_version(self) -> int:
        return int(self.conn.execute("PRAGMA data_version").fetchone()[0])

    def changed_since_last_frame(self) -> bool:
        """Has anything committed to this database since the last poll?"""
        found = self._read_data_version()
        moved = self._data_version is None or found != self._data_version
        self._data_version = found
        return moved

    def invalidate(self) -> None:
        """Force the next frame to query, whatever `data_version` says.

        For the poll that saw the counter move and then failed to read: the
        events it was about to fetch are still unread, and the counter will not
        move again just because this stream missed them.
        """
        self._data_version = None

    def events_after(self, slug: str, after_id: int) -> list[dict]:
        with db.read_txn(self.conn) as cur:
            return plan_store.events_after(cur, slug, after_id)

    def close(self) -> None:
        self.conn.close()


def _close(reader: _ProjectReader | None) -> None:
    if reader is None:
        return
    try:
        reader.close()
    except sqlite3.Error:
        # A connection that cannot even be closed is one this stream has
        # already stopped using; the process exit will free the handle.
        pass


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
    # Cursor = the highest event id seen, never the list length. Ids are
    # monotonic and never reused, so archiving a terminal plan's events out of
    # the hot table cannot shrink the list under a live cursor — which a length
    # cursor would read as "rewound", replaying history.
    cursors: dict[Path, int] = {}
    keys: dict[Path, tuple[Path, str]] = {}
    baseline: list[tuple[str, dict]] = []

    # One read-only connection per PROJECT database, held for the life of the
    # stream. Held, because `PRAGMA data_version` only moves for OTHER
    # connections' commits — a fresh connection each frame would see a fresh
    # counter and the gate would never fire. Per project rather than per plan,
    # because plans in a project share one database and N connections would buy
    # nothing. Opened before the baseline, so the first poll already has a
    # reading to compare against.
    readers: dict[Path, _ProjectReader] = {}

    for path in list(state_paths):
        try:
            key = plan_store.key_for_state_path(path)
            if key[0] not in readers:
                readers[key[0]] = _ProjectReader(key[0])
            data = plan_store.snapshot(*key)
        except (*_RETRY_PLAN_ERRORS, *_SKIP_PLAN_ERRORS):
            continue
        slug = _slug_for_path(path)
        cursors[path] = _max_event_id(data.get("events", []))
        keys[path] = key
        baseline.append((slug, data))

    if task_list_mode:
        if cfg_loader is None:
            from .cli import load_project_config  # lazy — cli imports watch, avoid cycle

            def _default_cfg_loader(sp: Path) -> Any:
                return load_project_config(_state_path_to_project(sp))

            cfg_loader = _default_cfg_loader

        bootstrap_sink = sink if sink is not None else sys.stdout
        bootstrap_task_list(list(cursors.keys()), cfg_loader, bootstrap_sink)

    # Operator mode wants ONLY wedge events; suppress the per-plan snapshot
    # baseline so the dashboard signal stays clean.
    if not operator:
        for slug, data in baseline:
            print(_snapshot_line(slug, data), file=sink, flush=True)

    if _before_first_tick is not None:
        _before_first_tick()

    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            # One `data_version` reading per project per FRAME, not per plan:
            # plans in a project share a database, and asking twice would have
            # the second plan compare against the first plan's reading and
            # conclude nothing had changed.
            moved: dict[Path, bool] = {}
            for path in list(cursors.keys()):
                orch_dir, slug_key = keys[path]
                try:
                    reader = readers.get(orch_dir)
                    if reader is None:
                        reader = readers[orch_dir] = _ProjectReader(orch_dir)
                    if orch_dir not in moved:
                        moved[orch_dir] = reader.changed_since_last_frame()
                    if not moved[orch_dir]:
                        # The idle poll, which is the common case by a wide
                        # margin: one PRAGMA and no query at all.
                        continue
                    events = reader.events_after(slug_key, cursors[path])
                except _RETRY_PLAN_ERRORS:
                    # Contention, not breakage. Both spellings are here on
                    # purpose: `read_txn` translates a busy at its own BEGIN
                    # into `db.DbBusy`, but a deferred BEGIN acquires nothing —
                    # the read snapshot is taken by the FIRST statement inside
                    # it, and a busy there arrives as SQLite's own
                    # `OperationalError`. Keep the connection and the cursor,
                    # and force the next tick to query: the events this poll
                    # missed are still unread, and `data_version` will not move
                    # again just because this stream did not get to them.
                    if (retryable := readers.get(orch_dir)) is not None:
                        retryable.invalidate()
                    moved.pop(orch_dir, None)
                    continue
                except _SKIP_PLAN_ERRORS:
                    # Unreadable for a reason a retry will not fix: no database,
                    # no such plan, or one written by a newer clu (upstream
                    # decision #6 — skip, never downgrade).
                    _close(readers.pop(orch_dir, None))
                    moved.pop(orch_dir, None)
                    cursors.pop(path, None)
                    keys.pop(path, None)
                    continue
                slug = _slug_for_path(path)
                seen = cursors[path]
                for evt in events:
                    if task_list_mode:
                        line_or_none = project_event_task(evt, slug, verbose=verbose)
                    else:
                        line_or_none = project_event(evt, slug, verbose=verbose, operator=operator)
                    if line_or_none is None:
                        continue
                    if json_mode:
                        print(
                            json.dumps({"ts": evt.get("ts"), "slug": slug, "event": evt}),
                            file=sink,
                            flush=True,
                        )
                    else:
                        print(line_or_none, file=sink, flush=True)
                cursors[path] = max(seen, _max_event_id(events))
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("", file=sink, flush=True)
    finally:
        for reader in readers.values():
            _close(reader)
    return 0


def project_event(
    event: dict[str, Any],
    plan_slug: str,
    *,
    verbose: bool = False,
    operator: bool = False,
) -> str | None:
    t = event.get("type")
    if not isinstance(t, str):
        return None  # malformed event — every emitter stamps a string "type"
    if operator:
        if t not in _OPERATOR_VISIBLE:
            return None
        # operator mode bypasses the _VERBOSE_ONLY gate so wedge signals
        # like stalled_claim_notified render at default volume.
    elif t in _VERBOSE_ONLY and not verbose:
        return None
    fmt = _FORMATTERS.get(t)
    return fmt(plan_slug, event) if fmt else None
