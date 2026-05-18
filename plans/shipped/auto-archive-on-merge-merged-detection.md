# auto-archive-on-merge-merged-detection — `state.is_branch_merged_into` git-ancestor wrapper

You are phase `merged-detection` of the `auto-archive-on-merge` plan.
Add a pure-function helper to `end_of_line/state.py` that returns
True iff a branch's HEAD is an ancestor of a base ref (default
`origin/main`). This is the load-bearing detection used by the
auto-archive rule (phase `auto-archive-rule`).

## Locked decisions (do NOT re-litigate)

See `plans/auto-archive-on-merge.md`. Summary:

- Lives in `end_of_line/state.py` (broad-import-safe; pure utility).
- Signature: `is_branch_merged_into(project_root: Path, branch: str,
  base_ref: str = "origin/main") -> bool`.
- Returns `False` (not exception) on missing refs / git errors /
  timeout — caller decides retry vs move on.
- No `git fetch`. Freshness is caller's responsibility.

## Read first

- `end_of_line/cli.py` — find `_commits_ahead_of_origin` (around line
  1060ish; `grep -n "_commits_ahead_of_origin" end_of_line/cli.py`).
  This is the existing subprocess-git pattern to mirror: timeout,
  capture_output, exit-code translation.
- `end_of_line/state.py` — module top to confirm `subprocess` is
  importable / already imported; existing pure utilities like
  `validate_slug`, `utcnow`, etc. for placement.
- `tests/__init__.py` — confirm `make_git_project` + `_git` helpers
  landed in the dry-merge-gate-cli-docs phase. Reuse them; don't
  re-implement.
- `tests/test_dry_merge.py` — pattern for tmp-git-repo tests using
  the shared helpers.

## Produce

1. **Failing tests first.** New file
   `tests/test_is_branch_merged_into.py`:
   - `test_returns_true_when_branch_is_ancestor` — tmp repo, feature
     branch off main, merge feature into main, `is_branch_merged_into
     (root, "feature", "main")` → True.
   - `test_returns_false_when_branch_ahead_of_base` — feature branch
     has commits past main (no merge) → False.
   - `test_returns_false_when_branch_missing` — pass nonexistent
     branch name → False (don't crash, don't raise).
   - `test_returns_false_when_base_ref_missing` — pass nonexistent
     base_ref → False.
   - `test_default_base_ref_is_origin_main` — default arg value
     verified via inspect or by passing no base_ref into a setup
     where `origin/main` is the only ref reachable.

2. **Implementation.** Add to `end_of_line/state.py`:
   ```python
   def is_branch_merged_into(
       project_root: Path,
       branch: str,
       base_ref: str = "origin/main",
   ) -> bool:
       """Return True iff `branch`'s HEAD is an ancestor of `base_ref`.

       Pure subprocess wrapper around `git merge-base --is-ancestor`.
       Returns False (not exception) when either ref doesn't exist
       or the git invocation times out — caller decides whether to
       retry or move on. No `git fetch` is performed.
       """
       try:
           result = subprocess.run(
               [
                   "git", "-C", str(project_root), "merge-base",
                   "--is-ancestor", branch, base_ref,
               ],
               capture_output=True,
               text=True,
               timeout=10,
           )
       except (subprocess.TimeoutExpired, OSError):
           return False
       return result.returncode == 0
   ```
   - Add `import subprocess` at module top if not already present.
   - Place near other pure utilities (e.g. after `validate_slug`).

3. **Acceptance.**
   - All 5 new tests green.
   - `python3 -m unittest discover -s tests` count +5 with zero
     regressions.
   - `python3 -c "from end_of_line.state import is_branch_merged_into;
     print(is_branch_merged_into)"` resolves the symbol.

4. **Commit + complete.**
   - `auto-archive-on-merge: phase merged-detection —
     state.is_branch_merged_into helper`
   - Stage: `end_of_line/state.py`,
     `tests/test_is_branch_merged_into.py`.
   - `clu complete --plan auto-archive-on-merge --phase
     merged-detection --token <T>`.

## Failure modes to watch

- **Git exit-code semantics.** `git merge-base --is-ancestor`
  returns 0 = is-ancestor, 1 = not-ancestor, 128 = ref doesn't
  exist (and prints to stderr). The test for missing refs catches
  the 128 case explicitly — assert `False`, not exception.
- **Timeout.** 10s is generous for an O(log N) operation. Don't
  raise on TimeoutExpired — return False.
- **Test isolation.** Use `tests.make_git_project` for tmp repos so
  XDG / git-config pollution doesn't leak. Don't write to the real
  CWD.
- **Symbol placement in state.py.** Don't put it inside a class;
  module-level function. Don't add it to `__all__` unless
  `__all__` already exists in state.py.
