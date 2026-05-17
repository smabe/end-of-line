# clu-watch-events — pure event projector with per-EVENT_* coverage

You are phase `events` of `clu-watch`. Build the pure-function event
projector in a new `end_of_line/watch.py` module. NO streaming, NO
CLI — just `project_event(event, plan_slug, *, verbose=False) -> str
| None` and a complete TDD case per `EVENT_*` constant.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch.md` § Phase 1. Summary:
- New module `end_of_line/watch.py`. Pure function.
- Returns `None` for verbose-only events when `verbose=False`.
- Line shape: `<slug>/<phase>: <transition>` for phase-scoped
  events; drop `/<phase>` for plan-scoped events.
- Blocker prompts include the blocker id.
- Truncate question/reason fields to 100 chars.

## Read first

- `end_of_line/state.py:78-135` — full `EVENT_*` constant block.
- `end_of_line/state.py:275-298` — `append_event` (defines the
  event dict shape: `type`, `ts`, plus the per-event fields).
- `end_of_line/cli.py:887,896,2151,2374,2487,2752,2771,2791,2818,
  2858,2860,2899,2930,2958` — every `append_event` call site shows
  what fields each EVENT_* type carries. Read at least 5 to see
  the shape variety.
- `end_of_line/notify.py` — existing event-rendering precedent
  (different audience: iMessage). Don't reuse the renderer
  directly; cite for stylistic cues only.

## Produce

1. **Failing tests first**
   (`tests/test_watch_project_event.py`, new file):
   - **Per-event coverage** — one test per EVENT_* constant. Build a
     minimal `event` dict with the fields the real call-site
     produces, call `project_event(event, "my-plan")`, assert the
     line shape. Group via a parametrized helper:
     ```python
     def _evt(type, **fields):
         return {"type": type, "ts": "2026-05-17T10:00:00Z", **fields}

     class ProjectEventTestCase(unittest.TestCase):
         def test_phase_started(self):
             out = project_event(
                 _evt(st.EVENT_PHASE_STARTED, phase="foundation",
                      attempts=1), "my-plan")
             self.assertEqual(out, "my-plan/foundation: started (attempt 1)")
         # ... one test per default-visible event ...
     ```
   - **Verbose filtering** — for each verbose-only event:
     - `test_<event>_filtered_default` — `verbose=False` →
       `project_event` returns `None`.
     - `test_<event>_shown_with_verbose` — `verbose=True` →
       returns a string.
   - **Blocker payload formatting:**
     - `test_phase_blocked_includes_blocker_id` — line contains
       blocker id substring.
     - `test_phase_blocked_truncates_long_question` — question >
       100 chars truncated with ellipsis.
   - **Plan-scoped events drop `/phase`:**
     - `test_plan_completed_no_phase_segment` — line is
       `my-plan: PLAN DONE` (or chosen shape), no `/something:`
       in it.
     - `test_paused_drops_phase_segment` — same shape.
   - **Unknown event types** — `project_event(_evt("garbage"), ...)`
     returns `None`. (Forward-compat: future events don't crash.)

2. **Implementation.**
   - `end_of_line/watch.py`:
     ```python
     """Streaming projection of plan state events for AI-agent
     consumption (Claude's Monitor tool). See plans/clu-watch.md."""
     from __future__ import annotations
     from typing import Any
     from . import state as st

     _DEFAULT_VISIBLE = frozenset({
         st.EVENT_PHASE_STARTED, st.EVENT_PHASE_COMPLETED,
         st.EVENT_PHASE_BLOCKED, st.EVENT_BLOCKER_ANSWERED,
         st.EVENT_BLOCKER_CONSUMED, st.EVENT_BLOCKER_SLA_EXCEEDED,
         st.EVENT_PHASE_MAX_ATTEMPTS, st.EVENT_PHASE_STALLED,
         st.EVENT_TASK_SPAWNED, st.EVENT_TASK_COMPLETED,
         st.EVENT_PLAN_COMPLETED, st.EVENT_DISPATCH_FAILED,
         st.EVENT_SYSTEMIC_FAILURE, st.EVENT_PAUSED, st.EVENT_RESUMED,
         st.EVENT_RETRY_REQUESTED, st.EVENT_QUEUE_POPPED,
         st.EVENT_WORKTREE_MISSING, st.EVENT_WORKTREE_CONFLICT_WARNING,
         # Queue v2 (added if defined at import time)
         getattr(st, "EVENT_QUEUE_APPENDED", None),
         getattr(st, "EVENT_QUEUE_REJECTED", None),
     }) - {None}

     _VERBOSE_ONLY = frozenset({
         st.EVENT_LEASE_EXPIRED, st.EVENT_LEASE_EXTENDED,
         st.EVENT_CLAIM_FORCE_RELEASED, st.EVENT_ATTEMPTS_RESET,
         st.EVENT_STUCK_BLOCKER_REPINGED, st.EVENT_STALLED_CLAIM_NOTIFIED,
         st.EVENT_WORKTREE_ATTACHED, st.EVENT_WORKTREE_CLEANED,
         st.EVENT_WORKTREE_RETAINED_AHEAD,
     })

     def _trunc(s: str | None, n: int = 100) -> str:
         if not s:
             return ""
         return s if len(s) <= n else s[:n - 1] + "…"

     def project_event(
         event: dict, plan_slug: str, *, verbose: bool = False,
     ) -> str | None:
         t = event.get("type")
         if t in _VERBOSE_ONLY and not verbose:
             return None
         if t not in _DEFAULT_VISIBLE and t not in _VERBOSE_ONLY:
             return None
         # ... per-type formatting, dispatch dict ...
     ```
   - Dispatch dict approach (preferred over if-chain):
     ```python
     _FORMATTERS: dict[str, Callable[[str, dict], str]] = {
         st.EVENT_PHASE_STARTED: lambda slug, e:
             f"{slug}/{e['phase']}: started (attempt {e.get('attempts', 1)})",
         st.EVENT_PHASE_COMPLETED: lambda slug, e:
             f"{slug}/{e['phase']}: completed",
         st.EVENT_PHASE_BLOCKED: lambda slug, e:
             f"{slug}/{e['phase']}: BLOCKED {e.get('blocker_id', '?')} — {_trunc(e.get('question'))}",
         # ... one entry per event type ...
     }
     ```
   - Module exports: `project_event`. Keep `_DEFAULT_VISIBLE` /
     `_VERBOSE_ONLY` / `_FORMATTERS` private.

3. **Acceptance.**
   - One test per EVENT_* constant (≈25 tests for default-visible +
     verbose; expect 30-40 total with edge cases).
   - All tests green.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - `python3 -c "from end_of_line import watch; print(watch.project_event({'type':'phase_started','phase':'x','attempts':1}, 'p'))"`
     prints the expected line shape (manual smoke).

4. **Commit + complete.**
   - Title: `clu-watch: phase events — pure event projector (#N)` (the
     `(#N)` placeholder gets filled in phase `docs` when the issue
     exists; for this phase, omit it).
   - Stage: `end_of_line/watch.py`,
     `tests/test_watch_project_event.py`.
   - `clu complete --plan clu-watch --phase events --token <T>`

## Failure modes to watch

- **Missing event fields** — events may lack expected fields
  (older state.json entries, hand-edits). Default each `.get` call
  with a sentinel; never KeyError. Test the "minimal event"
  (only `type` + `ts`) for every EVENT_*.
- **Skipping the queue v2 events** — `EVENT_QUEUE_APPENDED` may
  not exist in `state.py` at the time this phase ships (depends
  on queue-worker-callback merge order). `getattr(st,
  "EVENT_QUEUE_APPENDED", None)` + filter-None handles both cases.
  Tests should skip the v2 event tests if the constant is absent.
- **Module import order** — `watch.py` imports `state`. Don't
  import `cli` (would create a cycle). The projector takes raw
  dicts, doesn't need CLI types.
- **Over-formatting** — keep lines under ~120 chars even after
  truncation. Notifications wrap; long lines are hostile to the
  Monitor UI.
