# clu-watch-tips — `clu init` + `clu queue add` closing tips

You are phase `tips` of `clu-watch`. Add a one-line tip at the end of
`clu init` and `clu queue add` output suggesting `clu watch`. Mirror
the existing `_maybe_print_monitor_tip` helper. `--quiet` suppresses.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch.md` § Phase 4. Summary:
- `clu init` final line: `Tip: clu watch --project . --plan <slug>`
- `clu queue add` final line: `Tip: clu watch --project . --all`
- `--quiet` flag on both commands suppresses both monitor + watch
  tips.
- New helper `_maybe_print_watch_tip(scope: "plan" | "all", slug:
  str | None = None)`.

## Read first

- `end_of_line/cli.py` — find `_maybe_print_monitor_tip` (used by
  `clu init` and `clu queue add` per memory) for the helper
  pattern.
- `end_of_line/cli.py:cmd_init` — current closing print path.
- `end_of_line/cli.py:1802-1807` — current `cmd_queue_add` closing
  print path (operator branch).
- `tests/test_cli_hints.py` (or similar — grep for
  `monitor_tip` test file) — existing pattern for asserting tips.

## Produce

1. **Failing tests first** (`tests/test_watch_tip.py`, new file or
   extend existing hints test):
   - `test_clu_init_prints_watch_tip` — call `main(["init",
     "--project", str(p), "--plan", "foo", "--no-claude-md"])`,
     capture stdout, assert it contains `clu watch --project .
     --plan foo`.
   - `test_clu_init_quiet_suppresses_watch_tip` — same with
     `--quiet`, assert tip NOT in stdout.
   - `test_clu_queue_add_prints_watch_tip_all` — operator add of
     one slug, assert stdout contains `clu watch --project .
     --all`.
   - `test_clu_queue_add_quiet_suppresses_watch_tip` — with
     `--quiet`, assert tip absent.
   - `test_existing_monitor_tip_still_prints` — both tips coexist;
     monitor tip behavior unchanged (regression guard).

2. **Implementation.**
   - Add `--quiet` flag to `clu init` and `clu queue add`
     subparsers if not already present.
   - Add helper:
     ```python
     def _maybe_print_watch_tip(*, scope: str, slug: str | None = None,
                                quiet: bool = False) -> None:
         if quiet:
             return
         if scope == "plan" and slug:
             print(f"\nTip: `clu watch --project . --plan {slug}` "
                   f"streams state events (use with Claude's Monitor tool).")
         elif scope == "all":
             print(f"\nTip: `clu watch --project . --all` streams "
                   f"every queued plan (use with Claude's Monitor tool).")
     ```
   - In `cmd_init`, after the existing "Initialized" print, call
     `_maybe_print_watch_tip(scope="plan", slug=args.plan,
     quiet=getattr(args, "quiet", False))`.
   - In `cmd_queue_add` operator branch (around line 1806, after
     the existing monitor tip), call
     `_maybe_print_watch_tip(scope="all",
     quiet=getattr(args, "quiet", False))`.

3. **Acceptance.**
   - 5 new tests green.
   - Existing init + queue-add tests still green.
   - Full suite green.

4. **Commit + complete.**
   - Title: `clu-watch: phase tips — init + queue add closing tips`
   - Stage: `end_of_line/cli.py`, `tests/test_watch_tip.py` (or
     extension of existing hints test).
   - `clu complete --plan clu-watch --phase tips --token <T>`

## Failure modes to watch

- **`--quiet` already exists** — some commands may have it; verify
  by grepping `add_argument.*--quiet`. If present, just thread
  through. If absent, add fresh.
- **Tip ordering** — if both monitor + watch tips fire on the same
  command, keep them adjacent and consistent (both prefixed
  `Tip:`, blank line before the first one, no blank between
  them).
- **No tip on `--token`-mode `queue add`** — v2 worker-callback
  enqueue path should NOT print operator-facing tips. Check
  `args.token is None` before the call (or skip the print in the
  worker branch entirely).
