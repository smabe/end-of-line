# clu-watch-task-list-protocol — pure projector + status mapping

You are phase `protocol` of `clu-watch-task-list`. Build the pure event-
to-line projector for the task-list protocol. NO I/O, NO stream_loop
integration — just `project_event_task(event, plan_slug, *, verbose)
-> str | None` and a `_TASK_STATUS_MAP` dispatch. Phase `projector`
wires this into the stream loop.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch-task-list.md` § Phase 1. Summary:
- Line shapes: `TASK_CREATE task=<slug>/<phase> status=pending` and
  `TASK_UPDATE task=<slug>/<phase> status=<state> msg="<escaped>"`.
- Plan-scoped events drop `/phase` segment.
- Status enum: pending / in_progress / completed (no "blocked" —
  msg conveys the blocker).
- Msg escaping: double-quote inner content, escape `"` and `\`,
  truncate to 100 chars via `_trunc`.
- Event→status mapping per master (PHASE_STARTED→in_progress,
  PHASE_COMPLETED→completed, BLOCKED→in_progress with blocker payload,
  PLAN_COMPLETED→parent completed, etc.).

## Read first

- `end_of_line/watch.py:13-180` — `_DEFAULT_VISIBLE` / `_VERBOSE_ONLY`
  sets, `_FORMATTERS` dispatch dict, `_trunc` helper, `_fmt_blocked`
  pattern — mirror this shape.
- `end_of_line/watch.py:254-264` — `project_event` signature; build
  `project_event_task` as its sibling, same shape.
- `end_of_line/state.py:78-135` — EVENT_* constant block.
- `end_of_line/cli.py` `append_event` call sites (grep `append_event`)
  — same call sites as clu-watch-events; field shape per event.
- `tests/test_watch_project_event.py` — testing pattern (one test per
  EVENT_* constant, parametrized via `_evt` helper).

## Produce

