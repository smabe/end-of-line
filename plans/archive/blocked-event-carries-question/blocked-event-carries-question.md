# blocked-event-carries-question

Closes [#46](https://github.com/smabe/end-of-line/issues/46).

## Goal

Make the `EVENT_PHASE_BLOCKED` event payload carry the `question`
field (and optionally `options`) so the `clu watch --task-list`
Monitor stream renders `msg="BLOCKED <id> — <question>"` instead of
the current question-less `msg="BLOCKED <id>"`. Surfaced by
blocker-smoke validation 2026-05-17.

## Diagnosis

- **Hypothesis:** `clu block` writes `question` into the `blockers[]`
  array but emits an `EVENT_PHASE_BLOCKED` event with only `phase` +
  `blocker_id` — not `question`. The watch projector
  (`_task_msg_for`, `_fmt_blocked`) reads `event.get("question")`
  which is `None`, so the rendered msg drops the question.
- **Falsifiable test:** Already run — blocker-smoke's state.json
  events show `{"type": "phase_blocked", "phase": "blocker",
  "blocker_id": "q-1"}` with no `question` field. Confirmed.
- **Test result:** Hypothesis confirmed pre-plan. The fix target is
  the event-producer side.

## Non-goals

- **Path A (state-aware projector).** Issue #46 weighed both paths
  and recommended B. Don't make `project_event_task` cross-reference
  `state.blockers` — keeps the projector event-pure.
- **Schema version bump.** Adding a nullable field to an event
  payload is forward+backward compatible. No `SCHEMA_VERSION` change.
- **Backfilling the `options` field on the event** — projector
  doesn't need it today (msg only renders question text, not
  options). If a future use case wants options in the stream, that's
  a separate plan.
- **Touching `_task_msg_for` / `_fmt_blocked` / projection logic.**
  Already correct; just needs the field present in the event.
- **Existing phase-1 unit tests.** They construct synthetic events
  with `question=` directly; they continue to pass unchanged.

## Files to touch

- `end_of_line/state.py` — `add_blocker` (or wherever
  `EVENT_PHASE_BLOCKED` is appended) writes `question` into the
  event dict alongside `phase` + `blocker_id`.
- `end_of_line/cli.py` — `cmd_block` (if it builds the event itself
  rather than delegating to `state.add_blocker`) writes question
  into the appended event.
- `tests/` — new assertion that the production code path
  (`cmd_block` → state mutation) results in an event with
  `question` field set. Likely in `tests/test_block.py` or wherever
  cmd_block's CLI is tested.

## Failure modes to anticipate

- **Truncation interaction.** `_task_msg_for` calls `_trunc(question,
  100)` — already handles long questions. The event payload should
  store the FULL question, not the truncated one, so iMessage +
  CLI consumers see complete text. Truncation is a projector
  concern, not a storage concern.
- **Empty/None question.** `clu block` may be invoked without
  `--question` (free-text blocker). The event should carry an empty
  string or absent field; `_task_msg_for` already handles both
  via `event.get("question") or ""` and emits the bare
  `"BLOCKED <id>"` fallback.
- **Schema readers in tests** — anything that asserts the exact set
  of fields on `EVENT_PHASE_BLOCKED` could break. Need to grep.
- **`add_blocker` may be called from multiple paths.** Worker
  callback (`clu block`), operator command (unlikely), and possibly
  spawned-task flows. Single producer is the goal; verify there's no
  second event-append site.
- **Test isolation** — touching state.py invalidates ~50 tests that
  use state factories. Run full suite, not just touched-file subset.

## Done criteria

- Production `clu block` invocation results in an
  `EVENT_PHASE_BLOCKED` event dict containing `question` (the same
  string written to `blockers[].question`).
- `project_event_task` and `_fmt_blocked` (unchanged) render
  `msg="BLOCKED <id> — <question>"` against real production events.
- New unit test asserting `cmd_block` → state mutation produces an
  event with `question` field.
- Full suite green.
- Commit message links #46; #46 auto-closes on merge to main.

## Parking lot

(empty)
