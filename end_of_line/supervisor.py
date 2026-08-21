"""Single-tick supervisor logic.

Shape: **snapshot → detect → apply**, and the split is a correctness
requirement rather than tidiness. Detection shells out — `ps` with a 5s
timeout, a process-group reap that polls for seconds — and the
project database allows exactly ONE writer, so a transaction held across that
work would starve every worker callback in the project. So the tick reads one
snapshot, decides while holding nothing at all, and applies every change in a
single end-of-tick transaction guarded by PRECONDITIONS: the specific facts its
decision rested on, re-asserted inside that transaction (see
`plan_store.TickPreconditions`). A precondition that no longer holds discards
the whole tick — `[idle] concurrent_write` — and the 30s cron re-drives.

A discarded tick stays QUIET: nothing was written, so no dedup marker
committed, so sending its notifications would double-ping on the retry.

Everything that is not a database write runs OUTSIDE the transaction, and the
ordering rule is that durable state commits FIRST: coolant emissions, group
reaps and inbox events fire only after the apply, and notifications only after
`tick` returns.

Action priority (first match wins):
  1. Stale lease release
  2. Dead-PID release (issue #72: heartbeat-zombie keeps the lease fresh
     after worker death; catch within one tick instead of full lease TTL)
  3. Stalled heartbeat → emit phase_stalled once
  4. Stale-question escalation
  5. Answered-question resume (mark consumed)
  6. Plan halted/paused → idle
  7. Active claim → idle
  8. Project quota pause gate → idle while paused; one canary dispatches
     past the reset, fleet resumes when it survives (#94)
  9. Dispatch next pending phase
  10. All phases complete → mark plan done
  11. Idle
"""

from __future__ import annotations

import datetime as _dt
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import coolant, db, inbox, notify, plan_store, quota, state_blocker
from . import state as st
from .config import ORCHESTRATOR_DIR, ProjectConfig
from .plan_parser import parse_sessions_index


def _local_now() -> _dt.datetime:
    """Wall-clock local time. Indirection exists so tests can pin the hour."""
    return _dt.datetime.now()


def _death_detail(phase_id: str, quota_match: quota.QuotaMatch | None) -> str:
    """TickResult detail for the two worker-death paths (lease-expiry, dead-PID)."""
    detail = f"phase={phase_id}"
    if quota_match is not None:
        detail += f" quota={quota_match.signature}"
    return detail


# ---------------------------------------------------------------------------
# Stuck-tool detection — process-tree walker (worker-watchdog P2).
#
# The supervisor walks a worker pid's process tree to find descendants that
# have been alive a long time with low CPU usage — the signal for a wedged
# tool call (canonical: xcodebuild hanging on simulator HK auth). This is
# the pure walker; the threshold + emit logic lives in detect_stuck_tools.
# ---------------------------------------------------------------------------

# Drift tolerance (seconds) for `descendant.elapsed_seconds <= active_age + DRIFT`.
# Absorbs (a) ps's 1-second elapsed-time resolution and (b) wallclock skew
# between the worker process stamping `active_tool_started_at` and the
# supervisor process computing `now - active_tool_started_at`. Five seconds
# is generous for the same-host case clu targets; bump if NTP is loose or
# if we ever run worker + supervisor on different machines.
STUCK_TOOL_DRIFT_SECONDS = 5


@dataclass(frozen=True)
class Descendant:
    pid: int
    parent_pid: int
    elapsed_seconds: float
    cpu_seconds: float
    command: str


def _parse_duration(raw: str) -> float:
    """Parse a `ps` duration to seconds, PRESERVING fractions.

    Handles both etime ([[dd-]hh:]mm:ss) and CPU time ([hh:]mm:ss[.cc]).
    Returns 0.0 for empty input or the literal "-" that ps emits for
    unmeasurable fields.

    The centiseconds matter: the idle watchdog separates a worker waiting on
    the model from one doing nothing at all by how much processor time the
    tree accrues between ticks, and that difference is fractions of a second
    (measured: 0.15s / 0.26s / 1.27s over 30s for live processes, 0.00s for a
    dormant one). Truncating to whole seconds erases the entire signal.
    """
    s = raw.strip()
    if not s or s == "-":
        return 0.0
    days = 0
    if "-" in s:
        days_str, s = s.split("-", 1)
        try:
            days = int(days_str)
        except ValueError:
            return 0.0
    fraction = 0.0
    if "." in s:
        s, frac_str = s.split(".", 1)
        try:
            fraction = float(f"0.{frac_str}")
        except ValueError:
            return 0.0
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0.0
    while len(nums) < 3:
        nums.insert(0, 0)
    h, m, sec = nums[-3], nums[-2], nums[-1]
    return days * 86400 + h * 3600 + m * 60 + sec + fraction


def _parse_ps_output(raw: str) -> list[Descendant]:
    """Parse `ps -eo pid,ppid,etime,time,command` output. Skips header line."""
    out: list[Descendant] = []
    lines = raw.strip().split("\n")
    # Skip the header line if present — detected by first char not being a digit.
    start = 0 if lines[0].lstrip()[:1].isdigit() else 1
    for line in lines[start:]:
        parts = line.strip().split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        elapsed = _parse_duration(parts[2])
        cpu = _parse_duration(parts[3])
        out.append(Descendant(pid, ppid, elapsed, cpu, parts[4]))
    return out


