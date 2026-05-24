# session-arm-task-list — SessionStart hook arms per-plan `--task-list` Monitors automatically

Today the SessionStart hook (`end_of_line/hooks/clu_session_start.py`)
instructs Claude to arm one `clu watch --all --operator` Monitor (the
wedge dashboard channel). The per-plan `--task-list` Monitor — the one
that drives the nested-task-tree UI in Claude — has its arming
instructions buried inside `/clu-plan` Step 5.6, which only fires when
the operator is *authoring* a plan. Fresh sessions walking into
already-queued plans don't trigger that skill, so Claude either fails to
arm the per-plan Monitor entirely OR reaches for `--all` (stripping the
protocol) when the operator asks for "the monitor."

This plan moves the per-plan arming + TaskCreate/TaskUpdate protocol
out of `/clu-plan` SKILL.md and into the SessionStart hook itself.
The hook will detect active plans (STATUS_RUNNING in the current CWD's
registry entries) and emit explicit `Monitor(...)` tool-call shapes —
one per active plan, with the slug pre-filled — alongside the protocol
block. Detection happens in Python (deterministic) so Claude judgment is
removed from the critical path; that's what makes this surefire instead
of "documented and hopefully followed."

The dashboard `--all --operator` Monitor stays as-is — the two channels
are complementary (wedge events vs. live task tree). Single phase;
detection helper + INSTRUCTION extension + tests are one cohesive commit.

## Locked design decisions

### Phase 1 — detection + instruction extension

- **Active-plan filter:** STATUS_RUNNING only. Paused/halted/done plans
  don't emit Monitor events worth tracking. STATUS_RUNNING is what
  `clu watch` itself bootstraps as `in_progress`. Mid-session resume of
  a paused plan is rare; operator can arm manually or restart the session.

- **Detection scope:** current CWD via `os.getcwd()` →
  `registry.entries_for_project(Path(cwd).resolve())`. Same primitive
  `clu_inbox_surface.py:139` (`_resolve_project_root`) uses.

- **Failure tolerance:** corrupt registry, missing state files, schema
  drift → return `[]`. Mirrors `clu_session_start.py:74-82` and
  `registry.load_entry_state` at `registry.py:85-104`. Hook must never
  crash a session start.

- **Instruction shape:** one fenced `Monitor(...)` block per active plan
  with slug pre-filled, followed by a SINGLE shared
  TaskCreate/TaskUpdate protocol block (not per-plan). Protocol content
  compressed from `~/.claude/skills/clu-plan/SKILL.md` lines 327-373.

- **`additionalContext` cap discipline:** existing
  `test_additional_context_under_10k_chars` (line 42-45) checks the
  unconditional `INSTRUCTION` constant — that stays under 9500 chars
  unchanged. Add a runtime-side assertion that worst-case output
  (10 active plans, all expanded) also stays under 9500.

- **Idempotency:** the hook emits *instructions*, not Monitor calls.
  Claude skips arming when a prior Monitor for the same plan is already
  in flight (monitor-lifecycle ship `735fc06` proved survival across
  `/clear` and `/compact`). The dashboard instruction already says this;
  the per-plan block echoes the same line.

- **Conditional emission:** empty active-plans list → per-plan AND
  protocol blocks both omitted. No-plan sessions pay today's context cost.

- **Local import in detection helper:** `from end_of_line import
  registry, state as st` inside the function, not at module top — keeps
  the no-active-plans path cheap and matches `registry.py:93`'s pattern.

## Non-goals

- **Don't touch `/clu-plan` Step 5.6.** Skill's arming block still
  applies during plan authoring (operator just queued and is at-desk).
  SessionStart hook is the cold-start safety net, not a replacement. The
  feedback memory `feedback_clu_plan_task_list_monitor.md` stays current.

- **Don't change `--all --operator` dashboard arming** (#70 wedge
  channel). Stays unconditional in the same INSTRUCTION composition.

- **Don't filter beyond STATUS_RUNNING.** No "recently active" heuristic.
  Anything fancier needs its own design pass.

- **No new `clu watch` flag.** `clu watch --project . --plan <slug>
  --task-list` already exists. This plan only changes what the hook
  *says* about it.

## Files touched

- `end_of_line/hooks/clu_session_start.py` — P1 modified — add
  `_active_plans_for_cwd()` (~15 LOC), `TASK_LIST_PROTOCOL_INSTRUCTION`
  constant (~40 lines compressed), `_per_plan_arming_block()` (~10 LOC),
  extend `main()` to compose conditionally. API hotspot: `main()` return
  shape stays `hookSpecificOutput.additionalContext`; existing
  `INSTRUCTION` constant unchanged (cap test at line 42-45 still passes).

- `tests/test_session_start_hook.py` — P1 modified — add 8 tests for
  empty-plans / one-running / multi-running / non-running-excluded /
  other-project-excluded / corrupt-state-tolerated / protocol-present /
  runtime-under-10k. Reuses existing `SessionStartHookScriptTest` patterns.

## Per-phase done checklist

- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths.
- Call `clu complete --plan session-arm-task-list --phase impl --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| impl | `session-arm-task-list-impl.md` | Detection helper + INSTRUCTION extension + 8 tests | 1h |
