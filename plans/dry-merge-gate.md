# dry-merge-gate — semantic-conflict gate for multi-plan parallel batches (closes #50)

When N plans drain in parallel via `clu queue add` with `--worktree`,
each worker reads the codebase as of queue-time HEAD and is blind to
sibling workers' changes. Textual auto-merge usually succeeds, but
**hidden semantic conflicts** — one plan changes a public API and
another plan's brand-new test/code calls it by its old contract — slip
through silently and only surface at runtime.

Canonical incident: 2026-05-18 batch, merge SHA `1816c0f`. plan-locator
changed `cmd_answer`'s argparse signature; blocker-lifecycle's
brand-new test file called the OLD signature; merge auto-succeeded;
suite failed at runtime with `argparse: unrecognized arguments: 0`.

This plan adds a cross-plan rule that fires on `plan_done`: when ≥2
sibling plans in the same `--batch` are DONE, clu dry-merges their
branches in a scratch worktree and (optionally) runs the test suite.
Dirty result → auto-files a `merge-resolve-<batch>-<ts>` 1-phase
follow-up plan. Clean → no-op. Ships in 4 phases: schema → engine →
rule → CLI/docs.

## Locked design decisions

### Phase 1 — schema
- **Batch identity = explicit only.** `clu queue add --batch <name>
  <slug-1> <slug-2>` stamps each entry with `batch_id` (validated by
  `st.validate_slug`). No `--batch` → `batch_id=null` → gate never
  fires. Implicit-by-time-window detection deferred (non-goal).
- **`batch_id` propagates to plan state** at queue-pop time via
  `queue_advancement_rule._normal_pop_path` so the gate can group
  plans without re-reading queue history.
- **Worker `clu queue add --token` rejects `--batch`** — operator-only
  flag; workers can't tag themselves into a batch.
- **Schema bump unnecessary.** Additive `batch_id: str | None` field on
  queue entries, history entries, and plan state. `st.load` tolerates
  unknown / new fields. Verify this in tests before assuming.

### Phase 2 — engine
- **Pure-function module** `end_of_line/dry_merge.py` exporting
  `attempt_merge(project_root, base_ref, branches, test_command,
  *, timeout=300) -> MergeResult`. No state-file I/O; no cross-plan
  rule logic; just merges + (optionally) runs tests.
- **MergeResult outcomes:** `clean | textual_conflict | suite_failed`
  with conflict_files, test_exit_code, stderr_tail, merged_branches,
  base_sha for downstream consumers.
- **Scratch worktree** via `git worktree add --detach
  $(mktemp -d --prefix=clu-dry-merge-) <base_ref>`. Sequential
  `git merge --no-ff --no-edit <branch>` for each batch member.
  `try/finally` always tears down — leak prevention is load-bearing.
- **Reproducer test** synthesizes the cmd_answer regression: two
  branches in a tmp repo where A renames a function and B's new test
  calls the old name. Textual merge succeeds; suite fails. Locks
  the contract.

### Phase 3 — rule
- **Trigger = cross-plan rule on plan_done**, registered AFTER
  `queue_advancement_rule` and `worktree_conflict_rule` in
  `cross_plan_rules._RULES`. Fires when ≥2 plans in the same
  `batch_id` are `STATUS_DONE` AND have live worktree records.
- **Granularity = per-completion.** Each plan_done with ≥2 DONE
  siblings re-runs the gate against the current set. N(N-1)/2 runs
  worst-case for batch of N. Idempotency key: tuple of sorted
  sibling branch HEAD SHAs — same SHAs → no re-run.
- **Archived plans drop out** of the eligible set (worktree record
  gone). Gate only runs against plans whose branches still exist.
- **On clean:** stamp `gate_result` on each plan, emit notify only if
  this is the first non-trivial run (≥2 siblings, transitioning to
  green), no plan files written, no queue mutation.