def capture_ps_snapshot() -> str:
    """Run `ps -eo pid,ppid,etime,time,command` once, return stdout.

    Empty string on subprocess failure (treated as an empty process list
    by `_parse_ps_output`). Exposed so callers that walk multiple worker
    trees in one pass (`clu doctor`) can share a single snapshot instead
    of forking ps per plan.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,etime,time,command"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def walk_worker_tree(
    root_pid: int,
    *,
    ps_output: str | None = None,
) -> list[Descendant]:
    """Return descendants of root_pid in BFS order, excluding root itself.

    Shells out to `ps -eo pid,ppid,etime,time,command` unless ps_output is
    provided (tests pass a fixture string). The active-tool window in
    `_emit_stuck_tool` does the filtering; this walker is pure.
    """
    if ps_output is None:
        ps_output = capture_ps_snapshot()
        if not ps_output:
            return []

    procs = _parse_ps_output(ps_output)
    by_ppid: dict[int, list[Descendant]] = {}
    for p in procs:
        by_ppid.setdefault(p.parent_pid, []).append(p)

    out: list[Descendant] = []
    seen: set[int] = {root_pid}
    queue: list[int] = [root_pid]
    while queue:
        current = queue.pop(0)
        for child in by_ppid.get(current, []):
            if child.pid in seen:
                continue
            seen.add(child.pid)
            queue.append(child.pid)
            out.append(child)
    return out


Action = Literal[
    "dispatch",
    "idle",
    "lease_expired",
    "worker_dead",
    "escalate",
    "blocker_resumed",
    "halt",
    "plan_done",
    "error",
    "stalled",
]


@dataclass
class TickResult:
    action: Action
    detail: str = ""
    phase_id: str | None = None
    token: str | None = None
    # Rendered iMessage body, populated for actions that should ping the
    # user. cmd_tick dispatches AFTER tick() exits the state lock so a hung
    # Messages.app can't hold the lock.
    notify_body: str | None = None
    # Parallel iMessage emissions for the same tick — gap-fill notifications
    # (stuck-blocker re-pings, stalled-claim transitions) that fire alongside
    # the primary action rather than replacing it. Each entry is (kind, body).
    side_notifies: list[tuple[str, str]] = field(default_factory=list)
    # Plan's `state.worktree` record (`{path, branch, base_ref}`) captured
    # inside the state lock and handed to `dispatch_for_tick` so it can
    # `Popen(cwd=...)` without a second state load. None when the plan
    # runs against the main project root.
    worktree: dict | None = None

    def __str__(self) -> str:
        return f"[{self.action}] {self.detail}" if self.detail else f"[{self.action}]"


# Maps the actions that produce a notification to the notify-kind tag used
# for quiet-hours classification. Adding an action here is the one-line
# change a future contributor needs to make a tick path notify.
ACTION_NOTIFY_KIND: dict[Action, str] = {
    "stalled": notify.KIND_STALLED,
    "worker_dead": notify.KIND_STALLED,
    "plan_done": notify.KIND_COMPLETED,
    "halt": notify.KIND_HALTED,
}


@dataclass
class TickDelta:
    """Everything one tick decided to write, as data.

    Detection produces this against a snapshot while holding no lock;
    `plan_store.apply_tick_delta` writes all of it in one transaction, or none
    of it. `observed` carries the facts the snapshot saw and `pre` the subset
    this tick's decisions actually rested on — the `require_*` methods are how
    a detection path moves one across, and a path declares only its own.

    Two lists ride along that are NOT database writes and must not happen
    before the apply commits: `inbox_events` (host-database rows the gap-fill
    emitters raise) and, on `TickResult`, the notification bodies. A tick whose
    apply conflicts drops both, because their dedup markers were in the
    discarded write.
    """

    events: list[dict] = field(default_factory=list)
    claim_updates: dict[str, Any] = field(default_factory=dict)
    # The `claimed_by` `claim_updates` is compare-and-set against — the claim
    # the tick was looking at when it judged.
    claim_token: str | None = None
    blocker_updates: dict[str, dict] = field(default_factory=dict)
    release_claim: bool = False
    status: str | None = None
    claim_phase: str | None = None
    lease_minutes: int | None = None
    # `state.claim_phase` counts this off the event log; the apply has no
    # history to count, so the tick counts it against the snapshot.
    claim_attempts: int | None = None
    inbox_events: list[dict] = field(default_factory=list)

    observed: plan_store.TickPreconditions = field(
        default_factory=plan_store.TickPreconditions
    )
    pre: plan_store.TickPreconditions = field(
        default_factory=plan_store.TickPreconditions
    )

    def is_empty(self) -> bool:
        """True when this tick decided to write nothing, so it opens no txn."""
        return not (
            self.events
            or self.claim_updates
            or self.blocker_updates
            or self.release_claim
            or self.status is not None
            or self.claim_phase is not None
        )

    # -- precondition declarations --------------------------------------------
    #
    # One method per entry in the vocabulary. A path that needs a guarantee
    # none of these gives adds a method rather than widening one, because
    # widening is how a precondition set decays into a version counter.

    def require_claim(self) -> None:
        """The claim identity (or its absence) this decision read."""
        self.pre.expect_claim = self.observed.expect_claim

    def require_claim_field(self, name: str) -> None:
        """One claim field this decision judged."""
        self.pre.expect_claim_fields[name] = self.observed.expect_claim_fields.get(name)

    def require_event_log(self) -> None:
        """The event history this decision was derived from."""
        self.pre.expect_max_event_id = self.observed.expect_max_event_id

    def require_blocker(self, blocker_id: str) -> None:
        """One blocker's answered/consumed state."""
        state = self.observed.expect_blocker_state.get(blocker_id)
        if state is not None:
            self.pre.expect_blocker_state[blocker_id] = state

    def require_status(self) -> None:
        """The plan status this decision assumed."""
        self.pre.expect_status = self.observed.expect_status


def _delta_event(delta: TickDelta, data: dict, event_type: str, **fields: Any) -> None:
    """Record an event on the delta AND on the working snapshot.

    Both, deliberately. The delta is what the transaction writes; the snapshot
    copy is what the REST of this tick reads — the priority chain projects
    completed phases and attempt counts out of `data["events"]`, and a
    detection helper that wrote only one of the two would make the tick decide
    against a history it had already changed.
    """
    event = {"ts": st.utcnow(), "type": event_type, **fields}
    delta.events.append(event)
    data["events"].append(event)


def _carry_events(delta: TickDelta, data: dict, events: list[dict]) -> None:
    """`_delta_event` for events some other module already built."""
    delta.events.extend(events)
    data["events"].extend(events)


def _stamp_claim(delta: TickDelta, claim: dict, **fields: Any) -> None:
    """Write claim fields into the delta AND onto the working snapshot claim."""
    delta.claim_token = claim.get("claimed_by")
    delta.claim_updates.update(fields)
    claim.update(fields)


def _record_claim(delta: TickDelta, claim: dict, *names: str) -> None:
    """Route claim fields a shared `state` helper already stamped into the delta.

    `mark_tool_stuck_emitted`, `append_cpu_sample` and `mark_worker_idle_emitted`
    edit the claim dict in place and are shared with other callers, so this is
    how their writes reach the transaction. The snapshot's own copy of those
    fields is deep-copied by `snapshot_with_preconditions`, so the in-place edit
    cannot reach back and rewrite the value a precondition compares against.
    """
    delta.claim_token = claim.get("claimed_by")
    for name in names:
        delta.claim_updates[name] = claim.get(name)


