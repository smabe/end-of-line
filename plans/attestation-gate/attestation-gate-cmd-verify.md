# attestation-gate-cmd-verify — `clu verify` command

You are phase `cmd-verify` of the `attestation-gate` plan. Add the
`clu verify` CLI command + handler. On success, stamps
`attestations.verify` via the helper from phase `schema-config`. On
failure, does not stamp.

## Locked decisions (do NOT re-litigate)

See `plans/attestation-gate/attestation-gate.md`. Summary:

- **Command executes the verify command itself** — no worker
  self-attestation here. clu CAN run the test suite, so it does,
  closing the lying-loophole.
- **HEAD captured BEFORE the command runs** so the worker can't
  commit mid-test and pass off a stale stamp.
- **Resolution order**: `cfg.resolved_verify_command()` — quality
  block → top-level `test_command` → None (error).
- **Worker variant requires `--token`** validated against the live
  claim. Operator variant works without `--token`.
- **Timeout**: 600s default. Configurable later if needed.
- **No state mutation on failure.** Exit non-zero with stderr tail.

## Read first

- `end_of_line/cli.py:3332-3347` — `cmd_complete` (similar
  "release-claim with token" shape).
- `end_of_line/cli.py:_verify_commit_shas` — pattern for git
  subprocess invocation.
- `end_of_line/state.py:validate_claim_token` (or equivalent) — token
  validation helper called by other worker callbacks.
- `end_of_line/state.py:stamp_attestation` (just added in
  schema-config phase).
- `end_of_line/dry_merge.py:_run_test_command` (if it exists) — pattern
  for shlex-split + subprocess + timeout for project verify commands.
- `end_of_line/cli.py:cmd_block` (cli.py:3610) — argparse pattern for
  worker callbacks (`--project`, `--plan`, `--phase`, `--token`).
- `tests/test_force_complete.py` — pattern for command-handler tests
  that use a tmp git repo + state file.

## Produce

1. **Failing tests first.** New file `tests/test_cmd_verify.py`:
   - `test_verify_runs_command_and_stamps_on_success` — set
     `test_command="true"` (always rc=0) → `clu verify` succeeds,
     state shows `attestations.verify` with correct `commit_sha`.
   - `test_verify_does_not_stamp_on_failure` — set
     `test_command="false"` (rc=1) → `clu verify` exits non-zero,
     state has no `attestations.verify`.
   - `test_verify_uses_quality_block_when_set` —
     `quality.verify_command="true"` + `test_command="false"` →
     verify succeeds (quality block wins).
   - `test_verify_falls_back_to_test_command` — no quality block,
     `test_command="true"` → verify succeeds.
   - `test_verify_errors_when_neither_configured` — neither set →
     exit `ExitCode.GENERIC` with message containing
     `"no verify command configured"`.
   - `test_verify_worker_token_validated` — worker passes a forged
     token → exit `ExitCode.CLAIM_MISMATCH`, no stamp.
   - `test_verify_operator_no_token_works` — operator omits
     `--token` → command runs, stamp lands.
   - `test_verify_captures_head_before_running` — `test_command` is a
     script that creates a new commit mid-run; stamp's `commit_sha`
     is the pre-run HEAD, not the post-run HEAD.
   - `test_verify_timeout_returns_non_zero` —
     `test_command="sleep 10"` with timeout=1 → non-zero exit, no
     stamp, stderr mentions timeout.
   - `test_verify_emits_event_on_success` — state's event log gains
     `EVENT_VERIFY_STAMPED` entry with kind=verify + commit_sha.

2. **Implementation.**
   - `end_of_line/state.py` — add event constant:
     ```python
     EVENT_VERIFY_STAMPED = "verify_stamped"
     ```
   - `end_of_line/cli.py` — new handler:
     ```python
     def cmd_verify(args, cfg: ProjectConfig, state_path: Path) -> int:
         cmd = cfg.resolved_verify_command()
         if not cmd:
             return _die(
                 ExitCode.GENERIC,
                 "no verify command configured "
                 "(set quality.verify_command or test_command in .orchestrator.json)",
             )
         # Capture HEAD BEFORE running so a mid-run commit can't sneak by.
         head = _git_head_sha(cfg.project_root)
         if not head:
             return _die(ExitCode.GENERIC, "could not resolve HEAD SHA")
         try:
             result = subprocess.run(
                 shlex.split(cmd),
                 cwd=str(cfg.project_root),
                 capture_output=True, text=True,
                 timeout=600,
             )
         except subprocess.TimeoutExpired:
             return _die(ExitCode.GENERIC, f"verify timed out after 600s: {cmd}")
         if result.returncode != 0:
             tail = result.stderr.strip().splitlines()[-20:]
             return _die(
                 ExitCode.GENERIC,
                 f"verify failed (rc={result.returncode}):\n" + "\n".join(tail),
             )
         with st.mutate(state_path) as data:
             # If --token provided, validate against live claim.
             if args.token:
                 st.validate_claim_token(
                     data, token=args.token, phase=args.phase,
                 )
             st.stamp_attestation(data, kind="verify", commit_sha=head)
             st.append_event(
                 data, st.EVENT_VERIFY_STAMPED,
                 phase=args.phase, commit_sha=head,
             )
         print(f"verified at {head}")
         return ExitCode.OK
     ```
   - Argparse: `p_verify = sub.add_parser("verify", ...)` with
     `--project`, `--plan`, `--phase`, `--token` (optional).
   - Reuse or add `_git_head_sha(project_root)` helper if not present.

3. **Acceptance.**
   - All 10 new tests green.
   - Full suite green.
   - `clu verify --help` shows the new command with the four args.
   - Smoke test in a fresh tmp repo: `clu verify --project /tmp/foo
     --plan p --phase x` runs `test_command` and stamps on rc=0.
   - `grep -n 'EVENT_VERIFY_STAMPED\|cmd_verify' end_of_line/cli.py end_of_line/state.py` returns ≥3 hits.

4. **Commit + complete.**
   - Title: `attestation-gate: phase cmd-verify — clu verify executes + stamps verify attestation (#55)`
   - Stage: `end_of_line/cli.py`, `end_of_line/state.py`, `tests/test_cmd_verify.py`.
   - `clu complete --plan attestation-gate --phase cmd-verify --token <T>`.

## Failure modes to watch

- **HEAD-before-run capture.** If you capture HEAD after the subprocess
  returns, a worker could do `git commit && python3 -m unittest && clu verify`
  and slip a commit past the gate. The pre-run capture is load-bearing.
  Test for this explicitly (one of the 10 listed above).
- **`shlex.split` on Windows.** Not a concern — this project is
  macOS/Linux-only.
- **Timeout exit code.** `subprocess.TimeoutExpired` leaves child
  processes alive. The test for timeout should verify clu exits
  non-zero AND no stamp is written; don't worry about reaping.
- **Token validation when `--token` omitted.** The operator variant
  skips token validation entirely. The worker variant requires it.
  Don't conflate the two paths.
- **Event-log spam.** Successful verify writes one
  `EVENT_VERIFY_STAMPED`. Failed verify writes nothing. Don't
  emit on failure — keeps the event log signal-to-noise high.
