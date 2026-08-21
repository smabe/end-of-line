# session-start-blockers-surface — put open blockers in front of the session

You are phase `surface` of the `session-start-blockers` plan. The SessionStart
hook already walks the registry and loads each plan's state; you are reading the
open blockers out of that same load and rendering them, with options and a
routing instruction, so the operator can answer in the session. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/session-start-blockers.md`. Summary:

- Blockers get their OWN scan and emit condition. They must NOT sit behind the
  existing liveness gate — a paused plan is terminal, and an SLA-breached
  blocker is exactly what pauses a plan.
- Current project only, via `registry.entries_for_project(cwd)`.
- `MAX_BLOCKERS = 10` and a 9500-char ceiling — both DEFINED HERE. This file has
  no such constants today; the same-named ones live in `clu_inbox_surface.py:33,36`,
  which this phase must not import.
- The instruction names `clu answer --project . --plan <slug> --blocker <q-N>`
  and refuses to guess between multiple open blockers.
- Emit via `hookSpecificOutput.additionalContext` ONLY.

## Read first

- `plans/session-start-blockers.md` `## Findings log` — phase `answer-scope` may
  have recorded something here.
- `end_of_line/hooks/clu_session_start.py` — whole file. `_scan_entries` at
  88-123 (the `except` fallback at 122-123 is part of it), the liveness test at
  117, `main`'s emit gate at 174.
- `end_of_line/state.py:139` — `TERMINAL_STATUSES` includes `STATUS_PAUSED`.
- `end_of_line/supervisor.py:1078-1090` — the SLA rule that sets `PAUSED`.
- `end_of_line/state.py:1108-1115` — `open_blockers`, the projection to use.
- `end_of_line/hooks/clu_inbox_surface.py:37-51, 212-232` — the retired render
  and instruction. Mirror the SHAPE; do not re-import it, and do not revive the
  claim/processed machinery.
- `end_of_line/registry.py:55-57, 79-91` — `entries_for_project`;
  `load_entry_state` returns `None` for every failure mode it enumerates
  (`registry.py:96,102`). "Never raises" is its docstring's claim, not a
  guarantee against an unlisted exception — keep the caller's `try` in place.
- `tests/test_session_start_hook.py` — harness to extend. It has NO blocker
  fixtures today.

## Produce

1. **Failing tests first.** In `tests/test_session_start_hook.py`:
   - `test_open_blocker_is_surfaced_with_options` — question and each numbered
     option appear in `additionalContext`.
   - `test_paused_plan_blocker_is_still_surfaced` — plan status `paused` (the
     SLA case); the blocker still appears. This is the regression the whole
     phase exists to prevent, and it fails loudly against a naive implementation.
   - `test_blockers_from_other_projects_are_not_surfaced` — a blocker registered
     under another root is absent.
   - `test_answered_blocker_is_not_surfaced` — answered blockers disappear with
     no processed-flag bookkeeping.
   - `test_instruction_names_the_blocker_flag` — the emitted instruction contains
     `--blocker`.
   - `test_many_blockers_are_capped_and_output_stays_under_9500` — over
     `MAX_BLOCKERS` open blockers, each with a long question; output is capped,
     asserts `len(additionalContext) <= 9500` (the design ceiling, NOT the
     10,000 hard cap — a test that only checks 10k passes while violating the
     rule), and says how many were not shown.
   - `test_no_blockers_emits_no_blocker_section` — silence when nothing is open.

2. **Implementation.**
   - `end_of_line/hooks/clu_session_start.py`:
     - Define `MAX_BLOCKERS = 10` and `MAX_CONTEXT_CHARS = 9500` beside the
       existing instruction constants, and truncate each question.
     - **`st` is imported INSIDE `_scan_entries` (`:106`), not at module level.**
       Either add the module-level import or keep the use inside that function;
       referring to `st` anywhere else as the file stands is a `NameError`.
     - Collect open blockers inside the loop that already holds `data` — use
       `st.open_blockers(data)`. Do NOT call
       `notify_base.open_blockers_with_details`: it re-runs
       `load_entry_state` per row (`notify_base.py:105-110`), doubling every
       state read on a path with a 5-second timeout.
     - Scope the walk to `registry.entries_for_project(Path(os.getcwd()).resolve())`.
     - Add a `_blockers_block(...)` renderer next to the existing instruction
       constants, and a `BLOCKER_REPLY_INSTRUCTION`.
     - Change the emit condition so blockers alone are enough to emit.
     - Keep the module's fail-open contract: every new path exits 0 and logs to
       `~/.config/clu/session_start_hook.log`.
   - Docs: `docs/operations.md` and `docs/architecture.md` blocker-reply path;
     `README.md` (it currently says the affordance is lost — that becomes wrong);
     `end_of_line/skills/clu-monitor/SKILL.md`.
   - `python3 scripts/gen_skill_manifest.py` after the skill edit, or
     `tests/test_skill_sync.py` fails.

3. **Acceptance.**
   - All 7 new tests green.
   - `python3 -m unittest discover -s tests` fully green; report the count.
   - `basedpyright` clean.
   - Smoke: with an open blocker registered for this project,
     `echo '{}' | python3 end_of_line/hooks/clu_session_start.py` prints JSON
     containing the question, its options, and `--blocker`.
   - `grep -c open_blockers_with_details end_of_line/hooks/clu_session_start.py`
     returns 0.
   - Docs say what ships: `README.md` no longer claims the reply affordance is
     lost, and `docs/operations.md` / `docs/architecture.md` / the clu-monitor
     skill describe the blocker-at-session-start path.

4. **Commit + attest + complete.**
   - Record cross-phase findings in the master's `## Findings log` if any.
   - Commit: `session-start-blockers: phase surface — show open blockers at session start`.
   - Stage explicit paths.
   - After the commit: `clu verify` then `clu attest --simplify`, both with
     `--plan session-start-blockers --phase surface --token <T>`.
   - `clu complete --plan session-start-blockers --phase surface --token <T>`.

## Failure modes to watch

- **The liveness gate is a trap.** `_scan_entries` returns `any_live` from
  `status not in TERMINAL_STATUSES` and `main` returns 0 when nothing is live.
  Reusing that for blockers hides the longest-waiting blocker. The paused-plan
  test is what catches it — write it first.
- **`None` means LIVE in this file and SKIP everywhere else.** `_scan_entries`
  treats an unreadable state as live; `fleet.py`, `top.py`, `webserver.py` and
  `notify_base.py` all skip it. Match the file you are in, and do not "fix" the
  polarity as a drive-by.
- **You are editing a file that is live machine-wide.** The hook is installed by
  absolute path into the operator's `settings.json`, and under `pipx install -e .`
  that path is this working tree. A syntax error breaks every new Claude Code
  session on the machine, silently, because `main()` never runs far enough to
  reach its own logger. Run the smoke command after every edit.
- **The 5-second timeout is real** (`cli.py:2730`) and each entry opens a SQLite
  database. Project-scoping is what keeps this bounded; do not widen the walk.
