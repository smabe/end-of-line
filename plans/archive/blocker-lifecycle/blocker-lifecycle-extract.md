# blocker-lifecycle-extract — pure state_blocker module

You are phase `extract` of the `blocker-lifecycle` plan. Create
`end_of_line/state_blocker.py` as a pure module: state transitions
and notification decisions over the blocker subsection of `data`,
plus the relocated render helpers from `notify.py`. No I/O, no
state mutation — just functions over dicts.

## Locked decisions (do NOT re-litigate)

See `plans/blocker-lifecycle.md`. Summary:

- Module path: `end_of_line/state_blocker.py`.
- Pure functions only — no `st.mutate`, no `notify.send`, no
  subprocess, no file I/O.
- Notification decisions return `(blocker_id, kind, body)` tuples.
  Callers dispatch through the existing `notify.notify()` router.
- Render helpers move verbatim from `notify.py`; `notify.py`
  re-exports them for back-compat.

## Read first

- `end_of_line/notify.py` — find `render_blocker`, `render_stalled`,
  `render_halted` (they're already pure helpers; you're moving them).
- `end_of_line/supervisor.py` — rule 4 ("Answered-blocker resume")
  and the `side_notifies` block that emits `KIND_STUCK_BLOCKER`.
  Read but DO NOT modify yet — migration is phase 2.
- `end_of_line/state.py` — the blocker dataclass / dict shape,
  `EVENT_BLOCKER_CONSUMED`, `EVENT_BLOCKER_ANSWERED`,
  `STATUS_RUNNING`. Re-use these constants — do not invent new ones.
- `tests/test_supervisor.py` — existing blocker-related test patterns
  (fixture shape, claim-phase boilerplate).
- `tests/__init__.py` — `isolate_registry` and the canonical setUp
  template.

## Produce

1. **Failing tests first** in `tests/test_state_blocker.py`. ~15
   tests across the matrix. Names + assertions:

   `process_answered_blockers`:
   - `test_no_blockers_returns_empty` — empty data → `([], None)`.
   - `test_only_consumed_blockers_returns_empty` — consumed=True →
     `([], None)`.
   - `test_only_unanswered_blockers_returns_empty` — answer=None →
     `([], None)`.
   - `test_one_answered_unconsumed_returns_event_and_running` —
     `[(EVENT_BLOCKER_CONSUMED, q-1)]`, `STATUS_RUNNING`.
   - `test_multiple_answered_returns_one_event_per_blocker` — order
     matches data["blockers"] iteration order.
   - `test_answered_after_consumed_is_skipped` — defensive.

   `stuck_blocker_repings`:
   - `test_no_open_blockers_returns_empty`.
   - `test_recently_pinged_blocker_skipped` — created 31min ago,
     last_repinged_at 5min ago → empty.
   - `test_never_pinged_old_blocker_repings` — created 31min ago,
     no `last_repinged_at` → one tuple.
   - `test_stale_ping_repings` — last_repinged_at 31min ago → one
     tuple.
   - `test_under_thirty_min_not_repinged` — created 20min ago →
     empty.
   - `test_consumed_blocker_not_repinged` — defensive.
   - `test_returns_blocker_id_for_stamping` — tuple has 3 elements:
     `(blocker_id, kind, body)`.

   `render_*` (snapshot-style):
   - `test_render_blocker_includes_question_and_options`.
   - `test_render_stalled_includes_phase_and_plan_slug`.

2. **Implementation** in `end_of_line/state_blocker.py`:

   ```python
   from __future__ import annotations

   from datetime import datetime, timezone
   from typing import Any

   from end_of_line.state import (
       EVENT_BLOCKER_CONSUMED,
       STATUS_RUNNING,
   )

   STUCK_BLOCKER_THRESHOLD_MINUTES = 30


   def process_answered_blockers(
       data: dict[str, Any],
   ) -> tuple[list[tuple[str, str]], str | None]:
       """Return (events_to_append, target_status) for answered,
       not-yet-consumed blockers. Pure over the data dict.
       """
       events: list[tuple[str, str]] = []
       for bid, blocker in (data.get("blockers") or {}).items():
           if blocker.get("answer") is None:
               continue
           if blocker.get("consumed"):
               continue
           events.append((EVENT_BLOCKER_CONSUMED, bid))
       target_status = STATUS_RUNNING if events else None
       return events, target_status


   def stuck_blocker_repings(
       data: dict[str, Any],
       now: datetime,
   ) -> list[tuple[str, str, str]]:
       """Return (blocker_id, kind, body) tuples for blockers that
       need re-ping. Caller stamps last_repinged_at + dispatches.
       """
       # …implementation that mirrors the inline rule in
       # supervisor.py — see "Read first" for the source.

   def render_blocker(...): ...     # moved from notify.py
   def render_stalled(...): ...     # moved from notify.py
   def render_halted(...): ...      # moved from notify.py
   ```

   - `end_of_line/notify.py`: replace the three render function
     bodies with `from end_of_line.state_blocker import
     render_blocker, render_stalled, render_halted` so external
     importers see the same names.

3. **Acceptance.**
   - 15 new tests green.
   - Full suite green (no behavior change): `python3 -m unittest
     discover -s tests` reports the same pass count plus 15.
   - `grep -rn "def render_blocker" end_of_line/` returns exactly
     one definition (in `state_blocker.py`).

4. **Commit + complete.**
   - Title: `blocker-lifecycle: phase extract — pure state_blocker
     module + relocated renders`
   - Stage: `end_of_line/state_blocker.py`, `end_of_line/notify.py`,
     `tests/test_state_blocker.py`.
   - `clu complete --plan blocker-lifecycle --phase extract --token
     <T>`.

## Failure modes to watch

- **Import cycles.** `state_blocker.py` imports from `state.py`;
  `state.py` must NOT import from `state_blocker.py`. If it does,
  back out — the layering is wrong.
- **Time-source assumptions.** `stuck_blocker_repings` takes `now`
  as a parameter so tests don't need to monkey-patch
  `datetime.now`. Don't call `datetime.now()` inside the function.
- **Render-function signature drift.** Move the helpers verbatim
  (signatures unchanged) — phase 2 wires them up; if signatures
  drift, every callsite breaks at once.
- **Empty-dict vs None.** Blockers section can be `{}` OR absent;
  use `data.get("blockers") or {}` to handle both.
- **`/simplify` on a pure refactor.** Run it — even pure moves can
  surface near-duplicate helpers worth collapsing.
