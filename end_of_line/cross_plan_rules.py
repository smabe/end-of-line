"""Cross-plan post-loop rule chain.

See docs/adr/0002-one-tick-one-action.md — this module enforces
the "at most one effect per project per cron interval" invariant
across plans, paralleling supervisor.tick's per-plan chain.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from end_of_line import notify, queue, registry, state as st
from end_of_line.config import ProjectConfig, load_project_config

log = logging.getLogger(__name__)


@dataclass
class ProjectPlan:
    slug: str
    state: dict[str, Any]
    state_path: Path


@dataclass
class RuleResult:
    events_per_plan: dict[Path, list[dict]]
    rule_name: str
    notifies: list[tuple[str, str]] = field(default_factory=list)
    field_updates_per_plan: dict[Path, dict[str, Any]] = field(default_factory=dict)


ProjectRule = Callable[[Path, list[ProjectPlan]], "RuleResult | None"]

_RULES: list[ProjectRule] = []


def register_rule(rule: ProjectRule) -> None:
    _RULES.append(rule)


def run_rules(project_root: Path, plans: list[ProjectPlan]) -> RuleResult | None:
    for rule in _RULES:
        result = rule(project_root, plans)
        if result is not None:
            _apply(result)
            return result
    return None


def load_plans_for_project(project_root: Path, cfg: ProjectConfig) -> list[ProjectPlan]:
    plans: list[ProjectPlan] = []
    for entry in registry.entries_for_project(project_root):
        state_path = cfg.state_path(entry.plan_slug)
        if not state_path.exists():
            log.warning("cross_plan_rules: skipping %s — state file missing", entry.plan_slug)
            continue
        try:
            data = st.load(state_path)
        except (OSError, st.SchemaVersionMismatch) as exc:
            log.warning("cross_plan_rules: skipping %s — %s", entry.plan_slug, exc)
            continue
        plans.append(ProjectPlan(entry.plan_slug, data, state_path))
    return plans


_FREEZE_STATUSES: frozenset[str] = frozenset({
    st.STATUS_HALTED, st.STATUS_HALTED_REPLAN, st.STATUS_PAUSED,
})

_QUEUE_LOAD_ERRORS = (json.JSONDecodeError, st.SchemaVersionMismatch, KeyError, OSError)


def _is_plan_active(state: dict) -> bool:
    if state.get("current_claim"):
        return True
    return state.get("status") == st.STATUS_RUNNING


def _apply(result: RuleResult) -> None:
    all_paths = set(result.events_per_plan) | set(result.field_updates_per_plan)
    for state_path in all_paths:
        with st.mutate(state_path) as data:
            for event in result.events_per_plan.get(state_path, []):
                st.append_event(data, event["type"], **event.get("kwargs", {}))
            for fld, val in result.field_updates_per_plan.get(state_path, {}).items():
                data[fld] = val


def queue_advancement_rule(
    project_root: Path, plans: list[ProjectPlan],
) -> RuleResult | None:
    """Busy-gate / freeze / absorb / abandon / pop chain for the project queue.

    Owns queue.mutate itself (queue-lock outer) because the pop sequence
    (state-create → registry.register → queue.pop) must be atomic.
    The runner's _apply is a no-op for this rule (state-create happens
    inside the queue lock; no events_per_plan entries are returned).
    """
    # Deferred to avoid circular import — cli imports cross_plan_rules.
    from end_of_line.cli import (  # noqa: PLC0415
        _handle_corrupt_queue, _tick_one_plan,
    )

    cfg = load_project_config(project_root)
    queue_path = cfg.queue_path()
    if not queue_path.exists():
        return None

    # Busy gate: any live claim in the project freezes advancement.
    for p in plans:
        if p.state.get("current_claim"):
            return None

    try:
        queue_data = queue.load(queue_path)
    except _QUEUE_LOAD_ERRORS as exc:
        _handle_corrupt_queue(cfg, exc, queue_path)
        return RuleResult(events_per_plan={}, rule_name="queue_advancement")

    if not queue_data["queue"]:
        return None

    head = queue_data["queue"][0]
    slug = head["slug"]
    try:
        st.validate_slug(slug, kind="plan slug")
    except st.InvalidSlug as exc:
        print(f"queue head has invalid slug @ {project_root}: {exc}", file=sys.stderr)
        return None

    state_path = cfg.state_path(slug)
    existing_status: str | None = None
    if state_path.exists():
        try:
            existing_status = st.load(state_path).get("status")
        except (OSError, ValueError, st.SchemaVersionMismatch):
            existing_status = None

    project_slugs = {p.slug for p in plans}
    registered = slug in project_slugs

    if registered and existing_status in _FREEZE_STATUSES:
        return None

    if registered and existing_status in {st.STATUS_DONE, st.STATUS_RUNNING}:
        with queue.mutate(queue_path) as data:
            if not data["queue"] or data["queue"][0]["slug"] != slug:
                return None
            entry = data["queue"].pop(0)
            data["history"].append({
                **entry,
                "ended_at": st.utcnow(),
                "outcome": "absorbed",
            })
        return RuleResult(events_per_plan={}, rule_name="queue_advancement")

    plan_file = cfg.project_root / cfg.plan_dir / f"{slug}.md"
    if not plan_file.exists():
        with queue.mutate(queue_path) as data:
            if not data["queue"] or data["queue"][0]["slug"] != slug:
                return None
            entry = data["queue"].pop(0)
            data["history"].append({
                **entry,
                "ended_at": st.utcnow(),
                "outcome": "abandoned",
            })
        return RuleResult(
            events_per_plan={},
            rule_name="queue_advancement",
            notifies=[(
                notify.KIND_QUEUE_SKIPPED,
                notify.render_queue_skipped(slug, reason="plan file missing"),
            )],
        )

    # Normal pop: state-create → registry.register → queue.pop, all under
    # the queue lock so a crashed run can be replayed without losing the head.
    with queue.mutate(queue_path) as data:
        if not data["queue"] or data["queue"][0]["slug"] != slug:
            return None
        with st.locked(state_path):
            if not state_path.exists():
                fresh = st.empty_state(slug, cfg.plan_dir)
                st.append_event(
                    fresh, st.EVENT_QUEUE_POPPED,
                    slug=slug,
                    added_at=head.get("added_at"),
                    added_by=head.get("added_by", "operator"),
                    position=1,
                )
                st.save_atomic(state_path, fresh)
        registry.register(cfg.project_root, slug)
        data["queue"].pop(0)

    result = _tick_one_plan(slug, cfg, state_path, dispatch=True)
    print(f"tick (queue-pop) {slug} @ {cfg.project_root}: {result}")
    return RuleResult(events_per_plan={}, rule_name="queue_advancement")


def worktree_conflict_rule(
    project_root: Path, plans: list[ProjectPlan],
) -> RuleResult | None:
    """Detect conflicting active-without-worktree plan pairs; update in_conflict_with.

    Returns field updates and events for all plans whose in_conflict_with changed,
    plus KIND_HALTED notifies for each newly-conflicting canonical pair
    (lexicographically-smaller slug). Returns None when nothing changed.
    """
    conflicting = {
        p.slug for p in plans
        if not st.get_worktree(p.state) and _is_plan_active(p.state)
    }

    events_per_plan: dict[Path, list[dict]] = {}
    field_updates: dict[Path, dict[str, Any]] = {}
    notifies: list[tuple[str, str]] = []

    for p in plans:
        target_set = (conflicting - {p.slug}) if p.slug in conflicting else set()
        existing = set(p.state.get("in_conflict_with") or [])
        if target_set == existing:
            continue
        field_updates[p.state_path] = {"in_conflict_with": sorted(target_set)}
        plan_events: list[dict] = []
        for other in sorted(target_set - existing):
            if p.slug < other:
                plan_events.append({
                    "type": st.EVENT_WORKTREE_CONFLICT_WARNING,
                    "kwargs": {"other_slug": other},
                })
                notifies.append((
                    notify.KIND_HALTED,
                    notify.render_worktree_conflict(project_root, p.slug, other),
                ))
        if plan_events:
            events_per_plan[p.state_path] = plan_events

    if not field_updates:
        return None
    return RuleResult(
        events_per_plan=events_per_plan,
        field_updates_per_plan=field_updates,
        rule_name="worktree_conflict",
        notifies=notifies,
    )


register_rule(queue_advancement_rule)
register_rule(worktree_conflict_rule)
