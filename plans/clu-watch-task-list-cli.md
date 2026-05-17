# clu-watch-task-list-cli — argparse `--task-list` + mutex validation

You are phase `cli` of `clu-watch-task-list`. Add the `--task-list`
flag to `clu watch`, validate mutual exclusion with `--json` and
`--all` at the runtime layer, pass through to `stream_loop`. Translate
`FileNotFoundError` from missing master into `ExitCode.UNKNOWN_TASK`.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch-task-list.md` § Phase 4. Summary:
- Flag: `--task-list` (dest `watch_task_list`).
- Mutex with `--json` and `--all` validated in `cmd_watch` runtime
  (not argparse groups — they don't compose with the existing
  scope group).
- Missing master → `UNKNOWN_TASK` (6) with helpful message.

## Read first

- `end_of_line/cli.py:695-712` — existing `p_watch` argparse block.
  Add the new flag here.
- `end_of_line/cli.py:2867-2911` — current `cmd_watch` body. Add
  validation + pass-through.
- `tests/test_watch_cli.py` — existing CLI test pattern.

## Produce

1. **Failing tests first**
   (`tests/test_watch_task_cli.py`, new):
   - `test_task_list_and_json_mutually_exclusive` — call
     `main(["watch", "--task-list", "--json", "--project", str(p),
     "--plan", "foo"])`, assert `ExitCode.GENERIC`, stderr mentions
     "mutually exclusive".
   - `test_task_list_and_all_mutually_exclusive` — call with
     `--task-list --all`, assert `ExitCode.GENERIC`, stderr mentions
     "--task-list requires --plan".
   - `test_task_list_alone_passes_validation` — call with
     `--task-list --project P --plan foo` (with fixture state),
     pass `max_ticks=0` via a test seam or end-to-end up to bootstrap;
     assert exit code OK and bootstrap lines emitted.
   - `test_task_list_missing_master_returns_unknown_task` — fixture
     state exists but master `.md` missing → assert
     `ExitCode.UNKNOWN_TASK`, stderr mentions the missing path.
   - `test_help_text_mentions_task_list_protocol` — argparse `--help`
     output for `clu watch` contains "task-list" substring.

2. **Implementation.**
   - `end_of_line/cli.py:709` (after `--verbose` argparse):
     ```python
     p_watch.add_argument(
         "--task-list", action="store_true", default=False,
         dest="watch_task_list",
         help="Emit TASK_CREATE/TASK_UPDATE protocol lines for "
              "Claude's TaskCreate UI (mutex with --json and --all). "
              "See docs/operations.md § 'Task-list mode'.",
     )
     ```
   - `cmd_watch` (line 2867) — add validation block at the top:
     ```python
     task_list_mode: bool = getattr(args, "watch_task_list", False)
     if task_list_mode and args.json:
         return _die(ExitCode.GENERIC,
                     "--task-list and --json are mutually exclusive")
     if task_list_mode and all_mode:
         return _die(ExitCode.GENERIC,
                     "--task-list requires --plan or single-project "
                     "(mutually exclusive with --all)")
     ```
   - Wrap `stream_loop` call in try/except FileNotFoundError:
     ```python
     try:
         return watch.stream_loop(
             state_paths,
             json_mode=args.json,
             task_list_mode=task_list_mode,
             verbose=args.verbose,
             poll_interval=interval,
         )
     except FileNotFoundError as exc:
         return _die(ExitCode.UNKNOWN_TASK, str(exc))
     ```

3. **Acceptance.**
   - 5 new tests green.
   - Phases protocol / bootstrap / projector tests still green.
   - Full suite green.
   - Manual smoke: `clu watch --project . --plan <some-plan>
     --task-list` emits bootstrap TASK_CREATE lines.

4. **Commit + complete.**
   - Title: `clu-watch-task-list: phase cli — --task-list flag + mutex validation`
   - Stage: `end_of_line/cli.py`, `tests/test_watch_task_cli.py`.
   - `clu complete --plan clu-watch-task-list --phase cli --token <T>`

## Failure modes to watch

- **Mutex check order** — runtime validation should run BEFORE state-
  path resolution. Otherwise a `--task-list --all` with no valid
  registry would error on registry walk instead of the helpful mutex
  message.
- **Help-text wrapping** — argparse may wrap the help message;
  `--help` substring test should match a stable substring like
  "task-list" rather than the full sentence.
- **`load_project_config` for cfg_loader** — `cmd_watch` already
  imports it. Pass a lambda to `stream_loop` if needed, or rely on
  stream_loop's default fallback. Verify which is cleaner during
  implementation.
