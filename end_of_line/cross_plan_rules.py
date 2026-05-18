"""Cross-plan post-loop rule chain.

See docs/adr/0002-one-tick-one-action.md — this module enforces
the "at most one effect per project per cron interval" invariant
across plans, paralleling supervisor.tick's per-plan chain.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from end_of_line import registry, state as st
from end_of_line.config import ProjectConfig

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


def _apply(result: RuleResult) -> None:
    for state_path, events in result.events_per_plan.items():
        with st.mutate(state_path) as data:
            for event in events:
                st.append_event(data, event["type"], **event.get("kwargs", {}))
