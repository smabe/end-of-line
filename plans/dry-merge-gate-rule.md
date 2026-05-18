# dry-merge-gate-rule — cross-plan rule fires on plan_done, files follow-up on dirty

You are phase `rule` of the `dry-merge-gate` plan. Register a new
cross-plan rule in `end_of_line/cross_plan_rules.py` that fires when
≥2 sibling plans in the same `batch_id` are `STATUS_DONE` with live
worktree records. Rule calls `dry_merge.attempt_merge`, stamps a
`gate_result` field on each plan in the batch, and on dirty result
auto-writes a `merge-resolve-<batch>-<ts>` master + sub-plan to disk.

## Locked decisions (do NOT re-litigate)

See `plans/dry-merge-gate.md`. Summary:

- Rule registered LAST in `_RULES` (after `queue_advancement_rule`
  and `worktree_conflict_rule`). The 8-priority chain's at-most-one-
  effect invariant means the gate only runs when nothing else needs
  to fire that tick.
- **Eligibility:** plan must be `STATUS_DONE`, have a `batch_id`,
  and have a live worktree record (`st.get_worktree(data)`
  non-None). Archived plans drop out (worktree gone).
- **Idempotency key:** tuple of sorted sibling-branch HEAD SHAs.
  Stored in `gate_result.sha_key`. Same SHAs → skip re-run.
- **Granularity = per-completion.** Re-runs every plan_done with
  ≥2 eligible siblings. Bounded by N(N-1)/2 worst-case.
- **On clean:** stamp `gate_result = {outcome: "clean", sha_key,
  ts, batch_id}` on EACH plan in the eligible set. Notify only
  (no plan file written, no queue mutation). Use a new
  `notify.KIND_GATE_CLEAN`.
- **On dirty:** stamp `gate_result = {outcome: "textual_conflict"
  | "suite_failed", sha_key, ts, batch_id, follow_up_plan}`,
  notify (`notify.KIND_GATE_DIRTY`), write
  `plans/merge-resolve-<batch>-<YYYYMMDDhhmm>.md` master +
  `-fix.md` sub-plan. **Do NOT auto-queue** — operator approval.
- **Test command** comes from `cfg.test_command` (lands in phase
  `cli-docs`). For this phase: if the field doesn't exist yet on
  ProjectConfig, read it defensively via `getattr(cfg,
  "test_command", None)` so the phase-rule lands before the
  config field without import order pain.

## Read first

- `end_of_line/cross_plan_rules.py:1-260` — entire module; understand
  `register_rule`, `RuleResult`, `_apply`, `load_plans_for_project`.
- `end_of_line/cross_plan_rules.py:94-203` — `queue_advancement_rule`
  pattern: load plans, gate-check, take action, return RuleResult.
- `end_of_line/cross_plan_rules.py:206+` — `worktree_conflict_rule`
  pattern: scan plans for cross-plan condition, update
  `field_updates_per_plan`.
- `end_of_line/state.py:STATUS_DONE` + `st.get_worktree` (line 489).
- `end_of_line/dry_merge.py` — phase `engine` output; call this.
- `end_of_line/notify.py` — find `KIND_*` constants; add
  `KIND_GATE_CLEAN` + `KIND_GATE_DIRTY` + render helpers
  (`render_gate_clean(batch_id, slugs)` /
  `render_gate_dirty(batch_id, outcome, follow_up_path)`).
- `tests/test_cross_plan_rules.py` (find the actual filename via
  `ls tests/ | grep -i cross_plan`) — pattern for rule-test setup.

## Produce

