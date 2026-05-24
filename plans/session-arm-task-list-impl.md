# session-arm-task-list-impl — SessionStart hook arms per-plan `--task-list` Monitors automatically

You are phase `impl` of the `session-arm-task-list` plan. Add the
per-plan Monitor arming logic to `clu_session_start.py` so the
SessionStart hook detects active plans in CWD and emits explicit
`Monitor(...)` call shapes plus the TaskCreate/TaskUpdate protocol —
removing Claude's judgment from the critical path.

## Locked decisions (do NOT re-litigate)

See `plans/session-arm-task-list.md`. Summary:

- Filter: STATUS_RUNNING only. No paused/halted/done.
- Scope: current CWD via `os.getcwd()` → `registry.entries_for_project`.
- Failures: tolerate silently, return `[]`. Hook must never crash.
- Conditional emission: empty list → omit both per-plan + protocol blocks.
- Single shared protocol block. Don't duplicate per plan.
- Don't touch `/clu-plan` SKILL.md Step 5.6 (at-author path still applies).
- Don't change `--all --operator` dashboard arming (#70 wedge channel).
- Local import in detection helper (avoid module-load cost on no-plan sessions).

## Read first

- `end_of_line/hooks/clu_session_start.py` — module to extend. Existing
  `INSTRUCTION` constant + `main()` shape.
- `end_of_line/hooks/clu_inbox_surface.py:139-155` —
  `_resolve_project_root()` pattern for CWD detection.
- `end_of_line/registry.py:61-63` — `entries_for_project()` API.
- `end_of_line/registry.py:85-104` — `load_entry_state()` API + failure
  semantics (returns None on any recoverable failure).
- `end_of_line/state.py:109-122` — `STATUS_RUNNING` and TERMINAL_STATES set.
- `~/.claude/skills/clu-plan/SKILL.md` lines 327-378 — Step 5.6 arming
  block + "Reacting to task-list protocol notifications" section.
  Compress this content into the new `TASK_LIST_PROTOCOL_INSTRUCTION`
  constant — keep TASK_CREATE/TASK_UPDATE distinctions, `└ ` glyph,
  "Do NOT re-set subject" warning, parent/child subject rules, teardown
  trigger (`status=completed` with no `parent=` field).
- `tests/test_session_start_hook.py:24-65` — `SessionStartHookScriptTest`
  pattern to mirror.

## Produce

1. **Failing tests first.** Extend `tests/test_session_start_hook.py`
   with a new `SessionStartActivePlansTest` class. Tests:

   - `test_no_active_plans_omits_per_plan_block`: empty registry →
     additionalContext does NOT contain `--task-list` or `TASK_CREATE`.
     Dashboard instruction still present.
   - `test_one_running_plan_emits_arming_block`: register one plan with
     `status="running"` in `cwd` → additionalContext contains `Monitor(`
     and `--plan <slug> --task-list`.
   - `test_multiple_running_plans_arm_each`: 3 running plans → 3
     `Monitor(` blocks, ONE protocol block.
   - `test_non_running_plans_excluded`: paused / halted / done →
     excluded; if all plans are non-running, per-plan block omitted.
   - `test_other_project_plans_excluded`: plans for a different
     `project_root` than CWD → excluded.
   - `test_corrupt_state_tolerated`: state file is malformed JSON →
     `_active_plans_for_cwd()` returns `[]`, hook exits 0 with just
     dashboard instruction.
   - `test_protocol_block_present_when_plans_active`: additionalContext
     contains `TASK_CREATE`, `TASK_UPDATE`, `└ ` (U+2514 + space),
     `Do NOT re-set subject`.
   - `test_runtime_output_under_10k_with_max_plans`: 10 active plans
     → full additionalContext stays under 9500 chars.

   Setup pattern: each test uses `tempfile.TemporaryDirectory()` for
   `XDG_CONFIG_HOME` + a fake project dir with
   `plans/.orchestrator/<slug>.state.json`. Patch `os.getcwd` to return
   the fake project dir. Use `registry.register()` to populate, write
   state files with the schema `load_entry_state` expects (use the
   public `state.save()` API or mirror what `state.initialize_plan()`
   produces — don't hand-roll `{"status": "running"}` without the
   version field, or `load_entry_state` returns None and the filter
   looks broken).

