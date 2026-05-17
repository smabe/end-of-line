# small-cli-fixes-blockers-cli — `clu blockers list|show` (#32)

You are phase `blockers-cli` of the `small-cli-fixes` plan. Add a read-only
operator command that surfaces blocker payloads from `data["blockers"]` and
related events from `data["events"]`. Today the operator has to JSON-spelunk
`state.json` to see a blocker's question and options before answering with
`clu answer`. After this phase: `clu blockers --plan S` shows the list,
`clu blockers --plan S q-1` shows full payload.

## Locked decisions (do NOT re-litigate)

See `plans/small-cli-fixes.md`. Summary:

- **New top-level subcommand `blockers`** registered at cli.py around line
  369 (after `uninstall-hook`). Nested subparsers `list` and `show`.
- **`list`** filters `data["blockers"]` on `answer is None`. Empty case
  prints `"no open blockers on {args.plan}"` (stdout, exit 0).
- **`show <id>`** prints full blocker + related events; not-found →
  `_die(ExitCode.UNKNOWN_TASK, ...)`.
- **Blocker schema** (state.py:417-427): `id`, `phase_id`, `type`,
  `question`, `options` (list[str]), `context`, `asked_at`, `answer`,
  `answered_at`.

## Read first

- `end_of_line/state.py:417-427` — blocker dict structure.
- `end_of_line/state.py:80,83-85` — `EVENT_PHASE_BLOCKED`,
  `EVENT_BLOCKER_ANSWERED`, `EVENT_BLOCKER_CONSUMED` event constants
  (events join filter uses these).
- `end_of_line/cli.py:541-546` — `cmd_answer` argparse setup. Use this
  as your shape template for `add_common(p)` + positional pattern.
- `end_of_line/cli.py` around line 369 (after `uninstall-hook`) — where to
  register the new subcommand.
- `end_of_line/cli.py` (lines 651-683) — top-level dispatcher; you'll add a
  route for `args.cmd == "blockers"`.
- The state-loading pattern: `cfg = load_project_config(args.project.
  resolve())`, `state_path = cfg.state_path(args.plan)`, `data =
  st.load(state_path, expected_version=st.SCHEMA_VERSION)`. Reuse it; don't
  call `st.mutate` (read-only).

## Produce

1. **Failing tests first.** New `tests/test_blockers.py`:
   - `test_blockers_list_empty` — fresh state with no blockers, `clu blockers
     --project ... --plan ... list` prints "no open blockers..." to stdout,
     exit 0.
   - `test_blockers_list_open_only` — state with one open blocker
     (`answer is None`) and one consumed (`answer is not None`); list shows
     only the open one with its id, phase, asked_at, question, options.
   - `test_blockers_show_happy_path` — state with one open blocker `q-1`;
     `clu blockers ... show q-1` prints all fields including context.
   - `test_blockers_show_includes_related_events` — same state plus an
     `EVENT_PHASE_BLOCKED` event with `blocker_id="q-1"`; show output
     includes a section listing the event.
   - `test_blockers_show_not_found` — state with no blocker `q-99`,
     `clu blockers ... show q-99` exits with `UNKNOWN_TASK`, stderr
     mentions "no blocker q-99".
   Use `CluTestCase` if test-isolation-base is shipped; otherwise manual
   isolation.

2. **Implementation: argparse.**
   In cli.py around line 369:
   ```python
   p_blockers = sub.add_parser(
       "blockers", help="List or show open blockers on a plan",
   )
   blockers_subs = p_blockers.add_subparsers(dest="blockers_cmd")

   p_blockers_list = blockers_subs.add_parser(
       "list", help="List open blockers on the plan",
   )
   add_common(p_blockers_list)

   p_blockers_show = blockers_subs.add_parser(
       "show", help="Show a blocker by id with full context and events",
   )
   add_common(p_blockers_show)
   p_blockers_show.add_argument("blocker_id")
   ```

