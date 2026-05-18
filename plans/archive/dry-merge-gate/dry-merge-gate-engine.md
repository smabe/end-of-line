# dry-merge-gate-engine — `dry_merge.attempt_merge` pure-function engine

You are phase `engine` of the `dry-merge-gate` plan. Create
`end_of_line/dry_merge.py`: a pure-function engine that creates a
scratch worktree off `<base_ref>`, sequentially merges a list of
branches, optionally runs a test command, and returns a structured
result. No state-file I/O; no cross-plan rule logic — that ships in
phase `rule`.

## Locked decisions (do NOT re-litigate)

See `plans/dry-merge-gate.md`. Summary:

- `attempt_merge(project_root, base_ref, branches, test_command,
  *, timeout=300) -> MergeResult`. Module exports `MergeResult` +
  `attempt_merge`. No global state, no caching.
- Three outcomes: `clean`, `textual_conflict` (with `conflict_files:
  list[str]`), `suite_failed` (with `test_exit_code: int`,
  `stderr_tail: str`).
- Scratch worktree path via
  `tempfile.mkdtemp(prefix="clu-dry-merge-")`. `try/finally` always
  tears down: `git worktree remove --force <tmpdir>`. Leak-prevention
  is load-bearing.
- `test_command=None` → skip suite-run; report `clean` if textual
  merge clean. Operator can run textual-only mode without
  configuring a test runner.
- `shell=True` for test_command — operator-controlled per
  `.orchestrator.json` (lands in phase `cli-docs`); same trust model
  as `dispatch.command`.

## Read first

- `end_of_line/cli.py:932-1100` — `_remove_worktree_and_branch`,
  `_setup_worktree`, `_commits_ahead_of_origin`. Patterns for
  subprocess + git mechanics: timeout handling, capture_output,
  CalledProcessError translation, stderr-tail extraction.
- `end_of_line/dispatch.py` — pattern for `subprocess.run` with
  shell-true command, timeout, env handling.