2. **Implementation.**

   In `end_of_line/hooks/clu_session_start.py`:

   - **Add `_active_plans_for_cwd() -> list[str]`** (~15 LOC):
     ```python
     def _active_plans_for_cwd() -> list[str]:
         """Return slugs with status=running in the current CWD's
         registry entries. Tolerates all failures by returning []."""
         try:
             from end_of_line import registry, state as st
             cwd = Path(os.getcwd()).resolve()
             slugs: list[str] = []
             for entry in registry.entries_for_project(cwd):
                 data = registry.load_entry_state(entry)
                 if data is None:
                     continue
                 if data.get("status") == st.STATUS_RUNNING:
                     slugs.append(entry.plan_slug)
             return slugs
         except Exception:
             return []
     ```
     Local import (not module-level) — matches `registry.py:93`.

   - **Add `TASK_LIST_PROTOCOL_INSTRUCTION` module constant** (~40
     lines): compressed version of `/clu-plan` SKILL.md "Reacting to
     task-list protocol notifications" (lines 327-373). MUST contain
     literally:
     - `TASK_CREATE` and `TASK_UPDATE` (event line shapes)
     - `subject = "└ <phase>"` (U+2514 box-drawing char + space)
     - `Do NOT re-set subject` warning
     - parent `subject = <slug>` rule
     - teardown trigger: `TASK_UPDATE task=<slug> status=completed` with
       no `parent=` field → `TaskStop`
     - paused plans are NOT teardown triggers (operator can resume)

   - **Add `_per_plan_arming_block(slugs: list[str]) -> str`** (~10 LOC):
     intro sentence acknowledging idempotency (skip if Monitor already
     in flight from a prior session, per monitor-lifecycle #69), then
     one fenced `Monitor(...)` block per slug with:
     - `command="clu watch --project . --plan <slug> --task-list"`
     - `persistent=True`
     - `timeout_ms=3600000`
     - `description="clu <slug> phase progress"`

   - **Extend `main()`**: after composing the unconditional
     `INSTRUCTION` into `additional_context`, call
     `_active_plans_for_cwd()`. If non-empty, append
     `_per_plan_arming_block(slugs)` + `TASK_LIST_PROTOCOL_INSTRUCTION`
     to the additionalContext. Same try/except envelope — never let a
     detection bug crash the hook.

3. **Acceptance.**
   - All 8 new tests green.
   - Existing 6 tests in `SessionStartHookScriptTest` still green
     (especially `test_additional_context_under_10k_chars`).
   - `python3 -m unittest discover -s tests` → ~1369 → ~1377 (8 new).
   - Manual smoke: in a clu-managed project with an active plan,
     `echo '' | python3 -m end_of_line.hooks.clu_session_start` →
     output contains `--plan <slug> --task-list` and protocol block.
   - Grep no leakage: per-plan content does NOT appear when registry
     is empty.

4. **Commit + complete.**
   - Structured commit:
     - Title: `hook: SessionStart arms per-plan --task-list Monitors automatically`
     - Why: eliminate recurring "Claude sets up clu watch wrong / not
       at all" friction by removing judgment from the critical path.
     - What's new: `_active_plans_for_cwd()` detection + per-plan
       `Monitor(...)` arming block + TaskCreate/TaskUpdate protocol
       block emitted conditionally on STATUS_RUNNING.
     - Under the hood: `registry.entries_for_project(cwd)` →
       `STATUS_RUNNING` filter; all detection failures return `[]` to
       preserve hook crash-immunity. Local import inside helper.
     - Tests: ~1369 → ~1377.
   - Stage explicit paths: `end_of_line/hooks/clu_session_start.py`,
     `tests/test_session_start_hook.py`.
   - `clu complete --plan session-arm-task-list --phase impl --token <T>`.

## Failure modes to watch

- **Mocking `os.getcwd()` in tests** — must point at the fake project
  dir. Use `mock.patch.object(os, "getcwd", return_value=str(fake_dir))`.
  Otherwise tests scan the operator's real registry and become flaky.

- **State file format** — `load_entry_state` returns None on schema
  mismatch. Construct test state files with the same schema
  `state.save()` produces; don't hand-roll a minimal `{"status":
  "running"}` JSON without the version field, or `load_entry_state`
  will return None and the test will look like the filter is broken.

- **U+2514 glyph literal** — the box-drawing character must be literal
  in `TASK_LIST_PROTOCOL_INSTRUCTION`. Don't substitute with ASCII
  `|-`. The protocol-presence test asserts on the literal glyph.

- **Local import discipline** — `from end_of_line import registry,
  state as st` inside `_active_plans_for_cwd`, NOT at module top.
  Top-level forces registry's XDG guards to evaluate on every
  SessionStart, even for users with no registered plans.

- **`additionalContext` 10K cap** — existing constant-level test
  only checks the unconditional `INSTRUCTION`. The runtime-side
  assertion must construct 10 plans and read the composed
  additionalContext to confirm it stays under 9500 chars.
