# task-list-nesting

Closes [#40](https://github.com/smabe/end-of-line/issues/40).

## Goal

Make `clu watch --task-list` signal plan→phase hierarchy so the rendered
TaskCreate UI in Claude shows phases nested under their parent plan
instead of flat siblings. Originally-requested visual; smoke-validated
flat on 2026-05-17 with three rows at the same indent.

## Non-goals

- **#41 (TASK_CREATE_BATCH)** — separate issue, deferred.
- **#42 (BLOCKED/HALTED smoke)** — separate issue, deferred.
- **Claude Code TaskCreate API changes** — verified flat
  (claude-code-guide pass 2026-05-17): no `parent_id`, no
  `addBlockedBy`-driven indent, no metadata-key hierarchy. The
  on-wire `parent=` is purely a hint; SKILL.md translates it into
  a `└ ` glyph prefix on the child's `subject`.
- **Migrating plan-scoped events** — `EVENT_PLAN_COMPLETED`,
  `EVENT_PAUSED`, `EVENT_RESUMED` already target the parent task_id
  (`_PLAN_SCOPED_EVENTS` set in watch.py:207-209); no `parent=` field
  on those lines.
- **Out-of-order ad-hoc task creation** — SKILL.md already documents the
  "create on-the-fly with the update's status" fallback (lines 275-278).
  We add `parent=` to TASK_UPDATE so that path can also nest correctly,
  but we don't redesign the fallback itself.

## Files to touch

- `end_of_line/watch.py` — `bootstrap_task_list` emits
  `TASK_CREATE task=<slug>/<phase> parent=<slug> status=pending` on
  child lines (parent line stays untouched). `project_event_task`
  appends ` parent=<slug>` on every phase-scoped TASK_UPDATE; absent
  for `_PLAN_SCOPED_EVENTS`.
- `tests/test_watch_task_bootstrap.py` — assert exact line shape
  including `parent=<slug>` on child lines, absence on parent line.
- `tests/test_watch_task_protocol.py` — assert `parent=<slug>` on
  phase-scoped TASK_UPDATE lines; assert absence on plan-scoped lines
  (`test_plan_completed_uses_parent_task_id`,
  `test_paused_uses_parent_task_id`, `test_resumed_uses_parent_task_id`).
- `tests/test_watch_task_stream.py` — end-to-end stream loop emits
  full new line shape.
- `end_of_line/skills/clu-plan/SKILL.md` — § "Reacting to task-list
  protocol notifications": update line-shape docs to describe
  `parent=` field. Prescribe **the exact glyph**: when `parent=X`
  is present, the agent renders the child subject as
  `└ <phase-id>` (single box-drawing char + space + phase id) so
  all sessions look the same. Verified flat-API constraint — this
  is the ONLY visual-nesting path.
- `docs/operations.md` — § "Task-list mode" gains a one-paragraph
  description of the `parent=` field and the `└ ` render rule.
- `docs/reference.md` — API ref at lines 689-703 quotes the line
  shape; update to include `parent=` on phase-scoped lines.
- `README.md` — line 39 + line 189 mention the protocol; light
  touch to match new shape if the quoted example needs it.
- `tests/test_task_list_skill_wire.py` — asserts SKILL.md mentions
  protocol strings (lines 26, 30); confirm assertions still pass
  (or update if they depend on exact old shape).
- `tests/test_watch_task_stream.py` — additional fixtures at
  lines 95-102 (bootstrap_ordering), 116, 130, 162-163 (negative
  assertions on what's NOT in output) — verify each still holds.

## Failure modes to anticipate

- **TaskCreate UI ignores `parent=` entirely.** Confirmed flat. Visual
  nesting comes from SKILL.md instructing the agent to prepend `└ ` to
  the child's `subject`. The on-wire `parent=` is purely the hint that
  triggers the prefix.
- **Glyph drift across sessions** — without SKILL.md locking the exact
  character, different sessions could render `└` vs `↳` vs ` ` and
  visually fragment. SKILL.md must specify `└ ` verbatim.
- **Mid-stream subject churn** — every TASK_UPDATE carries `parent=`
  too. SKILL.md must NOT re-set `subject` on every TaskUpdate (would
  thrash the row), and must NOT strip `└ ` mid-stream (would un-nest
  visually). Only the initial TaskCreate sets the prefixed subject;
  TaskUpdate uses `taskId` to address the existing row and only
  updates `status` + `description` / msg.
- **Field ordering matters for the parser.** Currently
  `TASK_CREATE task=X status=pending` is positional-feeling. Adding
  `parent=Y` between `task=` and `status=` is the natural read. The
  SKILL.md parse rule has to be order-tolerant since this is human
  parsing, not strict.
- **Test fixtures hard-code expected lines** — every assertion in
  `test_watch_task_bootstrap.py` that says `"TASK_CREATE task=X/Y
  status=pending"` will break. Same for the protocol tests. Update
  them in lockstep.
- **Single-phase plans** (`test_bootstrap_single_phase_master_emits_parent_only`)
  — when there's no Sessions index, only the parent line emits.
  `parent=` is absent for parent. Keep this case untouched.
- **Worker may not have installed updated SKILL.md** — old SKILL.md
  parsers will see `parent=foo` and either error or treat it as part
  of the task name. The parse rule in old SKILL.md is permissive
  enough (regex-style), so the worst case is the new field becomes
  invisible — graceful degradation.

## Done criteria

- `bootstrap_task_list` emits `parent=<slug>` on every
  `TASK_CREATE task=<slug>/<phase>` line; **absent** on the parent
  `TASK_CREATE task=<slug>` line.
- `project_event_task` appends `parent=<slug>` to every phase-scoped
  TASK_UPDATE; **absent** for `_PLAN_SCOPED_EVENTS`
  (PLAN_COMPLETED, PAUSED, RESUMED).
- All existing watch tests pass with the new line shape (updated
  assertions where they hard-code old output).
- `/clu-plan` SKILL.md updated with: (a) the new line shape in
  the "two line shapes" doc block (b) the **exact glyph** for
  child subjects (`└ <phase-id>`) so all sessions render the
  same tree.
- `docs/operations.md` § "Task-list mode" mentions `parent=`.
- Manual smoke (or unit smoke if API confirms no visual nesting):
  re-run an `adventure-time-smoke`-style plan and verify either
  the UI now nests, or the subject-prefix glyph renders the tree
  visually. Receipts in commit message.
- Full test suite green (737 → 740-ish, +3-4 new assertions).

## Parking lot

(empty)