- `end_of_line/state.py:127-150` — worktree record schema; informs
  what NOT to model (we don't persist the scratch worktree).
- `tests/` — any test that creates a tmp git repo. Find with `grep
  -rln "git init" tests/`. Reuse the helper if there's one; build
  one in `tests/_helpers.py` (or this test file) if not.

## Produce

1. **Failing tests first.** New file `tests/test_dry_merge.py`:
   - Helper `_make_tmp_repo()` → `tempfile.mkdtemp()`, `git init`,
     `git config user.email/user.name`, initial commit on `main`.
     Returns `Path`. (If a similar helper exists elsewhere, import
     instead.)
   - `test_attempt_merge_clean` — two branches with additive changes
     to separate files; no test_command. Assert
     `result.outcome == "clean"`, `result.conflict_files == []`,
     scratch worktree gone (`git worktree list` doesn't show it).
   - `test_attempt_merge_textual_conflict` — two branches edit the
     same line of the same file. Assert `result.outcome ==
     "textual_conflict"`, conflict file path in
     `result.conflict_files`, scratch worktree gone.
   - `test_attempt_merge_suite_failed_cmd_answer_regression` —
     the reproducer:
       * Branch A renames `def foo(blocker_id, idx):` → `def
         foo(answer, *, plan=None):` in `src/util.py`, updates the
         existing test caller to match.
       * Branch B adds a brand-new file `tests/test_b.py` calling
         `foo("b-1", 0)` (OLD signature). NO textual overlap with
         branch A's diff.
       * `test_command = "python3 -m unittest discover -s tests"`.
       * Textual merge succeeds (different files); suite fails.
     Assert `result.outcome == "suite_failed"`,
     `result.test_exit_code != 0`, `"unexpected"` or `"TypeError"`
     in `result.stderr_tail`.
   - `test_attempt_merge_scratch_worktree_always_cleaned_up` —
     induce a merge failure mid-sequence; assert worktree gone
     anyway. Use 3 branches, second one conflicts.
   - `test_attempt_merge_test_command_timeout` — test_command that
     `sleep 5`; timeout=1; assert `suite_failed` with non-zero
     exit_code or a clear `subprocess.TimeoutExpired` translation.

2. **Implementation.** Create `end_of_line/dry_merge.py`:
   ```python
   """Dry-merge engine for multi-plan parallel batches.

   See plans/shipped/dry-merge-gate.md and docs/architecture.md.
   Pure function: takes project_root + base_ref + list of branches
   + optional test_command, returns a MergeResult. No state I/O.
   """
   from __future__ import annotations

   import subprocess
   import tempfile
   from dataclasses import dataclass, field
   from pathlib import Path


   _OUTCOME_CLEAN = "clean"
   _OUTCOME_TEXTUAL_CONFLICT = "textual_conflict"
   _OUTCOME_SUITE_FAILED = "suite_failed"


   @dataclass
   class MergeResult:
       outcome: str
       conflict_files: list[str] = field(default_factory=list)
       test_exit_code: int | None = None
       stderr_tail: str = ""
       merged_branches: list[str] = field(default_factory=list)
       base_sha: str = ""


   def attempt_merge(
       project_root: Path,
       base_ref: str,
       branches: list[str],
       test_command: str | None = None,
       *,
       timeout: int = 300,
   ) -> MergeResult:
       ...
   ```
   - Use `git -C <project_root> rev-parse <base_ref>` to get `base_sha`.
   - `git -C <project_root> worktree add --detach <tmpdir> <base_ref>`.
   - For each branch:
       * `git -C <tmpdir> merge --no-ff --no-edit <branch>`; on
         non-zero, capture `git -C <tmpdir> diff --name-only
         --diff-filter=U` → `conflict_files`; return early with
         `textual_conflict` outcome (after teardown).
   - If textual_clean and `test_command`:
       * `subprocess.run(test_command, shell=True, cwd=tmpdir,
         capture_output=True, text=True, timeout=timeout)`.
       * Non-zero exit → `suite_failed`; capture last ~2000 chars
         of stderr (or stdout if stderr empty) → `stderr_tail`.
   - Always tear down in `finally`: `git -C <project_root> worktree
     remove --force <tmpdir>`. Catch + log teardown errors but don't
     mask the original outcome.

3. **Acceptance.**
   - All 5 new tests green.
   - `python3 -m unittest discover -s tests` count increased by 5
     with zero regressions.
   - `python3 -c "from end_of_line.dry_merge import attempt_merge,
     MergeResult"` works.
   - `find /tmp -maxdepth 1 -name "clu-dry-merge-*"` empty after
     test run (cleanup is real).

4. **Commit + complete.**
   - `dry-merge-gate: phase engine — dry_merge.attempt_merge +
     cmd_answer reproducer (#50)`
   - Stage: `end_of_line/dry_merge.py`, `tests/test_dry_merge.py`,
     and any new test helper file.
   - `clu complete --plan dry-merge-gate --phase engine --token <T>`.

## Failure modes to watch

- **Scratch worktree leak.** The `try/finally` is the entire safety
  story. If `git worktree remove --force` itself fails (corrupt
  state, file lock), log the error to stderr but DON'T raise — the
  caller's outcome must reach them. Test
  `test_attempt_merge_scratch_worktree_always_cleaned_up` covers
  the happy + mid-failure paths; ensure it actually verifies
  filesystem absence, not just `git worktree list` output.
- **`subprocess.TimeoutExpired` on test_command.** Translate to a
  `MergeResult.suite_failed` with a sentinel `test_exit_code = -1`
  and `stderr_tail = "<timeout after Ns>"`. Don't let the exception
  escape.
- **Git env pollution.** Tests run in tmp repos; ensure no global
  `~/.gitconfig` interference. Set `GIT_AUTHOR_NAME` +
  `GIT_AUTHOR_EMAIL` + `GIT_COMMITTER_*` env vars in the helper or
  via `git -c user.email=...`. Mirror whatever pattern existing
  tmp-repo tests use.
- **Merge commit identity.** `--no-ff --no-edit` creates a merge
  commit with default message. That's fine for the dry-merge — the
  commit doesn't persist (worktree is torn down). Don't bikeshed
  the merge message.
- **`base_ref` not yet pushed.** If `<base_ref>` is `main` and the
  branch isn't yet remote-pushed, `git rev-parse` still resolves
  locally. Don't add a `git fetch` call here — operator owns
  freshness; engine is pure.
