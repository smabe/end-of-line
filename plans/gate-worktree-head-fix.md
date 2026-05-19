# gate-worktree-head-fix — worktree-aware HEAD resolution + tests

You are phase `fix` of the `gate-worktree-head` plan. Single phase.
Add `claim_git_root(data, cfg)` to `state.py`, swap 4 call sites in
`cli.py`, and add 3 worktree-mode tests. Closes #56.

## Locked decisions (do NOT re-litigate)

See `plans/gate-worktree-head.md`. Summary:

- Helper in `state.py` (public, no leading underscore — cli imports
  state as `st`).
- Returns `Path`, falls back to `cfg.project_root` when no worktree.
- 4 call sites in cli.py — three direct `_resolve_ref` calls + one
  inside `_compute_phase_diff` (which also gets its parameter renamed).
- Non-worktree behavior identical — existing tests must still pass.
- Don't refactor `_resolve_ref`; the callers are wrong.

## Read first

- `end_of_line/state.py:get_worktree` — pattern for worktree-record
  lookup. Helper goes next to it.
- `end_of_line/cli.py:3416` — `_claim_base_sha` already does
  "is the claim worktree-mode" branching. The new helper is its
  cousin. Don't deduplicate yet (only 2 sites).
- `end_of_line/cli.py:3374` — `_compute_phase_diff` signature.
- `end_of_line/cli.py:3438-3466` — `cmd_complete` gate block.
- `end_of_line/cli.py:3795` — `cmd_verify` HEAD capture.
- `end_of_line/cli.py:3826` — `cmd_attest` HEAD capture.
- `end_of_line/cli.py:1021` — `_resolve_ref(project_root, ref)`
  signature (don't change).
- `tests/test_cmd_verify.py`, `tests/test_cmd_attest.py`,
  `tests/test_complete_refusal.py` — find the existing fixture
  pattern (tmp repo + state file). Look for helpers like
  `_init_tmp_repo`, `_make_state`, or class-level `setUp` that
  builds the test bed.
- `end_of_line/worktree.py` (if it exists) or wherever worktree
  records are created — for the `current_claim`/state shape that
  includes a worktree record. Tests need to synthesize this.

## Produce

1. **Failing tests first.** Add one test to each of the three
   existing test files:

   `tests/test_cmd_verify.py`:
   ```python
   def test_verify_stamps_worktree_head_not_canonical(self) -> None:
       """In worktree-mode dispatch, the stamp must record the
       worktree's HEAD, not the canonical project's HEAD."""
       # Set up tmp canonical repo at commit C; add `git worktree
       # add -b clu/p` to a sibling dir; commit W on the worktree.
       # Synthesize state with current_claim having worktree record.
       # Run cmd_verify (with test_command="true").
       # Assert state's attestations.verify.commit_sha == W (worktree
       # HEAD), NOT C (canonical HEAD).
   ```

   `tests/test_cmd_attest.py`:
   ```python
   def test_attest_stamps_worktree_head_not_canonical(self) -> None:
       """Same as verify-side, for cmd_attest --simplify."""
   ```

   `tests/test_complete_refusal.py`:
   ```python
   def test_complete_refuses_when_stamp_is_canonical_but_worktree_advanced(self) -> None:
       """Worker commits W1 on worktree, stamps simplify at W1, then
       commits W2. cmd_complete must refuse — stamp is stale relative
       to worktree HEAD (W2). The pre-fix bug allowed this through
       because the gate compared canonical-HEAD vs stamped canonical-
       HEAD."""
   ```

   Use whatever git-worktree fixture pattern fits the existing test
   style. If none exists, build a minimal one: `git -C <canonical>
   worktree add -b clu/p <wt_path>`, then `git -C <wt_path> commit
   --allow-empty -m W1`, etc.

2. **Implementation.**

   `end_of_line/state.py` — add helper next to `get_worktree`:
   ```python
   def claim_git_root(data: dict, cfg) -> "Path":
       """Return the git context for the active claim.

       Worktree-mode plans dispatch into a per-plan worktree on a
       `clu/<slug>` branch; worker commits land there, not in the
       canonical repo. Falls back to canonical when no worktree
       record is present.
       """
       from pathlib import Path
       wt = get_worktree(data)
       if wt and wt.get("path"):
           return Path(wt["path"])
       return cfg.project_root
   ```
   (`cfg` is `ProjectConfig`; avoid the import cycle by leaving it
   un-annotated or using `TYPE_CHECKING` if state.py prefers strict
   typing.)

   `end_of_line/cli.py` — swap callsites. `cmd_complete` near line
   3438:
   ```python
   if not args.skip_verify or not args.skip_simplify:
       git_root = st.claim_git_root(data_snap, cfg)
       head_sha = _resolve_ref(git_root, "HEAD") or ""
       ...
       if not args.skip_simplify:
           base_sha = _claim_base_sha(claim, data_snap)
           if base_sha:
               files_changed, lines_changed = _compute_phase_diff(git_root, base_sha)
               ...
   ```

   `cmd_verify` near line 3795 — load state BEFORE resolving HEAD:
   ```python
   data_snap = st.load(state_path)
   git_root = st.claim_git_root(data_snap, cfg)
   head = _resolve_ref(git_root, "HEAD")
   if not head:
       return _die(ExitCode.GENERIC, "could not resolve HEAD SHA")
   # ... subprocess.run still uses cfg.project_root as cwd? NO —
   # use git_root, so the verify command runs in the worktree.
   try:
       result = subprocess.run(
           shlex.split(cmd),
           cwd=str(git_root),  # <— change from cfg.project_root
           ...
       )
   ```
   (Verify command should run in the worktree too — tests should
   exercise the worktree's test suite, not canonical's.)

   `cmd_attest` near line 3826 — same shape:
   ```python
   with st.mutate(state_path) as data:
       git_root = st.claim_git_root(data, cfg)
       head = _resolve_ref(git_root, "HEAD")
       if not head:
           return _die(ExitCode.GENERIC, "could not resolve HEAD SHA")
       st.assert_claim_match(data, args.token, args.phase)
       ...
   ```

   `_compute_phase_diff` near line 3374 — rename param + use it:
   ```python
   def _compute_phase_diff(git_root: Path, base_sha: str) -> tuple[int, int]:
       """Return (files_changed, lines_changed) for diff base_sha..HEAD
       in the given git context (canonical or worktree)."""
       result = subprocess.run(
           ["git", "-C", str(git_root), "diff", "--numstat",
            f"{base_sha}..HEAD"],
           ...
       )
       ...
   ```

3. **Acceptance.**
   - All 3 new tests green.
   - Full suite still green (1037 baseline → 1040 after additions).
   - `grep -n "claim_git_root\|_resolve_ref(cfg.project_root, .HEAD." end_of_line/cli.py` shows:
     - 3 hits of `claim_git_root` (cmd_complete, cmd_verify, cmd_attest)
     - 0 hits of `_resolve_ref(cfg.project_root, "HEAD")` (all swapped)
   - Smoke test: in a tmp worktree, commit something, call `clu
     attest --simplify`, inspect state — `attestations.simplify.commit_sha`
     equals the worktree HEAD.

4. **Commit + complete.**
   - Title: `gate-worktree-head: phase fix — worktree-aware HEAD resolution + tests (#56, closes #56)`
   - Stage: `end_of_line/state.py`, `end_of_line/cli.py`, `tests/test_cmd_verify.py`, `tests/test_cmd_attest.py`, `tests/test_complete_refusal.py`.
   - The gate now applies to you (after the fix). Run `clu verify`
     against the project's `test_command` (newly added — see
     companion config edit) and `clu attest --simplify` before
     `clu complete`. Self-dogfooding.
   - `clu complete --plan gate-worktree-head --phase fix --token <T>`.

## Failure modes to watch

- **The `data_snap` ordering in cmd_verify / cmd_attest.** The fix
  requires loading state BEFORE calling `_resolve_ref` (so the
  helper can read the worktree record). The original implementation
  resolved HEAD first, then loaded state. Make sure the new order
  doesn't break the existing tests that don't have a worktree
  record — `claim_git_root` falls back to canonical when no
  worktree, so this should be transparent.
- **`subprocess.run` cwd in cmd_verify.** Currently `cwd=str(cfg.project_root)`.
  Change to `cwd=str(git_root)` so the verify command runs in the
  worktree (where the worker's code changes are). The existing
  test `test_verify_runs_command_and_stamps_on_success` uses
  `test_command="true"` and doesn't depend on cwd — but
  `test_verify_captures_head_before_running` (if it exists) might.
  Check.
- **`_compute_phase_diff` param rename.** The argument-order change
  is technically internal (only `cmd_complete` calls it), but if
  any test calls it directly, update those too. `grep -n
  "_compute_phase_diff" tests/` to confirm.
- **Worktree-fixture cleanup.** `git worktree add` mutates the
  canonical repo's git dir. Tests must `git worktree remove` in
  tearDown OR build the canonical repo in a `tmp_path` so the
  whole tree is GC'd by unittest's tmpdir cleanup. Prefer the
  latter — simpler.
- **The state record for `current_claim` with a worktree.** Read
  how `claim_phase` records worktree info today (probably it
  stamps `current_claim.worktree_path` or similar). Tests need
  to synthesize the exact shape `get_worktree` expects to read.
  Check `state.py:get_worktree` body to get the field names right.
