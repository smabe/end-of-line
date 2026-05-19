# attestation-gate-cmd-attest — `clu attest --simplify` command

You are phase `cmd-attest` of the `attestation-gate` plan. Add the
`clu attest` CLI command + handler. v1 ships `--simplify` only — the
flag surface is extensible (future `--lint`, `--type-check` flavors
slot in without new commands).

## Locked decisions (do NOT re-litigate)

See `plans/attestation-gate/attestation-gate.md`. Summary:

- **Pure self-attestation, no command run.** clu cannot invoke
  `/simplify` (Claude-Code-side review skill); the worker's word is
  the only signal. Stamps `attestations.simplify` with current HEAD.
- **At-least-one flag required.** `clu attest` with no flag = error.
- **Token required.** Worker-only command. No operator-side variant —
  if the operator wants to bypass, they use `--skip-simplify` on
  complete.
- **`--simplify` is a `store_true` flag.** Future flavors will be
  additional `store_true` flags on the same command.

## Read first

- `end_of_line/cli.py:cmd_verify` (just added in cmd-verify phase) —
  reference shape for "stamp attestation with token validation".
- `end_of_line/state.py:stamp_attestation` (added in schema-config
  phase).
- `end_of_line/state.py:validate_claim_token` (or equivalent token
  validation helper).
- `end_of_line/cli.py:cmd_block` (cli.py:3610) — argparse pattern.
- `tests/test_cmd_verify.py` (from previous phase) — patterns for
  attestation-stamping tests.

## Produce

1. **Failing tests first.** New file `tests/test_cmd_attest.py`:
   - `test_attest_simplify_stamps_attestation` — worker calls
     `clu attest --simplify --token T` → state shows
     `attestations.simplify` with current HEAD SHA.
   - `test_attest_no_flag_errors` — `clu attest` with neither flag
     → exit `ExitCode.GENERIC` with message about needing a flag.
   - `test_attest_simplify_overwrites_prior_stamp` — call twice in a
     row (commit in between) → second stamp's `commit_sha` is the
     newer HEAD.
   - `test_attest_simplify_token_validated` — forged token → exit
     `ExitCode.CLAIM_MISMATCH`, no stamp.
   - `test_attest_simplify_requires_token` — no `--token` → argparse
     error (or `ExitCode.GENERIC` with clear message).
   - `test_attest_simplify_requires_live_claim` — token correct but
     no `current_claim` on the phase → error.
   - `test_attest_emits_event` — state's event log gains
     `EVENT_SIMPLIFY_STAMPED` entry.
   - `test_attest_extensible_flag_surface` — argparse accepts the
     `--simplify` flag; help text indicates more flags may land.

2. **Implementation.**
   - `end_of_line/state.py` — add event constant:
     ```python
     EVENT_SIMPLIFY_STAMPED = "simplify_stamped"
     ```
   - `end_of_line/cli.py` — new handler:
     ```python
     def cmd_attest(args, cfg: ProjectConfig, state_path: Path) -> int:
         if not args.simplify:
             return _die(
                 ExitCode.GENERIC,
                 "clu attest: at least one attestation flag required "
                 "(currently: --simplify)",
             )
         head = _git_head_sha(cfg.project_root)
         if not head:
             return _die(ExitCode.GENERIC, "could not resolve HEAD SHA")
         with st.mutate(state_path) as data:
             st.validate_claim_token(
                 data, token=args.token, phase=args.phase,
             )
             st.stamp_attestation(data, kind="simplify", commit_sha=head)
             st.append_event(
                 data, st.EVENT_SIMPLIFY_STAMPED,
                 phase=args.phase, commit_sha=head,
             )
         print(f"attested simplify at {head}")
         return ExitCode.OK
     ```
   - Argparse: `p_attest = sub.add_parser("attest", ...)` with
     `--project`, `--plan`, `--phase`, `--token` (required),
     `--simplify` (store_true). Help text: `"Attest that a quality
     pass ran on the current claim. Stamps current HEAD as the
     attested commit. Use --simplify after running /simplify;
     additional flavors land here."`.

3. **Acceptance.**
   - All 8 new tests green.
   - Full suite green.
   - `clu attest --help` shows `--simplify` flag + the four common args.
   - `grep -n 'EVENT_SIMPLIFY_STAMPED\|cmd_attest' end_of_line/cli.py end_of_line/state.py` returns ≥3 hits.

4. **Commit + complete.**
   - Title: `attestation-gate: phase cmd-attest — clu attest --simplify stamps simplify attestation (#55)`
   - Stage: `end_of_line/cli.py`, `end_of_line/state.py`, `tests/test_cmd_attest.py`.
   - `clu complete --plan attestation-gate --phase cmd-attest --token <T>`.

## Failure modes to watch

- **Token validation ordering.** Validate token BEFORE stamping. If
  stamping happens first (e.g. you forgot to wrap in `mutate` block),
  a forged token still gets the stamp written. The `with st.mutate`
  block + early `validate_claim_token` pattern is the contract.
- **Extensibility scaffolding.** Don't over-engineer for future
  flavors. Add `--simplify` as `store_true` and the dispatch
  conditional. Future flavors land as new flags and new branches —
  don't try to build a flag-to-kind mapping abstraction now.
- **Argparse `--token required`.** Use `required=True` on the
  argparse arg. If the user omits it, argparse exits with code 2
  before your handler runs.
- **`current_claim.phase_id` mismatch.** `validate_claim_token`
  checks both token AND phase. If the worker passes a `--phase`
  different from the live claim, that's a CLAIM_MISMATCH — same
  shape as forged token.
