# attestation-gate-complete-gate — `cmd_complete` refusal gate + threshold + skip flags

You are phase `complete-gate` of the `attestation-gate` plan. Add the
refusal gate to `cmd_complete`: refuses with `STATUS_TRANSITION` when
verify or (above-threshold) simplify attestation is missing or stale.
Add `--skip-verify` and `--skip-simplify` flags for operator bypass.

This is the load-bearing phase — without it, the callbacks from
cmd-verify and cmd-attest are advisory. With it, complete refuses
when the mandates were skipped.

## Locked decisions (do NOT re-litigate)

See `plans/attestation-gate/attestation-gate.md`. Summary:

- **Two gates in order**: verify (always), then simplify
  (threshold-gated).
- **Staleness rule**: `attestations.<kind>.commit_sha != current HEAD`.
- **Simplify threshold cumulative** across the claim's branch base
  to HEAD diff. Branch base resolution:
  - Worktree-mode: `state.get_worktree(data)["base_sha"]` (or
    equivalent — confirm exact key).
  - Non-worktree: `current_claim.head_sha_at_claim` (already captured
    at `claim_phase`).
- **Threshold check**: exceeds files OR lines → simplify required.
  No-commit phases (diff = 0) trivially pass.
- **`--skip-verify` and `--skip-simplify` independent flags**.
  Each emits `EVENT_OPERATOR_SKIP_*` audit event.
- **No-commit phases**: verify still required by default. The
  operator passes `--skip-verify` if the phase produced no commits
  and shouldn't be verified.

## Read first

- `end_of_line/cli.py:3332-3347` — current `cmd_complete` body. The
  gate inserts BEFORE `release_claim` and after `_verify_commit_shas`.
- `end_of_line/cli.py:_verify_commit_shas` — existing precondition.
  Model the gate on it.
- `end_of_line/state.py:get_worktree` (or similar) — confirm how to
  resolve the branch base SHA.
- `end_of_line/state.py:claim_phase` — confirm if/how
  `head_sha_at_claim` is captured (for non-worktree mode).
- `end_of_line/state.py:EVENT_*` constants — naming convention.
- `tests/test_force_complete.py` — pattern for cmd_complete-shaped
  handler tests.

## Produce

1. **Failing tests first.** New file `tests/test_complete_refusal.py`:

   **Verify gate**:
   - `test_complete_refused_when_no_verify_attestation` — phase has
     commits, no verify stamp → `ExitCode.STATUS_TRANSITION`, claim
     still live.
   - `test_complete_refused_when_verify_stale` — verify stamped
     before a subsequent commit → refused; error message names both
     SHAs.
   - `test_complete_accepts_fresh_verify_stamp` — verify stamped at
     current HEAD → complete succeeds.
   - `test_complete_with_skip_verify_bypasses_gate` —
     `--skip-verify` → complete succeeds without stamp; state has
     `EVENT_OPERATOR_SKIP_VERIFY` event.

   **Simplify gate**:
   - `test_complete_no_simplify_required_when_diff_below_threshold` —
     1 file × 10 lines (below default `{files:1, lines:30}`) → no
     simplify needed; complete succeeds (verify stamped).
   - `test_complete_simplify_required_when_files_exceed` — 2 files,
     5 lines each → exceeds files limit → refused without simplify
     stamp.
   - `test_complete_simplify_required_when_lines_exceed` — 1 file ×
     50 lines → exceeds lines limit → refused without simplify stamp.
   - `test_complete_simplify_stale_refused` — simplify stamped
     before a subsequent commit → refused.
   - `test_complete_with_skip_simplify_bypasses_gate` —
     `--skip-simplify` → succeeds without simplify stamp; state has
     `EVENT_OPERATOR_SKIP_SIMPLIFY` event.

   **Threshold override**:
   - `test_complete_honors_simplify_threshold_override` —
     `quality.simplify_threshold = {files: 5, lines: 100}` →
     3 files × 50 lines passes without simplify (above default,
     below override).
   - `test_complete_gate_everything_threshold` —
     `quality.simplify_threshold = {files: 0, lines: 0}` →
     even 1 file × 1 line requires simplify stamp.

   **Combined**:
   - `test_complete_both_skip_flags_independent` — `--skip-verify`
     and `--skip-simplify` together → succeeds with both events
     emitted.
   - `test_complete_no_commits_phase_still_requires_verify` —
     0 commits, no verify stamp → refused (verify is mandatory by
     default). With `--skip-verify` → succeeds.

