# cross-plan-rules — ADR-0002-aligned seam for post-loop passes

`cmd_tick_all` runs per-plan ticks, then per-project post-loop
passes. Two passes exist today: queue advancement
(`_advance_queue_for_project`) and worktree conflict scan
(`_detect_worktree_conflicts_for_project`). Each rebuilds the
per-project plan list from scratch, repeats the registry walk,
re-loads state files. Adding a third pass (e.g. stalled-cluster
detection, garbage-collection sweep) means a third hand-rolled loop.

This plan extracts a `cross_plan_rules.py` module: a rule protocol
`(project_root, plans: list[(slug, state, path)]) -> list[Event]`,
a rule registry, and a single shared `_plans_for_project` loader.
`cmd_tick_all`'s post-loop becomes `for project in distinct:
run_rules(project)`. ADR-0002's "at most one effect per project per
cron interval" invariant moves into the rule contract: each rule
MUST return at most one set of events per call, and the runner
enforces the rule ordering.

## Locked design decisions

### Cross-cutting

- **Module name: `end_of_line/cross_plan_rules.py`.** **Why:**
  parallel to `supervisor.tick` which owns the single-plan priority
  chain; this module owns the cross-plan post-loop chain. **How to
  apply:** keep the protocol shape close to supervisor rules (input
  data, output events).

- **Rule protocol returns events only — runner does the writes.**
  Each rule takes the per-project plan list and returns
  `list[Event]` paired with the target state file. The runner
  applies them under `state.mutate` and `state.save_atomic`. **Why:**
  keeps the lock-ordering rule (queue lock outer, state lock inner)
  in one place — rules don't take locks. **How to apply:** rules
  are pure functions over loaded data; no filesystem writes inside
  a rule body.

- **One pop / one emit per project per tick still holds.** The
  runner enforces it by short-circuiting the rule chain on the
  first rule that emits. Queue advancement is rule 1 (it can pop);
  worktree conflict scan is rule 2 (it can emit a warning pair).
  **Why:** ADR-0002. **How to apply:** runner has the same
  first-match-wins shape as `supervisor.tick`.

### Phase 1 — extract

- **New module `end_of_line/cross_plan_rules.py`:**
  - `ProjectRule = Callable[[Path, list[ProjectPlan]], RuleResult]`
    Protocol. `ProjectPlan = (slug, state_data, state_path)`.
    `RuleResult = (events_per_plan: dict[Path, list[Event]],
    rule_name: str) | None`.
  - `_RULES: list[ProjectRule] = []` registry, populated at module
    load by the migration phase.
  - `run_rules(project_root, plans) -> RuleResult | None` —
    first-match-wins.
  - `load_plans_for_project(project_root, cfg) ->
    list[ProjectPlan]` — hoisted from `cli.py:_plans_for_project`.
- **Tests in `tests/test_cross_plan_rules.py`:** ~10 tests covering
  the runner's first-match semantics, the loader's handling of
  missing state files, and the registry's stable iteration order.
- **Pure refactor: no rules registered yet** (migration in phase 2).

### Phase 2 — migrate

- Convert `_advance_queue_for_project` to a rule
  `queue_advancement_rule(project_root, plans)`. Inputs identical
  to today; output is the events that would be appended. The runner
  takes the queue lock + state lock as before — lock acquisition
  moves into the runner's "apply" step.
- Convert `_detect_worktree_conflicts_for_project` to a rule
  `worktree_conflict_rule(project_root, plans)`. Outputs the events
  + iMessage tuples for the canonical-pair (smaller slug) side.
- `cli.cmd_tick_all` post-loop collapses to:
  ```python
  for project in distinct_projects:
      plans = cross_plan_rules.load_plans_for_project(project, cfg)
      cross_plan_rules.run_rules(project, plans)
  ```
- Existing tests for queue advancement + worktree conflict scan
  rewrite against the rule interface — same fixtures, new entry
  point.

### Phase 3 — cleanup

- Delete the now-redundant per-project helpers from `cli.py`.
- `/simplify` over the `cli.cmd_tick_all` diff (it should shrink
  meaningfully).
- Suite green at ~+10 new tests over baseline.
- Add a comment in `cross_plan_rules.py` pointing at ADR-0002 so
  future maintainers see the invariant referenced.

## Non-goals

- **New rules.** Stalled-cluster detection, GC sweeps, etc. are
  future PRs that drop in as new rules — this plan provides the
  seam, not new behavior.
- **Per-plan tick chain changes.** `supervisor.tick` untouched.
- **Lock-ordering changes.** Queue lock outer / state lock inner
  preserved.
- **Auto-repair worker changes.** Already-synchronous, stays
  outside the rule chain.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green.
- Structured commit format.
- Stage explicit paths.
- Call `clu complete --plan cross-plan-rules --phase <id> --token
  <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| extract | `cross-plan-rules-extract.md` | New `cross_plan_rules.py` with protocol + registry + loader + runner; ~10 tests | 2h |
| migrate | `cross-plan-rules-migrate.md` | Convert queue advancement + worktree conflict scan to rules; collapse cmd_tick_all post-loop | 1.5h |
| cleanup | `cross-plan-rules-cleanup.md` | Delete redundant helpers, `/simplify`, ADR-0002 reference comment | 1h |
