# queue-worker-callback-cli — argparse flags + worker-mode validation

You are phase `cli` of `queue-worker-callback`. Extend the `queue add`
subparser with `--token` / `--plan` / `--phase` / `--reason` flags and
validate combinations at runtime. NO worker-mode execution yet — the
worker branch in `cmd_queue_add` is a stub that exits `_die(GENERIC,
"worker-mode queue add not yet implemented")`. Phase `dispatch` fills
the body.

## Locked decisions (do NOT re-litigate)

See `plans/queue-worker-callback.md` § Phase 2. Summary:
- `--token` presence is the discriminator. With `--token`:
  - `--plan` and `--phase` required;
  - single positional slug (multi-slug forbidden);
  - `--front` forbidden;
  - `--reason TEXT` optional (also allowed in operator mode).
- Validation via runtime checks inside `cmd_queue_add`, NOT argparse
  mutually-exclusive groups. Exit `ExitCode.GENERIC` on bad combos.

## Read first

- `end_of_line/cli.py:403-436` — current `queue add` argparse block.
- `end_of_line/cli.py:1726-1761` — current `cmd_queue_add` head
  (slug validation + project resolution sections we'll insert before).
- `end_of_line/cli.py:2874-2904` — `cmd_spawn` argparse for the
  `--token` / `--plan` / `--phase` flag shape to mirror (grep for
  the spawn subparser definition near the other sub.add_parser calls).
- `tests/test_queue_add.py` — existing test patterns.

## Produce

1. **Failing tests first** (`tests/test_queue_worker_cli.py`, new):
   - `test_token_alone_rejected_missing_plan_phase` — call `main([
     "queue", "add", "foo", "--token", "T", "--project", str(p)])`,
     assert `ExitCode.GENERIC`, stderr mentions "--plan" and
     "--phase".
   - `test_token_with_front_rejected` — call with `--token T --plan X
     --phase Y --front`, assert `ExitCode.GENERIC`, stderr mentions
     "--front".
   - `test_token_with_multi_slug_rejected` — `["queue", "add", "foo",
     "bar", "--token", "T", "--plan", "X", "--phase", "Y", ...]`,
     assert `ExitCode.GENERIC`, stderr mentions "single slug".
   - `test_token_combo_passes_parse_layer` — full valid worker-mode
     args; assert stub branch fires (currently returns GENERIC with
     "worker-mode queue add not yet implemented" — assert stderr
     matches that sentinel).
   - `test_operator_mode_unchanged` — existing happy path still
     returns `ExitCode.OK` (regression guard).
   - `test_reason_accepted_in_operator_mode` — operator add with
     `--reason "follow-up"` succeeds; queue entry has
     `reason: "follow-up"`.

2. **Implementation.**
   - `end_of_line/cli.py` argparse block (around line 413):
     - Add `p_queue_add.add_argument("--token", default=None, ...)`.
     - Add `p_queue_add.add_argument("--plan", dest="source_plan",
       default=None, ...)`.
     - Add `p_queue_add.add_argument("--phase", dest="source_phase",
       default=None, ...)`.
     - Add `p_queue_add.add_argument("--reason", default=None, ...)`.
   - `cmd_queue_add` (line 1726): at the top, before slug validation,
     add the mutual-exclusion gate:
     ```python
     if args.token is not None:
         if args.source_plan is None or args.source_phase is None:
             return _die(ExitCode.GENERIC,
                 "--token requires --plan and --phase")
         if args.front:
             return _die(ExitCode.GENERIC,
                 "--front is operator-only (forbidden with --token)")
         if len(args.slugs) != 1:
             return _die(ExitCode.GENERIC,
                 "--token requires a single slug")
         return _cmd_queue_add_worker(args)  # stub in this phase
     if args.source_plan is not None or args.source_phase is not None:
         return _die(ExitCode.GENERIC,
             "--plan/--phase require --token (worker mode only)")
     # ... existing operator body unchanged ...
     ```
   - Stub `_cmd_queue_add_worker(args) -> int`: `return _die(
     ExitCode.GENERIC, "worker-mode queue add not yet implemented")`.
     Phase `dispatch` replaces this body.
   - In operator-mode entry creation (around line 1785), add the four
     new fields (all `None`) plus thread `reason` from args:
     ```python
     {"slug": slug, "added_at": st.utcnow(), "added_by": "operator",
      "position_at_add": "front" if args.front else "tail",
      "source_plan": None, "source_phase": None,
      "source_token_fp": None, "reason": args.reason}
     ```

3. **Acceptance.**
   - 6 new tests green.
   - Existing `test_queue_add.py` tests still green.
   - Full suite green.

4. **Commit + complete.**
   - Title: `queue-worker-callback: phase cli — argparse flags + worker-mode gate (#17)`
   - Stage: `end_of_line/cli.py`, `tests/test_queue_worker_cli.py`.
   - `clu complete --plan queue-worker-callback --phase cli --token <T>`

## Failure modes to watch

- **Argparse `dest=` clash with operator `--plan`** — there's no
  operator `--plan` on `queue add` today (it's a `--project`-only
  command). But verify by searching the parser block. If a conflict
  surfaces, rename to `--source-plan` / `--source-phase`; lockable
  late decision.
- **`--reason` value leaking into argparse SystemExit** — argparse's
  default error path doesn't go through `_die`. Test the full happy
  path through `main([...])` to make sure no SystemExit escapes.