2. **Implementation.**
   - `end_of_line/state.py` — add event constants:
     ```python
     EVENT_OPERATOR_SKIP_VERIFY = "operator_skip_verify"
     EVENT_OPERATOR_SKIP_SIMPLIFY = "operator_skip_simplify"
     ```
   - `end_of_line/cli.py` — gate helper:
     ```python
     def _compute_phase_diff(project_root: Path, base_sha: str) -> tuple[int, int]:
         """Return (files_changed, lines_changed) for diff base_sha..HEAD."""
         result = subprocess.run(
             ["git", "-C", str(project_root), "diff", "--numstat",
              f"{base_sha}..HEAD"],
             capture_output=True, text=True, timeout=10,
         )
         if result.returncode != 0:
             return (0, 0)  # no commits / no diff
         files = 0
         lines = 0
         for line in result.stdout.strip().splitlines():
             parts = line.split("\t")
             if len(parts) < 3:
                 continue
             files += 1
             try:
                 added = int(parts[0]) if parts[0] != "-" else 0
                 deleted = int(parts[1]) if parts[1] != "-" else 0
                 lines += added + deleted
             except ValueError:
                 continue
         return (files, lines)
     ```
   - `cmd_complete` — insert gate AFTER `_verify_commit_shas` and
     BEFORE `with st.mutate(state_path) as data: st.release_claim(...)`:
     ```python
     # Quality gates — verify + (threshold-gated) simplify.
     data_snapshot = st.load(state_path)
     claim = data_snapshot.get("current_claim") or {}
     attestations = claim.get("attestations") or {}
     head_sha = _git_head_sha(cfg.project_root)

     if not args.skip_verify:
         v = attestations.get("verify")
         if not v or v.get("commit_sha") != head_sha:
             return _die(
                 ExitCode.STATUS_TRANSITION,
                 f"verify gate: stamp missing or stale "
                 f"(stamped at {v.get('commit_sha') if v else 'never'}, HEAD is {head_sha}). "
                 f"Run `clu verify` before complete, or pass --skip-verify.",
             )

     if not args.skip_simplify:
         base_sha = _claim_base_sha(claim)  # worktree base or head_sha_at_claim
         files_changed, lines_changed = _compute_phase_diff(cfg.project_root, base_sha)
         t_files, t_lines = cfg.simplify_threshold_or_default()
         if files_changed > t_files or lines_changed > t_lines:
             s = attestations.get("simplify")
             if not s or s.get("commit_sha") != head_sha:
                 return _die(
                     ExitCode.STATUS_TRANSITION,
                     f"simplify gate: diff is {files_changed} files / "
                     f"{lines_changed} lines (threshold: {t_files}/{t_lines}). "
                     f"Stamp missing or stale "
                     f"(stamped at {s.get('commit_sha') if s else 'never'}, HEAD is {head_sha}). "
                     f"Run `clu attest --simplify` before complete, or pass --skip-simplify.",
                 )
     ```
   - Inside the existing `with st.mutate` block, emit skip events if
     flags set:
     ```python
     if args.skip_verify:
         st.append_event(data, st.EVENT_OPERATOR_SKIP_VERIFY,
                         phase=args.phase, operator=True)
     if args.skip_simplify:
         st.append_event(data, st.EVENT_OPERATOR_SKIP_SIMPLIFY,
                         phase=args.phase, operator=True)
     ```
   - Argparse: add `--skip-verify` and `--skip-simplify` flags
     (`store_true`) to the `complete` subparser.
   - Helper `_claim_base_sha(claim)`: returns worktree base if
     present, else `claim["head_sha_at_claim"]`. Confirm exact keys
     by reading `claim_phase` and `register_worktree` first.

3. **Acceptance.**
   - All 13 new tests green.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - Manual smoke: in a tmp repo, make 2 commits totaling 50 lines,
     try `clu complete` without verify stamp → STATUS_TRANSITION
     refusal. Stamp via `clu verify`, retry → still refused (over
     threshold, no simplify). Stamp via `clu attest --simplify`,
     retry → succeeds.
   - `grep -n 'EVENT_OPERATOR_SKIP_VERIFY\|EVENT_OPERATOR_SKIP_SIMPLIFY\|_compute_phase_diff' end_of_line/' returns ≥4 hits.

4. **Commit + complete.**
   - Title: `attestation-gate: phase complete-gate — refusal gate + threshold + skip flags (#55)`
   - Stage: `end_of_line/cli.py`, `end_of_line/state.py`, `tests/test_complete_refusal.py`.
   - **For THIS phase**: you'll need to call `clu verify` + (if diff
     above threshold) `clu attest --simplify` before complete — the
     gate now applies to you. This is intentional dogfooding.
   - `clu complete --plan attestation-gate --phase complete-gate --token <T>`.

## Failure modes to watch

- **Branch base resolution.** Worktree-mode uses
  `get_worktree(data)["base_sha"]` or similar — confirm the exact key
  by reading `worktree.register` first. Non-worktree falls back to
  `claim["head_sha_at_claim"]` if captured (check `claim_phase`).
  If `head_sha_at_claim` isn't captured today, you'll need a small
  addition to `claim_phase` — but verify before assuming.
- **Diff against HEAD when HEAD == base.** `git diff base..HEAD` with
  no commits returns empty → 0 files / 0 lines → below any non-zero
  threshold → simplify gate trivially passes. This is correct.
- **`numstat` for binary files.** Shows `-\t-\t<path>`. Helper handles
  with the `parts[0] != "-"` guard. Don't count binary lines.
- **Stale stamp wording.** Error message MUST name both SHAs
  (stamped-at vs current-HEAD). The worker reading the log needs to
  diagnose at a glance: "oh, I committed after stamping."
- **`current_claim` snapshot vs live read.** Read claim INSIDE
  `with st.mutate(state_path)`. Or read snapshot before
  (current draft), validate, then mutate. Either works; the snapshot
  approach in the draft avoids holding the lock during git diff.
  Pick one and be consistent.
- **Dogfooding.** When you complete THIS phase, the gate applies
  to you. Diff for this phase is large (cli.py + state.py + tests).
  Run `clu verify` (suite must be green) AND `clu attest --simplify`
  before `clu complete`. The gate refusing your own complete is
  the test of last resort.