3. **Implementation: helper functions.**
   ```python
   def cmd_blockers_list(args) -> int:
       st.validate_slug(args.plan, kind="plan slug")
       cfg = load_project_config(args.project.resolve())
       state_path = cfg.state_path(args.plan)
       if not state_path.exists():
           return _die(ExitCode.UNKNOWN_TASK, f"no state at {state_path}")
       data = st.load(state_path, expected_version=st.SCHEMA_VERSION)
       open_blockers = [b for b in data.get("blockers", []) if b.get("answer") is None]
       if not open_blockers:
           print(f"no open blockers on {args.plan}")
           return ExitCode.OK
       for b in open_blockers:
           print(f"{b['id']} [{b['phase_id']}] (asked {b['asked_at']})")
           print(f"  {b['question']}")
           if b.get("options"):
               print("  Options:")
               for i, opt in enumerate(b["options"]):
                   print(f"    {i}. {opt}")
           print()
       return ExitCode.OK

   def cmd_blockers_show(args) -> int:
       st.validate_slug(args.plan, kind="plan slug")
       cfg = load_project_config(args.project.resolve())
       state_path = cfg.state_path(args.plan)
       if not state_path.exists():
           return _die(ExitCode.UNKNOWN_TASK, f"no state at {state_path}")
       data = st.load(state_path, expected_version=st.SCHEMA_VERSION)
       blocker = next(
           (b for b in data.get("blockers", []) if b["id"] == args.blocker_id),
           None,
       )
       if blocker is None:
           return _die(
               ExitCode.UNKNOWN_TASK,
               f"no blocker {args.blocker_id} on {args.plan}",
           )
       print(f"{blocker['id']} [{blocker['phase_id']}]")
       print(f"  asked: {blocker['asked_at']}")
       if blocker.get("answer") is not None:
           print(f"  answer: {blocker['answer']} (at {blocker['answered_at']})")
       print(f"  question: {blocker['question']}")
       if blocker.get("context"):
           print(f"  context: {blocker['context']}")
       if blocker.get("options"):
           print("  Options:")
           for i, opt in enumerate(blocker["options"]):
               print(f"    {i}. {opt}")
       related = [
           e for e in data.get("events", [])
           if e.get("blocker_id") == args.blocker_id
       ]
       if related:
           print("  Events:")
           for e in related:
               print(f"    {e.get('ts', '?')} {e.get('type', '?')}")
       return ExitCode.OK
   ```

4. **Implementation: dispatcher wire-up.**
   In top-level dispatcher (cli.py:651-683), add:
   ```python
   if args.cmd == "blockers":
       if args.blockers_cmd == "list":
           return cmd_blockers_list(args)
       if args.blockers_cmd == "show":
           return cmd_blockers_show(args)
       p_blockers.print_help(sys.stderr)
       return ExitCode.USAGE  # or whatever the existing usage-error code is
   ```

5. **Acceptance.**
   - All 5 new tests green.
   - Full suite green.
   - Manual smoke: `clu blockers --help` shows both subcommands. `clu
     blockers list --help` and `clu blockers show --help` work with
     `--project` and `--plan`. On a real plan with a blocker, output
     matches the formatting above.

6. **Commit + complete.**
   - Structured commit: `small-cli-fixes: phase blockers-cli — clu blockers
     list/show (#32)`.
   - Stage: `end_of_line/cli.py`, `tests/test_blockers.py`. If
     `docs/reference.md` documents per-command CLI shape, add the new
     subcommand there too (read the existing file first).
   - `clu complete --plan small-cli-fixes --phase blockers-cli --token <T>`.

## Failure modes to watch

- **`add_common(p)` injects `--project` + `--plan`** — verify both
  subcommands (list AND show) get these via `add_common(p_blockers_list)`
  and `add_common(p_blockers_show)`. Don't try to call `add_common` on
  the parent `p_blockers` — that won't propagate.
- **`args.blockers_cmd` is None when user runs bare `clu blockers`** —
  dispatcher should print help and exit non-zero. Match how other
  multi-subcommand commands (e.g. `clu queue`, `clu worktree`) handle the
  bare case.
- **Empty options list** — some blockers may have `options: []`. Handle
  with the `if blocker.get("options"):` guard.
- **`ExitCode.UNKNOWN_TASK` vs `ExitCode.NOT_FOUND`** — use whichever
  matches the existing `cmd_answer` no-such-blocker code. Grep for the
  precedent.
- **`SCHEMA_VERSION` mismatch** — old state files might be schema v1 and
  this code lives in a world where the SCHEMA_VERSION constant could be
  bumped by a parallel-running plan. Use `expected_version=st.SCHEMA_VERSION`
  and let mismatches surface naturally.
- **Output formatting** — the issue body's example uses 2-space indent
  for sub-fields. Match that exactly so the operator's existing eye knows
  where to look.