1. **Failing tests first.** New file `tests/test_dry_merge_gate_rule.py`:
   - Setup helper builds N plans-with-state-files-and-worktrees in
     a tmp project root. Each plan can be parameterized with
     `status`, `batch_id`, branch HEAD SHA.
   - `test_gate_skipped_when_single_done_sibling` — 1 plan DONE in
     batch → rule returns None.
   - `test_gate_skipped_when_no_batch_id` — 2 plans DONE, both
     `batch_id=None` → rule returns None.
   - `test_gate_skipped_when_one_plan_archived` — 2 plans, same
     batch, both DONE, but one has no worktree record → rule
     returns None (treat as already-out).
   - `test_gate_clean_stamps_gate_result_on_each_plan` — 2 plans
     DONE, same batch, both with worktrees that merge clean →
     each plan's state has `gate_result.outcome == "clean"` and
     the same `sha_key`. `notifies` includes `KIND_GATE_CLEAN`.
     No plan files written to `plans/`.
   - `test_gate_dirty_writes_followup_plan_pair_not_queued` —
     stub `attempt_merge` to return `textual_conflict`. Assert:
       * `plans/merge-resolve-<batch>-<ts>.md` exists (master).
       * `plans/merge-resolve-<batch>-<ts>-fix.md` exists (sub-plan).
       * Each plan's `gate_result.outcome ==
         "textual_conflict"`, `gate_result.follow_up_plan` ==
         master filename.
       * `notifies` includes `KIND_GATE_DIRTY`.
       * Queue file unchanged (NOT auto-queued).
   - `test_gate_idempotent_on_same_shas` — call run_rules twice
     with same sibling SHAs; second call returns None (no
     re-stamp, no second notify).
   - `test_gate_re_runs_after_new_sibling_done` — third plan
     completes; gate fires again with 3-branch merge; new
     `sha_key` differs from prior; new `gate_result` overwrites.

2. **Implementation.**
   - `end_of_line/notify.py`: add
     ```python
     KIND_GATE_CLEAN = "gate_clean"
     KIND_GATE_DIRTY = "gate_dirty"

     def render_gate_clean(batch_id: str, slugs: list[str]) -> str:
         return f"Batch {batch_id} dry-merge clean: {', '.join(slugs)}"

     def render_gate_dirty(batch_id, outcome, follow_up_path):
         return (f"Batch {batch_id} dry-merge DIRTY ({outcome}). "
                 f"Follow-up: {follow_up_path}")
     ```
   - `end_of_line/cross_plan_rules.py`:
     ```python
     from end_of_line import dry_merge
     from datetime import datetime, timezone

     def dry_merge_gate_rule(project_root, plans):
         cfg = load_project_config(project_root)
         # Group by batch_id where eligible
         eligible: dict[str, list[ProjectPlan]] = {}
         for p in plans:
             if p.state.get("status") != st.STATUS_DONE: continue
             bid = p.state.get("batch_id")
             if not bid: continue
             wt = st.get_worktree(p.state)
             if not wt: continue
             eligible.setdefault(bid, []).append(p)

         for bid, group in eligible.items():
             if len(group) < 2: continue
             branches = [st.get_worktree(p.state)["branch"]
                         for p in group]
             # Resolve branch HEAD SHAs for idempotency key.
             shas = sorted(
                 _git_rev_parse(project_root, b) for b in branches
             )
             sha_key = "|".join(shas)
             if any(p.state.get("gate_result", {}).get("sha_key")
                    == sha_key for p in group):
                 continue

             test_cmd = getattr(cfg, "test_command", None)
             result = dry_merge.attempt_merge(
                 project_root, base_ref="main", branches=branches,
                 test_command=test_cmd,
             )

             ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
             field_updates = {}
             notifies = []
             gate_result_base = {
                 "sha_key": sha_key,
                 "ts": st.utcnow(),
                 "batch_id": bid,
                 "outcome": result.outcome,
             }
             if result.outcome == "clean":
                 for p in group:
                     field_updates[p.state_path] = {
                         "gate_result": gate_result_base,
                     }
                 notifies.append((
                     notify.KIND_GATE_CLEAN,
                     notify.render_gate_clean(
                         bid, [p.slug for p in group]),
                 ))
             else:
                 fu_master, fu_sub = _write_followup_plan_pair(
                     cfg, bid, ts, result, group,
                 )
                 gr = {**gate_result_base,
                       "follow_up_plan": fu_master.name}
                 for p in group:
                     field_updates[p.state_path] = {"gate_result": gr}
                 notifies.append((
                     notify.KIND_GATE_DIRTY,
                     notify.render_gate_dirty(
                         bid, result.outcome, str(fu_master)),
                 ))

             return RuleResult(
                 events_per_plan={},
                 rule_name="dry_merge_gate",
                 notifies=notifies,
                 field_updates_per_plan=field_updates,
             )
         return None

     register_rule(dry_merge_gate_rule)
     ```
   - `_write_followup_plan_pair(cfg, batch_id, ts, result, group) ->
     tuple[Path, Path]`: writes the master + sub-plan files to
     `cfg.project_root / cfg.plan_dir`. Master body lists the
     failing files / stderr_tail / sibling slugs. Sub-plan body:
     "Resolve conflicts in `<files>` and/or fix failing tests.
     Commit + push. `clu complete --plan
     merge-resolve-<batch>-<ts> --phase fix --token <T>`."
   - `_git_rev_parse(project_root, branch) -> str`: subprocess
     wrapper, capture stdout, strip.
   - Register the rule LAST in `_RULES` (file end).

3. **Acceptance.**
   - All 7 new tests green.
   - `python3 -m unittest discover -s tests` zero regressions.
   - `grep -n "dry_merge_gate_rule" end_of_line/cross_plan_rules.py`
     shows registration after `worktree_conflict_rule`.
   - Manual smoke: in a tmp project, hand-craft two DONE plans with
     same batch_id + live worktrees, run a single
     `supervisor.tick`, observe `gate_result` written.

4. **Commit + complete.**
   - `dry-merge-gate: phase rule — cross-plan gate fires on
     plan_done, files follow-up on dirty (#50)`
   - Stage: `end_of_line/cross_plan_rules.py`, `end_of_line/notify.py`,
     `tests/test_dry_merge_gate_rule.py`.
   - `clu complete --plan dry-merge-gate --phase rule --token <T>`.

## Failure modes to watch

- **Rule ordering.** Registration order in `_RULES` IS the priority.
  `dry_merge_gate_rule` must be LAST so queue advancement and
  worktree-conflict detection happen first per ADR-0002 (one tick =
  one action). Test by reading
  `end_of_line/cross_plan_rules._RULES.index(dry_merge_gate_rule)`
  and asserting it's the largest index.
- **Auto-queue temptation.** The rule writes plan files but MUST NOT
  call `queue.mutate` to enqueue them. Operator approval boundary —
  mirrors clu-plan SKILL.md ship pattern. If a test asserts
  not-queued, run it after asserting written-to-disk so the
  ordering of side effects is clear.
- **Idempotency drift.** `sha_key` derivation must be deterministic
  across runs. Sort branches before computing. Don't include
  timestamps. Test with two consecutive `run_rules` calls.
- **Filename collisions.** `merge-resolve-<batch>-<YYYYMMDDhhmm>.md`
  — minute precision means two gate runs within the same minute
  would collide. Acceptable: idempotency check should prevent
  re-runs in that window. If a test wants two distinct files,
  freeze time or accept the second one no-ops.
- **`test_command` not yet on ProjectConfig.** Use `getattr(cfg,
  "test_command", None)` for forward-compat with phase `cli-docs`.
  The field lands officially there.
- **Live worktree but missing branch.** Worktree record can lag
  reality. If `_git_rev_parse` fails for a branch, drop that plan
  from the eligible set (log + continue) rather than crashing the
  rule chain.
