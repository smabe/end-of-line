# auto-archive-on-merge-rule — cross-plan rule + `_perform_archive` extraction + notify kind

You are phase `auto-archive-rule` of the `auto-archive-on-merge`
plan. This phase delivers the actual automation: extract a shared
`_perform_archive` helper from `cmd_archive`, register a new
cross-plan rule that fires when a `STATUS_DONE` plan's branch lands
on `origin/main`, and add the `KIND_PLAN_AUTO_ARCHIVED` notify kind.

## Locked decisions (do NOT re-litigate)

See `plans/auto-archive-on-merge.md`. Summary:

- **Rule registered LAST** in `cross_plan_rules._RULES` (after
  `dry_merge_gate_rule`). One-tick-one-action invariant: at most
  one auto-archive per project per tick.
- **Eligibility filter:** `status == STATUS_DONE` AND
  `get_worktree(state) is not None` AND
  `is_branch_merged_into(project_root, branch)`.
- **First-eligible-wins in registry order.** Multiple eligible plans
  drain across consecutive ticks; rule returns RuleResult after the
  first fire.
- **Action = `_perform_archive(cfg, slug, *, unregister=True)`.**
  Extracted from current `cmd_archive` body. cmd_archive itself calls
  it with `unregister=False` to preserve existing CLI semantics.
- **Forward-compat opt-out:** read `getattr(cfg, "auto_archive",
  True)` — the real config field lands in phase
  `config-opt-out-docs`. Defaulting True via `getattr` lets this
  phase ship without the config change.

## Read first

- `end_of_line/cli.py:3218-3286` — `cmd_archive` body (current
  un-extracted form).
- `end_of_line/cli.py:1032+` — `_maybe_cleanup_worktree`
  (delegated by current cmd_archive).
- `end_of_line/cli.py:932+` — `_remove_worktree_and_branch` (called
  by `_maybe_cleanup_worktree`).
- `end_of_line/cross_plan_rules.py` — full module. Pay attention to:
  - `register_rule` + `_RULES` ordering.
  - `RuleResult` shape (`events_per_plan`, `notifies`,
    `field_updates_per_plan`).
  - `dry_merge_gate_rule` (most recently shipped) for deferred-import
    pattern: `from end_of_line.cli import ... # noqa: PLC0415`.
  - `load_plans_for_project` for the input shape.
- `end_of_line/notify.py` — find `KIND_GATE_DIRTY` for the pattern
  to mirror; `render_*` helpers near it.
- `end_of_line/state.py:STATUS_DONE`, `get_worktree`, and the
  freshly-landed `is_branch_merged_into` from phase 1.
- `end_of_line/registry.py` — `registry.unregister(project_root,
  slug)` signature.
- `tests/test_dry_merge_gate_rule.py` — most recent cross-plan rule
  test file; mirror its tmp-project setup pattern.

## Produce

1. **Failing tests first.** New file `tests/test_auto_archive_rule.py`:
   - `test_skipped_when_status_not_done` — STATUS_RUNNING plan with
     worktree merged → rule returns None.
   - `test_skipped_when_no_worktree_record` — STATUS_DONE plan with
     no worktree (already cleaned) → rule returns None.
   - `test_skipped_when_branch_not_merged` — STATUS_DONE + live
     worktree but branch HEAD ahead of origin/main (use a tmp git
     fixture or monkey-patch `is_branch_merged_into` to return
     False) → rule returns None.
   - `test_fires_when_done_and_branch_merged` — STATUS_DONE +
     worktree + branch merged into origin/main. Assert:
       * `_perform_archive` invoked with `unregister=True` (spy /
         capture).
       * Worktree path no longer in registry's worktrees list.
       * Plan file moved to `plans/shipped/` (or absent from
         `plans/`).
       * Registry entry pruned for this slug.
       * `RuleResult.notifies` includes `KIND_PLAN_AUTO_ARCHIVED`
         with `slug` and `branch` in the rendered message.
   - `test_idempotent_after_fire` — second `run_rules` call with the
     same setup (now post-archive: worktree gone, registry empty) →
     returns None.
   - `test_first_eligible_wins_in_registry_order` — TWO eligible
     plans in the project; one tick's `run_rules` archives ONE
     (registry-first); both remain visible until next tick.
   - `test_disabled_by_auto_archive_false_via_getattr` — stub a
     ProjectConfig-like object with `auto_archive=False` (test uses
     monkey-patch of `load_project_config`); rule returns None even
     when conditions met. (P3 adds the real config field; this test
     locks the forward-compat contract.)

