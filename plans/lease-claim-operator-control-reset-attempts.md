# lease-claim-operator-control-reset-attempts — `clu release-claim --reset-attempts` (#30)

You are phase `reset-attempts` of the `lease-claim-operator-control` plan. Add a
flag on the existing `clu release-claim` so operator-driven aborts (mid-flight
restart, config change, scope re-think) don't burn attempts against the
`max_attempts_per_phase` cap. Today: every `phase_started` after the last
`retry_requested` counts; an operator release puts the next dispatch one closer
to the cap.

## Locked decisions (do NOT re-litigate)

See `plans/lease-claim-operator-control.md`. Summary:

- **One flag on existing `cmd_release_claim`:** `--reset-attempts`. When set,
  append `EVENT_ATTEMPTS_RESET = "attempts_reset"` event alongside the
  existing `EVENT_CLAIM_FORCE_RELEASED`.
- **Distinct event** so the audit log distinguishes operator-resets from
  worker-driven retries (which use `EVENT_RETRY_REQUESTED`).
- **`attempts_for_phase()` boundary expansion** (state.py:505-532): count
  `EVENT_PHASE_STARTED` events after the most recent of EITHER
  `EVENT_RETRY_REQUESTED` OR `EVENT_ATTEMPTS_RESET`.
- **Print line** updated to mention the reset when the flag is set.

## Read first

- `end_of_line/cli.py:2720-2748` — current `cmd_release_claim` full body.
  Your touch sites are the argparse declaration (around cli.py:532 with
  `--force` and `--reason`) and the body's `append_event` call.
- `end_of_line/state.py:505-532` — `attempts_for_phase()` current
  implementation. It walks events in reverse, picks the timestamp of the most
  recent `EVENT_RETRY_REQUESTED`, and counts `EVENT_PHASE_STARTED` events
  after that timestamp. You're adding `EVENT_ATTEMPTS_RESET` to the
  reset-boundary set.
- `end_of_line/state.py:76-128` — EVENT_* block; add the new constant.
- `tests/` — find existing `cmd_release_claim` tests (likely
  `tests/test_release_claim.py` or `tests/test_cli_validation.py` — grep).

## Produce

1. **Failing tests first.** Extend the existing release-claim test file (or
   create one if absent):
   - `test_release_claim_reset_attempts_emits_event` — set up state with a
     claim and one prior `EVENT_PHASE_STARTED` event. Run `clu release-claim
     --reset-attempts --force`. Reload state, assert
     `EVENT_ATTEMPTS_RESET` appended with `phase`, `operator: True`.
   - `test_attempts_for_phase_zeros_after_reset_event` — state with two
     `EVENT_PHASE_STARTED` and one trailing `EVENT_ATTEMPTS_RESET`, then
     one more `EVENT_PHASE_STARTED`. Assert `attempts_for_phase(data,
     phase) == 1` (only the post-reset start counts).
   - `test_attempts_for_phase_interleaved_reset_and_retry` — events:
     STARTED, RETRY_REQUESTED, STARTED, ATTEMPTS_RESET, STARTED. Assert
     attempts = 1 (only the latest reset boundary counts).
   - `test_release_claim_without_reset_flag_unchanged` — regression guard:
     bare `release-claim` does NOT emit `EVENT_ATTEMPTS_RESET`.

2. **Implementation: event constant.**
   In `end_of_line/state.py` EVENT_* block, add
   `EVENT_ATTEMPTS_RESET = "attempts_reset"`.

3. **Implementation: `attempts_for_phase()` boundary expansion.**
   In `end_of_line/state.py:505-532`, expand the reset-event filter to match
   either constant. Current code likely does something like
   `if event["type"] == EVENT_RETRY_REQUESTED`; change to
   `if event["type"] in (EVENT_RETRY_REQUESTED, EVENT_ATTEMPTS_RESET)`.
   Preserve the "most recent timestamp" semantics — the boundary is the
   latest of either, not the earliest.

4. **Implementation: argparse.**
   In `cmd_release_claim` argparse (around cli.py:532), add:
   ```python
   p_release_claim.add_argument(
       "--reset-attempts", action="store_true",
       help="Zero the phase's attempts counter on release (for operator-driven "
            "aborts that shouldn't burn against max_attempts_per_phase).",
   )
   ```

5. **Implementation: `cmd_release_claim` body.**
   After `st.release_claim(data)` and the existing `append_event(EVENT_CLAIM_
   FORCE_RELEASED, ...)`, if `args.reset_attempts`:
   ```python
   if args.reset_attempts:
       st.append_event(
           data, st.EVENT_ATTEMPTS_RESET,
           phase=phase, operator=True,
       )
   ```
   Update the final `print(f"Released claim on {args.plan}/{phase}.")` line
   to append `" Attempts reset."` when the flag was set.

6. **Acceptance.**
   - All 4 new tests green.
   - Full suite green (no regression in existing release-claim or
     attempts-related tests).
   - Manual smoke: dispatch a phase, observe `attempts_for_phase` = 1, run
     `clu release-claim --reset-attempts --force`, dispatch again, observe
     `attempts_for_phase` = 1 (NOT 2).

7. **Commit + complete.**
   - Structured commit: `lease-claim-operator-control: phase reset-attempts —
     --reset-attempts flag + attempts_for_phase boundary expansion (#30)`.
   - Stage: `end_of_line/state.py`, `end_of_line/cli.py`, and the test file.
   - `clu complete --plan lease-claim-operator-control --phase reset-attempts
     --token <T>`.

## Failure modes to watch

- **`attempts_for_phase()` change risks regression in EVERY plan's
  attempt-counting** — this is a load-bearing function. Run the full suite
  AND eyeball any test name containing `attempts` or `max_attempts` to be
  sure none break.
- **Event-timestamp comparison** — ensure your filter uses `event["ts"]`
  (or whatever the timestamp field is — check existing code at
  state.py:505-532) for the "most recent" comparison, not array index.
  Events are appended in order, so array-tail is usually most-recent, but
  match the existing pattern.
- **`--reset-attempts` without `--force`** — must still work on a stalled
  claim (forcing is only needed when the supervisor would otherwise refuse
  the release). Read the existing refusal logic at cli.py:2727-2731 and
  confirm `--reset-attempts` doesn't bypass any safety check.
- **Print line clarity** — operator should see clear confirmation that
  attempts were reset, not just "released."
