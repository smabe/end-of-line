# blocker-lifecycle — extract Blocker state machine + notification orchestrator

Today the Blocker lifecycle is split across three modules with no seam.
`supervisor.py` rule 4 ("Answered-blocker resume") inlines the
answered → consumed → status-flip-to-running transition inside the
priority chain. `supervisor.py` `side_notifies` inlines the
stuck-blocker re-ping rule. `notify.py` holds `render_blocker` /
`render_stalled` / `render_halted` as pure functions but they're
called from ad-hoc sites scattered through the supervisor + notify
loop. Tests for blocker behavior have to simulate multi-tick runs or
mock both state and iMessage.

This plan extracts a `state_blocker.py` module that owns (a) pure
state transitions over the blocker subsection of `data` and (b) the
notification decisions that fall out of those transitions. The
supervisor and notify loop become callers. No new worker callbacks
(ADR-0003 respected). The "one tick = one action" invariant is
strengthened — the state machine returns one transition per call,
and the supervisor calls it inside the existing `with st.mutate():`
window (ADR-0002 respected).

## Locked design decisions

### Cross-cutting

- **Module name: `end_of_line/state_blocker.py`.** Mirrors `state.py`
  naming. **Why:** the module operates on the blocker subsection of
  the same data dict; it's a logical extension of `state.py`, not a
  new layer. **How to apply:** import-from-state pattern, not
  new-types-everywhere.

- **Pure functions over the data dict, no I/O.** All functions take
  `data: dict` and return data (events, transitions, render bodies).
  No `st.mutate` calls, no `notify.send` calls, no file I/O. **Why:**
  tests run instantly against fixture dicts; supervisor + notify
  retain control of when side effects happen. **How to apply:**
  functions never import `notify_imessage`, `subprocess`, or
  `state.mutate`.

- **Notification decisions return `(kind, body)` tuples, not sends.**
  Callers iterate the return value and dispatch through the existing
  `notify.notify()` router. **Why:** keeps the Notifier/InboundPoller
  protocol seam (#11) intact and lets quiet-hours gating stay where
  it is.

### Phase 1 — extract

- **New module `end_of_line/state_blocker.py`:**
  - `process_answered_blockers(data) -> tuple[list[Event], str | None]`
    — finds blockers with `answer != null and not consumed`, returns
    `[(EVENT_BLOCKER_CONSUMED, blocker_id), ...]` events and a target
    status (`STATUS_RUNNING` or `None`).
  - `stuck_blocker_repings(data, now) -> list[tuple[str, str, str]]`
    — finds open blockers older than 30min with no recent re-ping,
    returns `[(blocker_id, KIND_STUCK_BLOCKER, rendered_body), ...]`
    so the caller can both notify and stamp `last_repinged_at`.
  - `render_blocker(blocker, plan_slug)`, `render_stalled(...)`,
    `render_halted(...)` — moved verbatim from `notify.py` (they're
    already pure).
- **Tests in `tests/test_state_blocker.py`:** ~15 tests covering the
  process_answered matrix (no blockers / answered-but-consumed /
  answered-and-not-consumed / multiple-blockers), the re-ping matrix
  (recently-pinged / never-pinged / stale-ping / under-30min), and
  the render output (snapshot-style).
- **Pure refactor: no behavior change.** All existing
  blocker-related tests stay green.

### Phase 2 — migrate

- `supervisor.py` rule 4 calls `process_answered_blockers(data)`
  inside its `with st.mutate():` window. Returns transition; the
  supervisor appends the events + flips status.
- `supervisor.py` side-notifies stuck-blocker rule calls
  `stuck_blocker_repings(data, now)`. Returns `(blocker_id, kind,
  body)` list; supervisor stamps `last_repinged_at` on each blocker
  + adds the (kind, body) pair to `side_notifies`.
- `cli.cmd_block` (worker callback): renders the blocker body via
  the moved `render_blocker` for the immediate `KIND_BLOCKER`
  notification.
- `notify.py`: re-exports the moved render functions for backward
  compat in case any external caller imports them by name.
- Suite still green at the same count — no new tests added in this
  phase, only callsite rewiring.

### Phase 3 — cleanup

- End-to-end test for the worker → operator → worker round-trip:
  spawn a blocker, simulate the answer via `cli.cmd_answer`, run 2
  ticks, assert the phase redispatches with the answer visible to
  the new worker.
- Delete now-unused inline rule helpers from `supervisor.py`.
- `/simplify` over the supervisor diff to catch any newly-duplicated
  blocker-shaped code.
- Suite green at ~+15 new tests over baseline.

## Non-goals

- **New blocker transitions.** Not adding auto-resolve, timeout-
  expire, or operator-cancel — that's a separate proposal.
- **Notify backend changes.** Discord adapter (post-#11) and iMessage
  backend stay as-is; this plan changes the *callers* of notify, not
  notify itself.
- **Blocker UX in inbox hook.** Active-blockers section was shipped
  in #11; not touching it.
- **Schema changes.** State file shape unchanged.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the
  hood / Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan blocker-lifecycle --phase <id> --token
  <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| extract | `blocker-lifecycle-extract.md` | New `state_blocker.py` with pure transition + reping + render functions; ~15 tests | 2h |
| migrate | `blocker-lifecycle-migrate.md` | Rewire supervisor rule 4 + side_notifies + cmd_block through new module | 1.5h |
| cleanup | `blocker-lifecycle-cleanup.md` | End-to-end round-trip test, `/simplify` pass, dead-code removal | 1h |