def _stamp_blocker(delta: TickDelta, blocker: dict, **fields: Any) -> None:
    """`_stamp_claim` for one blocker."""
    delta.blocker_updates.setdefault(blocker["id"], {}).update(fields)
    blocker.update(fields)


def _detect_stalled(data: dict, *, delta: TickDelta) -> TickResult | None:
    """Emit phase_stalled on the first tick we notice a stalled claim, then idle.

    Records `stalled_notified=True` on the claim so subsequent ticks fall
    through. Returns None when there's nothing to flag.
    """
    claim = data.get("current_claim")
    if not claim or claim.get("stalled_notified"):
        return None
    # `claude --print` workers buffer stdout; bundled /clu-phase doesn't
    # call `clu heartbeat`. Lease expiry still catches silent workers via
    # _detect_lease_expired. (#27)
    if claim.get("last_heartbeat_at") == claim.get("started_at"):
        return None
    threshold = st.stalled_threshold_for_phase(data, claim["phase_id"])
    age = st.heartbeat_age_seconds(claim) or 0.0
    if age < threshold * 60:
        return None
    token = claim.get("claimed_by", "")
    # This decision is "the heartbeat is old", so a heartbeat landing while the
    # tick was deciding must cancel it — that is the precondition doing its job.
    delta.require_claim_field("last_heartbeat_at")
    delta.require_claim_field("stalled_notified")
    _stamp_claim(delta, claim, stalled_notified=True)
    _delta_event(
        delta,
        data,
        st.EVENT_PHASE_STALLED,
        phase=claim["phase_id"],
        claimed_by=token,
        age_seconds=round(age, 1),
    )
    return TickResult(
        "stalled",
        f"phase={claim['phase_id']} age={age:.0f}s",
        phase_id=claim["phase_id"],
        token=token,
        notify_body=notify.render_stalled(data["plan_slug"], claim["phase_id"], age),
    )