- **On dirty:** stamp `gate_result`, emit notify, write
  `plans/merge-resolve-<batch>-<YYYYMMDDhhmm>.md` master +
  `-fix.md` sub-plan to disk. **Do NOT auto-queue** — operator
  approval boundary mirrors clu-plan's ship pattern.

### Phase 4 — cli-docs
- **`clu integrate --project P --batch B [--branches a,b,c]
  [--no-suite] [--base-ref REF]`** operator-on-demand command for
  replay-after-fix or stuck batches. Wraps the engine; does NOT fire
  the rule logic (no plan-state mutation, no follow-up emission).
  Useful exit-coded for shell scripting.
- **`.orchestrator.json:test_command`** — optional project config
  field. Absent → textual-merge-only mode (still catches the literal
  conflict class). Present → run command inside scratch worktree;
  shell-execution surface documented as operator-owned.

## Non-goals

- Implicit batch detection by time-window. Defer until evidence of
  operators forgetting `--batch` repeatedly.
- Auto-coupling gate to `clu archive`. Gate runs on plan_done; archive
  flow unchanged. Branches must still exist for merging.
- Auto-merging branches to main on green. Operator still owns the
  merge-to-main step.
- Auto-queueing the follow-up plan. Rule writes the plan files; the
  operator runs `clu queue add merge-resolve-<batch>-<ts>` manually.
- "Files touched" companion discipline in master plans — already
  shipped via `/clu-plan` SKILL.md update in this same commit cycle.

## Files touched

- `end_of_line/queue.py` — P1 modified — `batch_id` flows through
  queue + history entries. No public-function signature changes;
  data-shape additive.
- `end_of_line/state.py` — P1, P3 modified — P1 adds `batch_id` to
  `empty_state` baseline; P3 adds `gate_result` field to plan state.
  **API hotspot:** any code asserting on plan state keys must tolerate
  these new fields.
- `end_of_line/config.py` — P4 modified — `ProjectConfig.test_command:
  str | None`. **Schema hotspot:** `.orchestrator.json` gains
  `test_command` key.
- `end_of_line/cli.py` — P1, P4 modified — P1 adds `--batch` to
  `queue_add` argparse + rejects on worker callback; P4 adds
  `cmd_integrate` + argparse. **API hotspot:** `cmd_queue_add` /
  `_cmd_queue_add_worker` arg surface.
- `end_of_line/dry_merge.py` — P2 NEW — module surface:
  `attempt_merge(...)` + `MergeResult` dataclass.
- `end_of_line/cross_plan_rules.py` — P3 modified — new
  `dry_merge_gate_rule` registered last. **API hotspot:** `_RULES`
  list ordering matters; place AFTER `worktree_conflict_rule`.
- `docs/contract.md` — P4 modified — schema docs for `batch_id`,
  `gate_result`, `test_command`.
- `docs/architecture.md` — P4 modified — new subsection on the
  multi-plan integration gate under the cross-plan rule chain.
- `docs/operations.md` — P4 modified — `clu integrate` CLI reference;
  operator workflow for multi-plan batches.
- `tests/test_queue_batch_schema.py` — P1 NEW.
- `tests/test_dry_merge.py` — P2 NEW.
- `tests/test_dry_merge_gate_rule.py` — P3 NEW.
- `tests/test_cmd_integrate.py` — P4 NEW.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan dry-merge-gate --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| schema | `dry-merge-gate-schema.md` | `--batch` flag on `queue add`; `batch_id` in queue+history+state schemas; no behavior change | 2h |
| engine | `dry-merge-gate-engine.md` | `end_of_line/dry_merge.py` pure-function engine + `cmd_answer`-drift reproducer test | 3h |
| rule | `dry-merge-gate-rule.md` | Cross-plan rule fires on plan_done; calls engine; writes (not queues) follow-up plan on dirty | 3h |
| cli-docs | `dry-merge-gate-cli-docs.md` | `clu integrate` operator override + `.orchestrator.json:test_command` + docs (closes #50) | 2h |
