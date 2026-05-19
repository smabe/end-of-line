# attestation-gate — programmatic enforcement of /simplify + verify mandates (closes #55, supersedes #10)

The clu-phase SKILL.md asks workers to (a) re-run the project's primary
verification command before `clu complete` and (b) run `/simplify` on
non-trivial diffs. Both are contractual-only today — workers can lie or
forget and clu accepts the complete. The operator has observed enough
drift to want programmatic enforcement.

The plumbing for both is the same shape: one attestation slot on
`current_claim`, two worker callbacks that stamp it, one refusal gate
in `clu complete`. This plan ships all of it in one bundle, replacing
the parked-scope of #10 with a broader implementation.

## Locked design decisions

### Phase 1 — schema-config
- **State slot is a map**, not two parallel fields:
  `current_claim.attestations = {verify?: {at, commit_sha}, simplify?: {at, commit_sha}}`.
  Extensible to future flavors (`lint`, `type-check`) without schema
  bump. Each stamp = `{"at": ISO8601_Z, "commit_sha": str}`.
- **Stamp staleness** = `current HEAD != commit_sha`. Single-rule check,
  applied in `cmd_complete`. No timestamp expiry.
- **Config block name = `quality`**, sibling of `notify` / `dispatch`.
  Fields: `verify_command: str | None`, `simplify_threshold: {files: int, lines: int} | None`.
- **Verify command fallback**: `quality.verify_command` → top-level
  `test_command` → error. Top-level `test_command` already exists for
  the dry-merge gate; keeping the fallback avoids forcing every project
  to duplicate the config.
- **Simplify threshold default = `{files: 1, lines: 30}`** — exceeding
  EITHER triggers the gate. Matches the convention in
  clu-phase SKILL.md:124. `{files: 0, lines: 0}` = gate-everything.
- **Schema bump unnecessary.** Both `attestations` and `quality` are
  additive. Verify `st.load` tolerates the new field before assuming.

### Phase 2 — cmd-verify
- **`clu verify` runs the command itself** (no worker self-attestation
  for verify — clu CAN execute the test command, so it does, and lying
  becomes impossible). On rc=0 stamps `attestations.verify`. On rc!=0:
  no stamp, exit non-zero, stderr tail surfaced.
- **Worker variant takes `--token`** validated against the live claim.
  Operator variant works without (for manual re-verification or rescue).
- **Subprocess invocation**: `subprocess.run(shlex.split(cmd), cwd=project_root, capture_output=True, text=True)`.
  Matches the `dry_merge.attempt_merge` precedent. Default timeout 600s;
  configurable later if a project needs more.
- **HEAD captured BEFORE the command runs** (so a worker can't commit
  mid-test and pass off a stale verify). Stamp uses that pre-run SHA.
- **No state mutation on failure.** Failure = exit non-zero with the
  command's stderr tail; state file untouched. Operator can re-run.

### Phase 3 — cmd-attest
- **`clu attest --simplify` is pure self-attestation** — no command
  execution. clu cannot run /simplify (it's a Claude-Code-side review
  skill), so the worker's word is the only signal. Stamps
  `attestations.simplify` with current HEAD.
- **Extensible flag surface**: `clu attest --simplify` today; future
  `--lint`, `--type-check` flavors land as new flags on the same
  command. Each flag stamps a different key in `attestations`.
- **At-least-one flag required** — `clu attest` with no flag = error.
- **Token required.** Operators don't need an operator-side variant
  (there's no rescue case — if the operator wants to bypass, they pass
  `--skip-simplify` on complete instead).

### Phase 4 — complete-gate
- **Two gates, evaluated in order**:
  1. Verify gate. Missing `attestations.verify` OR `commit_sha != HEAD`
     → refuse with `STATUS_TRANSITION`.
  2. Simplify gate. Compute phase diff against the claim's branch base
     (`git diff <base>..HEAD --numstat`, sum files + lines). If above
     threshold, require non-stale `attestations.simplify`. Refuse
     otherwise.
- **Threshold computed cumulatively** across the phase claim's diff,
  not per-commit. Convention talks about "the change" — a phase of 5
  commits × 10 lines each should still gate. Branch base: from
  `state.get_worktree(data)` if worktree-mode, else from
  `current_claim.head_sha_at_claim` (already captured at claim time).
- **`--skip-verify` and `--skip-simplify`** independent flags on
  `cmd_complete`. Each bypass emits an audit event
  (`EVENT_OPERATOR_SKIP_VERIFY` / `EVENT_OPERATOR_SKIP_SIMPLIFY`) so
  the trail captures intentional bypasses. Skip is "this phase only" —
  doesn't carry over.
