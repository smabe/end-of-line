# lease-claim-operator-control

## Goal
Give the operator control over lease/claim lifecycle without state-file
hand-editing. Add init-time config knobs for lease/heartbeat/attempts (#26),
mid-flight `clu extend-lease` (#29), `clu release-claim --reset-attempts` (#30),
and suppress `phase_stalled` spam when worker never heartbeats (#27 —
buffered-stdout case from `claude --print`).

## Diagnosis
- **Hypothesis (#27, the bug-shaped issue):** `_detect_stalled` at
  `supervisor.py:75-103` emits `phase_stalled` based purely on
  `heartbeat_age_seconds(claim) >= threshold * 60`. When a worker uses
  `claude --print` (stdout buffered until exit), `last_heartbeat_at` equals
  `started_at` forever — but `heartbeat_age_seconds` at `state.py:363-373`
  falls back to `started_at` when `last_heartbeat_at` is None/equal, so age
  grows linearly and the detector fires every threshold interval. Inbox spam.
- **Falsifiable test (#27):** A test claim with `last_heartbeat_at == started_at`,
  current time at `started_at + threshold + 60s`, fed into `_detect_stalled`,
  currently emits `EVENT_PHASE_STALLED`. After the guard: returns None.
- **Test result (#27):** Will be written in phase 4. The bug shape is confirmed
  by code reading; the fix is a one-line early-return guard.
- The other three issues (#26, #29, #30) are pure feature adds — no diagnosis
  needed.

## Non-goals
- Worker-callable lease extension (token-required path) — operator-only suffices
  for v1. File as v2 issue if a real worker needs it.
- Auto-tuning init defaults based on plan size — operator picks per-plan at
  init time.
- Changing the existing stall-detector threshold semantics — only adding a
  guard for the never-heartbeated case.
- Schema-version bump — all changes are additive (new init flags override
  existing config keys; new event types are append-only).
- A `clu extend-lease` that takes absolute UTC time — minutes-from-now
  suffices. Operator can extend again if more is needed.
- Plumbing the new config flags through `clu doctor` — separate ticket if
  warranted later.

## Files to touch
- `end_of_line/state.py:76-128` — add `EVENT_LEASE_EXTENDED = "lease_extended"`
  and `EVENT_ATTEMPTS_RESET = "attempts_reset"` to the EVENT_* block.
- `end_of_line/state.py:505-532` — extend `attempts_for_phase()` to count
  `EVENT_PHASE_STARTED` events after the most recent of EITHER
  `EVENT_RETRY_REQUESTED` OR `EVENT_ATTEMPTS_RESET`. The existing logic
  picks the latest reset boundary; adding ATTEMPTS_RESET to the boundary
  set is the minimal change.
- `end_of_line/cli.py:219-255` (cmd_init argparse setup) — add three flags:
  `--lease-ttl-minutes INT`, `--stalled-heartbeat-minutes INT`,
  `--max-attempts-per-phase INT`. Argparse-level `type=int`; post-parse
  validation rejects ≤0 with `_die(ExitCode.INVALID_VALUE, ...)`.
- `end_of_line/cli.py` (cmd_init body, around line 1046 where `st.empty_state`
  is called) — after `empty_state` populates defaults, override
  `data["config"]["lease_ttl_minutes"]` etc. with any non-None CLI values
  before `st.save_atomic()` at line 1049.
- `end_of_line/cli.py` — new `cmd_extend_lease(args, cfg, state_path)`.
  Argparse: required `--project`, `--plan`, positional `minutes` (int,
  reject ≤0). Reads current `data["current_claim"]`; if None, `_die`
  with clear message. Computes `new_expires = max(now, current_lease_expires) +
  timedelta(minutes=args.minutes)` (extending from later of now/old-lease
  avoids backwards time). Writes via `st.mutate(state_path)`. Emits
  `EVENT_LEASE_EXTENDED` with `phase`, `extended_by_minutes`, `new_expires`,
  `operator: True`. Prints confirmation.
- `end_of_line/cli.py` (subcommand registry, near other init-style commands) —
  register `extend-lease` subcommand with `add_common(p)` (gets --project,
  --plan) + positional `minutes` arg.
- `end_of_line/cli.py:2720-2748` (cmd_release_claim) — add `--reset-attempts`
  flag at the argparse site. In the body, after `st.release_claim(data)` but
  before `append_event(EVENT_CLAIM_FORCE_RELEASED, ...)`, if
  `args.reset_attempts`: also `st.append_event(data, EVENT_ATTEMPTS_RESET,
  phase=phase, operator=True)`. Update the print line to mention reset when
  set.
- `end_of_line/supervisor.py:75-103` (_detect_stalled) — early-return guard
  before the `age >= threshold * 60` check: if `claim.get("last_heartbeat_at")
  == claim.get("started_at")` (worker never heartbeat at all), return None.
  Single-line comment naming the buffered-stdout case from #27. Counter-case
  is lease expiry, which still catches genuinely silent workers via the
  separate `_detect_lease_expired` path.
- `tests/test_init.py` (or wherever cmd_init flag tests live — likely
  `test_cli_validation.py`) — three new tests: each of the three flags writes
  the expected override into `state.json` `config`; rejects ≤0 with
  `INVALID_VALUE`.
- `tests/test_extend_lease.py` (new) — happy path (extends running claim by N
  minutes, emits event, updates `lease_expires`); refusal when no
  `current_claim`; refusal on bad minutes; lease extension from `now` when
  current `lease_expires` is in the past.
- `tests/test_release_claim.py` (or wherever cmd_release_claim is tested) —
  new tests: `--reset-attempts` emits `EVENT_ATTEMPTS_RESET`;
  `attempts_for_phase()` returns 0 after reset; next claim is attempt 1.
- `tests/test_heartbeat.py` (StalledSupervisorTestCase or sibling) — new
  test: claim with `last_heartbeat_at == started_at`, time advanced past
  threshold, `_detect_stalled` returns None, no `EVENT_PHASE_STALLED` in
  `data["events"]`. Plus an existence test that a claim WITH a real
  heartbeat older than threshold still fires (regression guard for the
  original behavior).
- `docs/contract.md` — document `EVENT_LEASE_EXTENDED`, `EVENT_ATTEMPTS_RESET`,
  the `attempts_for_phase()` reset-boundary expansion, and the new init flags
  in the config-schema section.
- `docs/operations.md` — operator how-to for `clu extend-lease`,
  `clu release-claim --reset-attempts`, and the three new `clu init` flags.
  Mention the #27 stall-suppression behavior in the troubleshooting section.

## Failure modes to anticipate
- **`--lease-ttl-minutes 0` or negative** — argparse `type=int` accepts but doesn't
  validate sign. Post-parse rejection with `_die(ExitCode.INVALID_VALUE, ...)`
  required; same for the other two init flags and for `extend-lease`'s
  positional.
- **`clu extend-lease 30` while claim is stalled (lease already expired)** —
  semantic ambiguity resolved by `max(now, current_lease_expires)` baseline.
  Extending from `now` is the operator-intuitive case.
- **`--reset-attempts` on a plan with no claim** — same refusal path as bare
  `release-claim`; the existing early-return at cli.py:2724-2726 handles it.
- **`attempts_for_phase()` boundary expansion regression** — the current logic
  uses ONE reset event; adding a second must preserve "latest-of-either"
  semantics. Test both old (RETRY_REQUESTED only) and new (ATTEMPTS_RESET only)
  paths plus the interleaved case.
- **The no-heartbeat stall guard could mask a real silent worker.** Mitigation:
  the lease-expiry path (`_detect_lease_expired`) still fires; operator still
  gets `claim_force_released` event when lease elapses. The guard only suppresses
  the chatty heartbeat-stall ping for the buffered-stdout case.
- **Init flag overrides written AFTER `empty_state()` populates defaults** — must
  be inside the same `st.mutate` window (or before the first `save_atomic`) so
  the overrides actually land in the persisted file.
- **`extend-lease` lease-time arithmetic** — `lease_expires` is stored as ISO
  8601 string. Must parse with `datetime.fromisoformat`, add `timedelta`,
  re-format with `.isoformat()`. Verify ISO round-trip matches the format
  written by claim creation (state.py:321).
- **Event ordering in `attempts_for_phase()`** — events have `ts` field; the
  "most recent" reset must be by timestamp, not by array-index. Match existing
  pattern.
- **Inbox surfaces for the new events** — `EVENT_LEASE_EXTENDED` and
  `EVENT_ATTEMPTS_RESET` are operator-initiated; they should NOT spawn iMessage
  notifications. Check `notify.py` routing — most events skip notification by
  default (only specific ones route), so this is likely a non-issue, but verify.

## Done criteria
- **Phase 1 (#26):** `clu init --plan foo --lease-ttl-minutes 720
  --stalled-heartbeat-minutes 60 --max-attempts-per-phase 5` writes those values
  into `state.json` `config`. Bad values rejected with `INVALID_VALUE`. New
  tests green. Closes #26.
- **Phase 2 (#29):** `clu extend-lease --project P --plan S 60` extends a
  running claim's lease by 60 minutes, emits `EVENT_LEASE_EXTENDED`, refuses
  with clear stderr if no claim. Handles past-lease case via `max(now, …)`.
  New tests green. Closes #29.
- **Phase 3 (#30):** `clu release-claim --project P --plan S --reset-attempts`
  zeros the attempts counter (verified via `attempts_for_phase()` returning 0
  for that phase). Emits `EVENT_ATTEMPTS_RESET` distinct from
  `EVENT_RETRY_REQUESTED` so the audit log can tell operator-resets from
  worker-retries. New tests green. Closes #30.
- **Phase 4 (#27):** A claim with `last_heartbeat_at == started_at` advanced
  past `stalled_heartbeat_minutes` threshold does NOT emit `phase_stalled`.
  A claim with a real (older) heartbeat past threshold still DOES emit. New
  tests green. Closes #27.
- Full suite green at 536+N tests at every phase boundary.
- Docs updated (`contract.md`, `operations.md`).
- All work shipped on the `lease-claim-operator-control` branch via clu's
  worktree.
- One commit per phase, `/simplify` between.

## Parking lot
(empty)
