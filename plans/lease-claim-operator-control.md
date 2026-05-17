# lease-claim-operator-control — operator control of lease/claim lifecycle (#26 #29 #30 #27)

Four operator-facing improvements that close the lease/claim-control gap without
state-file hand-editing. Today: lease/heartbeat/attempt defaults are hardcoded at
init time; mid-flight bumps require editing `state.json`; operator-driven aborts
burn attempts; `claude --print` workers (the canonical clu dispatch) spam
`phase_stalled` every threshold-interval because stdout buffering means
`last_heartbeat_at` never moves.

Smallest-first: init knobs (the prerequisite — bigger defaults reduce the need for
the operator commands), then `clu extend-lease`, then `--reset-attempts`, then the
`phase_stalled` no-heartbeat suppression guard.

## Locked design decisions

### Phase 1 — `clu init` config knobs (#26)
- **Three new flags:** `--lease-ttl-minutes`, `--stalled-heartbeat-minutes`,
  `--max-attempts-per-phase`. Argparse `type=int`; post-parse validation rejects
  `≤0` with `_die(ExitCode.INVALID_VALUE, ...)`.
- **Write site:** after `st.empty_state(...)` populates defaults at cli.py:1046,
  override `data["config"]["..."]` for any non-None CLI value, before
  `st.save_atomic()` at cli.py:1049. Same `st.mutate` window.
- **No schema bump.** Additive change — the keys already exist in
  `empty_state()` (state.py:156-174) with `DEFAULT_*` constants
  (state.py:50-54).
- **Defaults unchanged.** Operator opt-in via flags.

### Phase 2 — `clu extend-lease` (#29)
- **Operator-only command, NO `--token`** — matches `cmd_release_claim` and
  `cmd_answer` shape (cli.py:2720, cli.py:2751). Worker-callable lease
  extension is v2 (not in scope).
- **Argparse:** `add_common(p)` for `--project`/`--plan` + positional `minutes`
  (int, reject ≤0).
- **Semantics:** `new_expires = max(now, current_lease_expires) +
  timedelta(minutes=args.minutes)`. The `max(now, ...)` baseline handles
  past-lease (stalled) claims — extends from `now`, never backwards.
- **Refusal:** if `data["current_claim"]` is None, `_die` with clear stderr.
- **Event:** new `EVENT_LEASE_EXTENDED = "lease_extended"` in state.py
  EVENT_* block. Fields: `phase`, `extended_by_minutes`, `new_expires`,
  `operator: True`.
- **Lease arithmetic:** parse ISO 8601 via `datetime.fromisoformat`, add
  `timedelta`, re-emit via `.isoformat()`. Match the format written by claim
  creation (state.py:321).

### Phase 3 — `clu release-claim --reset-attempts` (#30)
- **One new flag on existing `cmd_release_claim`** (cli.py:2720-2748). When set,
  append `EVENT_ATTEMPTS_RESET = "attempts_reset"` event alongside the existing
  `EVENT_CLAIM_FORCE_RELEASED` (so the audit log distinguishes operator-resets
  from worker-driven retries).
- **`attempts_for_phase()` boundary expansion** (state.py:505-532): count
  `EVENT_PHASE_STARTED` events after the most recent of EITHER
  `EVENT_RETRY_REQUESTED` OR `EVENT_ATTEMPTS_RESET`. Mirror existing semantics.
- **Print line** updated to mention the reset when the flag is set.
- **Refusal path** unchanged — bare `release-claim` without a claim still
  early-returns at cli.py:2724-2726; `--reset-attempts` doesn't change that.

### Phase 4 — `phase_stalled` no-heartbeat guard (#27)
- **One-line early-return guard in `_detect_stalled`** (supervisor.py:75-103),
  BEFORE the `age >= threshold * 60` check: if `claim.get("last_heartbeat_at")
  == claim.get("started_at")`, return None.
- **Rationale (single-line comment in code):** `claude --print` buffers stdout
  until exit; the bundled `/clu-phase` skill doesn't call `clu heartbeat`
  between tool calls, so `last_heartbeat_at` never moves. The lease-expiry
  path still catches genuinely silent workers; this only suppresses the chatty
  heartbeat-stall ping.
- **Diagnostic before the fix:** spend 30 seconds confirming the buffering
  theory on a real dispatch (read `data["events"]` for a recent
  `phase_stalled`-bearing plan; verify `last_heartbeat_at == started_at`).

## Non-goals

- Worker-callable lease extension (token-required path) — operator-only for v1.
- Auto-tuning init defaults based on plan size — operator picks per-plan.
- Changing the existing stall-detector threshold semantics — only adding the
  no-heartbeat guard.
- Schema-version bump — all changes additive.
- Absolute-UTC `extend-lease` — minutes-from-now suffices.
- Plumbing the new init flags through `clu doctor` — separate ticket if
  warranted.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood / Tests /
  `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan lease-claim-operator-control --phase <id> --token
  <T>` with the worker token on success.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| init-knobs | `lease-claim-operator-control-init-knobs.md` | Three `clu init` flags + validation + state writes (#26) | 1.5h |
| extend-lease | `lease-claim-operator-control-extend-lease.md` | New `cmd_extend_lease` with `EVENT_LEASE_EXTENDED` + lease arithmetic (#29) | 2h |
| reset-attempts | `lease-claim-operator-control-reset-attempts.md` | `--reset-attempts` flag + `EVENT_ATTEMPTS_RESET` + `attempts_for_phase()` boundary expansion (#30) | 1.5h |
| stall-guard | `lease-claim-operator-control-stall-guard.md` | Diagnostic + `_detect_stalled` no-heartbeat early-return + docs sweep (closes #26 #29 #30 #27) | 1.5h |