- **Two flags, not one umbrella `--skip-quality`** — verify and
  simplify have different risk profiles (correctness vs style); making
  them independent forces the operator to think.
- **No-commit phases pass both gates by default** — `git diff --numstat
  <base>..HEAD` returns empty when HEAD == base, threshold = 0 = below
  any non-zero limit. Verify still required unless `--skip-verify`
  (because a no-commit phase could still have side effects worth
  verifying — better to force the worker to be explicit).

### Phase 5 — skill-update
- **Worker contract update** in `end_of_line/skill/SKILL.md`. New
  section "Pre-complete callbacks" between "Step-by-step protocol"
  and "Quality mandates":
  ```bash
  clu verify --project "$PROJECT_ROOT" --plan "$PLAN" --phase "$PHASE" --token "$TOKEN"
  # then, if diff > threshold:
  clu attest --simplify --project "$PROJECT_ROOT" --plan "$PLAN" --phase "$PHASE" --token "$TOKEN"
  ```
- **Wording is firm**: "If you call complete without these, clu refuses
  with `STATUS_TRANSITION` and your phase ticks attempts++. After 3
  attempts the plan halts."
- **Docs**: `docs/contract.md` gains the `attestations` schema +
  `quality` config block; `docs/reference.md` gains the `clu verify` /
  `clu attest` CLI entries.

## Non-goals

- **Auto-running /simplify from clu.** Not possible — /simplify is a
  Claude-Code-side skill. Self-attestation is the only mechanism.
- **Multi-step verify commands.** v1 is single-string + `shlex.split`.
  Projects with multiple verify steps wrap them in a script (matches
  the `dispatch.command` shape).
- **Per-phase threshold overrides.** Threshold lives on the project,
  not the phase. If a phase legitimately exceeds it, the operator uses
  `--skip-simplify` on complete.
- **Retroactive enforcement** on already-claimed phases. The gate fires
  on `cmd_complete`; phases claimed before the upgrade and completing
  after will hit the gate. Operators expecting this can pre-stamp
  via `clu attest --simplify` or pass `--skip-*`.

## Files touched

- `end_of_line/state.py` — P1 modified — `empty_state` adds
  `current_claim` carries no default `attestations` (lazy-init in
  `cmd_verify` / `cmd_attest`); add helper `stamp_attestation(data,
  kind, commit_sha)`. **API hotspot:** none — additive on a nested
  optional field.
- `end_of_line/config.py` — P1 modified — `QualitySpec` dataclass +
  `ProjectConfig.quality: QualitySpec`. **Schema hotspot:**
  `.orchestrator.json` gains `quality.{verify_command,
  simplify_threshold}` block.
- `end_of_line/cli.py` — P2, P3, P4 modified — P2 adds `cmd_verify` +
  argparse; P3 adds `cmd_attest` + argparse; P4 adds refusal gate +
  `--skip-verify` / `--skip-simplify` flags to `cmd_complete`. **API
  hotspot:** `cmd_complete` argparse surface; existing callers
  unaffected (additive flags).
- `end_of_line/skill/SKILL.md` — P5 modified — new pre-complete
  section + firmer wording on quality mandates. Workers read this on
  every dispatch.
- `docs/contract.md` — P5 modified — `attestations` schema, `quality`
  config block.
- `docs/reference.md` — P5 modified — `clu verify` / `clu attest`
  CLI reference.
- `tests/test_attestations.py` — P1 NEW.
- `tests/test_cmd_verify.py` — P2 NEW.
- `tests/test_cmd_attest.py` — P3 NEW.
- `tests/test_complete_refusal.py` — P4 NEW.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan attestation-gate --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| schema-config | `attestation-gate-schema-config.md` | `attestations` slot on `current_claim` + `QualitySpec` config dataclass + parser | 2h |
| cmd-verify | `attestation-gate-cmd-verify.md` | `clu verify` CLI + handler; runs command, stamps on rc=0; worker + operator variants | 3h |
| cmd-attest | `attestation-gate-cmd-attest.md` | `clu attest --simplify` CLI + handler; pure self-attestation; extensible flag surface | 2h |
| complete-gate | `attestation-gate-complete-gate.md` | `cmd_complete` refusal gate + threshold computation + `--skip-*` flags + audit events | 3h |
| skill-update | `attestation-gate-skill-update.md` | clu-phase SKILL.md + docs (`contract.md`, `reference.md`) update; no code changes | 1h |