def _emit_stuck_blocker_repings(
    data: dict,
    config: ProjectConfig,
    side_notifies: list[tuple[str, str]],
    *,
    delta: TickDelta,
) -> None:
    """Re-ping any blocker open ≥30min since asked (or last reping)."""
    now = st._now_utc()
    project_root = str(config.project_root.resolve())
    for blocker_id, kind, body in state_blocker.stuck_blocker_repings(data, now):
        for b in data["blockers"]:
            if b["id"] != blocker_id:
                continue
            # The decision was "this blocker is still open" — an answer landing
            # mid-tick cancels the re-ping rather than racing it.
            delta.require_blocker(blocker_id)
            _stamp_blocker(delta, b, last_repinged_at=st.utcnow())
            try:
                age_min = int((now - st.parse_iso(b["asked_at"])).total_seconds() // 60)
            except (KeyError, ValueError):
                age_min = 0
            _delta_event(
                delta,
                data,
                st.EVENT_STUCK_BLOCKER_REPINGED,
                blocker_id=b["id"],
                phase=b["phase_id"],
                age_min=age_min,
            )
            side_notifies.append((kind, body))
            delta.inbox_events.append(
                {
                    "type": "stuck_blocker",
                    "plan_slug": data["plan_slug"],
                    "project_root": project_root,
                    "summary": (
                        f"Blocker {b['id']} on phase {b['phase_id']} open {age_min}min"
                    ),
                    "details": {
                        "blocker_id": b["id"],
                        "phase_id": b["phase_id"],
                        "question": b["question"],
                        "options": list(b["options"]),
                    },
                }
            )
            break


def _emit_stalled_claim_notify(
    data: dict,
    config: ProjectConfig,
    side_notifies: list[tuple[str, str]],
    *,
    delta: TickDelta,
) -> None:
    """One-shot signal on lease-expiry transition while plan is RUNNING.

    Sits before the existing ``release_if_expired`` branch so the operator
    learns about the stalled worker before the claim is auto-cleared. Stamps
    ``stalled_notified`` on the (about-to-be-released) claim for defense in
    depth in case the auto-release path ever changes.
    """
    claim = data.get("current_claim")
    if not claim:
        return
    if data["status"] != st.STATUS_RUNNING:
        return
    if claim.get("stalled_notified"):
        return
    try:
        expires = st.parse_iso(claim["lease_expires"])
    except (KeyError, ValueError):
        return
    now = st._now_utc()
    if expires >= now:
        return
    age_min = int((now - expires).total_seconds() // 60)
    # This decision read the LEASE, not the heartbeat: a worker still pinging
    # past its lease is exactly the case this exists to surface, so a heartbeat
    # landing mid-tick must not cancel it.
    delta.require_claim_field("lease_expires")
    delta.require_claim_field("stalled_notified")
    _stamp_claim(delta, claim, stalled_notified=True)
    _delta_event(
        delta,
        data,
        st.EVENT_STALLED_CLAIM_NOTIFIED,
        phase=claim["phase_id"],
        stalled_min=age_min,
    )
    side_notifies.append(
        (
            notify.KIND_STALLED_CLAIM,
            notify.render_stalled_claim(
                data["plan_slug"],
                claim["phase_id"],
                age_min,
            ),
        )
    )
    delta.inbox_events.append(
        {
            "type": "stalled_claim",
            "plan_slug": data["plan_slug"],
            "project_root": str(config.project_root.resolve()),
            "summary": (
                f"Claim on phase {claim['phase_id']} stalled {age_min}min past lease"
            ),
            "details": {
                "phase_id": claim["phase_id"],
                "stalled_min": age_min,
                "claimed_by": claim.get("claimed_by"),
            },
        }
    )


def _emit_stuck_tool(
    data: dict,
    config: ProjectConfig,
    *,
    delta: TickDelta,
    ps_output: str | None = None,
) -> None:
    """Detect long-lived low-CPU descendants of the worker pid and emit
    EVENT_TOOL_STUCK + inbox event once per (claim, descendant_pid).

    Detection only — no auto-kill. Best-effort observability: if the ps
    walk fails or the claim has no pid, we silently skip. `ps_output` is
    a test seam; production callers leave it None to shell out.
    """
    threshold = config.stuck_tool_threshold_seconds
    if threshold == 0:
        return
    claim = data.get("current_claim")
    if not claim:
        return
    pid = claim.get("pid")
    if not pid:
        return
    active_at = claim.get("active_tool_started_at")
    if not active_at:
        # No active Bash tool call → nothing to be stuck in. Workers
        # without the PreToolUse/PostToolUse hooks installed silently
        # produce zero events; lease expiry is the safety net.
        return
    try:
        active_age_s = (st._now_utc() - st.parse_iso(active_at)).total_seconds()
    except ValueError:
        # Corrupt marker — worker stamped non-ISO via clu activity. The only
        # way this lands is a bug in our writer or a hand-edited claim row;
        # either way the operator should know. Log once-per-tick to stderr
        # rather than appending an event every tick (which would flood the
        # log until the operator fixes the value).
        print(
            f"clu supervisor: ignoring corrupt active_tool_started_at "
            f"{active_at!r} on plan={data['plan_slug']} "
            f"phase={claim['phase_id']}",
            file=sys.stderr,
        )
        return

    cpu_max = config.stuck_tool_cpu_threshold_seconds
    descendants = walk_worker_tree(pid, ps_output=ps_output)
    plan_slug = data["plan_slug"]
    phase_id = claim["phase_id"]
    project_root = str(config.project_root.resolve())

    for d in descendants:
        # Descendants older than the active window pre-date the current
        # Bash call — session-level infra (MCP servers, polling shells).
        # They were never candidates to be stuck "inside" the active tool.
        if d.elapsed_seconds > active_age_s + STUCK_TOOL_DRIFT_SECONDS:
            continue
        if d.elapsed_seconds < threshold:
            continue
        if d.cpu_seconds > cpu_max:
            continue
        if st.tool_stuck_already_emitted(claim, d.pid):
            continue
        # The candidate window came from `active_tool_started_at`: a new Bash
        # call starting mid-tick slides that window, so this emit is void.
        delta.require_claim_field("active_tool_started_at")
        delta.require_claim_field("stuck_tool_emitted_at")
        st.mark_tool_stuck_emitted(claim, d.pid, st.utcnow())
        _record_claim(delta, claim, "stuck_tool_emitted_at")
        command_excerpt = d.command[:200]
        # `ps` reports ELAPSED at whole-second resolution, so the float
        # `_parse_duration` now returns is integral here. Emit it as an int so
        # the event payload keeps the shape its readers (`clu watch`, the
        # inbox dashboard) already render. CPU time is the opposite case —
        # its centiseconds are real, and the idle watchdog depends on them.
        elapsed_whole = int(d.elapsed_seconds)
        _delta_event(
            delta,
            data,
            st.EVENT_TOOL_STUCK,
            plan=plan_slug,
            phase=phase_id,
            worker_pid=pid,
            descendant_pid=d.pid,
            command=command_excerpt,
            elapsed_seconds=elapsed_whole,
            cpu_seconds=d.cpu_seconds,
        )
        delta.inbox_events.append(
            {
                "type": "tool_stuck",
                "plan_slug": plan_slug,
                "project_root": project_root,
                "summary": (
                    f"Worker on {plan_slug}/{phase_id} stuck in subprocess "
                    f"for {elapsed_whole}s ({command_excerpt[:60]})"
                ),
                "details": {
                    "phase_id": phase_id,
                    "worker_pid": pid,
                    "descendant_pid": d.pid,
                    "command": command_excerpt,
                    "elapsed_seconds": elapsed_whole,
                    "cpu_seconds": d.cpu_seconds,
                },
            }
        )


def _emit_worker_idle(
    data: dict,
    config: ProjectConfig,
    side_notifies: list[tuple[str, str]],
    *,
    delta: TickDelta,
    tree_ps_output: str | None = None,
) -> None:
    """Fire EVENT_WORKER_IDLE once per claim when the worker is PID-alive but
    doing nothing: no active Bash tool, and the worker's whole process tree
    accrued almost no processor time across an uninterrupted ~10-minute window.

    The metric is CUMULATIVE processor time, sampled once per tick and read as
    a DELTA across the window — not instantaneous `%cpu`, which `man ps`
    defines as "a decaying average over up to a minute of previous (real)
    time" and which a healthy worker waiting on the model sits under anyway.
    It is summed across the tree rather than read from `claim.pid` alone: the
    pid clu tracks is the PTY shim, whose own CPU is near zero while its
    `claude` child does the work.

    Detection only — no auto-kill. `tree_ps_output` is a test seam;
    production callers leave it None to shell out.
    """
    claim = data.get("current_claim")
    if not claim:
        return
    pid = claim.get("pid")
    if not pid:
        return
    if claim.get("active_tool_started_at"):
        return

    # ONE `ps` snapshot serves both halves of the sample: `walk_worker_tree`
    # picks the descendants out of it, and the root's own line is read
    # directly — the walker excludes the root by contract, and the shim IS
    # the root, so dropping it would measure everything except the process
    # most likely to be wedged.
    now = st._now_utc()
    snapshot = tree_ps_output if tree_ps_output is not None else capture_ps_snapshot()
    descendants = walk_worker_tree(pid, ps_output=snapshot)
    root_cpu: float | None = None
    for proc in _parse_ps_output(snapshot):
        if proc.pid == pid:
            root_cpu = proc.cpu_seconds
            break

    if root_cpu is not None:
        # Descendants that died between the parse and now simply are not in
        # this snapshot — the sum is of whatever `ps` saw in one instant, and
        # a shrinking sum is caught downstream as a negative delta.
        tree_cpu_seconds = root_cpu + sum(d.cpu_seconds for d in descendants)
        st.append_cpu_sample(
            claim,
            tree_cpu_seconds,
            now,
            retain_seconds=(
                config.worker_idle_window_minutes * 60.0
                + config.worker_idle_max_sample_gap_seconds
            ),
        )
        # The sample rides the delta with no precondition of its own: the tick
        # is the only writer of `cpu_samples`, and the compare-and-set on
        # `claimed_by` already refuses to stamp a claim that moved. The EMIT
        # below is what gets guarded.
        _record_claim(delta, claim, "cpu_samples")
    # A `ps` we could not read (or one the worker pid is missing from) is
    # "cannot judge", not "measured zero" — no sample is appended, and the
    # resulting hole fails the next window's contiguity check on its own.

    if not st.worker_idle_window_satisfied(
        claim,
        now,
        min_samples=config.worker_idle_min_samples,
        window_min=config.worker_idle_window_minutes,
        max_sample_gap=config.worker_idle_max_sample_gap_seconds,
        cpu_delta_threshold=config.worker_idle_cpu_delta_threshold_seconds,
    ):
        return
    if st.worker_idle_already_emitted(claim):
        return

    plan_slug = data["plan_slug"]
    phase_id = claim["phase_id"]
    project_root = str(config.project_root.resolve())
    samples = claim.get("cpu_samples") or []
    low_cpu_minutes = 0.0
    if samples:
        try:
            oldest_ts = st.parse_iso(samples[0]["ts"])
            low_cpu_minutes = (now - oldest_ts).total_seconds() / 60.0
        except (KeyError, ValueError):
            pass

    # "No Bash tool is running" is the premise of the whole judgment — a tool
    # call starting mid-tick means the worker is not idle after all.
    delta.require_claim_field("active_tool_started_at")
    delta.require_claim_field("worker_idle_notified")
    st.mark_worker_idle_emitted(claim, now)
    _record_claim(delta, claim, "worker_idle_notified", "worker_idle_notified_at")
    _delta_event(
        delta,
        data,
        st.EVENT_WORKER_IDLE,
        plan=plan_slug,
        phase=phase_id,
        pid=pid,
        low_cpu_minutes=round(low_cpu_minutes, 1),
    )
    side_notifies.append(
        (
            notify.KIND_WORKER_IDLE,
            notify.render_worker_idle(plan_slug, phase_id, pid, low_cpu_minutes),
        )
    )
    delta.inbox_events.append(
        {
            "type": "worker_idle",
            "plan_slug": plan_slug,
            "project_root": project_root,
            "summary": (
                f"Worker on {plan_slug}/{phase_id} idle for ~{low_cpu_minutes:.0f}min "
                f"(pid {pid}, no tool, no CPU movement across the window)"
            ),
            "details": {
                "phase_id": phase_id,
                "pid": pid,
                "low_cpu_minutes": round(low_cpu_minutes, 1),
            },
        }
    )


def _lease_expired(claim: dict) -> bool:
    """`state.release_if_expired`'s predicate, without the mutation.

    The release itself is a delta field now, so the two halves that function
    fused — "is it past?" and "clear it and log it" — are separate here.
    """
    try:
        expires = st.parse_iso(claim["lease_expires"])
    except (KeyError, ValueError):
        return False
    return expires <= st._now_utc()


def _reap_and_record(
    orch_dir: Path,
    slug: str,
    *,
    pgid: int,
    cmdline_match: str,
    phase_id: str,
    pid: int,
) -> None:
    """Reap the worker's process group, then stamp the event that says so.

    Runs only AFTER the tick's write committed. The reap polls for up to five
    seconds and the group is the worker plus its backgrounded heartbeat loop
    (#75), which is precisely the kind of work that must never sit inside a
    transaction holding the project's write lock.

    The event is best-effort by construction: the durable transition (the
    release) is already committed, so losing this audit line to a busy store
    costs an observability record, where raising here would cost the tick.
    """
    reap = st.reap_orphan_pgroup(pgid, cmdline_match=cmdline_match)
    try:
        plan_store.op_append_events(
            orch_dir,
            slug,
            [
                {
                    "ts": st.utcnow(),
                    "type": st.EVENT_PHASE_ORPHAN_REAPED,
                    "phase": phase_id,
                    "pid": pid,
                    "signaled": reap.signaled,
                    "cmdline_mismatch": reap.cmdline_mismatch,
                }
            ],
        )
    except (*db.DEGRADABLE_ERRORS, st.SchemaVersionMismatch) as exc:
        print(
            f"clu supervisor: reaped {slug}/{phase_id} but could not record it: {exc}",
            file=sys.stderr,
        )


def tick(state_path: Path, config: ProjectConfig) -> TickResult:
    if not plan_store.exists_for_path(state_path):
        return TickResult("idle", f"no state at {state_path}")

    orch_dir, slug = plan_store.key_for_state_path(state_path)
    # ONE read transaction: the claim, the blockers, the status and the event
    # high-water mark all describe the same instant, and the transaction is
    # closed before the first subprocess runs.
    data, observed = plan_store.snapshot_with_preconditions(orch_dir, slug)
    delta = TickDelta(observed=observed)
    side_notifies: list[tuple[str, str]] = []
    # Work that must not run until the apply has committed: coolant emissions
    # and group reaps, in the order the branch that queued them wants.
    post_commit: list[Callable[[], None]] = []
    # `dispatch_for_tick` uses this as a read; taking it off the snapshot costs
    # nothing and avoids a second load.
    worktree = st.get_worktree(data)

    def _commit(result: TickResult) -> TickResult:
        """Apply this tick's delta, then everything that follows a commit.

        A precondition that no longer holds discards the delta whole — no
        events, no markers, no notifications — and the 30s cron re-derives.
        """
        try:
            token = _apply(delta, orch_dir, slug)
        except plan_store.TickConflict:
            return TickResult("idle", "concurrent_write")
        if result.action == "dispatch":
            result.token = token
            result.detail = f"phase={result.phase_id} token={token}"
        for step in post_commit:
            step()
        for event in delta.inbox_events:
            try:
                inbox.write_event(**event)
            except db.DEGRADABLE_ERRORS:
                pass
        # Gap-fill emissions piggyback on whichever primary action this tick
        # produces — they're not their own first-class action.
        result.side_notifies = side_notifies
        result.worktree = worktree
        return result

    # Pre-detect the gap-fill side effects so they fire even when the primary
    # action is "idle" or "lease_expired". All four record into `delta` and
    # into the working snapshot; none preempts the chain below.
    _emit_stalled_claim_notify(data, config, side_notifies, delta=delta)
    _emit_stuck_blocker_repings(data, config, side_notifies, delta=delta)
    _emit_stuck_tool(data, config, delta=delta)
    _emit_worker_idle(data, config, side_notifies, delta=delta)

    if claim := data.get("current_claim"):
        pid = claim.get("pid")
        phase_id = claim["phase_id"]
        claimed_by = claim.get("claimed_by")
        if _lease_expired(claim):
            # The decision is about the LEASE. A heartbeat or an activity stamp
            # landing mid-tick touches neither `lease_expires` nor the event
            # log, so neither aborts this release — which is the whole point of
            # preconditions over a version counter.
            delta.require_claim()
            delta.require_claim_field("lease_expires")
            _delta_event(
                delta,
                data,
                st.EVENT_LEASE_EXPIRED,
                phase=phase_id,
                claimed_by=claimed_by,
            )
            delta.release_claim = True
            data["current_claim"] = None
            # Quota classification (#94) reads the snapshotted claim's
            # log_path — the on-disk log outlives the released claim. A quota
            # death's phase_started is forgiven via EVENT_QUOTA_DEATH, so the
            # straggler that lease-expired on a quota kill burns no attempt.
            quota_match = quota.classify_log_tail(claim.get("log_path"))
            if quota_match is not None:
                # The pause ROW is its own transaction, taken here while the
                # tick holds nothing; the two plan EVENTS ride the delta.
                harvest: dict = {"events": []}
                paused_until = quota.record_quota_death(
                    harvest,
                    quota_match,
                    phase_id=phase_id,
                    token=claimed_by,
                    orchestrator_dir=orch_dir,
                )
                _carry_events(delta, data, harvest["events"])
                side_notifies.append(
                    notify.quota_pause_notification(
                        data["plan_slug"],
                        quota_match.line,
                        paused_until,
                    )
                )
            if claimed_by and phase_id and config.coolant.enabled:
                post_commit.append(
                    lambda: coolant.emit_stop(
                        session_id=claimed_by,
                        agent_id=coolant.format_agent_id(
                            data["plan_slug"],
                            phase_id,
                        ),
                        agent_type=coolant.AGENT_TYPE,
                        script_override=config.coolant.script_dir,
                    )
                )
            if pid:
                # Reap the whole process GROUP, not just the worker PID:
                # the backgrounded heartbeat loop is in the worker's pgroup
                # and would otherwise reparent to launchd and survive — the
                # #75 orphan. Robust to #72-skill-drift, unlike a single-PID
                # reap that relies on the worker-side `kill -0` self-clean.
                pgid = claim.get("pgid") or pid
                post_commit.append(
                    lambda: _reap_and_record(
                        orch_dir,
                        slug,
                        pgid=pgid,
                        cmdline_match=data["plan_slug"],
                        phase_id=phase_id,
                        pid=pid,
                    )
                )
            return _commit(TickResult("lease_expired", _death_detail(phase_id, quota_match)))

        # issue #72: heartbeat-keeper subprocess survives worker death
        # (EXIT trap doesn't fire on SIGKILL/OOM/crash) and keeps the
        # lease looking fresh until full TTL. The dead-PID probe is the
        # tick-side half of the fix; the shell-side `kill -0 $WORKER_PID`
        # loop condition in /clu-phase SKILL.md ships in the same change
        # as the worker-side half.
        # Marker = the plan slug, present in EVERY dispatch template's worker
        # cmdline. The old `/clu-phase <plan> <phase>` marker is absent from
        # `/plan ...`-style templates (e.g. the incident host's), so it made
        # claim_worker_alive falsely report a LIVE worker dead — releasing +
        # "reaping" a healthy worker — and made the reap itself a no-op.
        cmdline_match = data["plan_slug"]
        if pid and not st.claim_worker_alive(
            claim,
            cmdline_match=cmdline_match,
        ):
            # The heartbeat daemon may already have reported this death
            # (#104) — it detects within ~120s and pings the operator +
            # inbox + watch through notify-worker-dead. If so, suppress the
            # duplicate operator notification here, but STILL emit the
            # supervisor's own event, release, and reap — those are the
            # durable transition, and the daemon deliberately does not do
            # them (that is death-recovery). Read the marker before release
            # wipes the claim.
            already_reported = st.worker_death_already_reported(claim)
            # Quota classification (#94) must read the log BEFORE the release
            # is applied — the released claim is what carries log_path. A
            # quota match suppresses the misleading worker-dead notify body
            # (the operator-facing KIND_QUOTA_* ping rides side_notifies
            # instead) and forgives the attempt via EVENT_QUOTA_DEATH.
            quota_match = quota.classify_log_tail(claim.get("log_path"))
            if quota_match is not None:
                harvest = {"events": []}
                paused_until = quota.record_quota_death(
                    harvest,
                    quota_match,
                    phase_id=phase_id,
                    token=claimed_by,
                    orchestrator_dir=orch_dir,
                )
                _carry_events(delta, data, harvest["events"])
                side_notifies.append(
                    notify.quota_pause_notification(
                        data["plan_slug"],
                        quota_match.line,
                        paused_until,
                    )
                )
            # Order matters: durable state first (event + release), best-effort
            # coolant + reap after the commit. If the reap raises (e.g. ps
            # timeout), the claim is already released and the event is durable
            # — next tick won't re-fire.
            delta.require_claim()
            _delta_event(
                delta,
                data,
                st.EVENT_PHASE_WORKER_DEAD,
                phase=phase_id,
                pid=pid,
            )
            delta.release_claim = True
            data["current_claim"] = None
            if config.coolant.enabled and claimed_by and phase_id:
                post_commit.append(
                    lambda: coolant.emit_stop(
                        session_id=claimed_by,
                        agent_id=coolant.format_agent_id(
                            data["plan_slug"],
                            phase_id,
                        ),
                        agent_type=coolant.AGENT_TYPE,
                        script_override=config.coolant.script_dir,
                    )
                )
            post_commit.append(
                # Group reap (worker + heartbeat), see lease-expiry note above.
                lambda: _reap_quietly(claim.get("pgid") or pid, cmdline_match)
            )
            return _commit(
                TickResult(
                    "worker_dead",
                    _death_detail(phase_id, quota_match),
                    phase_id=phase_id,
                    token=claimed_by,
                    notify_body=None
                    if (quota_match is not None or already_reported)
                    else notify.render_worker_dead(
                        data["plan_slug"],
                        phase_id,
                        pid,
                    ),
                )
            )

    # Surface stalled claims once. Don't release the claim — the lease
    # owns retry; this event is just the signal the notification adapter
    # (Day-2 Cliff 2) hangs off of.
    if stalled := _detect_stalled(data, delta=delta):
        return _commit(stalled)

    # Defer SLA escalation during quiet hours — an overnight rollover would
    # otherwise ping the user at 3am. The blocker stays aged for the next
    # loud tick.
    if not notify.in_quiet_window(config.notify, _local_now()):
        sla_hours = data["config"].get(
            "blocked_question_sla_hours",
            st.DEFAULT_SLA_HOURS,
        )
        now = st._now_utc()
        for b in st.open_blockers(data):
            try:
                asked = st.parse_iso(b["asked_at"])
            except (KeyError, ValueError):
                continue
            age_hours = (now - asked).total_seconds() / 3600.0
            if age_hours >= sla_hours and data["status"] != st.STATUS_PAUSED:
                delta.require_blocker(b["id"])
                delta.require_status()
                delta.status = st.STATUS_PAUSED
                data["status"] = st.STATUS_PAUSED
                _delta_event(
                    delta,
                    data,
                    st.EVENT_BLOCKER_SLA_EXCEEDED,
                    blocker_id=b["id"],
                    age_hours=round(age_hours, 1),
                )
                return _commit(
                    TickResult(
                        "escalate",
                        f"blocker={b['id']} age_hours={age_hours:.1f}",
                    )
                )

    # Newly-answered blocker → mark consumed (worker sees on next dispatch)
    events, target_status = state_blocker.process_answered_blockers(data)
    if events:
        for ev_type, blocker_id in events:
            for b in data["blockers"]:
                if b["id"] == blocker_id:
                    delta.require_blocker(blocker_id)
                    _stamp_blocker(delta, b, consumed=True)
                    break
            _delta_event(delta, data, ev_type, blocker_id=blocker_id)
        if target_status:
            delta.require_status()
            delta.status = target_status
            data["status"] = target_status
        return _commit(TickResult("blocker_resumed", f"blocker={events[0][1]}"))

    if data["status"] in st.TERMINAL_STATUSES:
        return _commit(TickResult("idle", f"plan status={data['status']}"))

    if claim := data.get("current_claim"):
        return _commit(
            TickResult(
                "idle",
                f"phase={claim['phase_id']} in_flight lease={claim['lease_expires']}",
            )
        )

    # Any open blocker on this plan pins the lane: plan-file order
    # encodes implicit dependencies between phases, so dispatching the
    # successor while the predecessor is blocked routinely violates a
    # "must merge before" constraint. Operator answers + priority-4
    # consume re-opens the lane. (#28)
    if blockers := st.open_blockers(data):
        return _commit(
            TickResult(
                "idle",
                f"open_blocker={blockers[0]['id']} pins lane",
            )
        )

    plan_path = config.project_root / config.plan_dir / f"{data['plan_slug']}.md"
    phases = parse_sessions_index(plan_path)
    if not phases:
        # Reads markdown off disk, so it can fail without any write of its own
        # — but a gap-fill emitter may still have queued one, and that commits.
        return _commit(TickResult("error", f"no Sessions index in {plan_path}"))

    completed = st.completed_phase_ids(data)
    max_attempts = data["config"].get("max_attempts_per_phase", st.DEFAULT_MAX_ATTEMPTS)
    for phase in phases:
        if phase.id in completed or st.phase_has_open_blocker(data, phase.id):
            continue
        prior_attempts = st.attempts_for_phase(data, phase.id)
        if prior_attempts >= max_attempts:
            # Only reachable from STATUS_RUNNING — the TERMINAL_STATUSES
            # short-circuit above sends every subsequent halt tick to
            # "idle", so notify fires exactly once per transition.
            delta.require_claim()
            delta.require_event_log()
            delta.require_status()
            delta.status = st.STATUS_HALTED
            _delta_event(
                delta,
                data,
                st.EVENT_PHASE_MAX_ATTEMPTS,
                phase=phase.id,
                attempts=prior_attempts,
            )
            return _commit(
                TickResult(
                    "halt",
                    f"phase={phase.id} attempts={prior_attempts}",
                    notify_body=notify.render_halted(
                        data["plan_slug"],
                        phase.id,
                        prior_attempts,
                    ),
                )
            )
        # Project quota pause gate (#94): a classified quota death
        # pauses the whole project until the parsed reset. Only a plan
        # with a dispatchable phase consults the gate, so the canary
        # slot is stamped for a plan that will actually dispatch.
        # Watchdog priorities 1–5 above keep running against in-flight
        # claims while paused. The gate is consulted here, while this tick
        # holds NO transaction, so its canary write is its own transaction
        # again rather than joining the tick's.
        gate = quota.gate_decision(orch_dir, data["plan_slug"], st._now_utc())
        if not gate.dispatch:
            return _commit(TickResult("idle", gate.detail))
        if gate.resumed:
            _delta_event(delta, data, st.EVENT_QUOTA_RESUMED)
            side_notifies.append(
                (
                    notify.KIND_QUOTA_RESUMED,
                    notify.render_quota_resumed(data["plan_slug"]),
                )
            )
        # The claim is minted inside the apply — one write for the claim row
        # and the `phase_started` event that names its token.
        delta.require_claim()
        delta.require_event_log()
        delta.require_status()
        delta.claim_phase = phase.id
        delta.lease_minutes = st.lease_ttl_for_phase(data, phase.id)
        delta.claim_attempts = _started_count(data, phase.id) + 1
        return _commit(TickResult("dispatch", phase_id=phase.id))

    # All phases attempted — but wait for pending spawned tasks.
    if all(p.id in completed for p in phases):
        pending_tasks = [t for t in data["spawned_tasks"] if t["status"] == "pending"]
        if not pending_tasks:
            delta.require_claim()
            delta.require_event_log()
            delta.require_status()
            delta.status = st.STATUS_DONE
            _delta_event(delta, data, st.EVENT_PLAN_COMPLETED)
            commit_count = sum(
                len(evt.get("commits") or [])
                for evt in data["events"]
                if evt.get("type") == st.EVENT_PHASE_COMPLETED
            )
            return _commit(
                TickResult(
                    "plan_done",
                    data["plan_slug"],
                    notify_body=notify.render_completed(
                        data["plan_slug"],
                        commit_count,
                    ),
                )
            )
        return _commit(
            TickResult(
                "idle",
                f"phases done; {len(pending_tasks)} spawned task(s) pending",
            )
        )

    return _commit(TickResult("idle", "all phases blocked or none dispatchable"))


def _apply(delta: TickDelta, orch_dir: Path, slug: str) -> str | None:
    """Write the tick's decision, or skip the transaction when there is none.

    A tick that decided nothing takes no write lock at all — the common case
    for a plan with a healthy worker, where the whole visit is a read.
    """
    if delta.is_empty():
        return None
    return plan_store.apply_tick_delta(orch_dir, slug, delta.pre, delta)


def _reap_quietly(pgid: int, cmdline_match: str) -> None:
    """Best-effort group reap with no event of its own (the dead-PID path)."""
    try:
        st.reap_orphan_pgroup(pgid, cmdline_match=cmdline_match)
    except Exception:
        pass


def _started_count(data: dict, phase_id: str) -> int:
    """Raw `phase_started` count for a phase — what `claim_phase` stamps.

    Deliberately NOT `attempts_for_phase`, which applies retry floors and quota
    forgiveness: the two counters have always differed (`clu top`'s ATT column
    shows this one, dispatch budgets on the other) and this migration preserves
    both as they are.
    """
    return sum(
        1
        for evt in data["events"]
        if evt.get("type") == st.EVENT_PHASE_STARTED and evt.get("phase") == phase_id
    )


@dataclass
class ZombieSweepResult:
    """One state file the registry-independent sweep terminalized (or, in
    dry-run, would terminalize). `reaped` is True when a worker process group
    was actually signaled."""

    plan_slug: str
    reaped: bool
    terminalized: bool


def sweep_zombie_states(
    cfg: ProjectConfig,
    registered_slugs: set[str],
    *,
    dry_run: bool = False,
) -> list[ZombieSweepResult]:
    """Registry-independent reaper for `status=running` zombies.

    Walks the plans in a project's database (never the files in its directory)
    for UNREGISTERED ones stuck at `running` whose worker is gone
    (`state.is_zombie_state`), then terminalizes + reaps them. This is the
    backstop for the "unregistered + running" window that `tick-all`'s registry
    walk can never reach (#75): the documented crash-recovery self-heal
    (architecture.md "Crash recovery") only fires while the queue head is still
    present, so a fully-unregistered zombie like `fm-docs-sweep` would
    otherwise sit at `running` forever.

    Registered slugs are skipped — tick-all / the supervisor own them, and a
    registered plan may legitimately sit claimless between phases. Unreadable
    plans are skipped (operator's `clu doctor` surfaces those).
    Idempotent: re-checks the zombie predicate inside the write transaction so
    a concurrent tick that just revived a plan isn't terminalized.

    Scope: `tick-all` calls this once per project it visits, and it visits only
    projects that appear in the registry. A project whose *every* plan is
    unregistered is never visited, so its zombies are reachable only via
    `clu doctor --project <that project>`. In practice a zombie shares a project
    with live plans (the `fm-docs-sweep` incident did), so the auto-sweep covers
    it; the all-unregistered-project case is the documented residual gap.
    """
    orch_dir = cfg.project_root / cfg.plan_dir / ORCHESTRATOR_DIR
    results: list[ZombieSweepResult] = []
    # The plans in the DATABASE, not the files in the directory. Legacy
    # `*.state.json` left behind by the storage migration are inert — globbing
    # would feed every one of them to the zombie predicate, and each is
    # unregistered and stuck at whatever status it froze with.
    for slug in plan_store.plan_slugs(orch_dir):
        if slug in registered_slugs:
            continue
        try:
            # One read, and it carries the facts the decision rests on: the
            # apply below re-asserts them rather than re-reading, so nothing
            # can move between "this looks like a zombie" and "this was
            # observed to look like a zombie".
            data, observed = plan_store.snapshot_with_preconditions(orch_dir, slug)
        except (*db.DEGRADABLE_ERRORS, ValueError, st.SchemaVersionMismatch):
            # `db.DbBusy` is the one this list did not used to need: reading a
            # state FILE could not be "busy", so a contended plan is a failure
            # mode the store introduced. Skipping one plan is right — the sweep
            # is a backstop that runs every tick, and the next one re-reads it.
            continue
        if not st.is_zombie_state(data):
            continue
        if dry_run:
            results.append(ZombieSweepResult(slug, reaped=False, terminalized=False))
            continue
        # The reap shells out to `ps` and `kill` and polls for seconds, so it
        # runs here — against the snapshot, with nothing held.
        reap = st.reap_claim(data)
        claim = data.get("current_claim") or {}
        # The re-check that used to happen inside the write window is now a
        # PRECONDITION: `is_zombie_state` reads the status and the claim, and
        # both are re-asserted inside the apply's transaction. A concurrent
        # tick that revived this plan between the scan and the write aborts the
        # apply with `TickConflict` and nothing is written — including the
        # version bump, which is the churn the old code avoided by saving only
        # on the act path.
        pre = plan_store.TickPreconditions(
            expect_claim=observed.expect_claim,
            expect_status=observed.expect_status,
        )
        delta = TickDelta(
            release_claim=bool(claim),
            status=st.STATUS_HALTED,
            events=[
                {
                    "ts": st.utcnow(),
                    "type": st.EVENT_PLAN_ABANDONED,
                    "reason": "zombie_sweep",
                }
            ],
        )
        try:
            plan_store.apply_tick_delta(orch_dir, slug, pre, delta)
        except plan_store.TickConflict:
            continue
        except (*db.DEGRADABLE_ERRORS, ValueError, st.SchemaVersionMismatch):
            # Same reason as the read above: the write lock is the project's
            # now, so a tick working any plan in this project can hold it past
            # the budget. Taking down the sweep — and, from `clu doctor`, the
            # health report around it — over a plan the next tick will re-scan
            # is the wrong trade. `DbBusy` is the contended case; the rest of
            # the family covers a store that broke between the read and the
            # write.
            continue
        # After the commit: coolant shells out to a script, and the write lock
        # it used to be emitted under is the whole project's now.
        if claim and cfg.coolant.enabled and claim.get("claimed_by") and claim.get("phase_id"):
            coolant.emit_stop(
                session_id=claim["claimed_by"],
                agent_id=coolant.format_agent_id(slug, claim["phase_id"]),
                agent_type=coolant.AGENT_TYPE,
                script_override=cfg.coolant.script_dir,
            )
        results.append(
            ZombieSweepResult(slug, reaped=bool(reap and reap.signaled), terminalized=True)
        )
    return results
