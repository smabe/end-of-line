# clu-demo-showcase — make clu demo show off the dashboard UI

`clu demo` should be a believable, watchable showcase of `clu top`/`clu serve`,
especially the phase-progress strip (#86) — today it isn't. Two reasons,
verified from source: (a) the demo is four **one-phase** plans (`_master_plan`,
`demo.py:55-64`) so every worker sits at a trivial `phase 1/1`; (b) the fleet
shrinks fast — `block`/`dead` exit at `_PRE_LIFECYCLE_STEPS=2` (~10s,
`demo_worker.py:41-42`), and `block` releases its claim so `gather_rows` skipped
it. (busy/idle DO run ~1h via `DEFAULT_MAX_STEPS=720` × `5s`,
`demo_worker.py:48-49`.)

Phase `multiphase` gives the demo plans multiple phases with workers parked at
varied positions (3/5, 2/4) so the done/active/pending strip renders. Phase
`longer` makes the fleet watchable for minutes — a longer activity window for
the lifecycle scenarios.

**DEPENDS ON `clu-dashboard-blocked` shipping first** (sequential dispatch).
Once blocked plans render in the dashboard, the demo's `block` scenario becomes
a persistent amber "needs-you" row instead of vanishing — a genuinely useful
thing to showcase. This plan is authored to run AFTER `clu-dashboard-blocked`
lands on main.

## Diagnosis
- **Hypothesis:** the demo *feels* like seconds because the lifecycle scenarios
  leave within ~10s (`block` releases its claim; `dead` orphans then is reaped)
  and the survivors sit static at `1/1` (one-phase plans). NOT because busy/idle
  exit early.
- **Falsifiable test (run FIRST):** `clu demo` then watch `clu top` ~30s — confirm
  block/dead leave by ~15s and busy/idle persist but show static `1/1`. **If
  busy/idle ALSO vanish quickly**, the duration/lease is the real cause —
  re-scope before touching the file list.
- **Verified statically:** durations + the block-releases-claim mechanism (cited
  above). Only "do busy/idle *visibly* persist?" needs the live run.

## Locked design decisions

### Phase multiphase — multi-phase plans + varied positions
- **Multi-phase masters.** `_master_plan` (`demo.py:55-64`) takes a phase count
  (+ids) so the Sessions index has N phases, not one. Each scenario gets its own N.
- **Pre-complete a contiguous prefix to park the worker mid-list.**
  `completed_phase_ids` derives from the event log (`state.py:1130-1135`), so a
  new `_prefill_completed(state_path, done_ids)` does `with st.mutate(path) as
  data: st.append_event(data, st.EVENT_PHASE_COMPLETED, phase=pid)` for each
  prefix phase. `up` (`demo.py:107-117`) calls it AFTER `clu init` (which parses
  `state["phases"]`) and BEFORE the dispatch tick — the tick then claims the
  first uncompleted phase (`supervisor.py:760-789`, linear scan). The worker
  never completes (heartbeats forever) so it stays put (`supervisor.py:734-740`).
- **Contiguous-prefix only** (matches the strip): the strip derives `done` from
  `phase_index-1` (linear), so complete phases `1..k` and claim `k+1` → `k` done
  segments. Never pre-complete a non-contiguous set.
- **Scenario → (total, active-position):** `busy` 4/5 · `idle` 2/4 ·
  `block` 3/3 · `dead` 1/3 → strips `●●●◉○` / `◉○○○` / `●●◉` / `◉○○`.

### Phase longer — longer / watchable fleet
- **Raise `_PRE_LIFECYCLE_STEPS`** (`demo_worker.py:42`, currently 2) so
  `block`/`dead` show ~6 steps (~30s) of real activity before their lifecycle
  event. **Keep `DEFAULT_STEP_SECONDS=5.0`** — the short-sleep cadence is
  signal-safe (a multi-minute `time.sleep` defers Ctrl-C); lengthen by step
  COUNT, never the per-step sleep.
- **`block` now persists as a blocked row** (depends on `clu-dashboard-blocked`):
  after its ~30s window it calls `clu block`, releasing the claim — and the
  dashboard now shows it as an amber blocked row. So the demo demonstrates a
  *persistent* blocked state, not a vanishing worker. `dead` still correctly
  leaves (a real terminal state).
- **busy/idle stay long-lived** (720 steps ≈ 1h already ample); only revisit if
  the Diagnosis test shows them vanishing.
- **(Optional, decide at impl)** stagger dispatch with a small per-scenario
  offset in `up` so rows aren't phase-locked (prior art: phase-locked starts read
  as fake). Cheap; fold in if it doesn't bloat the tick path.

## Non-goals
- **No change to the worker callback contract** (heartbeat/block/complete token
  flow, `dispatch.py` substitution). The demo rides the real pipeline unchanged.
- **No intra-phase live advancement** (a worker walking 3/5→4/5 on screen).
  Workers hold their claimed phase; varied positions come from pre-completed
  phases. *Parked* — live advancement is a bigger worker-lifecycle change.
- **`dead` still leaves the dashboard** — it's a real terminal state (orphaned
  claim → reaped), not a waiting one; only `block` becomes persistent (via
  `clu-dashboard-blocked`).
- **No new deps.** Stdlib only; existing pgroup/signal teardown unchanged.

## Files touched
- `end_of_line/demo.py` — P-multiphase modified — `_master_plan` multi-phase +
  per-scenario phase counts; new `_prefill_completed`; `up` inserts prefill
  between init and dispatch. P-longer: optional staggered dispatch.
- `end_of_line/demo_worker.py` — P-longer modified — raise `_PRE_LIFECYCLE_STEPS`;
  confirm/keep `DEFAULT_MAX_STEPS`/`DEFAULT_STEP_SECONDS`.
- `tests/test_demo.py` — P-multiphase modified — multi-phase scaffold + prefill →
  claimed phase id matches the target position (mirror `_cli`/`_dispatch` patch
  pattern, no real subprocess).
- `tests/test_demo_worker.py` — P-longer modified — `RunWorkerTest` asserts the
  new pre-lifecycle step count drives block/dead timing.

## Per-phase done checklist
- TDD: failing tests first. `/code-review` after if >1 file / >30 lines.
- Full suite green: `python3 -m unittest discover -s tests` (report count).
- Structured commit; stage explicit paths (no `git add -A`).
- After the commit: `clu verify` then `clu attest --simplify` (each
  `--plan clu-demo-showcase --phase <id> --token <T>`), then
  `clu complete --plan clu-demo-showcase --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| multiphase | `clu-demo-showcase-multiphase.md` | Multi-phase demo masters + `_prefill_completed` (contiguous prefix) so workers park at varied positions (busy 4/5, idle 2/4, block 3/3, dead 1/3); the strip renders done/active/pending | 2.5h |
| longer | `clu-demo-showcase-longer.md` | Longer activity window (`_PRE_LIFECYCLE_STEPS`↑), block persists as a blocked row (needs clu-dashboard-blocked), optional staggered dispatch | 1.5h |
