# lease-claim-operator-control-stall-guard — `phase_stalled` no-heartbeat guard (#27)

You are phase `stall-guard` of the `lease-claim-operator-control` plan. Suppress
the `phase_stalled` event emission when a worker has NEVER heartbeat (i.e.
`last_heartbeat_at == started_at`). This is the canonical case for `claude
--print` workers: stdout buffers until exit, the bundled `/clu-phase` skill
doesn't call `clu heartbeat` between tool calls, so the supervisor sees age =
threshold and fires every threshold-interval for the duration of the phase.
Inbox spam. The lease-expiry detector still catches genuinely silent workers; we
only suppress the chatty stall ping.

Closes #27 AND closes #26 #29 #30 (this is the final phase of the
`lease-claim-operator-control` plan). Includes the cross-plan docs sweep.

## Locked decisions (do NOT re-litigate)

See `plans/lease-claim-operator-control.md`. Summary:

- **One-line early-return guard in `_detect_stalled`** (supervisor.py:75-103),
  BEFORE the `age >= threshold * 60` check: if `claim.get("last_heartbeat_at")
  == claim.get("started_at")`, return None.
- **Rationale (single-line comment):** `claude --print` buffers stdout; bundled
  `/clu-phase` doesn't heartbeat. Lease-expiry still catches silent workers.
- **Diagnostic before the fix:** confirm the buffering theory by inspecting
  events from a recent stalled plan (verify
  `last_heartbeat_at == started_at` actually holds in practice).

## Read first

- `end_of_line/supervisor.py:75-103` — current `_detect_stalled` body. You're
  adding ONE guard line at the top.
- `end_of_line/state.py:363-373` — `heartbeat_age_seconds(claim)`. Note
  that it falls back to `started_at` when `last_heartbeat_at` is missing, so
  the age math is technically correct — but emitting `phase_stalled` based on
  it is what we're guarding against.
- `tests/test_heartbeat.py` (around `StalledSupervisorTestCase` near line
  137) — the canonical test class for stall detection. Your new tests slot
  in here.
- `docs/contract.md` — the events section. Add brief docs for
  `EVENT_LEASE_EXTENDED` and `EVENT_ATTEMPTS_RESET` (carried in from earlier
  phases) AND the new stall-guard behavior.
- `docs/operations.md` — operator how-to surfaces. Add brief docs for the
  three new init flags, `clu extend-lease`, `clu release-claim
  --reset-attempts`, and the stall-guard behavior in troubleshooting.
- `docs/reference.md` — public CLI surface; update if necessary.

## Produce

1. **Diagnostic FIRST (30 seconds).** Pick a recent end-of-line plan (look in
   `plans/.orchestrator/*.state.json`) that has a `phase_stalled` event in
   its events array. Verify `current_claim` (or the historical claim from the
   matching `phase_started`) has `last_heartbeat_at == started_at`. If
   confirmed: proceed to the fix. If NOT confirmed (heartbeats are landing
   for some plans), the guard is too aggressive — STOP and `clu block` with
   a question for the operator.

2. **Failing tests first.** In `tests/test_heartbeat.py` (or a sibling
   `tests/test_supervisor_stall.py`):
   - `test_no_heartbeat_does_not_emit_phase_stalled` — set up a claim where
     `last_heartbeat_at == started_at`, advance time past threshold, call
     `_detect_stalled(data)`, assert result is None and no
     `EVENT_PHASE_STALLED` appended.
   - `test_real_heartbeat_past_threshold_still_emits` — regression guard:
     claim where `last_heartbeat_at > started_at` and both older than
     threshold, `_detect_stalled` returns a TickResult with kind "stalled"
     and event is appended.
   - `test_no_heartbeat_lease_expiry_still_catches` — separate test (lease
     detector, not stall detector): claim with no heartbeat where the
     lease HAS expired, `_detect_lease_expired` still fires
     `EVENT_LEASE_EXPIRED`. Confirms we didn't break the silent-worker
     safety net.

3. **Implementation: one-line guard.**
   In `_detect_stalled` (supervisor.py:75-103), at the top of the function,
   BEFORE the `age >= threshold * 60` check:
   ```python
   if claim.get("last_heartbeat_at") == claim.get("started_at"):
       # `claude --print` workers buffer stdout; bundled /clu-phase doesn't
       # call `clu heartbeat`. Lease expiry still catches silent workers via
       # _detect_lease_expired. (#27)
       return None
   ```

4. **Docs sweep.**
   - `docs/contract.md`:
     - In the events list, add `EVENT_LEASE_EXTENDED` (fields: `phase`,
       `extended_by_minutes`, `new_expires`, `operator`) and
       `EVENT_ATTEMPTS_RESET` (fields: `phase`, `operator`).
     - Update the `attempts_for_phase()` description: reset boundary is now
       most-recent of `EVENT_RETRY_REQUESTED` OR `EVENT_ATTEMPTS_RESET`.
     - Document the stall-detector guard: `phase_stalled` is suppressed
       when `last_heartbeat_at == started_at` (canonical `claude --print`
       case).
   - `docs/operations.md`:
     - `clu init` flags: document `--lease-ttl-minutes`,
       `--stalled-heartbeat-minutes`, `--max-attempts-per-phase`.
     - New operator commands: `clu extend-lease --plan S MINUTES`,
       `clu release-claim --reset-attempts`.
     - Troubleshooting: note that absent-heartbeat workers no longer
       emit `phase_stalled` (operator should watch for lease-expiry
       instead for silent-worker detection).
   - `docs/reference.md`: add the new subcommands and flags to the public
     CLI surface table.

5. **Acceptance.**
   - All 3 new tests green.
   - Full suite green.
   - Manual: search a fresh dispatch's `events[]` for any
     `phase_stalled` — should be absent for plans where the worker never
     heartbeat.
   - Docs files updated and pass a manual read-through for coherence with
     existing copy.

6. **Commit + complete.**
   - Structured commit: `lease-claim-operator-control: phase stall-guard —
     phase_stalled no-heartbeat guard + docs sweep (closes #26 #29 #30 #27)`.
   - Stage: `end_of_line/supervisor.py`, `tests/test_heartbeat.py` (or
     sibling), `docs/contract.md`, `docs/operations.md`, `docs/reference.md`.
   - `clu complete --plan lease-claim-operator-control --phase stall-guard
     --token <T>`. This is the LAST phase — the plan completes and the
     queue advances to `small-cli-fixes`.

## Failure modes to watch

- **Diagnostic disproves the hypothesis** — if a real recent plan shows
  `last_heartbeat_at > started_at` AND `phase_stalled` still spammed, the
  guard is targeted at the wrong condition. STOP, `clu block` with the
  diagnostic findings, ask the operator before proceeding.
- **Existing test `test_emits_stall_event_when_heartbeat_stale`** (or
  similar) — must still pass after the guard. The regression test in
  produce step 2 covers this; verify by running just `test_heartbeat.py`
  before the full suite.
- **`_detect_lease_expired` interaction** — make sure your tests confirm
  the lease-expiry path still fires for genuinely-silent workers; otherwise
  the guard would create a "silent worker = invisible" hole.
- **Docs drift** — the four-issue close (`closes #26 #29 #30 #27`) is the
  audit trail. All four issues should be referenced in the commit message
  AND in the docs additions. Don't lose one.
- **`docs/reference.md` may have a per-command schema you must extend** —
  not just a free-text addition. Read the existing format before adding.
