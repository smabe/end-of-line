# auto-archive-on-merge — automatic cleanup once a shipped plan lands on origin/main

Today, after a clu plan ships and the operator merges the branch to
main, the worktree + branch + registry entry linger as ghost state.
Operator has to remember `clu archive --plan <slug>` + `clu unregister
--all-archived` to clean up. This plan automates that.

A new cross-plan rule fires each cron tick. For every `STATUS_DONE`
plan with a live worktree whose branch is an ancestor of
`origin/main`, the rule runs the existing archive logic (worktree
teardown + plan-file move + registry unregister) and emits a
`KIND_PLAN_AUTO_ARCHIVED` notify. Worktrees ahead of origin/main
(operator wip) are retained; branches missing entirely are skipped.

End-to-end flow becomes: worker finishes → plan_done → dry-merge-gate
fires (clean) → operator does `git merge --no-ff clu/<slug> && git
push` (one human step) → next cron tick → auto-archive fires →
operator gets a notify confirming cleanup. No `clu archive` or
`clu unregister` calls needed.

## Locked design decisions

### Phase 1 — merged-detection
- **Pure function** `state.is_branch_merged_into(project_root,
  branch, base_ref="origin/main") -> bool`. Wraps `git merge-base
  --is-ancestor`. Returns False (not exception) on missing refs —
  caller decides retry vs move on.
- **Default base_ref = `origin/main`**, not `main`. Requires the
  operator to have explicitly pushed. Local-only "merged into main"
  doesn't trigger auto-archive.
- **No `git fetch`** in the helper. Freshness is the caller's
  responsibility; the rule fires per cron tick which is plenty of
  cadence.

### Phase 2 — auto-archive-rule
- **Cross-plan rule registered LAST** in `_RULES` (after
  `dry_merge_gate_rule`). One-tick-one-action invariant per ADR-0002:
  at most one auto-archive per project per tick. Multiple eligible
  plans drain across consecutive ticks.
- **Eligibility filter:** `status == STATUS_DONE` AND `get_worktree`
  non-None AND `is_branch_merged_into(branch)`. First-eligible-wins
  in registry order.
- **Action = `_perform_archive(cfg, slug, *, unregister=True)`** —
  extracted helper from `cmd_archive` body. cmd_archive calls it
  with `unregister=False` (preserves existing CLI semantics); rule
  calls it with `unregister=True` to also prune the registry.
- **Notify:** `KIND_PLAN_AUTO_ARCHIVED` emitted on each fire so the
  operator always sees the cleanup.
- **Forward-compat opt-out:** rule reads `getattr(cfg,
  "auto_archive", True)`. Phase 3 adds the real config field; phase 2
  doesn't depend on it.

### Phase 3 — config-opt-out-docs
- **`.orchestrator.json:auto_archive: bool`** field on
  `ProjectConfig`. Default `true`. Operators who want manual control
  set `false`.
- **Type-strict loading:** non-bool value → `ConfigError`. No
  truthy-coercion of strings.
- **Docs:** new "Auto-archive on merge" subsection in
  `docs/architecture.md` (cross-plan rule chain) +
  `docs/operations.md` (operator workflow + opt-out snippet) +
  `docs/contract.md` (schema for `auto_archive` config field +
  `KIND_PLAN_AUTO_ARCHIVED` notify kind).

## Non-goals

- Auto-merge to main on dry-merge-gate clean. Operator-approval
  boundary — same reasoning as dry-merge-gate's archive-coupling
  non-goal.
- Auto-archive plans without worktrees (already cleaned; nothing
  to do).
- Auto-archive in `STATUS_HALTED / PAUSED / BLOCKED / RUNNING` —
  only `STATUS_DONE`.
- Cross-project rules (per-project pattern, like the rest of
  `cross_plan_rules._RULES`).
- Auto-fetch `origin/main` before checking. Caller / operator owns
  freshness.

## Files touched

- `end_of_line/state.py` — P1 NEW `is_branch_merged_into` helper.
  **API hotspot:** public function exported from state module.
- `end_of_line/cli.py` — P2 modified — extract
  `_perform_archive(cfg, slug, *, unregister=True)` from
  `cmd_archive` body. **API hotspot:** internal helper signature
  used by both `cmd_archive` and the rule.
- `end_of_line/cross_plan_rules.py` — P2 modified — new
  `auto_archive_rule` registered LAST. **API hotspot:** `_RULES`
  ordering.
- `end_of_line/notify.py` — P2 modified — `KIND_PLAN_AUTO_ARCHIVED`
  + `render_plan_auto_archived(slug, branch)`.
- `end_of_line/config.py` — P3 modified — `ProjectConfig.auto_archive:
  bool = True`. **Schema hotspot:** `.orchestrator.json:auto_archive`.
- `docs/architecture.md` — P3 modified — new subsection on
  auto-archive rule under cross-plan chain.
- `docs/contract.md` — P3 modified — schema entries.
- `docs/operations.md` — P2 (brief) + P3 (full) — operator workflow.
- `tests/test_is_branch_merged_into.py` — P1 NEW.
- `tests/test_auto_archive_rule.py` — P2 NEW.
- `tests/test_config.py` — P3 modified — `auto_archive` field cases.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan auto-archive-on-merge --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| merged-detection | `auto-archive-on-merge-merged-detection.md` | `state.is_branch_merged_into` git-ancestor wrapper + 5 tests | 1h |
| auto-archive-rule | `auto-archive-on-merge-rule.md` | Extract `_perform_archive` helper; new cross-plan rule; `KIND_PLAN_AUTO_ARCHIVED` notify; brief operations.md note | 3h |
| config-opt-out-docs | `auto-archive-on-merge-config-docs.md` | `.orchestrator.json:auto_archive` field (default `true`) + full architecture.md/contract.md/operations.md updates | 2h |
