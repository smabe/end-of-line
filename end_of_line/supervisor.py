"""Single-tick supervisor logic.

Action priority (first match wins):
  1. Stale lease release
  2. Dead-PID release (issue #72: heartbeat-zombie keeps the lease fresh
     after worker death; catch within one tick instead of full lease TTL)
  3. Stalled heartbeat → emit phase_stalled once
  4. Stale-question escalation
  5. Answered-question resume (mark consumed)
  6. Plan halted/paused → idle
  7. Active claim → idle
  8. Dispatch next pending phase
  9. All phases complete → mark plan done
  10. Idle
"""

from __future__ import annotations

import datetime as _dt
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from . import coolant, inbox, notify, state_blocker
from . import state as st
from .config import ORCHESTRATOR_DIR, ProjectConfig
from .plan_parser import parse_sessions_index


def _local_now() -> _dt.datetime:
    """Wall-clock local time. Indirection exists so tests can pin the hour."""
    return _dt.datetime.now()


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
    elapsed_seconds: int
    cpu_seconds: int
    command: str


def _parse_duration(raw: str) -> int:
    """Parse a `ps` duration to integer seconds, truncating fractions.

    Handles both etime ([[dd-]hh:]mm:ss) and CPU time ([hh:]mm:ss[.cc]).
    Returns 0 for empty input or the literal "-" that ps emits for
    unmeasurable fields.
    """
    s = raw.strip()
    if not s or s == "-":
        return 0
    days = 0
    if "-" in s:
        days_str, s = s.split("-", 1)
        try:
            days = int(days_str)
        except ValueError:
            return 0
    if "." in s:
        s = s.split(".", 1)[0]
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    while len(nums) < 3:
        nums.insert(0, 0)
    h, m, sec = nums[-3], nums[-2], nums[-1]
    return days * 86400 + h * 3600 + m * 60 + sec


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


