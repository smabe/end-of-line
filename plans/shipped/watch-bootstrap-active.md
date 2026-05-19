# watch-bootstrap-active — emit TASK_UPDATE for active phase at connect (closes #62)

`clu watch --task-list` correctly emits TASK_CREATE for every plan +
phase on connect, but doesn't emit any TASK_UPDATE for a phase that
is already in_progress when the consumer connects. Result: late-armed
consumers (the normal case — `clu queue add` fires the cron tick
immediately, Monitor arms a few seconds later) see all phases as
pending until the next state transition.

Single phase: extend the bootstrap to emit TASK_UPDATE lines for
plan + active phase when state.json carries a `current_claim`.

Sequence note: this plan touches `end_of_line/watch.py`, which is
ALSO modified by lease-reliability/supervisor-wire (orphan_reaped
line). lease-reliability ships first; this plan dispatches off
post-merge `main`, so no overlap.

## Locked design decisions

### Phase 1 — emit-bootstrap (closes #62)
- **Site:** `end_of_line/watch.py`, the `--task-list` mode's connect-time bootstrap function (where the TASK_CREATE batch is emitted).
- **Trigger:** after the TASK_CREATE batch, before the `[snapshot]` line, check `data.get("current_claim")`. If present and not None, emit two TASK_UPDATE lines: one for the plan (parent), one for the active phase.
- **Line shape:** matches existing runtime TASK_UPDATE exactly. Status `in_progress`. `msg="bootstrap: plan running"` for the parent and `msg="bootstrap: already active"` for the phase.
- **No state mutation.** Bootstrap is read-only against state.json.
- **Out of scope:** blocker/paused/halted bootstrap state (filed as out-of-scope in the issue body; can extend later).

## Non-goals

- No retroactive emission of completed-phase TASK_UPDATEs. If the consumer missed `phase-1` completing, they see it as pending forever — only the active phase gets bootstrap reconciliation.
- No protocol version bump. The line shape is identical to runtime; consumers can't tell the difference (the point).
- No `[snapshot]` removal. Operator-context lines stay for human readability.
- No bootstrap for plans without `current_claim` (queued, paused, completed) — those correctly bootstrap as all-pending.

## Files touched

- `end_of_line/watch.py` — P1 modified — bootstrap reconciliation. **API hotspot:** the bootstrap function shape (caller is `cli.py::cmd_watch` — no signature change, just additional emit lines).
- `tests/test_clu_watch.py` (or wherever watch tests live) — P1 modified — new tests for the active-phase bootstrap case.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format.
- Stage explicit paths.
- `clu verify` + `clu attest --simplify` per the gate.
- Call `clu complete --plan watch-bootstrap-active --phase emit-bootstrap --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| emit-bootstrap | `watch-bootstrap-active-emit-bootstrap.md` | TASK_UPDATE for active phase + plan on connect (closes #62) | 30min |
