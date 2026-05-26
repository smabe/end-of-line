"""Cross-plan post-loop rule chain.

See docs/adr/0002-one-tick-one-action.md — this module enforces
the "at most one effect per project per cron interval" invariant
across plans, paralleling supervisor.tick's per-plan chain.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from end_of_line import dry_merge, notify, queue, registry
from end_of_line import state as st
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


_FREEZE_STATUSES: frozenset[str] = frozenset(
    {
        st.STATUS_HALTED,
        st.STATUS_HALTED_REPLAN,
        st.STATUS_PAUSED,
    }
)

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
    project_root: Path,
    plans: list[ProjectPlan],
) -> RuleResult | None:
    """Busy-gate / freeze / absorb / abandon / pop chain for the project queue.

    Owns queue.mutate itself (queue-lock outer) because the pop sequence
    (state-create → registry.register → queue.pop) must be atomic.
    The runner's _apply is a no-op for this rule (state-create happens
    inside the queue lock; no events_per_plan entries are returned).
    """
    # Deferred to avoid circular import — cli imports cross_plan_rules.
    from end_of_line.cli import (  # noqa: PLC0415
        _handle_corrupt_queue,
        _tick_one_plan,
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
            data["history"].append(
                {
                    **entry,
                    "ended_at": st.utcnow(),
                    "outcome": "absorbed",
                }
            )
        return RuleResult(events_per_plan={}, rule_name="queue_advancement")

    plan_file = cfg.project_root / cfg.plan_dir / f"{slug}.md"
    if not plan_file.exists():
        with queue.mutate(queue_path) as data:
            if not data["queue"] or data["queue"][0]["slug"] != slug:
                return None
            entry = data["queue"].pop(0)
            data["history"].append(
                {
                    **entry,
                    "ended_at": st.utcnow(),
                    "outcome": "abandoned",
                }
            )
        return RuleResult(
            events_per_plan={},
            rule_name="queue_advancement",
            notifies=[
                (
                    notify.KIND_QUEUE_SKIPPED,
                    notify.render_queue_skipped(slug, reason="plan file missing"),
                )
            ],
        )

    # Normal pop: state-create → registry.register → queue.pop, all under
    # the queue lock so a crashed run can be replayed without losing the head.
    with queue.mutate(queue_path) as data:
        if not data["queue"] or data["queue"][0]["slug"] != slug:
            return None
        with st.locked(state_path):
            if not state_path.exists():
                fresh = st.empty_state(slug, cfg.plan_dir)
                if head.get("batch_id"):
                    fresh["batch_id"] = head["batch_id"]
                st.append_event(
                    fresh,
                    st.EVENT_QUEUE_POPPED,
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
    project_root: Path,
    plans: list[ProjectPlan],
) -> RuleResult | None:
    """Detect conflicting active-without-worktree plan pairs; update in_conflict_with.

    Returns field updates and events for all plans whose in_conflict_with changed,
    plus KIND_HALTED notifies for each newly-conflicting canonical pair
    (lexicographically-smaller slug). Returns None when nothing changed.
    """
    conflicting = {
        p.slug for p in plans if not st.get_worktree(p.state) and _is_plan_active(p.state)
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
                plan_events.append(
                    {
                        "type": st.EVENT_WORKTREE_CONFLICT_WARNING,
                        "kwargs": {"other_slug": other},
                    }
                )
                notifies.append(
                    (
                        notify.KIND_HALTED,
                        notify.render_worktree_conflict(project_root, p.slug, other),
                    )
                )
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


def _git_rev_parse(project_root: Path, branch: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", branch],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    return r.stdout.strip()


def _write_followup_plan_pair(
    cfg: ProjectConfig,
    batch_id: str,
    ts: str,
    result: dry_merge.MergeResult,
    group: list[ProjectPlan],
) -> tuple[Path, Path]:
    """Write master + sub-plan for a dirty merge result. Returns (master, sub)."""
    slug = f"merge-resolve-{batch_id}-{ts}"
    sub_slug = f"{slug}-fix"
    plan_dir = cfg.project_root / cfg.plan_dir
    plan_dir.mkdir(parents=True, exist_ok=True)
    master_path = plan_dir / f"{slug}.md"
    sub_path = plan_dir / f"{sub_slug}.md"

    sibling_slugs = ", ".join(p.slug for p in group)
    conflict_section = ""
    if result.conflict_files:
        files_list = "\n".join(f"- `{f}`" for f in result.conflict_files)
        conflict_section = f"\n## Conflict files\n\n{files_list}\n"
    stderr_section = ""
    if result.stderr_tail:
        stderr_section = f"\n## Test output (tail)\n\n```\n{result.stderr_tail}\n```\n"

    master_path.write_text(
        f"# {slug} — resolve merge conflicts for batch {batch_id}\n\n"
        f"Branches: {sibling_slugs}\n"
        f"{conflict_section}"
        f"{stderr_section}"
        f"\n## Sessions index\n\n"
        f"| Session | Plan file | Scope | Effort |\n"
        f"|---|---|---|---|\n"
        f"| fix | `{sub_slug}.md` | Resolve conflicts and fix failing tests | 1h |\n"
    )

    conflict_files_str = ", ".join(f"`{f}`" for f in result.conflict_files) or (
        "the failing tests" if result.outcome == "suite_failed" else "the conflicting files"
    )
    sub_path.write_text(
        f"# {sub_slug} — fix conflicts for batch {batch_id}\n\n"
        f"Resolve conflicts in {conflict_files_str} and/or fix failing tests.\n"
        f"Commit + push. `clu complete --plan {slug} --phase fix --token <T>`.\n"
    )
    return master_path, sub_path


def dry_merge_gate_rule(
    project_root: Path,
    plans: list[ProjectPlan],
) -> RuleResult | None:
    """Fire when ≥2 sibling DONE plans share a batch_id and have live worktrees.

    Calls dry_merge.attempt_merge; on clean stamps gate_result on each plan;
    on dirty also writes a merge-resolve follow-up plan pair to disk (not queued).
    """
    cfg = load_project_config(project_root)

    eligible: dict[str, list[ProjectPlan]] = {}
    for p in plans:
        if p.state.get("status") != st.STATUS_DONE:
            continue
        bid = p.state.get("batch_id")
        if not bid:
            continue
        if not st.get_worktree(p.state):
            continue
        eligible.setdefault(bid, []).append(p)

    for bid, group in eligible.items():
        if len(group) < 2:
            continue

        # Resolve HEAD SHAs for the idempotency key — drop any plan whose
        # branch has disappeared (lag between worktree record and reality).
        live_pairs: list[tuple[ProjectPlan, str, str]] = []
        for p in group:
            branch = st.get_worktree(p.state)["branch"]
            try:
                sha = _git_rev_parse(project_root, branch)
                live_pairs.append((p, branch, sha))
            except subprocess.CalledProcessError:
                log.warning(
                    "dry_merge_gate: branch %s not found in %s, skipping %s",
                    branch,
                    project_root,
                    p.slug,
                )

        if len(live_pairs) < 2:
            continue

        live_group = [p for p, _, _ in live_pairs]
        live_branches = [b for _, b, _ in live_pairs]
        sha_key = "|".join(sorted(sha for _, _, sha in live_pairs))

        # Idempotency: same SHA set → skip
        if any(p.state.get("gate_result", {}).get("sha_key") == sha_key for p in live_group):
            continue

        test_cmd = getattr(cfg, "test_command", None)
        result = dry_merge.attempt_merge(
            project_root,
            base_ref="main",
            branches=live_branches,
            test_command=test_cmd,
        )

        ts = datetime.now(UTC).strftime("%Y%m%d%H%M")
        gate_result_base: dict[str, Any] = {
            "sha_key": sha_key,
            "ts": st.utcnow(),
            "batch_id": bid,
            "outcome": result.outcome,
        }

        field_updates: dict[Path, dict[str, Any]] = {}
        notifies: list[tuple[str, str]] = []

        if result.outcome == "clean":
            for p in live_group:
                field_updates[p.state_path] = {"gate_result": gate_result_base}
            notifies.append(
                (
                    notify.KIND_GATE_CLEAN,
                    notify.render_gate_clean(bid, [p.slug for p in live_group]),
                )
            )
        else:
            fu_master, _ = _write_followup_plan_pair(cfg, bid, ts, result, live_group)
            gr = {**gate_result_base, "follow_up_plan": fu_master.name}
            for p in live_group:
                field_updates[p.state_path] = {"gate_result": gr}
            notifies.append(
                (
                    notify.KIND_GATE_DIRTY,
                    notify.render_gate_dirty(bid, result.outcome, str(fu_master)),
                )
            )

        return RuleResult(
            events_per_plan={},
            rule_name="dry_merge_gate",
            notifies=notifies,
            field_updates_per_plan=field_updates,
        )

    return None


register_rule(dry_merge_gate_rule)


def ready_to_ship_rule(
    project_root: Path,
    plans: list[ProjectPlan],
) -> RuleResult | None:
    """Surface KIND_READY_TO_SHIP when DONE plans have unmerged
    worktree branches and no in-flight ship_pending stamp
    (clu-ship.md phase 7).

    Slots between `dry_merge_gate_rule` and `auto_archive_rule`:
    - dry_merge_gate runs first so batch-validate dirty surfaces
      before we'd point the operator at `clu ship`.
    - auto_archive owns plans whose branch is already merged
      into origin/main; we skip those.

    Dedup: stamps `data["ready_to_ship_announced"] = {"branch_sha":
    <sha>}` after firing so subsequent ticks at the same branch
    tip don't re-spam. Re-fires when the worker pushes new commits
    (branch_sha changes).
    """
    cfg = load_project_config(project_root)

    eligible: list[tuple[ProjectPlan, str, str]] = []  # (plan, branch, sha)
    for p in plans:
        if p.state.get("status") != st.STATUS_DONE:
            continue
        wt = st.get_worktree(p.state)
        if not wt:
            continue
        branch = wt["branch"]
        if st.is_branch_merged_into(project_root, branch):
            continue
        if p.state.get("ship_pending"):
            continue
        r = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--verify", branch],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            continue
        branch_sha = r.stdout.strip()
        announced = p.state.get("ready_to_ship_announced") or {}
        if announced.get("branch_sha") == branch_sha:
            continue
        eligible.append((p, branch, branch_sha))

    if not eligible:
        return None

    mode = cfg.dispatch.ship_mode
    slugs = [e[0].slug for e in eligible]
    field_updates = {
        p.state_path: {"ready_to_ship_announced": {"branch_sha": sha}} for p, _, sha in eligible
    }
    return RuleResult(
        events_per_plan={},
        rule_name="ready_to_ship",
        notifies=[
            (
                notify.KIND_READY_TO_SHIP,
                notify.render_ready_to_ship(slugs, mode),
            )
        ],
        field_updates_per_plan=field_updates,
    )


register_rule(ready_to_ship_rule)


def auto_archive_rule(
    project_root: Path,
    plans: list[ProjectPlan],
) -> RuleResult | None:
    """Archive the first STATUS_DONE plan whose worktree branch is merged into origin/main.

    Fires at most once per tick (first-eligible-wins). Skips when
    `cfg.auto_archive` is False (forward-compat for phase config-opt-out-docs).
    """
    from end_of_line.cli import _perform_archive  # noqa: PLC0415

    cfg = load_project_config(project_root)
    if not getattr(cfg, "auto_archive", True):
        return None

    for p in plans:
        if p.state.get("status") != st.STATUS_DONE:
            continue
        wt = st.get_worktree(p.state)
        if not wt:
            continue
        branch = wt["branch"]
        if not st.is_branch_merged_into(project_root, branch):
            continue
        try:
            _perform_archive(cfg, p.slug, unregister=True)
        except Exception as exc:
            log.warning(
                "auto_archive_rule: %s archive failed — %s",
                p.slug,
                exc,
            )
            continue
        return RuleResult(
            events_per_plan={},
            rule_name="auto_archive",
            notifies=[
                (
                    notify.KIND_PLAN_AUTO_ARCHIVED,
                    notify.render_plan_auto_archived(p.slug, branch),
                )
            ],
        )
    return None


register_rule(auto_archive_rule)
