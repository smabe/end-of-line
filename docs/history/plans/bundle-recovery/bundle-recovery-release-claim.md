# bundle-recovery-release-claim — operator escape hatch for stuck claims

You are phase `release-claim` of the `bundle-recovery` plan.
Implement GH issue #8: `clu release-claim --project P --plan S
[--force] [--reason "..."]` clears a stuck `current_claim` and emits
a distinct event so the audit trail distinguishes operator action
from automatic lease expiry.

The previous phase (`systemic-failure`) established the new event +
distinct-reason pattern. Mirror it for the claim-force-release event.

## Read first

- GH issue #8 body (full acceptance criteria):
  ```
  gh issue view 8 --repo smabe/end-of-line
  ```
- The triage comment on #8 (already posted):
  ```
  gh issue view 8 --repo smabe/end-of-line --comments \
      --jq '.comments[].body' --json comments
  ```
- `end_of_line/cli.py` — `cmd_pause` (cli.py around line 111), `cmd_resume`,
  `cmd_retry`. `release-claim` is an operator command in the same family;
  match the argparse + handler style.
- `end_of_line/state.py`:
  - `release_claim()` helper at **state.py:312**. You can call into this
    rather than reinventing the clear-claim primitive.
  - `EVENT_LEASE_EXPIRED` at **state.py:74** — the auto-clear event your
    new event sits next to. Add `EVENT_CLAIM_FORCE_RELEASED = "claim_force_released"`.
  - `current_claim` shape — `phase_id`, `token`, `lease_expires`,
    `last_heartbeat_at`, `log_path`. The fresh-heartbeat check uses
    `last_heartbeat_at` against the configured `stalled_heartbeat_minutes`.
- `end_of_line/supervisor.py:67-97` — the lease-expired auto-clear path
  is the model. Your operator-triggered release does the same thing
  but stamps a different event.
- The just-committed `systemic-failure` phase — read its diff to see
  the audit-event pattern + how it's tested. Mirror those choices.

## Produce

1. **Failing tests first.** New file `tests/test_release_claim.py`.
   Cover all five branches from the issue body's acceptance criteria:
   - **Paused-plan release** → `status == "paused"`, claim cleared,
     `EVENT_CLAIM_FORCE_RELEASED` appended, exit 0. (No `--force`
     required — paused means there's no live worker to protect.)
   - **Running-plan with STALE heartbeat** → claim cleared, event
     appended, exit 0. (Stale means worker is presumed dead.)
   - **Running-plan with FRESH heartbeat refused (no `--force`)** →
     non-zero exit (`ExitCode.UNKNOWN_TASK` or a new `LIVE_CLAIM`;
     reuse if it fits), clear stderr suggesting `clu pause` first or
     `--force`. No state mutation.
   - **`--force` release on a fresh-heartbeat running plan** → claim
     cleared, event appended, exit 0. Event carries `forced: True` so
     audit reflects the override.
   - **No active claim (no-op)** → exit 0, clean stderr ("no claim to
     release"), **no event appended** (the audit trail shouldn't grow
     a no-op entry).
   - **`--reason "..."` passed** → reason text shows up in the event
     payload.
   Use `isolate_registry(self, tmp_path)` in `setUp`.

2. **Implementation.**
   - **`end_of_line/state.py`:** add `EVENT_CLAIM_FORCE_RELEASED = "claim_force_released"`
     adjacent to `EVENT_LEASE_EXPIRED` at state.py:74. No new status.
   - **`end_of_line/cli.py`:** new `release-claim` subparser. `add_common`
     for `--project/--plan`. Add `--force` (store_true, default False),
     `--reason` (optional string). Handler `cmd_release_claim`:
     - `with st.mutate(state_path) as data:` — load, check claim presence,
       check status + heartbeat freshness, decide allow/refuse/no-op.
     - On allow: clear `current_claim`, append event with `phase`,
       `token`, `forced`, `reason` (if any), `released_by_operator`.
     - On refuse: return `_die(ExitCode.UNKNOWN_TASK, ...)` (or new
       `LIVE_CLAIM` if you mint it — but prefer reuse) with a stderr
       suggesting `--force` or `clu pause`.
   - **`docs/operations.md`:** add a troubleshooting row "stuck claim
     after worker died → `clu release-claim --plan X`". Reference the
     new event so operators reading audit logs understand it.

3. **`/simplify`** if the diff crosses 1 file or 30 lines.

4. **Full suite green:** `python3 -m unittest discover -s tests`.

5. **Commit** (structured format). Stage explicit paths:
   `git add end_of_line/cli.py end_of_line/state.py docs/operations.md tests/test_release_claim.py`.

6. **Close GH #8:** use the PATH-defensive close pattern from
   `plans/bundle-recovery.md`.

## Constraints

- **Don't reinvent `release_claim`.** The state-layer helper exists
  at state.py:312; call it.
- **`--force` is a real safety switch.** Fresh-heartbeat-without-force
  must refuse with a clear message. Tests must cover this; it's the
  most important branch.
- **No-op is silent in the audit log.** Don't append `EVENT_CLAIM_FORCE_RELEASED`
  when there was no claim to release — that pollutes the trail.
- **No new status.** This is a recovery action, not a state transition;
  the plan's status stays whatever it was (running, paused, halted).
- Don't add a `--all` flag to release across all plans. That's a
  separate convenience command; out of scope.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan bundle-recovery --phase release-claim \
    --token <token> --commit <sha>
```

## Escape hatch

`clu block` if:
- The `systemic-failure` phase's event-shape changed in a way that
  makes a parallel shape for `EVENT_CLAIM_FORCE_RELEASED` awkward.
  Surface the conflict.
- The `stalled_heartbeat_minutes` config is missing from the test
  state files in unexpected ways. Surface the issue rather than
  inventing a default in the handler.
- You find a separate codepath that already releases claims for
  operator actions (e.g. `clu pause` might already clear claims; the
  triage assumed it doesn't but verify). If it does, the issue
  semantics change — surface and ask.
