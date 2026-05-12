# clu-tick-default-dispatch-flip — the actual flip

You are phase `flip` of `clu-tick-default-dispatch`. One phase, one
commit. Flip `clu tick` to dispatch-by-default with `--dry-tick`
opt-out.

## Locked decisions (do NOT re-litigate)

See `plans/clu-tick-default-dispatch.md`. Summary:

- Default behavior: dispatch (spawn worker).
- Opt-out flag: `--dry-tick`.
- `--dispatch` flag removed entirely.
- `cmd_tick_all` (cron) unchanged.

## Read first

- `end_of_line/cli.py` — locate the `clu tick` subparser setup
  (search for `"tick"` near the top, NOT `"tick-all"`). It currently
  has a `--dispatch` action="store_true". Identify the exact lines
  to edit.
- `end_of_line/cli.py` — `cmd_tick` function. Currently calls
  `_tick_one_plan(..., dispatch=args.dispatch)`. The new call
  should be `_tick_one_plan(..., dispatch=not args.dry_tick)`.
- `end_of_line/cli.py` — `cmd_tick_all`. Confirm it passes
  `dispatch=True` explicitly (it does per earlier reads). No
  change.
- `tests/test_*.py` — any test that invokes `main(["tick", ...])`
  with or without `--dispatch`. `grep -rn '"tick"' tests/` or
  similar to enumerate. Each test needs updating.
- `README.md` — search for `clu tick --dispatch` in case the docs
  show that pattern.
- `docs/operations.md` — search for the same; update if found.

## Produce

1. **TDD: failing test first.** Add to `tests/test_dispatch.py` (or
   wherever cmd_tick tests live — could be `test_supervisor.py` or
   a dedicated `test_tick.py`):

   ```python
   def test_tick_default_dispatches(self):
       # Patch dispatch_for_tick; assert it gets called when
       # `clu tick` is invoked without --dry-tick.
       with mock.patch(
           "end_of_line.dispatch.dispatch_for_tick"
       ) as mocked:
           rc = main(["tick", "--project", str(self.project), "--plan", "test-plan"])
       self.assertTrue(mocked.called)
       self.assertEqual(rc, ExitCode.OK)

   def test_tick_dry_tick_skips_dispatch(self):
       with mock.patch(
           "end_of_line.dispatch.dispatch_for_tick"
       ) as mocked:
           rc = main(["tick", "--project", ..., "--plan", "...", "--dry-tick"])
       self.assertFalse(mocked.called)
       self.assertEqual(rc, ExitCode.OK)

   def test_tick_old_dispatch_flag_rejected(self):
       # --dispatch is GONE. argparse should reject it with SystemExit.
       with self.assertRaises(SystemExit):
           main(["tick", "--project", ..., "--plan", "...", "--dispatch"])
   ```

   Adapt the fixtures to whatever pattern existing tick tests use.
   Run the suite — the first test FAILS (current default is no
   dispatch); the second PASSES (dry-tick == current default
   behavior, but flag doesn't exist yet so this might fail too);
   the third FAILS (--dispatch still accepted).

2. **Edit the subparser.** Find the `clu tick` setup:
   ```python
   p_tick = sub.add_parser("tick", help="Run one supervisor tick")
   add_common(p_tick)  # or however --project / --plan are added
   p_tick.add_argument(
       "--dispatch", action="store_true",
       help="Actually spawn worker via configured dispatch.command",
   )
   ```
   Replace `--dispatch` with `--dry-tick`:
   ```python
   p_tick.add_argument(
       "--dry-tick", action="store_true",
       help="Skip worker spawn (state mutation only — debug use). "
            "Default is to dispatch.",
   )
   ```

3. **Edit `cmd_tick`** to invert the flag:
   ```python
   def cmd_tick(args) -> int:
       ...
       result = _tick_one_plan(
           args.plan, cfg, state_path,
           dispatch=not args.dry_tick,
       )
       ...
   ```
   Confirm `args.dry_tick` exists on the namespace (it will because
   argparse auto-creates from the long flag).

4. **Update help text** for the `tick` subparser if you didn't
   already:
   ```python
   p_tick = sub.add_parser(
       "tick",
       help="Run one supervisor tick (dispatches worker by default; "
            "use --dry-tick for state mutation only).",
   )
   ```

5. **Update existing tests that pass `--dispatch`.** Run:
   ```bash
   grep -rn -- '--dispatch' tests/
   ```
   Replace each usage. If a test expected no-dispatch, add `--dry-tick`.
   If a test expected dispatch and was passing `--dispatch`, drop the
   flag (it's now the default).

6. **Update README / docs** if any mention `clu tick --dispatch`.
   ```bash
   grep -rn -- '--dispatch' README.md docs/
   ```
   Replace with the new shape OR remove the flag from the example.

7. **Run the full suite from a clean process** per mandate #9.
   New tests pass; existing tests pass with their flag updates.
   Expect count delta: +3 from the new tests, minus 0 (existing
   tests updated, not deleted).

8. **`/simplify`.** Multi-file diff (cli.py + tests + maybe README).
   Substantive enough — run /simplify.

9. **Commit.** Title:
   `clu-tick-default-dispatch: flip default to dispatch, --dry-tick opt-out`.
   Structured message. Highlight the breaking change in the Why
   block: `--dispatch` flag removed; scripts that pass it will
   error.

10. **Re-run suite one more time** from a clean process, then call
    `clu complete` with token + SHA + count + one-line note about
    any scripts that needed updating.

## Failure modes to watch for

- **Two flags by the same name accidentally retained.** If you
  add `--dry-tick` without removing `--dispatch`, argparse will
  accept both and the semantics get confusing. Verify the old
  `add_argument("--dispatch", ...)` is gone, not just the new one
  added.
- **`args.dispatch` referenced elsewhere.** Search for it:
  `grep -rn "args.dispatch\b" end_of_line/`. After removing the
  arg, any reference to `args.dispatch` is dead code; remove it
  too.
- **Test that uses `args.dispatch` directly** (not via main
  argv). Rare but possible if a unit test constructs an argparse
  Namespace by hand. Search and update.
- **`add_common(p_tick)` may add the flag.** Verify. If
  `add_common` is responsible for `--dispatch`, change `add_common`
  OR add the flag only to subparsers that need it (likely
  unnecessary — `add_common` is probably just --project/--plan).

## Done criteria

- `--dispatch` flag is gone from the subparser; `--dry-tick`
  replaces the opt-in/opt-out semantics with the opposite default.
- `cmd_tick` calls `_tick_one_plan(..., dispatch=not args.dry_tick)`.
- 3 new tests pass; existing tests updated where they passed
  `--dispatch`.
- Full suite green from clean process.
- README / docs scrubbed of `--dispatch` references.
- One commit, structured message highlighting the breaking change.
- `clu complete` called with summary.
