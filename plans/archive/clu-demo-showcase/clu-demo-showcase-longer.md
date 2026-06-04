# clu-demo-showcase-longer — longer, watchable demo fleet

You are phase `longer` of the `clu-demo-showcase` plan. Make the demo fleet
watchable for minutes: a longer activity window for the lifecycle scenarios, and
`block` now persisting as a blocked dashboard row (the `clu-dashboard-blocked`
feature shipped before this plan). One commit.

## Locked decisions (do NOT re-litigate)

See `plans/clu-demo-showcase.md`. Summary:

- Raise `_PRE_LIFECYCLE_STEPS` so `block`/`dead` show ~6 steps (~30s) of activity
  before their lifecycle event. **Keep `DEFAULT_STEP_SECONDS=5.0`** (short-sleep
  cadence is signal-safe; lengthen by step COUNT, never the per-step sleep).
- `block` releases its claim after the window → `clu-dashboard-blocked` (already
  shipped to main) renders it as a persistent amber blocked row. `dead` still
  correctly leaves (terminal state).
- busy/idle stay long-lived (720 steps ≈ 1h); only revisit if the Diagnosis test
  shows them vanishing.
- Optional: stagger dispatch with a small per-scenario offset so rows aren't
  phase-locked.

## Read first

- `end_of_line/demo_worker.py:38-49` (`_IDLE_WRITE_STEPS`, `_PRE_LIFECYCLE_STEPS`,
  `DEFAULT_STEP_SECONDS`, `DEFAULT_MAX_STEPS`), `:208-253` (`run_worker` loop +
  `scenario_action` per step; the `ACT_BLOCK`/`ACT_DEAD` returns).
- `end_of_line/demo.py` `up`/`_dispatch` (if doing the stagger).
- `plans/clu-dashboard-blocked.md` — confirm the blocked-row feature is on main
  before relying on `block` persisting (it ships first, sequentially).
- `tests/test_demo_worker.py:190-218` (`RunWorkerTest` — inject `runner`/`clock`/
  `sleep`, assert argv calls + step timing).

## Produce

1. **Failing tests first** (`tests/test_demo_worker.py`):
   - `RunWorkerTest`: with the raised `_PRE_LIFECYCLE_STEPS`, `block`/`dead` emit
     their lifecycle action at the new step (assert the `clu block` argv / the
     dead exit fires after ~6 heartbeats, not 2). busy/idle still run to
     `max_steps`.

2. **Implementation** (`end_of_line/demo_worker.py`):
   - Raise `_PRE_LIFECYCLE_STEPS` (2 → ~6). Confirm `DEFAULT_STEP_SECONDS`/
     `DEFAULT_MAX_STEPS` unchanged. (Optional `demo.py` stagger if folded in.)

3. **Acceptance.**
   - All new tests green; full suite green (report count).
   - **Live smoke** (the real payoff): `clu demo` → watch `clu top` + `clu serve`
     (restart serve for the page) for ~1 min → 4 concurrent workers at varied
     phase positions (strips render); `block` shows ~30s of activity then becomes
     a persistent amber blocked row; `dead` shows activity then leaves; busy/idle
     persist. `clu demo down` tears down with no orphans (`clu doctor` sweep
     clean).
   - Run the Diagnosis falsifiable test (master) first if not already: confirm
     busy/idle persist.

4. **Commit + attest + complete.**
   - Commit: `clu-demo-showcase: phase longer — watchable fleet + persistent blocked row`.
   - Stage: `end_of_line/demo_worker.py` (+ `end_of_line/demo.py` if staggered),
     `tests/test_demo_worker.py`.
   - After the commit: `clu verify --plan clu-demo-showcase --phase longer --token <T>`
     then `clu attest --simplify --plan clu-demo-showcase --phase longer --token <T>`.
   - `clu complete --plan clu-demo-showcase --phase longer --token <T>`.

## Failure modes to watch

- **`block` persistence depends on `clu-dashboard-blocked` being on main.** This
  plan runs sequentially AFTER it — confirm with a `grep` that `gather_rows`
  emits blocked rows before relying on the live smoke showing a persistent
  blocked row. If somehow not present, the block worker still *functions* (calls
  `clu block`), it just won't render — surface that, don't fake it.
- **Long sleep swallows Ctrl-C** — do NOT lengthen `DEFAULT_STEP_SECONDS`; raise
  step COUNT only. The 5s cadence keeps each iteration a teardown re-check point.
- **Lease vs longer run** — a longer activity window must still fit the master's
  Effort/lease; busy/idle at 720×5s already imply a ~1h lease — keep parity.
- **Teardown after longer runs** — re-verify `clu demo down` / Ctrl-C kill the
  worker pgroups with no orphans (the longer run is the same teardown path, just
  more elapsed time).
