# clu-demo-showcase-multiphase — multi-phase demo plans at varied positions

You are phase `multiphase` of the `clu-demo-showcase` plan. Make the demo plans
multi-phase and park each worker at a varied phase position so the dashboard's
phase-progress strip renders done/active/pending instead of a trivial `1/1`.
One commit.

## Locked decisions (do NOT re-litigate)

See `plans/clu-demo-showcase.md`. Summary:

- Multi-phase masters (`_master_plan` takes a phase count); each scenario its
  own N.
- Park the worker mid-list by pre-completing a **contiguous prefix**:
  `_prefill_completed` appends `EVENT_PHASE_COMPLETED` events (the source
  `completed_phase_ids` derives from), called AFTER init, BEFORE the dispatch
  tick. The tick claims the first uncompleted phase; the worker holds it.
- Scenario → (total, position): `busy` 4/5 · `idle` 2/4 · `block` 3/3 ·
  `dead` 1/3.

## Read first

- `end_of_line/demo.py:55-64` (`_master_plan`, currently one-phase Sessions
  index), `:77-91` (`scaffold`), `:101-117` (`_dispatch`/`up` — the init→dispatch
  flow where prefill slots in).
- `end_of_line/state.py:1130-1135` (`completed_phase_ids` derives from
  `EVENT_PHASE_COMPLETED` events), `:139` (`EVENT_PHASE_COMPLETED`),
  `append_event` (~:370), `st.mutate`.
- `end_of_line/supervisor.py:760-789` (tick claims the first uncompleted phase —
  linear scan), `:734-740` (claim in-flight → no re-dispatch; worker holds).
- `end_of_line/demo_worker.py` `SCENARIOS` + `command_template` (how scenarios →
  plans).
- `tests/test_demo.py` `ScaffoldTest`/`UpTest` — patch `demo._cli`/`demo._dispatch`
  to drive init/tick without real subprocess.

## Produce

1. **Failing tests first** (`tests/test_demo.py`):
   - `_master_plan` emits a multi-phase Sessions index for a given count
     (N rows, valid phase ids).
   - After `scaffold` + init + `_prefill_completed(state_path, prefix_ids)` +
     tick, the claimed phase id is the expected mid-list one, and
     `gather_rows`-style state shows `phase_index`/`phase_total` = the target
     (e.g. busy → 4/5). Use the existing `_cli`/`_dispatch` patch pattern.

2. **Implementation** (`end_of_line/demo.py`):
   - `_master_plan(slug, phase_ids)` → a Sessions index with one row per phase id.
   - Per-scenario `(total, position)` map → derive `phase_ids` (e.g. `["a".."d"]`)
     and the contiguous prefix to pre-complete (`phase_ids[:position-1]`).
   - `_prefill_completed(state_path, done_ids)`: `with st.mutate(state_path) as
     data:` append `EVENT_PHASE_COMPLETED` per id.
   - `up`: call `_prefill_completed` AFTER `clu init`, BEFORE `_dispatch`.

3. **Acceptance.**
   - All new tests green; full suite green (report count).
   - Manual (or a driven test): a scaffolded `demo-busy` after prefill+tick has a
     claim on phase index 4 of 5; the derived row shows `4/5`.
   - `grep` confirms the prefix is contiguous (complete `1..k`, claim `k+1`).

4. **Commit + attest + complete.**
   - Commit: `clu-demo-showcase: phase multiphase — multi-phase plans at varied positions`.
   - Stage: `end_of_line/demo.py`, `tests/test_demo.py`.
   - After the commit: `clu verify --plan clu-demo-showcase --phase multiphase --token <T>`
     then `clu attest --simplify --plan clu-demo-showcase --phase multiphase --token <T>`.
   - `clu complete --plan clu-demo-showcase --phase multiphase --token <T>`.

## Failure modes to watch

- **Pre-completed ids must match the Sessions-index ids exactly.** Derive the
  done-ids from the same list `_master_plan` emits (single source) — a typo
  completes nothing → worker claims phase 1, wrong position, no error.
- **Prefill must run AFTER init, BEFORE the tick.** Init parses the Sessions
  index into `state["phases"]`; prefill needs that to exist; the explicit
  `_dispatch` tick (not cron) must make the first claim after prefill.
- **Effort/lease vs run length.** The multi-phase master's `Effort` column drives
  the lease (`parse_effort_minutes`); keep it ≥ the worker run so busy/idle don't
  lease-expire mid-watch.
- **Non-contiguous prefix breaks the strip** — the strip shows `phase_index-1`
  done segments; only a contiguous prefix keeps `k done` truthful.
