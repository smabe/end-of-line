# clu-ship-ergonomics-cli — hook prompt, --project defaults, watch fleet mode (#1 #2 #3)

You are phase `cli` of the `clu-ship-ergonomics` plan. Fix the
operator-facing first-impression bugs from the 2026-05-23 field
session: broken `clu answer` syntax in the inbox-hook + `render_blocker`,
alarming `AttributeError` traceback when `--project` is omitted from
`ship` / `blockers`, and the `clu watch --all --task-list` mutex
blocking single-Monitor fleet view.

One commit; suite green; `clu complete`.

## Locked decisions (do NOT re-litigate)

See `plans/clu-ship-ergonomics.md`. Summary:

- Drop `<blocker_id>` from both the inbox-hook `INSTRUCTION` literal
  AND `render_blocker`'s `answer_cmd` f-string. Actual CLI is
  `clu answer [--project P] [--plan SLUG] <answer>` — one positional.
- `cmd_ship`, `cmd_blockers_list`, `cmd_blockers_show` all switch to
  the existing `_resolve_project_arg(args)` helper at
  `cli.py:2764-2774`. No new helper.
- Other `required=True --project` subcommands stay required — out of
  scope (`archive`, `validate`, `integrate`, `worktree-attach`,
  `worktree-reattach`, `doctor`, `migrate-archive`).
- `clu watch --all --task-list` works by deleting 4 lines at
  `cli.py:3354-3357`. `watch.py` already streams per-plan task IDs.
- The iMessage reply hint at `state_blocker.py:113-117`
  (`Reply: <plan_slug> <number>`) is a SEPARATE grammar parsed by
  `notify_inbound.REPLY_RE`. DO NOT touch.

## Read first

- `end_of_line/hooks/clu_inbox_surface.py:42-48` — `INSTRUCTION`
  literal that goes wrong.
- `end_of_line/state_blocker.py:100-127` — `render_blocker`; bug at
  L104 `answer_cmd` f-string, reused at L126.
- `end_of_line/cli.py:822-833` — actual `cmd_answer` argparse (single
  positional `answer`, no `blocker_id`).
- `end_of_line/cli.py:2764-2774` — `_resolve_project_arg` helper.
- `end_of_line/cli.py:468` — `cmd_ship` `--project` argparse line.
- `end_of_line/cli.py:5043` — `cmd_blockers_list` `args.project.resolve()`.
- `end_of_line/cli.py:5065` — `cmd_blockers_show` `args.project.resolve()`.
- `end_of_line/cli.py:3354-3357` — `cmd_watch` mutex block to delete.
- `end_of_line/watch.py:266-376` — task-list protocol (confirm per-plan
  output; no source changes needed here).
- `tests/test_notify_render.py:46-49` — current wrong assertion + comment.
- `tests/test_watch_task_cli.py:57-66` — current mutex test to flip.
- `tests/test_answer.py:51-68` — pattern for testing `cmd_answer`
  with/without `--project`; mirror this for blockers tests.
- `grep -rn render_blocker end_of_line/` — confirm callers; only
  `notify.py` re-export and `cli.py:4944` (blockers show) consume
  the rendered text without parsing the command.
- `tests/` — grep for any existing test of `clu_inbox_surface` or
  `INSTRUCTION`. Create `tests/test_clu_inbox_surface.py` if absent.

## Produce

1. **Failing tests first.** Write before any source edits, run, confirm
   they fail, THEN implement.

   - `tests/test_clu_inbox_surface.py` (verify path; create if missing):
     - `test_instruction_uses_correct_clu_answer_syntax`: import
       `INSTRUCTION` from `end_of_line.hooks.clu_inbox_surface`;
       assert `"clu answer --plan <slug> <answer>"` substring; assert
       `"<blocker_id>"` is NOT in `INSTRUCTION`.

   - `tests/test_notify_render.py:46-49` — update
     `test_includes_copy_pastable_clu_answer_command`: assert body
     contains `f"clu answer --plan {PLAN} <choice>"` (NOT BID). Drop
     the stale comment at L48.

   - `tests/test_blockers*.py` (verify path with `ls tests/test_blockers*`;
     if multiple files, pick the one covering CLI dispatch — likely
     `tests/test_blockers_cli.py`):
     - `test_blockers_list_defaults_project_to_cwd`: run
       `python -m end_of_line.cli blockers list --plan <slug>` from
       a project's CWD with NO `--project`; assert no `AttributeError`
       traceback, exit code is a clean `_die` (`UNKNOWN_TASK` or `OK`)
       or normal output.
     - `test_blockers_show_defaults_project_to_cwd`: same shape for
       `blockers show --plan <slug> <bid>`.

   - `tests/test_ship*.py` (verify path):
     - `test_ship_defaults_project_to_cwd`: run
       `clu ship --plan X --check` from a project CWD with no
       `--project`; assert argparse accepts it (no required-arg error).

   - `tests/test_watch_task_cli.py:57-66`: rename
     `test_task_list_and_all_mutually_exclusive` to
     `test_task_list_with_all_emits_per_plan_lines` (or invert the
     existing assertion); assert combination does NOT `_die`; assert
     output contains `TASK_CREATE` lines with at least two distinct
     plan slugs in the `task=` field.

   Run `python3 -m unittest <new tests>` and confirm RED before moving
   on.