2. **Implementation.**
   - **Extract `_perform_archive` in `end_of_line/cli.py`.** Pull
     the body of current `cmd_archive` (lines ~3218-3286) into:
     ```python
     def _perform_archive(
         cfg: ProjectConfig,
         plan: str,
         *,
         unregister: bool = False,
     ) -> tuple[dict | None, dict | None, bool]:
         """Shared archive engine. Returns (before, after, plan_moved).

         When `unregister=True`, also prunes the registry entry.
         Caller (cmd_archive or auto_archive_rule) owns user-facing
         output. Raises on git mv failure (cmd_archive translates
         to _die; rule logs + skips).
         """
         ...
     ```
     `cmd_archive` becomes a thin wrapper: validate slug, load
     state, refuse-if-RUNNING, call `_perform_archive(cfg, args.plan,
     unregister=False)`, print the existing "Archive {slug}: ..."
     messages, return exit code.
   - **`end_of_line/notify.py`** — add at the existing KIND_*
     cluster:
     ```python
     KIND_PLAN_AUTO_ARCHIVED = "plan_auto_archived"

     def render_plan_auto_archived(slug: str, branch: str) -> str:
         return (f"Auto-archived {slug} (branch {branch} merged "
                 f"to origin/main)")
     ```
   - **`end_of_line/cross_plan_rules.py`** — append rule:
     ```python
     def auto_archive_rule(project_root, plans):
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
                     p.slug, exc,
                 )
                 continue
             return RuleResult(
                 events_per_plan={},
                 rule_name="auto_archive",
                 notifies=[(
                     notify.KIND_PLAN_AUTO_ARCHIVED,
                     notify.render_plan_auto_archived(p.slug, branch),
                 )],
             )
         return None

     register_rule(auto_archive_rule)
     ```
   - **`docs/operations.md`** — brief 1-paragraph note in the
     plan-lifecycle section: "After merging clu/<slug> to main and
     pushing, the next cron tick auto-archives the plan (worktree
     removed, branch removed, registry entry pruned). Operator
     receives `KIND_PLAN_AUTO_ARCHIVED` notify confirming the
     cleanup." Full operator workflow + opt-out instructions land
     in phase `config-opt-out-docs`.

3. **Acceptance.**
   - All 7 new tests green.
   - `python3 -m unittest discover -s tests` zero regressions.
   - `grep -n "register_rule(auto_archive_rule)"
     end_of_line/cross_plan_rules.py` shows registration AFTER
     `register_rule(dry_merge_gate_rule)`.
   - Manual smoke (optional): in a tmp project, hand-craft a
     STATUS_DONE plan with merged branch + worktree, run a single
     `supervisor.tick`, observe worktree gone + notify emitted.

4. **Commit + complete.**
   - `auto-archive-on-merge: phase auto-archive-rule — cross-plan
     rule + _perform_archive helper + KIND_PLAN_AUTO_ARCHIVED`
   - Stage: `end_of_line/cli.py`, `end_of_line/cross_plan_rules.py`,
     `end_of_line/notify.py`, `docs/operations.md`,
     `tests/test_auto_archive_rule.py`.
   - `clu complete --plan auto-archive-on-merge --phase
     auto-archive-rule --token <T>`.

## Failure modes to watch

- **Refactor regression in `cmd_archive`.** Extraction must
  preserve the existing user-facing strings ("Archive {slug}: no
  worktree to clean.", "Archive {slug}: removed ...", "Archive
  {slug}: retained ..."). Existing tests for cmd_archive will catch
  drift — re-run them, don't skip.
- **Refuse-if-RUNNING stays in `cmd_archive`**, NOT in
  `_perform_archive`. The rule's eligibility filter (`status ==
  STATUS_DONE`) already excludes RUNNING; the helper shouldn't
  re-check, so it remains reusable from both paths.
- **Circular import.** `cross_plan_rules.py` already does deferred
  imports of cli helpers — mirror that pattern. Don't move
  `_perform_archive` into `cross_plan_rules.py`; keep it in cli.py
  where its siblings live.
- **Registry mutation under the rule's tick.** `registry.unregister`
  acquires its own JSON lock; should not deadlock with the
  cross-plan-rules tick. Verify by running the
  `test_fires_when_done_and_branch_merged` test in isolation +
  full-suite mode.
- **One-per-tick invariant.** Rule returns `RuleResult` after the
  first fire — supervisor's chain handles "one action per tick"
  automatically. Don't loop archiving all eligible plans in one
  call.
- **`getattr` default.** `getattr(cfg, "auto_archive", True)` —
  literal True, not the string "True". A misspelled default would
  be an always-False soft-disable bug.
- **Plan file already moved by /post-ship.** If the operator ran
  `/post-ship` manually (moving plans to `plans/archive/<slug>/`),
  the auto-archive's `git mv` step would no-op (plan_md not in
  plans/ root). That's safe — the helper's `if plan_md.exists()`
  guard handles it. Test
  `test_fires_when_done_and_branch_merged` should NOT pre-move the
  plan file; let `_perform_archive` do its job.
