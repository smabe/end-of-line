# cross-plan-rules-migrate — convert two existing passes to rules

You are phase `migrate` of the `cross-plan-rules` plan. Convert
`_advance_queue_for_project` and `_detect_worktree_conflicts_for_project`
into registered rules, then collapse `cmd_tick_all`'s post-loop to a
single `run_rules` call per distinct project.

## Locked decisions (do NOT re-litigate)

See `plans/cross-plan-rules.md`. Summary:

- Rule order: queue advancement first, worktree conflict scan
  second.
- Lock-ordering: queue lock outer (queue advancement rule),
  state lock inner (runner's `_apply`). Don't invert.
- One pop / one emit per project per cron interval still holds via
  the first-match-wins runner.

## Read first

- `end_of_line/cross_plan_rules.py` — what extract shipped:
  `ProjectRule`, `RuleResult`, `register_rule`, `run_rules`,
  `load_plans_for_project`, `_apply`.
- `end_of_line/cli.py:_advance_queue_for_project` — the current
  shape, especially the freeze / absorb / abandon / normal-pop
  branch chain.
- `end_of_line/cli.py:_detect_worktree_conflicts_for_project` —
  canonical-pair rule (slug_a < slug_b emits), the
  `in_conflict_with` field rewrite, the iMessage tuple.
- `tests/test_queue_advancement.py`,
  `tests/test_worktree_conflict_scan.py` — existing tests that
  need their entry point swapped.

## Produce

1. **Failing tests first.** Adapt existing tests (don't write new
   ones for the rewire). Each existing test that drives
   `_advance_queue_for_project` directly switches to calling
   `cross_plan_rules.run_rules` after registering
   `queue_advancement_rule`. Same fixture, new entry point.

   Example:
   ```python
   # OLD:
   _advance_queue_for_project(project_root, cfg)

   # NEW:
   from end_of_line import cross_plan_rules
   plans = cross_plan_rules.load_plans_for_project(project_root, cfg)
   result = cross_plan_rules.run_rules(project_root, plans)
   self.assertEqual(result.rule_name, "queue_advancement")
   ```

2. **Implementation.**

   - `end_of_line/cross_plan_rules.py` add two registered rules:
     ```python
     def queue_advancement_rule(project_root: Path,
                                plans: list[ProjectPlan]) -> RuleResult | None:
         # Owns the busy-gate / freeze / absorb / abandon / pop chain
         # under queue.mutate(queue_path). Returns events + notifies
         # for the popped slug, OR None if nothing to do.
         ...

     def worktree_conflict_rule(project_root: Path,
                                plans: list[ProjectPlan]) -> RuleResult | None:
         # Computes the conflicting set, returns events +
         # KIND_HALTED notifies for newly-conflicting pairs where
         # this plan is the lexicographically-smaller slug.
         ...

     register_rule(queue_advancement_rule)
     register_rule(worktree_conflict_rule)
     ```
     The rule bodies are the existing logic, lifted from cli.py,
     minus the lock acquisition (the runner does that). The queue
     rule still takes `queue.mutate` itself because the pop is one
     atomic queue-lock operation; the runner handles only the
     resulting state-file writes.

   - `end_of_line/cli.py:cmd_tick_all` post-loop:
     ```python
     for project_root in distinct_projects:
         plans = cross_plan_rules.load_plans_for_project(project_root, cfg)
         result = cross_plan_rules.run_rules(project_root, plans)
         if result is not None:
             for kind, body in result.notifies:
                 notify.notify(cfg, kind, body, ...)
     ```

3. **Acceptance.**
   - Full suite green at the post-extract count (existing tests
     adapted, not added).
   - `grep -n "def _advance_queue_for_project" end_of_line/cli.py`
     returns the original definition (cleanup deletes it in phase 3
     — for now it's dead code).
   - `grep -n "queue_advancement\|worktree_conflict" end_of_line/cross_plan_rules.py`
     returns the two registered rules.

4. **Commit + complete.**
   - Title: `cross-plan-rules: phase migrate — queue advancement +
     worktree conflict scan as rules`
   - Stage: `end_of_line/cross_plan_rules.py`, `end_of_line/cli.py`,
     plus adapted test files.
   - `clu complete --plan cross-plan-rules --phase migrate --token
     <T>`.

## Failure modes to watch

- **Lock ordering inversion.** The queue advancement rule takes
  `queue.mutate(queue_path)` INSIDE its own body (queue lock outer)
  and the runner's `_apply` takes state locks AFTER the rule
  returns (state lock inner). Don't try to lift the queue lock
  into the runner — queue+state pair must serialize as
  queue-outer, state-inner. Read
  `docs/architecture.md` § "Lock ordering" before changing this.
- **Notify timing.** Fire notifies AFTER the state writes succeed
  (in `cmd_tick_all`, after `run_rules` returns). If the writes
  fail, the notifies shouldn't fire.
- **Cron-tick determinism.** `distinct_projects` order across tick
  runs must be stable. Sort the iteration if the registry doesn't
  already return a stable order.
- **Tests resetting the registry.** Any test that calls
  `run_rules` must reset `_RULES` in setUp / tearDown to avoid
  cross-test pollution. Mirror the pattern in extract's tests.
- **At-most-one invariant.** If both rules would emit on the same
  project in the same tick, only the first runs (queue
  advancement). The worktree scan runs next tick. This is
  intentional — don't "fix" it.