2. **Implementation.**

   - `end_of_line/hooks/clu_inbox_surface.py:42-48` — rewrite the
     `INSTRUCTION` literal:
     ```python
     INSTRUCTION = (
         "\nIf the user's next message reads as a reply to one of these "
         "blockers (letter, number, or natural pick), call "
         "`clu answer --plan <slug> <answer>` via Bash. "
         "If multiple blockers are open and the reply is ambiguous, ask "
         "the user which plan they mean — don't guess.\n"
     )
     ```

   - `end_of_line/state_blocker.py:104` — change f-string to:
     ```python
     answer_cmd = f"clu answer --plan {plan_slug} <choice>"
     ```
     The `blocker_id` parameter remains in the function signature
     (used in the body text at L115); only the `answer_cmd` line
     drops it.

   - `end_of_line/cli.py:468` — change `cmd_ship` argparse:
     ```python
     p_ship.add_argument("--project", type=Path, default=None)
     ```
     Then find the `cmd_ship` body callsites of
     `args.project.resolve()` (the exploration found ~L4076 and
     ~L4101; verify exact line numbers). Replace each with
     `_resolve_project_arg(args)`.

   - `end_of_line/cli.py:5043` (`cmd_blockers_list`) — replace
     `cfg = load_project_config(args.project.resolve())` with
     `cfg = load_project_config(_resolve_project_arg(args))`.

   - `end_of_line/cli.py:5065` (`cmd_blockers_show`) — same fix.

   - `end_of_line/cli.py:3354-3357` — delete the 4-line mutex block:
     ```python
     if task_list_mode and all_mode:
         return _die(ExitCode.GENERIC,
                     "--task-list requires --plan or single-project "
                     "(mutually exclusive with --all)")
     ```

   - Test files updated to match per step 1.

3. **Acceptance.**

   - All new tests green; all previously-modified tests green.
   - Full suite: `python3 -m unittest discover -s tests`. Report
     pass count (expect ~1351 → 1356-1358).
   - Manual smoke (run from `/Users/smabe/projects/end-of-line` CWD,
     no `--project` flag):
     - `python3 -m end_of_line.cli ship --check 2>&1 | head -5` —
       no `AttributeError`; usage or actual check output.
     - `python3 -m end_of_line.cli blockers list 2>&1 | head -5` —
       no `AttributeError`.
     - `python3 -m end_of_line.cli watch --all --task-list 2>&1 | head -3` —
       emits `TASK_CREATE` lines, no `_die`.
   - `grep -n "blocker_id" end_of_line/hooks/clu_inbox_surface.py` —
     no match.
   - `grep -n "blocker_id} <choice>" end_of_line/state_blocker.py` —
     no match.

4. **`/code-review`** (mandatory; diff is >1 file and >30 LOC).
   Apply ≤5 LOC mechanical fixes in the same commit per CLAUDE.md
   "Apply review findings, don't park them."

5. **Commit + complete.**
   - Structured commit:
     ```
     clu-ship-ergonomics: phase cli — hook prompt + --project defaults + watch fleet mode

     ## Why
     ...

     ## What's new
     ...

     ## Under the hood
     ...

     ## Tests
     ...

     Co-Authored-By: ...
     ```
     Reference `docs/design-briefs/clu-ship-field-feedback.md` (frictions
     #1 #2 #3) in the body. NO `closes #N` (no GH issue per plan
     decision).
   - Stage explicit paths: `end_of_line/hooks/clu_inbox_surface.py`,
     `end_of_line/state_blocker.py`, `end_of_line/cli.py`, plus each
     modified/new test file.
   - `clu complete --plan clu-ship-ergonomics --phase cli --token <T>`.

## Failure modes to watch

- **Other callers of `render_blocker`.** Before changing L104, grep
  `render_blocker` across `end_of_line/`. Confirm callers consume
  rendered text (not parse). The grep already showed `notify.py:21`
  (re-export), `notify.py:168` (comment), `notify.py:174` (__all__),
  and `cli.py:4944` (`cmd_blockers_show` display). All safe.
- **iMessage reply hint vs CLI hint.** The line `Reply: <plan_slug>
  <number>` at `state_blocker.py:113-117` is parsed by
  `notify_inbound.REPLY_RE`. DO NOT change. Only modify `answer_cmd`
  (the Terminal copy-paste line).
- **`tests/test_clu_inbox_surface.py` may not exist.** If absent,
  create with minimal scaffold; mirror import style from another
  `tests/test_*hook*.py` or `tests/test_*surface*.py` file.
- **`_resolve_project_arg` is in `cli.py`.** No new import needed —
  same module. Confirm with `grep -n _resolve_project_arg
  end_of_line/cli.py | head -5` if unsure.
- **`cmd_blockers_show` and `cmd_blockers_list` are both registered
  via `add_common()`.** The argparse layer already declares
  `default=None`. The bug is the runtime `.resolve()` call, not the
  argparse declaration. Don't change argparse for those.
- **The exact line numbers in `cmd_ship` body may have drifted.**
  Re-grep `args.project.resolve()` inside `cmd_ship` to find the
  actual sites before editing.
