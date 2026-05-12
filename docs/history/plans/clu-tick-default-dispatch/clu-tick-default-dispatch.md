# clu-tick-default-dispatch — flip `clu tick` to dispatch by default

Make `clu tick` actually spawn the worker by default, with a
`--dry-tick` opt-out for state-mutation-only operation. Closes the
silent footgun where a manual `clu tick` without `--dispatch`
claims a phase without spawning anyone, producing a phantom claim
that blocks cron for 30 minutes until the lease expires.

## Goal

After this plan ships:
- `clu tick --project P --plan S` spawns the worker (the common
  manual case).
- `clu tick --project P --plan S --dry-tick` does the current
  no-dispatch behavior (state mutation only — for debugging).
- The `--dispatch` flag is removed; behavior is now the default.

## Diagnosis

- **Hypothesis:** The current `clu tick` defaults `dispatch=False`,
  so manual ticks claim a phase but skip `dispatch_for_tick`. The
  supervisor still atomically writes `current_claim` to state. cron's
  `tick-all` returns "idle" on any plan with an active claim, so the
  phantom claim blocks cron until the 30-min lease expires. A real
  operator hit this on 2026-05-11 with the `worker-path-config/env`
  phase; iMessage fired at the 10-min stalled threshold.
- **Falsifiable test:** Add a unit test that calls `cmd_tick`
  without `--dry-tick`. Assert that `dispatch_for_tick` is called
  (mock the dispatch module). If the default is still
  no-dispatch, the test fails.
- **Test result:** TBD — gate in the impl phase.

## Locked decisions (do NOT re-litigate)

- **New default: `--dispatch=True`**. Manual ticks spawn the
  worker.
- **Opt-out flag is `--dry-tick`** (not `--no-dispatch`). Reads
  better at the CLI: "I want a dry tick" vs the double-negative.
- **Remove `--dispatch` entirely** rather than keeping it as a
  no-op. Cleaner. If any existing scripts pass it, they break
  loudly — preferable to silent semantic change.
- **`cmd_tick_all` (cron entry point) is UNCHANGED.** It already
  passes `dispatch=True` to `_tick_one_plan`. No semantic change
  there; just verify the call site still works.
- **`_tick_one_plan` signature unchanged.** Still takes
  `dispatch: bool` kwarg. Only the `cmd_tick` -> `_tick_one_plan`
  wiring changes.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| flip | `clu-tick-default-dispatch-flip.md` | TDD: failing test asserting dispatch fires; remove `--dispatch` flag; add `--dry-tick` flag; update `cmd_tick` wiring; update help text; verify cron path unchanged. | 45m |

## Failure modes to anticipate

- **Existing scripts that pass `--dispatch` break.** This is by
  design — silent semantic change is worse. The release notes /
  commit message should call it out, and `docs/operations.md` may
  mention the migration.
- **Test that uses `cmd_tick` programmatically.** Any test passing
  the old args needs updating; expect 1-2 such tests.
- **README example commands.** Search the README for `clu tick`
  examples; update to remove `--dispatch` if present.
- **Help text duplication.** The `--dispatch` description in the
  subparser is gone; the `--dry-tick` description should say
  "skip worker spawn (state mutation only — debug use)".

## Done criteria

- `clu tick` (no flags beyond `--project`/`--plan`) spawns the
  worker.
- `clu tick --dry-tick` performs the current no-dispatch
  behavior.
- The `--dispatch` flag is removed from the subparser; existing
  scripts that pass it exit with argparse's "unrecognized argument"
  error.
- Falsifiable test from Diagnosis is committed and green.
- `cmd_tick_all` behavior unchanged; cron path verified by existing
  tick-all tests.
- Full suite green.
- One commit, structured message, no `Fixes` trailer (no open issue
  yet — saved as auto-memory, not GH issue).

## Parking lot
- Future: if operators want a SIGTERM-safe foreground tick (to
  watch worker output live without backgrounding), add
  `--foreground` to attach stdout/stderr to the terminal. Currently
  the worker always backgrounds via `start_new_session=True`.
