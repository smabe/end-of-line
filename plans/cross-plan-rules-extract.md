# cross-plan-rules-extract — protocol + registry + loader + runner

You are phase `extract` of the `cross-plan-rules` plan. Create
`end_of_line/cross_plan_rules.py` with the rule protocol, an empty
rule registry, a hoisted `load_plans_for_project` loader, and a
first-match-wins runner. No rules are registered yet — migration
happens in phase 2.

## Locked decisions (do NOT re-litigate)

See `plans/cross-plan-rules.md`. Summary:

- Module path: `end_of_line/cross_plan_rules.py`.
- Rules return events; runner applies them under locks.
- Runner is first-match-wins (ADR-0002 invariant).
- Loader hoisted from `cli.py:_plans_for_project`.

## Read first

- `end_of_line/cli.py` — `_plans_for_project`,
  `_advance_queue_for_project`,
  `_detect_worktree_conflicts_for_project`, `cmd_tick_all`. Don't
  modify yet — this phase just relocates the loader.
- `end_of_line/supervisor.py` — `TickResult` dataclass for shape
  reference (RuleResult should mirror it loosely).
- `end_of_line/state.py` — `Event` shape, `mutate`,
  `save_atomic`.
- `end_of_line/queue.py` — `mutate` context manager (lock-ordering
  reference).
- `docs/adr/0002-one-tick-one-action.md` — the invariant this
  module enforces. Reference in a top-of-file comment.

## Produce

1. **Failing tests first** in `tests/test_cross_plan_rules.py`.
   ~10 tests:

   - `test_run_rules_empty_registry_returns_none` — no rules
     registered → `None`.
   - `test_run_rules_first_match_wins` — register 2 fake rules;
     both would emit; only the first runs.
   - `test_run_rules_skips_silent_rules` — register a rule that
     returns `None`; runner proceeds to the next rule.
   - `test_run_rules_stable_iteration_order` — registration order
     is iteration order.
   - `test_load_plans_for_project_no_plans_returns_empty` — project
     with no registered plans.
   - `test_load_plans_for_project_one_plan_loads_state` — single
     plan, returns one `ProjectPlan` tuple.
   - `test_load_plans_for_project_skips_missing_state` — registry
     entry exists but state file is gone; loader logs + skips.
   - `test_load_plans_for_project_skips_schema_mismatch` — wrong
     schema_version; logger warns, plan omitted.
   - `test_register_rule_appends_to_registry` — `register_rule`
     helper for tests / migration.
   - `test_runner_does_not_take_state_locks` — verify by
     interleaving: while runner is executing rule, an external
     reader can still `st.load(path)` without blocking. (Use a
     fake rule that calls back into the test.)

2. **Implementation** in `end_of_line/cross_plan_rules.py`:

   ```python
   """Cross-plan post-loop rule chain.

   See docs/adr/0002-one-tick-one-action.md — this module enforces
   the "at most one effect per project per cron interval" invariant
   across plans, paralleling supervisor.tick's per-plan chain.
   """
   from __future__ import annotations

   import logging
   from dataclasses import dataclass
   from pathlib import Path
   from typing import Any, Callable, Protocol

   from end_of_line import state as st
   from end_of_line.config import Config
   from end_of_line.registry import entries_for_project

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


   ProjectRule = Callable[[Path, list[ProjectPlan]], RuleResult | None]

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


   def load_plans_for_project(project_root: Path, cfg: Config) -> list[ProjectPlan]:
       plans: list[ProjectPlan] = []
       for entry in entries_for_project(project_root):
           try:
               data = st.load(entry.state_path)
           except (FileNotFoundError, st.SchemaVersionMismatch, OSError) as exc:
               log.warning("cross_plan_rules: skipping %s — %s", entry.slug, exc)
               continue
           plans.append(ProjectPlan(entry.slug, data, entry.state_path))
       return plans


   def _apply(result: RuleResult) -> None:
       for state_path, events in result.events_per_plan.items():
           with st.mutate(state_path) as data:
               for event in events:
                   st.append_event(data, event["type"], **event.get("kwargs", {}))
   ```

3. **Acceptance.**
   - 10 new tests green.
   - Full suite green at baseline (no rules registered, so
     `cmd_tick_all` is unchanged).
   - `grep -n "_plans_for_project" end_of_line/cli.py` shows the
     helper still exists in cli.py (migration is phase 2 — don't
     delete yet).

4. **Commit + complete.**
   - Title: `cross-plan-rules: phase extract — protocol + registry
     + loader + runner`
   - Stage: `end_of_line/cross_plan_rules.py`,
     `tests/test_cross_plan_rules.py`.
   - `clu complete --plan cross-plan-rules --phase extract --token
     <T>`.

## Failure modes to watch

- **Lock acquisition inside rules.** Rules MUST NOT take locks
  inside their function body — the runner's `_apply` step takes
  state locks for writes. If a rule reads from one state file to
  decide what to write to another, it does so against the loaded
  snapshot, not under a fresh lock.
- **Registry singleton.** `_RULES` is module-global. Tests must
  clear it in setUp / tearDown (`_RULES.clear()`) or use a context
  manager that snapshots + restores. Don't leak rules across tests.
- **`Callable` vs Protocol.** Python's `Protocol` for callables is
  fiddly; the typing alias `ProjectRule = Callable[...]` is the
  simpler form and matches how the rest of clu types callables.
- **`entries_for_project`.** If `registry.py` doesn't export this
  helper, add it — it's a one-liner over `entries()`. Don't
  duplicate the filter logic inline.
- **Notification path.** `RuleResult.notifies` exists so the
  worktree-conflict rule can emit `KIND_HALTED` iMessages. The
  runner is responsible for actually firing them (after the
  state writes succeed). Don't fire notifies before the writes —
  that risks a misleading ping if the write fails.