1. **Failing tests first** (`tests/test_watch_task_protocol.py`, new):
   - **Per-event coverage** — for each event in the visible set with a
     task-mapping:
     - `test_phase_started_emits_task_update_in_progress` — assert
       output is `TASK_UPDATE task=my-plan/foundation status=in_progress
       msg="started (attempt 1)"`.
     - `test_phase_completed_emits_completed` — assert status
       `completed`.
     - `test_phase_blocked_includes_blocker_id_in_msg` — msg contains
       the blocker id substring; status `in_progress`.
     - `test_phase_max_attempts_emits_in_progress_with_halt_marker` —
       msg contains "HALTED" or similar marker.
     - `test_systemic_failure_emits_in_progress_with_signature` — msg
       contains the signature.
     - `test_plan_completed_uses_parent_task_id` — line has
       `task=my-plan` (no `/phase`), status `completed`.
     - `test_paused_uses_parent_task_id` — `task=my-plan`, status
       `in_progress`, msg "paused".
     - `test_resumed_uses_parent_task_id` — same.
     - `test_phase_stalled_msg_stalled` — task includes phase, msg
       "stalled".
   - **Filtered events return None:**
     - `test_task_spawned_returns_none` — not in task mapping → None.
     - `test_worktree_attached_returns_none_default` — verbose-only,
       default → None.
     - `test_worktree_attached_returns_in_progress_with_verbose` —
       verbose=True → emits with msg.
     - `test_unknown_event_returns_none`.
   - **Msg escaping:**
     - `test_msg_with_quotes_escaped` — blocker question containing
       `"` → output msg has `\"`.
     - `test_msg_with_backslash_escaped` — question with `\` →
       output msg has `\\`.
   - **Msg truncation:**
     - `test_long_question_truncated_to_100_chars` — input >100 chars
       → msg ends with `…` and is ≤100 chars total inside quotes.

2. **Implementation.** In `end_of_line/watch.py`, add to the existing
   module (do NOT create a new file):

   ```python
   _TASK_STATUS_MAP: dict[str, str] = {
       st.EVENT_PHASE_STARTED: "in_progress",
       st.EVENT_PHASE_COMPLETED: "completed",
       st.EVENT_PHASE_BLOCKED: "in_progress",
       st.EVENT_PHASE_MAX_ATTEMPTS: "in_progress",
       st.EVENT_SYSTEMIC_FAILURE: "in_progress",
       st.EVENT_PLAN_COMPLETED: "completed",
       st.EVENT_PAUSED: "in_progress",
       st.EVENT_RESUMED: "in_progress",
       st.EVENT_PHASE_STALLED: "in_progress",
   }
   # Verbose-only mapping (only emitted with verbose=True):
   _TASK_VERBOSE_STATUS_MAP: dict[str, str] = {
       st.EVENT_LEASE_EXTENDED: "in_progress",
       st.EVENT_LEASE_EXPIRED: "in_progress",
       st.EVENT_CLAIM_FORCE_RELEASED: "in_progress",
       st.EVENT_ATTEMPTS_RESET: "in_progress",
       st.EVENT_STUCK_BLOCKER_REPINGED: "in_progress",
       st.EVENT_STALLED_CLAIM_NOTIFIED: "in_progress",
   }
   # Plan-scoped events (no /phase segment in task id):
   _PLAN_SCOPED_EVENTS: frozenset[str] = frozenset({
       st.EVENT_PLAN_COMPLETED, st.EVENT_PAUSED, st.EVENT_RESUMED,
   })


   def _escape_msg(s: str) -> str:
       return s.replace("\\", "\\\\").replace('"', '\\"')


   def _task_msg_for(event: dict[str, Any]) -> str:
       """Render the msg field for a TASK_UPDATE based on event type."""
       t = event.get("type")
       if t == st.EVENT_PHASE_STARTED:
           return f"started (attempt {event.get('attempts', 1)})"
       if t == st.EVENT_PHASE_COMPLETED:
           return "completed"
       if t == st.EVENT_PHASE_BLOCKED:
           bid = event.get("blocker_id", "?")
           q = _trunc(event.get("question") or "")
           return f"BLOCKED {bid} — {q}"
       if t == st.EVENT_PHASE_MAX_ATTEMPTS:
           return f"HALTED (max attempts on {event.get('phase')})"
       if t == st.EVENT_SYSTEMIC_FAILURE:
           sig = _trunc(event.get("signature") or "")
           return f"SYSTEMIC FAILURE — {sig}"
       if t == st.EVENT_PLAN_COMPLETED:
           return "plan done"
       if t == st.EVENT_PAUSED:
           reason = _trunc(event.get("reason") or "")
           return f"paused{f' — {reason}' if reason else ''}"
       if t == st.EVENT_RESUMED:
           return "resumed"
       if t == st.EVENT_PHASE_STALLED:
           return "stalled"
       # Verbose-only events — generic msg derived from type
       return t.replace("_", " ")


   def project_event_task(
       event: dict[str, Any],
       plan_slug: str,
       *,
       verbose: bool = False,
   ) -> str | None:
       t = event.get("type")
       if t not in _TASK_STATUS_MAP:
           if not (verbose and t in _TASK_VERBOSE_STATUS_MAP):
               return None
           status = _TASK_VERBOSE_STATUS_MAP[t]
       else:
           status = _TASK_STATUS_MAP[t]

       if t in _PLAN_SCOPED_EVENTS:
           task_id = plan_slug
       else:
           phase = event.get("phase", "?")
           task_id = f"{plan_slug}/{phase}"

       msg = _escape_msg(_task_msg_for(event))
       return f'TASK_UPDATE task={task_id} status={status} msg="{msg}"'
   ```

3. **Acceptance.**
   - ~15 new tests green.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - `grep -n "project_event_task\|_TASK_STATUS_MAP" end_of_line/watch.py`
     shows the new public + private surface.

4. **Commit + complete.**
   - Title: `clu-watch-task-list: phase protocol — pure projector + status mapping`
   - Stage: `end_of_line/watch.py`, `tests/test_watch_task_protocol.py`.
   - `clu complete --plan clu-watch-task-list --phase protocol --token <T>`

## Failure modes to watch

- **Missing event fields** — events may lack `attempts`, `question`,
  `signature`. Default each `.get` with sentinels; never KeyError.
- **EVENT_PHASE_STARTED's `attempts` field naming** — verify the call
  site at `cli.py` (claim_phase event emission) uses `attempts` not
  `attempt`. If mismatched, fix to match the call site, NOT the other
  way around.
- **Msg truncation for short msgs** — `_trunc(s, 100)` should pass
  through unchanged for short inputs. Verify edge: empty string → "".
- **Constant import order** — `_TASK_STATUS_MAP` references
  `st.EVENT_*` constants; ensure they're all defined when this module
  loads. They are (state.py loads first), but if an EVENT_* is
  conditionally defined (like queue v2 events behind getattr), use the
  same `getattr(st, "EVENT_X", None)` + filter-None pattern as
  `_DEFAULT_VISIBLE` uses.
