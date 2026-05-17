# clu-watch-cli — `clu watch` subcommand wiring

You are phase `cli` of `clu-watch`. Wire phase `stream`'s
`stream_loop` to a new `clu watch` subcommand. Argparse, project/plan
resolution, `--all` registry walk, exit codes.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch.md` § Phase 3. Summary:
- Args: `--project P`, `--plan S`, `--all`, `--json`, `--verbose`,
  `--interval F`.
- `--plan` and `--all` mutually exclusive (argparse group).
- Bare `clu watch` → CWD project + all plans in that project.
- `--all` (no `--project`) → every registered plan host-wide.
- Exit codes: OK on SIGINT, UNKNOWN_TASK on missing plan, GENERIC
  on argparse violation.
- Default `--interval`: 1.0 for single-project, 5.0 for `--all`.

## Read first

- `end_of_line/watch.py` (shipped phases `events` + `stream`).
- `end_of_line/cli.py:153-171` — ExitCode (no new code).
- `end_of_line/cli.py:2669-2676` — `cmd_logs` as a sibling shape.
- `end_of_line/cli.py` argparse main section — find where other
  `sub.add_parser` calls live; mirror.
- `end_of_line/cli.py:_resolve_project_arg` — for CWD-default
  project resolution.
- `end_of_line/registry.py:entries()` — for `--all` enumeration.

## Produce

1. **Failing tests first** (`tests/test_watch_cli.py`, new):
   - `test_argparse_plan_and_all_mutually_exclusive` — call
     `main(["watch", "--plan", "x", "--all", "--project", str(p)])`,
     assert non-zero exit and stderr message.
   - `test_argparse_plan_requires_project` — call `main(["watch",
     "--plan", "x"])` from outside a project, assert clear error.
   - `test_unknown_plan_exits_unknown_task` — call with
     `--plan nonexistent --project P`, assert `ExitCode.UNKNOWN_TASK`.
   - `test_cwd_default_resolution` — CWD has `.orchestrator.json`,
     bare `clu watch` (with `max_ticks=1` via test seam) emits a
     snapshot for each registered plan in the project. (Test seam:
     `cmd_watch` accepts a private `_max_ticks` arg, set via env
     var or test-only kwarg.)
   - `test_all_mode_enumerates_registry` — registry has 3 plans
     across 2 projects; `clu watch --all` produces 3 snapshot lines.
   - `test_json_flag_propagates` — `--json` → snapshot + first event
     are JSON-parseable.
   - `test_verbose_flag_propagates` — `--verbose` includes a
     lease-extended event line that bare mode suppresses.
   - `test_interval_flag_parsed` — `--interval 0.5` passes float
     through to `stream_loop`.

2. **Implementation.**
   - Argparse (mirror existing subparsers):
     ```python
     p_watch = sub.add_parser(
         "watch",
         help="Stream state-machine events for one plan, one project, "
              "or every registered plan. One line per transition; "
              "designed for AI-agent consumption via Claude's Monitor.",
     )
     p_watch.add_argument("--project", type=Path, default=None)
     scope = p_watch.add_mutually_exclusive_group()
     scope.add_argument("--plan", default=None)
     scope.add_argument("--all", action="store_true", default=False)
     p_watch.add_argument("--json", action="store_true", default=False)
     p_watch.add_argument("--verbose", action="store_true", default=False)
     p_watch.add_argument("--interval", type=float, default=None,
         help="Poll interval seconds (default: 1.0 single-project, "
              "5.0 with --all)")
     ```
   - Dispatch:
     ```python
     def cmd_watch(args) -> int:
         from . import watch, registry
         state_paths: list[Path] = []
         if args.all:
             entries_list = [
                 (e.project_root, e.plan_slug)
                 for e in registry.entries()
                 if (args.project is None
                     or Path(e.project_root).resolve() == args.project.resolve())
             ]
             for proj, slug in entries_list:
                 cfg = load_project_config(Path(proj))
                 state_paths.append(cfg.state_path(slug))
         elif args.plan:
             cfg = load_project_config(_resolve_project_arg(args))
             # Validate plan exists in registry
             if not _is_registered(cfg.project_root, args.plan):
                 return _die(ExitCode.UNKNOWN_TASK,
                     f"plan {args.plan!r} is not registered")
             state_paths.append(cfg.state_path(args.plan))
         else:
             # Bare `clu watch` → CWD project + all its registered plans
             cfg = load_project_config(_resolve_project_arg(args))
             for e in registry.entries():
                 if Path(e.project_root).resolve() == cfg.project_root.resolve():
                     state_paths.append(cfg.state_path(e.plan_slug))
         interval = args.interval if args.interval is not None else (
             5.0 if args.all else 1.0
         )
         return watch.stream_loop(
             state_paths,
             json_mode=args.json,
             verbose=args.verbose,
             poll_interval=interval,
         )
     ```
   - Register `cmd_watch` in the `commands` dispatch dict (find
     the dict around line 719 — `"spawn": cmd_spawn` etc.).
   - Verify `cfg.state_path(slug)` exists on `ProjectConfig`; if
     not, use the existing path-resolution helper (whatever
     `cmd_status` uses to derive `state_path`).

3. **Acceptance.**
   - 8 new tests green.
   - Phases `events` + `stream` tests still green.
   - Full suite green.
   - Manual smoke: `clu watch --project . --plan
     queue-worker-callback` (if available) streams events.

4. **Commit + complete.**
   - Title: `clu-watch: phase cli — watch subcommand wiring`
   - Stage: `end_of_line/cli.py`, `tests/test_watch_cli.py`.
   - `clu complete --plan clu-watch --phase cli --token <T>`

## Failure modes to watch

- **`load_project_config` outside a project** — bare `clu watch`
  with no CWD project should fail with a helpful error. Test the
  no-project case explicitly.
- **State-path resolution divergence** — `cmd_status` and
  `cmd_logs` may use different helpers (`state_path` vs
  `_resolve_log_path`). Use the one that produces the canonical
  state.json path (probably `cfg.state_path(slug)` if it exists;
  otherwise inline the `plans/.orchestrator/<slug>.state.json`
  pattern).
- **Test seam for `max_ticks`** — passing `max_ticks` through the
  CLI is wrong (user-facing flag for a test concern). Instead,
  tests should call `watch.stream_loop` directly with `max_ticks=1`
  and only smoke `cmd_watch` for the resolution paths (not the
  loop). Argparse tests check parser shape, not stream-loop
  behavior.
