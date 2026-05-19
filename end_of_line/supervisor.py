"""Single-tick supervisor logic.

Action priority (first match wins):
  1. Stale lease release
  2. Stalled heartbeat → emit phase_stalled once
  3. Stale-question escalation
  4. Answered-question resume (mark consumed)
  5. Plan halted/paused → idle
  6. Active claim → idle
  7. Dispatch next pending phase
  8. All phases complete → mark plan done
  9. Idle
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from . import coolant, inbox, notify, state as st, state_blocker
from .config import ProjectConfig
from .plan_parser import parse_sessions_index

def _local_now() -> _dt.datetime:
    """Wall-clock local time. Indirection exists so tests can pin the hour."""
    return _dt.datetime.now()

Action = Literal[
    "dispatch", "idle", "lease_expired", "escalate",
    "blocker_resumed", "halt", "plan_done", "error", "stalled",
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
    threshold = data["config"].get(
        "stalled_heartbeat_minutes", st.DEFAULT_STALLED_HEARTBEAT_MIN,
    )
    age = st.heartbeat_age_seconds(claim) or 0.0
    if age < threshold * 60:
        return None
    token = claim.get("claimed_by", "")
    claim["stalled_notified"] = True
    st.append_event(
        data, st.EVENT_PHASE_STALLED,
        phase=claim["phase_id"], claimed_by=token,
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
    data: dict, config: ProjectConfig,
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
                data, st.EVENT_STUCK_BLOCKER_REPINGED,
                blocker_id=b["id"], phase=b["phase_id"], age_min=age_min,
            )
            side_notifies.append((kind, body))
            try:
                inbox.write_event(
                    type="stuck_blocker",
                    plan_slug=data["plan_slug"],
                    project_root=project_root,
                    summary=(
                        f"Blocker {b['id']} on phase {b['phase_id']} "
                        f"open {age_min}min"
                    ),
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
    data: dict, config: ProjectConfig,
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
        data, st.EVENT_STALLED_CLAIM_NOTIFIED,
        phase=claim["phase_id"], stalled_min=age_min,
    )
    side_notifies.append((
        notify.KIND_STALLED_CLAIM,
        notify.render_stalled_claim(
            data["plan_slug"], claim["phase_id"], age_min,
        ),
    ))
    try:
        inbox.write_event(
            type="stalled_claim",
            plan_slug=data["plan_slug"],
            project_root=str(config.project_root.resolve()),
            summary=(
                f"Claim on phase {claim['phase_id']} stalled "
                f"{age_min}min past lease"
            ),
            details={
                "phase_id": claim["phase_id"],
                "stalled_min": age_min,
                "claimed_by": claim.get("claimed_by"),
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

        if claim := data.get("current_claim"):
            pid = claim.get("pid")
            phase_id = claim["phase_id"]
            claimed_by = claim.get("claimed_by")
            if st.release_if_expired(data):
                if claimed_by and phase_id:
                    coolant.emit_stop(
                        session_id=claimed_by,
                        agent_id=coolant.format_agent_id(
                            data["plan_slug"], phase_id,
                        ),
                        agent_type=coolant.AGENT_TYPE,
                    )
                if pid:
                    reap = st.reap_orphan_pid(
                        pid,
                        cmdline_match=f"/clu-phase {data['plan_slug']} {phase_id}",
                    )
                    st.append_event(
                        data, st.EVENT_PHASE_ORPHAN_REAPED,
                        phase=phase_id, pid=pid,
                        signaled=reap.signaled,
                        cmdline_mismatch=reap.cmdline_mismatch,
                    )
                return _attach(TickResult("lease_expired", f"phase={phase_id}"))

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
                "blocked_question_sla_hours", st.DEFAULT_SLA_HOURS,
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
                        data, st.EVENT_BLOCKER_SLA_EXCEEDED,
                        blocker_id=b["id"], age_hours=round(age_hours, 1),
                    )
                    return _attach(TickResult(
                        "escalate", f"blocker={b['id']} age_hours={age_hours:.1f}",
                    ))

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
            return _attach(TickResult(
                "idle",
                f"phase={claim['phase_id']} in_flight lease={claim['lease_expires']}",
            ))

        # Any open blocker on this plan pins the lane: plan-file order
        # encodes implicit dependencies between phases, so dispatching the
        # successor while the predecessor is blocked routinely violates a
        # "must merge before" constraint. Operator answers + priority-4
        # consume re-opens the lane. (#28)
        if blockers := st.open_blockers(data):
            return _attach(TickResult(
                "idle", f"open_blocker={blockers[0]['id']} pins lane",
            ))

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
                    data, st.EVENT_PHASE_MAX_ATTEMPTS,
                    phase=phase.id, attempts=prior_attempts,
                )
                return _attach(TickResult(
                    "halt",
                    f"phase={phase.id} attempts={prior_attempts}",
                    notify_body=notify.render_halted(
                        data["plan_slug"], phase.id, prior_attempts,
                    ),
                ))
            ttl = st.lease_ttl_for_phase(data, phase.id)
            token = st.claim_phase(data, phase.id, ttl)
            return _attach(TickResult(
                "dispatch",
                detail=f"phase={phase.id} token={token}",
                phase_id=phase.id,
                token=token,
            ))

        # All phases attempted — but wait for pending spawned tasks.
        if all(p.id in completed for p in phases):
            pending_tasks = [
                t for t in data["spawned_tasks"] if t["status"] == "pending"
            ]
            if not pending_tasks:
                data["status"] = st.STATUS_DONE
                st.append_event(data, st.EVENT_PLAN_COMPLETED)
                commit_count = sum(
                    len(evt.get("commits") or [])
                    for evt in data["events"]
                    if evt.get("type") == st.EVENT_PHASE_COMPLETED
                )
                return _attach(TickResult(
                    "plan_done",
                    data["plan_slug"],
                    notify_body=notify.render_completed(
                        data["plan_slug"], commit_count,
                    ),
                ))
            return _attach(TickResult(
                "idle", f"phases done; {len(pending_tasks)} spawned task(s) pending",
            ))

        return _attach(TickResult("idle", "all phases blocked or none dispatchable"))
