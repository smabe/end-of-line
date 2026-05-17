# worktree-blocker-followups

## Goal
Ship four follow-on issues from the clu-worktrees + clu-inbox cycles as a single
plan: #33 (blocker iMessage body), #28 (blocker lane-pin), #25 (worktree attach),
#34 (complete/archive worktree cleanup + cmd_worktree_gc upstream-aware retrofit).
Smallest-first; each phase = one issue = one commit (phase 4 splits into a
phase-0 refactor commit + the feature commit).

## Diagnosis
- **Hypothesis (#28 + #34, the bug-shaped issues):**
  - #28: `supervisor.tick`'s priority-7 for-loop at `supervisor.py:307` skips
    blocked phases via `phase_has_open_blocker` but advances to *successor*
    phases in the same plan. Per-phase guard ≠ per-lane guard.
  - #34: `cmd_complete` at `cli.py:2566-2577` literally has no worktree code
    path — it appends `EVENT_PHASE_COMPLETED` and returns. `cmd_archive` does
    not exist yet at all. `cmd_worktree_gc` removes worktrees for done/halted
    plans without checking that commits are reachable from `origin/<default>`.
- **Falsifiable test:**
  - #28: existing test `test_skips_phase_with_open_blocker` at
    `tests/test_supervisor.py:106-114` currently asserts B dispatches when
    A is blocked. The assertion IS the bug contract.
  - #34: grep `cmd_complete` body for `worktree` — zero matches.
- **Test result:** Both hypotheses confirmed by reading the code. Proceed.

## Non-goals
- Don't change the blocker schema (`options` stays `list[str]`, no label+value).
- Don't add a Slack render path. #11 owns that.
- Don't refactor the supervisor 8-priority chain into a registry.
- Don't change the `clu answer` CLI shape.
- Don't enforce an iMessage body-length limit globally — only fall back when
  `render_blocker` would exceed it.
- Don't add `EVENT_PLAN_ARCHIVED` unless `cmd_archive` semantics require it
  beyond the worktree-cleaned event.
- Don't retrofit `cmd_worktree_reattach` to use the new
  `autodetect_branch_and_base_ref` helper (reattach preserves existing values).

## Files to touch
- `end_of_line/notify.py` — `render_blocker` enrichment + soft-limit fallback (#33)
- `end_of_line/supervisor.py` — open-blocker lane guard before line 307 (#28)
- `end_of_line/cli.py` — `cmd_worktree_attach` + `autodetect_branch_and_base_ref`
  helper (#25); `_remove_worktree_and_branch` extracted (phase-0 refactor) +
  `_is_commit_reachable_from_origin` helper + `cmd_complete` worktree cleanup +
  new `cmd_archive` + `cmd_worktree_gc` upstream-aware retrofit (#34)
- `end_of_line/state.py` — `EVENT_WORKTREE_ATTACHED`, `EVENT_WORKTREE_CLEANED`,
  `EVENT_WORKTREE_RETAINED_AHEAD` constants
- `tests/test_notify_render.py` (new) — `render_blocker` tests (covers existing gap)
- `tests/test_supervisor.py` — flip `test_skips_phase_with_open_blocker`; add
  successor-not-dispatched test; cross-plan independence test
- `tests/test_worktree_attach.py` (new) — mirrors `test_worktree_reattach.py`
- `tests/test_complete_cleanup.py` (new) — cmd_complete worktree cleanup
- `tests/test_archive.py` (new) — cmd_archive happy path + ahead-of-origin retain
- `tests/test_worktree_gc.py` — extend with upstream-aware retain cases
- `docs/architecture.md`, `docs/contract.md`, `docs/reference.md` — per-phase sweeps
- `README.md` — only if `clu archive` becomes user-visible enough

## Failure modes to anticipate
- `git merge-base --is-ancestor` exits 1 for non-ancestor, ≠1 for invalid refs.
  Must distinguish "not reachable" from "origin/main missing" and retain in both.
- Worktree dir may have been `rm -rf`'d manually between init and complete.
  Cleanup must be idempotent against missing path.
- Branch may have been force-pushed; reachability MUST be checked by stored SHA,
  not by branch name resolution.
- Detached HEAD in worktree: `branch --show-current` returns empty. `attach`
  refuses with a clear message.
- Lane-pin must NOT pin across plans — `cmd_tick_all` iterates per-plan and
  each tick loads its own state file; preserve that.
- iMessage body length: no global limit exists, but oversize bodies risk
  AppleScript failure. Fallback path must be tested.
- Lock contention: `cmd_archive` mutates state under `st.mutate` — must not
  race with a mid-dispatch claim. Reuse the existing mutate window.
- Test pollution: any test touching `registry.register` (init, attach) MUST
  call `tests.isolate_registry(self, tmp_path)` in setUp (project mandate).
- `cmd_worktree_gc` retrofit must stay backwards-compatible: existing operators
  expect `gc` to remove worktrees for done plans even if `origin/main` is
  unreachable in their setup. Retain-and-warn, don't refuse.

## Done criteria
- Phase 1 (#33): `render_blocker` body includes question + numbered options +
  copy-pastable `clu answer --plan SLUG <blocker_id> <choice>`; falls back to
  short form + `clu blockers --plan SLUG` hint above soft limit. New
  `test_notify_render.py` covers short / long / no-options / oversize. Tests green.
- Phase 2 (#28): `supervisor.tick` returns idle when any blocker is open on the
  plan, regardless of phase. `test_skips_phase_with_open_blocker` flipped to
  assert idle. New test covers A→B→C with A blocked, asserts B not dispatched.
  Cross-plan independence test added. Tests green.
- Phase 3 (#25): `clu worktree attach --project P --plan S --path PATH` writes
  `state.worktree` with autodetected branch + base_ref (via new
  `autodetect_branch_and_base_ref` helper), emits `EVENT_WORKTREE_ATTACHED`.
  Refuses on existing record (→ `STATUS_TRANSITION`, points to `reattach`);
  refuses on invalid git path; refuses on detached HEAD. `clu doctor --worktree`
  reports `ok` post-attach. Mirrors `test_worktree_reattach.py` shape. Tests green.
- Phase 4a (#34 refactor): `_remove_worktree_and_branch` extracted from
  `_rollback_worktree` + `cmd_worktree_gc` as its own commit. No behavior change.
  Tests still green.
- Phase 4b (#34 feature): `_is_commit_reachable_from_origin` helper added.
  `cmd_complete` removes worktree+branch when stored SHA is reachable from
  `origin/<default-branch>`; emits `EVENT_WORKTREE_CLEANED` or
  `EVENT_WORKTREE_RETAINED_AHEAD` with unpushed SHAs. `cmd_archive` ships with
  same semantics at plan level + idempotent against already-clean state.
  `cmd_worktree_gc` retrofitted to retain-and-warn when commits ahead of origin.
  Tests green.
- Full suite green at end of each phase. Count rises from current 503 to ~525+.
- All four GitHub issues closeable (`Fixes #25 #28 #33 #34` split across commits).

## Parking lot
(empty)