def _detect_stalled(data: dict) -> TickResult | None:
    """Emit phase_stalled on the first tick we notice a stalled claim, then idle.

    Mutates the claim with `stalled_notified=True` so subsequent ticks fall
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
    claim["stalled_notified"] = True
    st.append_event(
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
) -> None:
    """Re-ping any blocker open ≥30min since asked (or last reping)."""
    now = st._now_utc()
    project_root = str(config.project_root.resolve())
    for blocker_id, kind, body in state_blocker.stuck_blocker_repings(data, now):
        for b in data["blockers"]:
            if b["id"] != blocker_id:
                continue
            b["last_repinged_at"] = st.utcnow()
            try:
                age_min = int((now - st.parse_iso(b["asked_at"])).total_seconds() // 60)
            except (KeyError, ValueError):
                age_min = 0
            st.append_event(
                data,
                st.EVENT_STUCK_BLOCKER_REPINGED,
                blocker_id=b["id"],
                phase=b["phase_id"],
                age_min=age_min,
            )
            side_notifies.append((kind, body))
            try:
                inbox.write_event(
                    type="stuck_blocker",
                    plan_slug=data["plan_slug"],
                    project_root=project_root,
                    summary=(f"Blocker {b['id']} on phase {b['phase_id']} open {age_min}min"),
                    details={
                        "blocker_id": b["id"],
                        "phase_id": b["phase_id"],
                        "question": b["question"],
                        "options": list(b["options"]),
                    },
                )
            except OSError:
                pass
            break


def _emit_stalled_claim_notify(
    data: dict,
    config: ProjectConfig,
    side_notifies: list[tuple[str, str]],
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
    claim["stalled_notified"] = True
    st.append_event(
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
    try:
        inbox.write_event(
            type="stalled_claim",
            plan_slug=data["plan_slug"],
            project_root=str(config.project_root.resolve()),
            summary=(f"Claim on phase {claim['phase_id']} stalled {age_min}min past lease"),
            details={
                "phase_id": claim["phase_id"],
                "stalled_min": age_min,
                "claimed_by": claim.get("claimed_by"),
            },
        )
    except OSError:
        pass


def _emit_stuck_tool(
    data: dict,
    config: ProjectConfig,
    *,
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
        # way this lands is a bug in our writer or a hand-edited state.json;
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
        st.mark_tool_stuck_emitted(claim, d.pid, st.utcnow())
        command_excerpt = d.command[:200]
        st.append_event(
            data,
            st.EVENT_TOOL_STUCK,
            plan=plan_slug,
            phase=phase_id,
            worker_pid=pid,
            descendant_pid=d.pid,
            command=command_excerpt,
            elapsed_seconds=d.elapsed_seconds,
            cpu_seconds=d.cpu_seconds,
        )
        try:
            inbox.write_event(
                type="tool_stuck",
                plan_slug=plan_slug,
                project_root=project_root,
                summary=(
                    f"Worker on {plan_slug}/{phase_id} stuck in subprocess "
                    f"for {d.elapsed_seconds}s ({command_excerpt[:60]})"
                ),
                details={
                    "phase_id": phase_id,
                    "worker_pid": pid,
                    "descendant_pid": d.pid,
                    "command": command_excerpt,
                    "elapsed_seconds": d.elapsed_seconds,
                    "cpu_seconds": d.cpu_seconds,
                },
            )
        except OSError:
            pass


def _emit_worker_idle(
    data: dict,
    config: ProjectConfig,
    side_notifies: list[tuple[str, str]],
    *,
    ps_output: str | None = None,
    tree_ps_output: str | None = None,
    lsof_output: str | None = None,
) -> None:
    """Fire EVENT_WORKER_IDLE once per claim when the worker is PID-alive but
    doing nothing: no active Bash tool, CPU ≤1% over ≥10 min, no open
    Anthropic API socket.

    CPU is sampled across the whole worker process tree, not claim.pid alone —
    a wedged worker can idle at ~0% while a child (test run, build) burns CPU,
    and sampling the root only would miss it and false-fire. Detection only —
    no auto-kill. `ps_output` (the `ps -p <pids> -o %cpu=` output),
    `tree_ps_output` (the `walk_worker_tree` snapshot), and `lsof_output` are
    test seams; production callers leave all None to shell out.
    """
    claim = data.get("current_claim")
    if not claim:
        return
    pid = claim.get("pid")
    if not pid:
        return
    if claim.get("active_tool_started_at"):
        return

    # Sample this tick's CPU across the worker's whole process tree. The tree
    # walk supplies the pid set (root + descendants); ONE `ps -p <pids> -o
    # %cpu=` reads instantaneous %cpu for the set, which we sum. The root pid
    # is always in the set, so the pid list is never empty. (Descendant pids
    # that die between the walk and the ps just drop out of ps's output — we
    # sum whatever survives. Descendant.cpu_seconds from the tree is cumulative
    # CPU time, a different quantity — deliberately not used here.)
    now = st._now_utc()
    descendants = walk_worker_tree(pid, ps_output=tree_ps_output)
    tree_pids = [pid] + [d.pid for d in descendants]
    if ps_output is not None:
        raw_cpu = ps_output
    else:
        try:
            result = subprocess.run(
                ["ps", "-p", ",".join(str(p) for p in tree_pids), "-o", "%cpu="],
                capture_output=True,
                text=True,
                timeout=2,
            )
            raw_cpu = result.stdout
        except (subprocess.TimeoutExpired, OSError):
            raw_cpu = ""
    cpu_pct: float | None = None
    for line in raw_cpu.splitlines():
        token = line.strip()
        if not token:
            continue
        try:
            value = float(token)
        except ValueError:
            continue
        cpu_pct = (cpu_pct or 0.0) + value

    if cpu_pct is not None:
        st.append_cpu_sample(claim, cpu_pct, now)

    if not st.worker_idle_window_satisfied(claim, now):
        return
    if st.worker_idle_already_emitted(claim):
        return

    # API-socket heuristic suppression.
    if lsof_output is not None:
        lsof_text = lsof_output
    else:
        try:
            lsof_result = subprocess.run(
                ["lsof", "-p", str(pid), "-i"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            lsof_text = lsof_result.stdout
        except (subprocess.TimeoutExpired, OSError):
            # Can't check; emit anyway (false negative > false positive).
            lsof_text = ""
    if "anthropic" in lsof_text.lower():
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

    st.mark_worker_idle_emitted(claim, now)
    st.append_event(
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
    try:
        inbox.write_event(
            type="worker_idle",
            plan_slug=plan_slug,
            project_root=project_root,
            summary=(
                f"Worker on {plan_slug}/{phase_id} idle for ~{low_cpu_minutes:.0f}min "
                f"(pid {pid}, no tool, no API socket, CPU ≤1%)"
            ),
            details={
                "phase_id": phase_id,
                "pid": pid,
                "low_cpu_minutes": round(low_cpu_minutes, 1),
            },
        )
    except OSError:
        pass


def tick(state_path: Path, config: ProjectConfig) -> TickResult:
    if not state_path.exists():
        return TickResult("idle", f"no state at {state_path}")

    side_notifies: list[tuple[str, str]] = []
    worktree: dict | None = None

    def _attach(result: TickResult) -> TickResult:
        # Gap-fill emissions piggyback on whichever primary action this tick
        # produces — they're not their own first-class action.
        result.side_notifies = side_notifies
        result.worktree = worktree
        return result

    with st.mutate(state_path) as data:
        # Snapshot the worktree record while we hold the state lock — dispatch
        # only ever uses it as a read, so a second `st.load` outside the lock
        # would be redundant work + a race window.
        worktree = st.get_worktree(data)
        # Pre-detect the gap-fill side effects so they fire even when the
        # primary action is "idle" or "lease_expired". Both helpers mutate
        # data + side_notifies in place; neither preempts the chain below.
        _emit_stalled_claim_notify(data, config, side_notifies)
        _emit_stuck_blocker_repings(data, config, side_notifies)
        _emit_stuck_tool(data, config)
        _emit_worker_idle(data, config, side_notifies)

        if claim := data.get("current_claim"):
            pid = claim.get("pid")
            phase_id = claim["phase_id"]
            claimed_by = claim.get("claimed_by")
            if st.release_if_expired(data):
                if claimed_by and phase_id and config.coolant.enabled:
                    coolant.emit_stop(
                        session_id=claimed_by,
                        agent_id=coolant.format_agent_id(
                            data["plan_slug"],
                            phase_id,
                        ),
                        agent_type=coolant.AGENT_TYPE,
                        script_override=config.coolant.script_dir,
                    )
                if pid:
                    # Reap the whole process GROUP, not just the worker PID:
                    # the backgrounded heartbeat loop is in the worker's pgroup
                    # and would otherwise reparent to launchd and survive — the
                    # #75 orphan. Robust to #72-skill-drift, unlike a single-PID
                    # reap that relies on the worker-side `kill -0` self-clean.
                    pgid = claim.get("pgid") or pid
                    reap = st.reap_orphan_pgroup(pgid, cmdline_match=data["plan_slug"])
                    st.append_event(
                        data,
                        st.EVENT_PHASE_ORPHAN_REAPED,
                        phase=phase_id,
                        pid=pid,
                        signaled=reap.signaled,
                        cmdline_mismatch=reap.cmdline_mismatch,
                    )
                return _attach(TickResult("lease_expired", f"phase={phase_id}"))

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
                # Order matters: durable state first (event + release +
                # coolant), best-effort reap last. If the reap raises (e.g.
                # ps timeout), the claim is already released and the event is
                # on disk — next tick won't re-fire.
                st.append_event(
                    data,
                    st.EVENT_PHASE_WORKER_DEAD,
                    phase=phase_id,
                    pid=pid,
                )
                st.release_claim_and_emit(
                    data,
                    coolant_enabled=config.coolant.enabled,
                    coolant_script_override=config.coolant.script_dir,
                )
                try:
                    # Group reap (worker + heartbeat), see lease-expiry note above.
                    pgid = claim.get("pgid") or pid
                    st.reap_orphan_pgroup(pgid, cmdline_match=cmdline_match)
                except Exception:
                    pass
                return _attach(
                    TickResult(
                        "worker_dead",
                        f"phase={phase_id}",
                        phase_id=phase_id,
                        token=claimed_by,
                        notify_body=notify.render_worker_dead(
                            data["plan_slug"],
                            phase_id,
                            pid,
                        ),
                    )
                )

        # Surface stalled claims once. Don't release the claim — the lease
        # owns retry; this event is just the signal the notification adapter
        # (Day-2 Cliff 2) hangs off of.
        if stalled := _detect_stalled(data):
            return _attach(stalled)

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
                    data["status"] = st.STATUS_PAUSED
                    st.append_event(
                        data,
                        st.EVENT_BLOCKER_SLA_EXCEEDED,
                        blocker_id=b["id"],
                        age_hours=round(age_hours, 1),
                    )
                    return _attach(
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
                        b["consumed"] = True
                        break
                st.append_event(data, ev_type, blocker_id=blocker_id)
            if target_status:
                data["status"] = target_status
            return _attach(TickResult("blocker_resumed", f"blocker={events[0][1]}"))

        if data["status"] in st.TERMINAL_STATUSES:
            return _attach(TickResult("idle", f"plan status={data['status']}"))

        if claim := data.get("current_claim"):
            return _attach(
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
            return _attach(
                TickResult(
                    "idle",
                    f"open_blocker={blockers[0]['id']} pins lane",
                )
            )

        plan_path = config.project_root / config.plan_dir / f"{data['plan_slug']}.md"
        phases = parse_sessions_index(plan_path)
        if not phases:
            return _attach(TickResult("error", f"no Sessions index in {plan_path}"))

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
                data["status"] = st.STATUS_HALTED
                st.append_event(
                    data,
                    st.EVENT_PHASE_MAX_ATTEMPTS,
                    phase=phase.id,
                    attempts=prior_attempts,
                )
                return _attach(
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
            ttl = st.lease_ttl_for_phase(data, phase.id)
            token = st.claim_phase(data, phase.id, ttl)
            return _attach(
                TickResult(
                    "dispatch",
                    detail=f"phase={phase.id} token={token}",
                    phase_id=phase.id,
                    token=token,
                )
            )

        # All phases attempted — but wait for pending spawned tasks.
        if all(p.id in completed for p in phases):
            pending_tasks = [t for t in data["spawned_tasks"] if t["status"] == "pending"]
            if not pending_tasks:
                data["status"] = st.STATUS_DONE
                st.append_event(data, st.EVENT_PLAN_COMPLETED)
                commit_count = sum(
                    len(evt.get("commits") or [])
                    for evt in data["events"]
                    if evt.get("type") == st.EVENT_PHASE_COMPLETED
                )
                return _attach(
                    TickResult(
                        "plan_done",
                        data["plan_slug"],
                        notify_body=notify.render_completed(
                            data["plan_slug"],
                            commit_count,
                        ),
                    )
                )
            return _attach(
                TickResult(
                    "idle",
                    f"phases done; {len(pending_tasks)} spawned task(s) pending",
                )
            )

        return _attach(TickResult("idle", "all phases blocked or none dispatchable"))


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

    Scans a project's `.orchestrator/*.state.json` for UNREGISTERED files stuck
    at `running` whose worker is gone (`state.is_zombie_state`), then
    terminalizes + reaps them. This is the backstop for the "unregistered +
    running" window that `tick-all`'s registry walk can never reach (#75): the
    documented crash-recovery self-heal (architecture.md "Crash recovery") only
    fires while the queue head is still present, so a fully-unregistered zombie
    like `fm-docs-sweep` would otherwise sit at `running` forever.

    Registered slugs are skipped — tick-all / the supervisor own them, and a
    registered plan may legitimately sit claimless between phases. Corrupt /
    stale-schema files are skipped (operator's `clu doctor` surfaces those).
    Idempotent: re-checks the zombie predicate under the lock so a concurrent
    tick that just revived a plan isn't terminalized.

    Scope: `tick-all` calls this once per project it visits, and it visits only
    projects that appear in the registry. A project whose *every* plan is
    unregistered is never visited, so its zombies are reachable only via
    `clu doctor --project <that project>`. In practice a zombie shares a project
    with live plans (the `fm-docs-sweep` incident did), so the auto-sweep covers
    it; the all-unregistered-project case is the documented residual gap.
    """
    orch_dir = cfg.project_root / cfg.plan_dir / ORCHESTRATOR_DIR
    results: list[ZombieSweepResult] = []
    if not orch_dir.is_dir():
        return results
    suffix = ".state.json"
    for path in sorted(orch_dir.glob(f"*{suffix}")):
        slug = path.name[: -len(suffix)]
        if slug in registered_slugs:
            continue
        try:
            data = st.load(path)
        except (OSError, ValueError, st.SchemaVersionMismatch):
            continue
        if not st.is_zombie_state(data):
            continue
        if dry_run:
            results.append(ZombieSweepResult(slug, reaped=False, terminalized=False))
            continue
        # Re-load + re-check under the lock so a concurrent tick that just
        # revived this plan isn't terminalized. Use `locked` (not `mutate`) and
        # save only on the act path — `mutate` would re-write the unchanged file
        # on the revived-no-op branch (needless atomic rewrite + mtime churn).
        with st.locked(path):
            live = st.load(path)
            if not st.is_zombie_state(live):
                continue
            reap = st.reap_claim(live)
            if live.get("current_claim"):
                st.release_claim_and_emit(live, **cfg.coolant.release_kwargs())
            st.terminalize(live, reason="zombie_sweep")
            st.save_atomic(path, live)
        results.append(
            ZombieSweepResult(slug, reaped=bool(reap and reap.signaled), terminalized=True)
        )
    return results
