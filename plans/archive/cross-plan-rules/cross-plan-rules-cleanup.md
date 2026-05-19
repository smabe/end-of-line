# cross-plan-rules-cleanup — delete helpers, simplify, ADR comment

You are phase `cleanup` of the `cross-plan-rules` plan. Delete the
now-dead `_advance_queue_for_project` and
`_detect_worktree_conflicts_for_project` helpers, run `/simplify`
over the cumulative diff, and verify the ADR-0002 invariant is
documented at the top of `cross_plan_rules.py`.

## Locked decisions (do NOT re-litigate)

See `plans/cross-plan-rules.md`. Summary:

- The two per-project helpers in `cli.py` are dead after migrate;
  delete them.
- ADR-0002 reference comment must be at the top of
  `cross_plan_rules.py`.
- `/simplify` is mandatory.

## Read first

- `end_of_line/cli.py` post-migrate — confirm the helpers are no
  longer called from anywhere.
- `end_of_line/cross_plan_rules.py` — confirm the top-of-file
  comment references `docs/adr/0002-one-tick-one-action.md` (from
  extract).
- `docs/adr/0002-one-tick-one-action.md` — re-read so the
  reference comment matches the ADR's spirit.

## Produce

1. **Delete dead helpers.**
   - `end_of_line/cli.py`: remove `_advance_queue_for_project`,
     `_detect_worktree_conflicts_for_project`, and
     `_plans_for_project` (moved to `cross_plan_rules.py` as
     `load_plans_for_project`).
   - Update imports.

2. **Grep-verify invariants.**
   - `git grep -n "_advance_queue_for_project\|_detect_worktree_conflicts_for_project\|_plans_for_project" end_of_line/`
     → 0 matches.
   - `git grep -n "ADR-0002\|0002-one-tick-one-action" end_of_line/cross_plan_rules.py`
     → at least 1 match (the top-of-file reference).

3. **`/simplify` pass.** Run over the cumulative diff:
   `git diff main...HEAD -- end_of_line/cross_plan_rules.py
   end_of_line/cli.py`. Pay special attention to the shrunken
   `cmd_tick_all` — there may be more collapse opportunities now
   that the post-loop is gone.

4. **Acceptance.**
   - Both grep counts match.
   - Full suite green at the post-extract count + 0.
   - `/simplify` output recorded in commit body's "Under the
     hood" section.
   - `wc -l end_of_line/cli.py` smaller than pre-plan baseline
     (record the delta in the commit body).

5. **Commit + complete.**
   - Title: `cross-plan-rules: phase cleanup — delete helpers,
     /simplify, ADR-0002 reference`
   - Stage: `end_of_line/cli.py`, `end_of_line/cross_plan_rules.py`,
     plus any files `/simplify` touches.
   - `clu complete --plan cross-plan-rules --phase cleanup --token
     <T>`.

## Failure modes to watch

- **External imports of deleted helpers.** Grep the WHOLE repo
  (tests, examples, skills) before deleting. Tests are most likely
  to still import — fix those import sites first.
- **`/simplify` proposing to re-inline rules.** If `/simplify`
  suggests folding `queue_advancement_rule` back into
  `cmd_tick_all` "because it's only used once," decline — the
  whole point of the plan is the seam for future rules.
- **Docstrings.** `cli.py` docstrings or comments referencing the
  deleted helpers (e.g. "cmd_tick_all walks
  `_advance_queue_for_project` per project") must be updated.
- **ADR comment drift.** The top-of-file comment in
  `cross_plan_rules.py` should be ONE paragraph naming the
  invariant and pointing at the ADR. Don't expand it into a tutorial
  — the ADR file is the canonical statement.
