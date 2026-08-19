# worker-death-visibility-death-recovery — the daemon releases the claim it just reported dead

You are phase `death-recovery` of the `worker-death-visibility` plan. Phase
`death-report` made the heartbeat daemon report a dead worker within two
minutes; the claim it reported still sits there until a supervisor tick
arrives to clear it. You close that half: the daemon releases the claim
itself, quota-classifying first, so the phase is redispatchable by whichever
tick runs next rather than waiting for one to re-derive a death that is
already on record. One commit; closes #104.

## Locked decisions (do NOT re-litigate)

See `plans/worker-death-visibility.md`. Summary:

- The daemon releases via `release_claim_and_emit` with BOTH `expected_token`
  and `expected_phase` — the unvalidated form is documented as
  supervisor-only, and the daemon holds the token to prove ownership.
- Quota classification runs BEFORE the release, inside the same lock window.
  Release clears the claim that carries `log_path`, and `classify_log_tail`
  reads it. Getting this order wrong silently kills the #94 quota pause and
  the attempt forgiveness that goes with it.
- The daemon does NOT reap the process group. Its own `setsid` puts it outside
  the group, the worker PID is gone by definition, and the supervisor's reap
  stays the backstop for the orphaned-keeper case.
- This does not fix "no supervisor tick ran for eight hours" (#103). It makes
  the phase redispatchable; something still has to dispatch it.

## Read first

- `plans/worker-death-visibility.md` `## Findings log` — phases `foreground-gates` and `death-report` both ran before you. The lock-timeout plumbing and the dedup marker you build on are theirs; read what they recorded about both.
- `end_of_line/heartbeat_daemon.py` — as it stands AFTER phase `death-report`. Your work extends the death path that phase added; do not re-derive it from the pre-phase-2 shape described in the master.
- `end_of_line/state.py:794-813` — `release_claim`. Read the docstring: passing neither `expected_*` clears unconditionally and is supervisor-only; passing exactly one is a programming error that raises `ValueError`.
- `end_of_line/state.py:815-851` — `release_claim_and_emit`. It snapshots `phase_id` and `claimed_by` before delegating, so coolant gets stable values, and discards the snapshot if `release_claim` raises `ClaimMismatch`.
- `end_of_line/supervisor.py:696-757` — the supervisor's worker-dead branch, the reference ordering: classify quota (`:706`) → record quota death (`:708`) → append event (`:727`) → release-and-emit (`:733`) → best-effort reap (`:739-742`). The comment at `:723-726` explains why durable state comes before the reap.
- `end_of_line/supervisor.py:706-721` — the quota branch, including `quota.record_quota_death` and the `notify.quota_pause_notification` side-notify.
- `end_of_line/quota.py` — `classify_log_tail` and `record_quota_death`. Confirm what `classify_log_tail` does with a missing or unreadable `log_path` before you rely on it from a different process.
- `end_of_line/cli.py`, the `notify-worker-dead` command added by phase `death-report` — you extend it rather than adding a second command.

## Produce

1. **Failing tests first.**
   - Extend `tests/test_notify_worker_dead.py`:
     - After the call, `current_claim` is `None` and the release is reflected
       in the state file on disk.
     - A wrong token releases nothing and returns `ExitCode.CLAIM_MISMATCH`.
     - With a quota signature in the worker log, `EVENT_QUOTA_DEATH` is
       recorded and the quota pause is written BEFORE the release — assert on
       the recorded quota state, not just on ordering of calls, so the test
       fails if a future edit reorders them.
     - With no quota signature, the ordinary death notification body is sent.
     - Coolant stop fires for the released claim when coolant is enabled, and
       does not when it is disabled.
   - Extend `tests/test_supervisor_worker_dead_dedup.py`: a tick arriving
     after the daemon already released finds no claim, takes no worker-dead
     action, and does not notify. This is the "tick runs five seconds later"
     sequence and it must be a clean no-op.
   - In `tests/test_supervisor_worker_dead_dedup.py`: after the daemon's
     release, a supervisor tick dispatches the phase again (subject to the
     attempt budget) rather than idling. This is the test that proves the
     phase is actually recoverable, not merely tidied up.

2. **Implementation.**
   - `end_of_line/cli.py`, in `cmd_notify_worker_dead`, inside the existing
     `st.mutate` window and AFTER the event append: classify quota from the
     claim's `log_path`, record the quota death and build the quota pause
     notification when it matches, then call `release_claim_and_emit` with
     both `expected_token` and `expected_phase` and the project's coolant
     settings. Mirror the supervisor's ordering exactly — read
     `supervisor.py:696-757` beside your edit rather than from memory.
   - Suppress the ordinary death notification body when quota matched, the way
     the supervisor does; the operator gets the quota pause ping instead.
   - `docs/contract.md`: update the event's semantics paragraph to say the
     claim is released by the reporter.
   - `docs/operations.md`: the recovery behavior and its limit — the phase
     becomes redispatchable, and a tick still has to dispatch it.
   - `docs/reference.md`: keep the command's described behavior accurate.

3. **Acceptance.**
   - All new tests green; full suite green with count and delta recorded.
   - An end-to-end check in a scratch project: stamp a claim with a PID that
     is not running, run the daemon with `detach=False` and `max_ticks=1`, and
     confirm the state file afterwards has the death event, no
     `current_claim`, and one inbox entry.
   - `grep -n "release_claim" end_of_line/cli.py` shows the new call passes
     both `expected_*` arguments — the one-argument form raises `ValueError`
     and the zero-argument form is a security regression.
   - Quota ordering is asserted by a test that fails if classification moves
     after the release.

4. **Commit + attest + complete.**
   - **Record cross-phase findings** in `## Findings log` — this is the last
     phase, so record anything the operator or a follow-up issue needs,
     particularly anything you learned about the quota path from a
     non-supervisor caller.
   - Commit: `worker-death-visibility: phase death-recovery — daemon releases the dead claim (closes #104)`. This phase carries the `closes` because #104 needs both halves; phase `death-report` referenced the issue without closing it.
   - Stage explicit paths: `end_of_line/cli.py`, the doc files, your test
     files, and `plans/worker-death-visibility.md` if you logged a finding.
   - After the commit:
     - `clu verify --plan worker-death-visibility --phase death-recovery --token <T>`
     - `clu attest --simplify --plan worker-death-visibility --phase death-recovery --token <T>`
   - `clu complete --plan worker-death-visibility --phase death-recovery --token <T>`

## Failure modes to watch

- **Release before quota classification is the silent one.** Both orderings
  pass a naive test and both leave a released claim. The difference only shows
  up as a plan that burns an attempt toward a max-attempts halt instead of
  pausing until the quota resets — days later, on a different plan. Assert the
  ordering.
- **The daemon is now a second releaser of claims.** Every reader that assumed
  "the claim is released, therefore a supervisor tick ran" is now wrong. The
  three readers of `current_claim` were checked at plan time — `top.py:517`,
  `webserver.py:533`, `fleet.py:34` — and all three render a claimless plan as
  absence, exactly as they already do after a supervisor release, so none of
  them breaks. What changes is only the inference an operator might draw from
  a released claim; say so in your completion summary.
- **`ClaimMismatch` on release is a normal race, not a failure.** A supervisor
  tick may have released first. Exit cleanly.
- **Do not let recovery run when the death report was suppressed as a
  duplicate.** If the dedup marker short-circuits the command, it must
  short-circuit before the release too, or a second invocation releases a
  claim belonging to a *newly dispatched* worker. Phase `death-report`'s early
  return is the guard; make sure your code sits after it.
- **Redispatch is not resumption.** The staged work from the dead attempt is
  still in the worktree, and the next attempt inherits it via
  `_prev_attempt_context` (def `dispatch.py:508`), fed by
  `_last_termination_reason` (`:498-506`) off the `_TERMINATION_REASONS` table
  (`:470`). That table has no entry for the new event — verified at plan time,
  so this is a fact, not a risk — meaning the redispatched worker is told
  nothing about why the last attempt died. Add the entry; it is one line in a
  file you are already reasoning about, and the reason string should name the
  end-of-turn-wait cause so the next attempt does not repeat it.
