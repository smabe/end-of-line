"""Single-tick supervisor logic.

Action priority (first match wins):
  1. Stale lease release
  2. Stale-question escalation
  3. Answered-question resume (mark consumed)
  4. Plan halted/paused → idle
  5. Active claim → idle
  6. Dispatch next pending phase
  7. All phases complete → mark plan done
  8. Idle
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import state as st
from .config import ProjectConfig
from .plan_parser import parse_sessions_index

Action = Literal[
    "dispatch", "idle", "lease_expired", "escalate",
    "blocker_resumed", "halt", "plan_done", "error",
]


@dataclass
class TickResult:
    action: Action
    detail: str = ""
    phase_id: str | None = None
    token: str | None = None

    def __str__(self) -> str:
        return f"[{self.action}] {self.detail}" if self.detail else f"[{self.action}]"


def tick(state_path: Path, config: ProjectConfig) -> TickResult:
    if not state_path.exists():
        return TickResult("idle", f"no state at {state_path}")

    with st.mutate(state_path) as data:
        # 1. Stale lease
        if claim := data.get("current_claim"):
            if st.release_if_expired(data):
                return TickResult("lease_expired", f"phase={claim['phase_id']}")

        # 2. Stale question
        sla_hours = data["config"].get("blocked_question_sla_hours", st.DEFAULT_SLA_HOURS)
        now = st._now_utc()
        for b in data["blockers"]:
            if b["answer"] is not None:
                continue
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
                return TickResult(
                    "escalate", f"blocker={b['id']} age_hours={age_hours:.1f}",
                )

        # 3. Newly-answered blocker → mark consumed (worker sees on next dispatch)
        for b in data["blockers"]:
            if b["answer"] is not None and not b.get("consumed"):
                b["consumed"] = True
                if data["status"] == st.STATUS_PAUSED:
                    data["status"] = st.STATUS_RUNNING
                st.append_event(data, st.EVENT_BLOCKER_CONSUMED, blocker_id=b["id"])
                return TickResult("blocker_resumed", f"blocker={b['id']}")

        # 4. Halted / paused / done → idle
        if data["status"] in st.TERMINAL_STATUSES:
            return TickResult("idle", f"plan status={data['status']}")

        # 5. Active claim → idle
        if claim := data.get("current_claim"):
            return TickResult(
                "idle",
                f"phase={claim['phase_id']} in_flight lease={claim['lease_expires']}",
            )

        # 6. Dispatch next phase
        plan_path = config.project_root / config.plan_dir / f"{data['plan_slug']}.md"
        phases = parse_sessions_index(plan_path)
        if not phases:
            return TickResult("error", f"no Sessions index in {plan_path}")

        completed = st.completed_phase_ids(data)
        max_attempts = data["config"].get("max_attempts_per_phase", st.DEFAULT_MAX_ATTEMPTS)
        ttl = data["config"].get("lease_ttl_minutes", st.DEFAULT_LEASE_TTL_MIN)
        for phase in phases:
            if phase.id in completed or st.phase_has_open_blocker(data, phase.id):
                continue
            prior_attempts = st.attempts_for_phase(data, phase.id)
            if prior_attempts >= max_attempts:
                if data["status"] != st.STATUS_HALTED:
                    data["status"] = st.STATUS_HALTED
                    st.append_event(
                        data, st.EVENT_PHASE_MAX_ATTEMPTS,
                        phase=phase.id, attempts=prior_attempts,
                    )
                return TickResult("halt", f"phase={phase.id} attempts={prior_attempts}")
            token = st.claim_phase(data, phase.id, ttl)
            return TickResult(
                "dispatch",
                detail=f"phase={phase.id} token={token}",
                phase_id=phase.id,
                token=token,
            )

        # 7. All done — but wait for pending spawned tasks
        if all(p.id in completed for p in phases):
            pending_tasks = [
                t for t in data["spawned_tasks"] if t["status"] == "pending"
            ]
            if not pending_tasks:
                data["status"] = st.STATUS_DONE
                st.append_event(data, st.EVENT_PLAN_COMPLETED)
                return TickResult("plan_done", data["plan_slug"])
            return TickResult(
                "idle", f"phases done; {len(pending_tasks)} spawned task(s) pending",
            )

        return TickResult("idle", "all phases blocked or none dispatchable")
