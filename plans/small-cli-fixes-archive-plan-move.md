# small-cli-fixes-archive-plan-move — `clu archive` plan-file git-mv (#31)

You are phase `archive-plan-move` of the `small-cli-fixes` plan. Extend
`cmd_archive` so that in addition to its existing worktree+branch cleanup
(shipped in #34 as `21c4fd5`), it also `git mv plans/<slug>.md
plans/shipped/<slug>.md`. Replaces the manual post-ship step the operator
runs today. Closes the final issue in this batch — `small-cli-fixes` itself
completes and the queue is drained.

Closes #31 AND closes #23 #32 (last phase of `small-cli-fixes`).

## Locked decisions (do NOT re-litigate)

See `plans/small-cli-fixes.md`. Summary:

- **Touch site:** `cmd_archive` (cli.py:2854-2891). After
  `_maybe_cleanup_worktree(...)` at line 2880, still inside the `mutate`
  window, add the git-mv step.
- **Idempotent:** if `plans/<slug>.md` doesn't exist (already moved or
  manually staged elsewhere), skip silently.
- **`plans/shipped/` creation:** `mkdir(parents=True, exist_ok=True)` if
  missing. Directory does NOT currently exist; first archive creates it.
- **git-mv failure:** surface via `_die(ExitCode.GIT_FAILURE, ...)` (or
  whatever existing exit code other git-mutation paths use — grep
  `subprocess.run.*"git"` for the pattern).
- **Plan file already at shipped/** — `git mv` refuses; let the error
  surface, don't silently overwrite.
- **Status print** updated to mention the plan-file move when it happened.

## Read first

- `end_of_line/cli.py:2854-2891` — current `cmd_archive` full body. The
  worktree-cleanup half (`_maybe_cleanup_worktree`) is at line 2877-2880.
  Your insertion goes after that, before the final status print.
- `end_of_line/cli.py` — `_remove_worktree_and_branch` body (somewhere
  around 2750-2820 based on the recent worktree-blocker-followups ship).
  Look at how it invokes `subprocess.run(["git", ...])` — match the same
  pattern (cwd, check, timeout, error surfacing).
- `end_of_line/config.py` — confirm how `cfg.plan_dir` is exposed. The
  master plan suggests `cfg.plan_dir_resolved`; if that doesn't exist,
  compose manually as `cfg.project_root / cfg.plan_dir`.
- `tests/` — search for `test_archive*.py`; exploration noted none exists.
  This phase creates it.

## Produce

1. **Failing tests first.** New `tests/test_archive.py` (or extend if
   exploration was wrong and it exists):
   - `test_archive_moves_plan_file` — set up a project with `plans/foo.md`
     and a state file in `STATUS_DONE` (or whatever non-RUNNING terminal
     state allows archive); run `clu archive --project ... --plan foo`,
     assert `plans/foo.md` no longer exists, `plans/shipped/foo.md`
     exists.
   - `test_archive_creates_shipped_dir_if_missing` — explicitly verify
     `plans/shipped/` is created on first archive.
   - `test_archive_idempotent_on_already_moved_plan` — pre-create
     `plans/shipped/foo.md`, remove `plans/foo.md`, run archive,
     expect exit 0 (no plan file to move).
   - `test_archive_git_mv_failure_surfaces` — set up state where `plans/
     foo.md` exists but is NOT tracked by git (so `git mv` fails); assert
     non-zero exit + stderr mentions git or the failed file.
   - `test_archive_status_print_mentions_plan_move` — capture stdout, run
     archive, assert the move is mentioned in the printed line.
   - Don't break worktree-cleanup behavior: include a smoke test that the
     existing worktree-cleanup half still works AFTER the plan-move step
     is added (the test `test_archive` from #34 should still pass).

2. **Implementation.**
   In `cmd_archive` (cli.py:2854-2891), after the worktree-cleanup
   `_maybe_cleanup_worktree(...)` call at line 2880, BEFORE the status
   print:
   ```python
   # Move the plan file to plans/shipped/ if it's still in plans/.
   plan_dir = cfg.project_root / cfg.plan_dir
   plan_md = plan_dir / f"{args.plan}.md"
   plan_moved = False
   if plan_md.exists():
       shipped_dir = plan_dir / "shipped"
       shipped_dir.mkdir(parents=True, exist_ok=True)
       try:
           subprocess.run(
               ["git", "mv", str(plan_md), str(shipped_dir / plan_md.name)],
               check=True,
               capture_output=True,
               text=True,
               cwd=cfg.project_root,
               timeout=30,
           )
           plan_moved = True
       except subprocess.CalledProcessError as exc:
           return _die(
               ExitCode.GIT_FAILURE,  # or whatever existing git-failure exit code is
               f"git mv failed for {plan_md}: {exc.stderr or exc}",
           )
       except subprocess.TimeoutExpired:
           return _die(
               ExitCode.GIT_FAILURE,
               f"git mv timed out for {plan_md}",
           )
   ```
   Update the existing status print at the end to include the plan-move
   when `plan_moved`:
   ```python
   move_note = f" Plan file moved to shipped/." if plan_moved else ""
   if before is None:
       print(f"Archive {args.plan}: no worktree to clean.{move_note}")
   elif after is None:
       print(
           f"Archive {args.plan}: removed {before['path']} "
           f"(branch {before['branch']}).{move_note}"
       )
   else:
       print(
           f"Archive {args.plan}: retained {before['path']} "
           f"(branch {before['branch']} ahead of origin).{move_note}"
       )
   ```

3. **Acceptance.**
   - All 5 new tests green (plus the pre-existing archive worktree-cleanup
     test still passes).
   - Full suite green.
   - Manual smoke: archive a real done plan (e.g. one of the older
     shipped ones if any are still in `plans/`); confirm `plans/<slug>.md`
     moves to `plans/shipped/`.

4. **Docs update.**
   - `docs/operations.md` — update the archive how-to to mention the
     plan-file move and the new `plans/shipped/` directory.

5. **Commit + complete.**
   - Structured commit: `small-cli-fixes: phase archive-plan-move —
     git mv plans/<slug>.md to shipped/ (closes #23 #32 #31)`.
   - Stage: `end_of_line/cli.py`, `tests/test_archive.py`,
     `docs/operations.md`.
   - `clu complete --plan small-cli-fixes --phase archive-plan-move
     --token <T>`. This is the LAST phase of the LAST plan in the
     batch — the queue is drained after this commit lands.

## Failure modes to watch

- **`cfg.plan_dir_resolved` may not exist** — fall back to
  `cfg.project_root / cfg.plan_dir`. The latter is documented at
  cli.py:2891 area or in config.py.
- **`subprocess.run([..., "git", "mv", ...])` cwd** — MUST be
  `cfg.project_root`, not the current process cwd (which could be the
  clu-managed worktree, not the canonical repo root). The state path
  lives in `cfg.project_root` regardless of dispatch cwd.
- **Plan file in subdir** — if `plan_dir` is nested (e.g.
  `"specs/plans"`), shipped/ should be a sibling of `<plan>.md`, not at
  repo root. `plan_md.parent / "shipped"` gets this right.
- **`git mv` on an uncommitted plan file** — the original commit that
  added the plan file (commit `3684928` for these three) must be on the
  current branch's history. Verify the worktree's branch has the plan
  file committed before testing.
- **The archive runs in clu's worktree for the plan** — when a real
  archive runs, cwd is the clu-managed worktree
  (`/Users/smabe/projects/end-of-line-<slug>`). The git-mv runs inside
  that worktree. After the move, the operator (or this phase's commit
  workflow) commits the move on the clu-managed branch. Eventually
  that branch merges to main, and `plans/shipped/<slug>.md` lands.
  Important: don't try to commit FROM `cmd_archive` itself — just
  git-mv leaves the move staged; the worker phase commits as part of
  its normal phase-commit step.
- **Be careful not to clobber the master plan's own pending move** —
  if the operator is archiving the LAST plan in this batch and the
  worker is dispatched in the clu-managed worktree for `small-cli-fixes`,
  the move applies to `plans/small-cli-fixes.md`. That's fine — it's
  the plan that just finished. The sub-plan files
  (`small-cli-fixes-*.md`) stay in plans/ unless this phase also moves
  them — they don't, by design (only the master moves; sub-plans are
  worker-internal artifacts). Confirm by checking what
  `bundle-inbound` etc. did in the shipped/ archive.
