# false-alarms-p1 — idle watchdog: replace the signal, fix the window

You are phase `p1` of the `false-alarms` plan. This phase makes the idle watchdog capable of a TRUE positive for the first time: it retires the instantaneous-`%cpu` signal and the dead `lsof` suppression, and replaces the window predicate that today can only be satisfied by a worker that was demonstrably working. One commit.

## Locked decisions (do NOT re-litigate)

See the master `plans/false-alarms.md`. The decisions binding this phase:

- **The `lsof` suppression is DELETED, not repaired.** Measured this session: `lsof -p <pid> -i` takes 15.29s against the hardcoded `timeout=1` (`supervisor.py:723`), so it has never once completed — every call raises `TimeoutExpired` and falls through to EMIT (`supervisor.py:727-729`). Independently, a repaired version cannot work: idle Claude Code sessions hold the same ESTABLISHED sockets to `160.79.104.10:https` as busy ones, so "has an API socket" does not discriminate. Adding `-a` alone makes things WORSE — scoped to the PTY shim pid it matches nothing, so alerts get more frequent.
- **Liveness = cumulative processor-time delta across the worker tree, measured across the whole window.** Not per-tick (truncation and jitter swamp it), not instantaneous `%cpu` (`man ps` line 143: "a decaying average over up to a minute of previous (real) time" — a healthy worker waiting on the model sits under the 1.0 threshold).
- **Fractional seconds are preserved.** The separating signal is entirely inside the fraction `_parse_duration` currently truncates.
- **Samples are retired by AGE, not by count.** This is what removes the cadence coupling; bumping the cap to 21 would paper over the same defect.
- **The four threshold values are operator-signed-off, not drafting defaults** (2026-08-21, recorded in the master's Status): window 10.0 min, max sample gap 60s, CPU delta 1.0s, min samples 3. Changing one during implementation is an approach switch, not a tuning decision.
- **Tune toward missing a wedge, not toward crying wolf.** A missed wedge costs one lease expiry, which the lease TTL already handles; a false alarm costs the operator's trust in every warning clu emits.

## Work

- `end_of_line/supervisor.py` — delete the `lsof` branch entirely (`:715-731`, including the `lsof_output` test seam in the signature and docstring). Replace the `%cpu` sample with a cumulative processor-time sample summed across `tree_pids`. Correct the two false comments (`:670` "instantaneous %cpu"; and the `%cpu`-based rationale in the docstring at `:648`).

  ```python
  # sample: sum cumulative CPU seconds across the tree, not instantaneous %cpu.
  # walk_worker_tree already carries Descendant.cpu_seconds (supervisor.py:90);
  # :673-675 explicitly declines to use it — that comment goes away with this change.
  descendants = walk_worker_tree(pid, ps_output=tree_ps_output)
  tree_cpu_seconds: float = root_cpu + sum(d.cpu_seconds for d in descendants)
  st.append_cpu_sample(claim, tree_cpu_seconds, now)   # field now holds CUMULATIVE seconds
  ```

  Note the root pid's own CPU is NOT in `walk_worker_tree`'s output — it excludes the root by contract (`supervisor.py:170`). Today's `ps -p <tree_pids> -o %cpu=` call covers the root because `tree_pids` prepends it (`:677`); the replacement must keep the root in the sum.

- `end_of_line/supervisor.py` — `_parse_duration` (`:94-100`) returns `float`, preserving the `[.cc]` centiseconds `ps` already emits. `Descendant.elapsed_seconds` and `.cpu_seconds` (`:89-90`) become `float`. Check the one other consumer, `_emit_stuck_tool`'s `cpu_max` comparison (`supervisor.py:598-604`, against `config.stuck_tool_cpu_threshold_seconds` — the sibling threshold pair at `config.py:150-151`), still type-checks: a float compared to an int config value is fine, but basedpyright is a hard gate here (`clu verify`).

- `end_of_line/state.py` — rewrite `worker_idle_window_satisfied` (`:942-965`). Three added preconditions, and the span is measured between OBSERVED samples rather than to `now`:

  ```python
  # span is samples[-1] - samples[0]: what was OBSERVED, not what elapsed.
  # Today it is `now - samples[0]`, and since the caller appends with `now`
  # immediately before calling (supervisor.py:703 then :710), samples[-1].ts IS
  # now — so the current span is (N-1) intervals and the cap makes it 570s
  # against a 600s requirement. That is the inversion this rewrite removes.
  span      = samples[-1].ts - samples[0].ts          >= window_min
  contiguous = every adjacent gap                      <= max_sample_gap
  recent     = now - samples[-1].ts                    <= max_sample_gap
  quiet      = samples[-1].cpu - samples[0].cpu        <= cpu_delta_threshold
  ```

  `quiet` replaces the per-sample `any(s["cpu"] > cpu_threshold)` test — with cumulative values, a per-sample ceiling is meaningless.

- `end_of_line/state.py` — `append_cpu_sample` (`:934-939`) trims by AGE, not by `WORKER_IDLE_SAMPLE_CAP`. Retain anything newer than `window_min + max_sample_gap`; keep a generous absolute cap (e.g. 200) purely as an unbounded-growth guard, not as the retention rule. Retire or re-document `WORKER_IDLE_SAMPLE_CAP` (`:301-304`) — its current comment describes it as "~10 minutes of history", which is the false claim that hid this bug.

- `end_of_line/config.py` — four thresholds, mirroring the sibling stuck-tool pattern at `:150-151`: `worker_idle_window_minutes` (10.0), `worker_idle_max_sample_gap_seconds` (60), `worker_idle_cpu_delta_threshold_seconds` (1.0), `worker_idle_min_samples` (3). Today's values are unreachable keyword defaults with no operator override.

- `end_of_line/dispatch.py` — comment-only. `:296` states tree-awareness was added "so this doesn't false-fire WORKER_IDLE"; tree-awareness only ever covered the CPU sum, never the socket check, which stayed on the root pid. Deleting the socket branch makes the sentence moot as well as wrong — correct it to say what the shim actually guarantees.

- `docs/architecture.md` — two edits. `:50-52` lists what the watchdog stack tree-walks ("stuck-tool tree walk, idle-CPU sum, killpg reapers, cmdline marker") — update the idle-CPU entry to say cumulative tree CPU rather than a `%cpu` sum. And `:191` names `lsof` as one of the things the design rests on; that becomes false when p1 deletes it.

  *(Precision note: `:50-52` does not actually CLAIM the socket check was tree-aware — it omits the socket check entirely. The correction is that the list is now wrong about what the idle watchdog samples, not that it made a false tree-awareness claim.)* Per the master's Background findings its framing implies the socket check was tree-aware; it never was, and it is gone now.

- `tests/test_supervisor_worker_idle.py` — the existing suite hand-seeds `cpu_samples` (`_idle_samples()` at `:22` fabricates timestamps directly), so no current test exercises the cadence/retention coupling that produced this bug. Add real-tick drives; keep the seeded unit cases for the predicate itself.

- `tests/test_state.py` — predicate cases: contiguity rejection, recency rejection, cumulative-delta pass/fail, age-based retention.

- `end_of_line/notify.py` — **added at execution.** `render_worker_idle` asserted "no open Anthropic socket, CPU ≤1% across the window" in the notification itself; deleting the socket check makes the operator-facing warning state a check it never ran, which is the exact defect this plan exists to remove. Wording confirmed by the operator at the p1 review gate. The `lsof` investigate hint below it stays — that is advice for a human, not a clu invocation.

- `end_of_line/cli.py` — **added at execution.** `clu doctor`'s stuck-tool row interpolates `Descendant` fields directly, so the `int → float` change would have printed `elapsed=780.0s`. One line, `:g` formatting.

- `tests/test_supervisor_stuck_tool.py` — **added at execution.** Three tests asserted the exact truncation this phase deletes; they fail by construction against the new parse.

- `tests/test_config.py` — **added at review.** The four new thresholds had no load-path coverage at all, which is how a validator mismatch reached the diff (see the review finding below).

- `docs/operations.md` — **added at review.** The contiguity rule silently disables the watchdog at tick cadences at or above the max sample gap; the caution sits beside the `StartInterval` guidance that sets that cadence.

- Consumes: `walk_worker_tree(root_pid: int, *, ps_output: str | None) -> list[Descendant]`; `Descendant.cpu_seconds`; `st.append_cpu_sample(claim: dict, cpu: float, now: datetime) -> None`; `_record_claim(delta, claim, field)`
- Produces: `state.worker_idle_window_satisfied(claim, now, *, min_samples: int, window_min: float, max_sample_gap: float, cpu_delta_threshold: float) -> bool`; `supervisor._parse_duration(raw: str) -> float`; `Descendant.cpu_seconds: float`

## Decisions & findings

### Decision: delete the socket suppression rather than repair it  *(status: active)*
- **Rationale:** it is dead three ways over, and the deepest one is unfixable. It always times out (15.29s vs 1s, measured); it reads machine-wide sockets because `lsof` ORs its selectors without `-a` (161 lines across 20+ unrelated processes); the string `anthropic` never appears because those IPs have no PTR record (`grep -ci anthropic` → 0 against 12 live ESTABLISHED API sockets). And repairing all three still fails: idle sessions hold the same sockets as busy ones, so the predicate cannot separate the cases it exists to separate.
- **Alternatives considered:** repair with `-a` + `tree_pids` + numeric-peer matching (rejected — measured non-discriminating, and A1's finding that scoping to the shim pid matches nothing makes alerts strictly more frequent); keep it as a secondary AND-gate (rejected — a check that is true of every Claude process can only mask real wedges).
- **Evidence:** `supervisor.py:715-731`; timing and socket probes run this session; `dispatch.py:295` for the shim-pid identity.

### Decision: retire samples by age rather than raising the cap  *(status: active)*
- **Rationale:** the cap is not a memory bound, it is an accidental calibration. 20 samples at 30s spans 570s against a 600s window, so the predicate is unsatisfiable under continuous sampling; at a faster cadence the cap would evict before the span could ever be reached, and at a slower one `min_samples` becomes the real gate. Age-based retention makes correctness independent of tick rate, which is the only property that survives an operator changing `StartInterval`.
- **Alternatives considered:** cap = 21 (the exact arithmetic minimum) or 22 with jitter margin — rejected: it re-derives a magic number from a cadence the operator can change, and leaves the same class of bug one config edit away.
- **Evidence:** `state.py:301-304`, `state.py:942-965`, `supervisor.py:703,710`, `docs/operations.md:169`.

### Addendum: every surviving `lsof` reference, grepped at resume (2026-08-21, HEAD `ca84108`)  *(status: active)*
- The Work list names the `lsof` branch itself. `grep -rn lsof end_of_line/ docs/ tests/` at resume found five more sites the list does not name, and the ones marked EDIT are required by the described work — edit them and report, per the worker brief:
  - **EDIT** `end_of_line/supervisor.py:5` — module docstring cites "`lsof` with 1s" as one of the slow probes the tick cannot hold a lock across. Drop the `lsof` item; `ps` and the reap keep the sentence true.
  - **EDIT** `end_of_line/plan_store.py:1478` — the same sentence, duplicated as a comment. Same fix.
  - **EDIT** `docs/reference.md:446` — documents `lsof_output` as a test seam; the seam is being deleted.
  - **EDIT** `tests/test_supervisor_tick_restructure.py:369` — passes `lsof_output="nothing"` into `_emit_worker_idle`; it will not compile against the new signature. Its `:7` and `:117` docstrings also describe an `lsof` probe seam.
  - **KEEP** `end_of_line/notify.py:238` — this is operator-facing advice in a notification ("`lsof -p {pid} -i` for sockets"), telling a human how to investigate. It is not a clu invocation and stays valid.
- The Done criterion "no `lsof` invocation remains anywhere in `end_of_line/`" is satisfied by deleting the branch alone; the sites above are stale prose the same change falsifies.

### Finding: the Work list under-predicted by five files, and two of them are a planning defect  *(status: active)*
- **What the plan should have seen.** Changing `_parse_duration`'s return type to float was in the Work list; every *rendering* surface that formats a `Descendant` field was not. The plan traced the type change to its one comparison consumer (`_emit_stuck_tool`) and stopped there, so `cli.py`'s doctor row and the three truncation assertions in `tests/test_supervisor_stuck_tool.py` were invisible to it. Likewise, deleting the socket check was scoped as a code deletion without asking which operator-facing STRINGS describe that check — `notify.py` was the answer.
- **Rule for the remaining phases:** when a phase changes a type or deletes a mechanism, grep for the field NAME and for the mechanism's vocabulary across rendering and notification surfaces, not only across call sites that compute with it.

### Finding: review caught a crash the full green gate did not  *(status: active)*
- `worker_idle_min_samples` was loaded through `_validate_non_negative_int`, which accepts 0, while its three sibling float thresholds used `_validate_positive_float`, which rejects 0 with an explicit rationale. With a zero minimum, `worker_idle_window_satisfied`'s `len(samples) < min_samples` guard passes on an empty history and the next line indexes `stamps[-1]` — **probed: `IndexError: list index out of range`**, which crashes the supervisor tick.
- Reachable in production: the loader accepts `worker_idle_min_samples: 0` from `.orchestrator.json` (probed), and `cpu_samples` is empty on any tick where `ps` cannot be read or the worker pid is absent from the snapshot.
- **Fixed in the same commit:** added `_validate_positive_int` and rewired the threshold to it; made the predicate refuse an empty history on its own terms rather than via a caller-supplied minimum; added load-path tests for all four thresholds and an empty-history predicate test.
- **Why it survived 2389 green tests:** the new thresholds had no config-load coverage whatsoever, so nothing exercised the validator that was wrong. A threshold added to `ProjectConfig` needs a load test, not only a use-site test.

### Finding: the contiguity rule is coupled to the tick cadence, and fails silently  *(status: active — operator decision recorded)*
- The contiguity rule rejects any window with an adjacent gap wider than `worker_idle_max_sample_gap_seconds` (60.0). At the documented 30s `StartInterval` default that is 2× margin. At 60s — this project's own pre-tick-on-action default (`docs/operations.md`) — gaps sit exactly on the ceiling and ordinary cron jitter pushes them over, so the window is never satisfied and the watchdog goes permanently deaf. Past 60s it never fires at all.
- The failure is indistinguishable from "no worker was ever idle", which is the silent-wedge outcome the master's plan-level Done criteria name as the one way this work could end up worse than what it replaces.
- **Operator decision (p1 review gate): document it, do not add a detector.** A caution now sits beside the `StartInterval` guidance in `docs/operations.md`. Changing the threshold was not on the table — it is signed off, so a retune would be an approach switch. A `clu doctor` check was declined as behaviour this phase was not scoped for.
- This machine is at `StartInterval 30` (verified), so nothing is broken today; the trap is latent.

## Failure modes to anticipate

- **The float change ripples further than expected.** `_parse_duration` feeds both `elapsed_seconds` and `cpu_seconds`, and `_emit_stuck_tool` compares the latter against config ints. basedpyright is a hard gate (`clu verify`), so a partial type change fails the build rather than passing quietly.
- **`ps` cumulative time resets if a pid is recycled mid-window** — a reused pid names a different process, whose CPU time starts from zero, so the delta reads negative or flat. This project already treats pid reuse as a live hazard rather than a theoretical one: `state.py:314-320` anchors the cmdline marker on slug-alphabet boundaries specifically to survive it (#76). A negative delta must be treated as "cannot judge" — skip the emit — never as "very quiet".
- **The root pid's CPU is dropped.** `walk_worker_tree` excludes the root by contract; the current code compensates via `tree_pids`. Losing it means measuring the shim's children but not the shim, which is nearly the whole signal.
- **Descendants dying mid-window shrink the cumulative sum**, producing a spurious negative or flat delta right after a test run finishes. Same handling as above.
- **The new predicate is satisfiable where the old one was not**, so tests that passed by never firing will start firing. Existing green is not evidence here — A1 flagged exactly this: "raising the sample cap or lowering `window_min` turns a currently-unsatisfiable predicate into a live one; every existing test passes, and the fleet starts alerting."

## Done criteria

- **Observable, and this is the phase's gate:** drive a claim through 20 real `tick()` calls at simulated 30s spacing with a fixture tree whose cumulative CPU advances ~0.25s per tick (the measured live-worker rate) — assert NO `worker_idle` event is emitted. Then drive an identical claim whose cumulative CPU does not move at all — assert exactly one `worker_idle` IS emitted. Both assertions read the emitted event log, not the predicate in isolation. The second half is what proves the detector can now produce a true positive; today it cannot.
- A test asserts the contiguity rule directly against the historical false-alarm shape: samples quiet, then a gap longer than `max_sample_gap`, then more quiet samples → NOT satisfied. Under today's code that same fixture fires.
- No `lsof` invocation remains anywhere in `end_of_line/`.
- `supervisor.py` contains no comment claiming `%cpu` is instantaneous; `dispatch.py:296` no longer claims tree-awareness prevents a `WORKER_IDLE` false-fire; and `docs/architecture.md` no longer implies the socket check was tree-aware.
- **If the busy-worker fixture and the dormant-worker fixture cannot be separated by the cumulative-delta threshold, STOP** — that is this phase's branch-on-failure, not a signal to widen the threshold until they part. The separation was measured between a *waiting* process and a *never-ran* process, not against a real wedge (master, Background findings); this criterion is where that assumption gets tested.
- `python3 -m unittest discover -s tests` green; `clu verify` green (basedpyright included).
