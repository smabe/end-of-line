# false-alarms-p2 — activity marker: close the leak, delete the dead path

You are phase `p2` of the `false-alarms` plan. The active-tool marker can currently be left stamped forever by a failed or denied Bash call, which permanently silences the idle watchdog for that claim — the same "deaf watchdog" failure p1 exists to avoid. This phase closes that leak, bounds the marker's age, and removes two functions whose docstrings claim a role they do not have. One commit.

## Locked decisions (do NOT re-litigate)

See the master `plans/false-alarms.md`. The decisions binding this phase:

- **The leak is any command that exits NONZERO, and no hook event closes it.** Probed this session against a headless worker with all three tool-exit events registered on matcher `Bash`: `echo ok` fired `PreToolUse` then `PostToolUse`; `exit 3` and `ls /nonexistent` fired `PreToolUse` and NOTHING ELSE; a Bash call denied by `--allowedTools` fired `PreToolUse` then `PostToolUse` normally. `PostToolUseFailure` did not fire in any case despite being registered.

  Two consequences, and both invert what this phase was originally scoped to do. First, **wiring `PostToolUseFailure` is NOT the fix** — it never fires, so it would close nothing; it is excluded on that evidence rather than adopted. Second, **permission denials are not the leaking path** — they clear correctly. The leaking path is every failing test run, every failing build, and every `grep` that matches nothing (exit 1), which is far more of a worker's normal operation than denials ever were.

- **The age bound is therefore the whole fix, not a backstop.** There is no event to wire, so a marker that is never cleared can only be made harmless by expiring. Any design that depends on some future event arriving repeats the mistake this probe just exposed.
- **Non-Bash tools stay excluded, and the asymmetry is safe** because the marker's only job is scoping which descendant processes are stuck-tool candidates, and only a Bash call spawns descendants.
- **The hook block's three guards are deliberate and stay.** `[ -n "$CLU_TOKEN" ]` (so the hook opens only for clu-dispatched sessions), trailing `|| true` (Claude Code treats hook exit 2 as BLOCKING — a transient failure inside `clu activity` exiting 2 would freeze every Bash call), and `2>/dev/null`. Do not "tidy" any of them.
- **A marker with no upper age bound is a silence switch.** Whatever clears it can be lost — a crashed worker, a dropped stamp, a `|| true` swallowing the call. The suppression must expire on its own.

## Work

> **Line hints re-anchored at `3c5d969` (after p1 shipped).** p1 rewrote `supervisor.py` and
> `state.py`, so every `:NNN` below that names those two files was measured against the
> pre-p1 tree. Current positions, confirmed by grep this session — anchor on the SYMBOL, these
> are secondary:
> `_emit_worker_idle`'s early return on `active_tool_started_at` → `supervisor.py:684` (was
> :665-666) · `mark_active_tool_start` → `state.py:1036` (was :968) · `clear_active_tool` →
> `state.py:1049` (was :981) · `stamp_activity_marker` → `state.py:1059` · `op_activity` →
> `plan_store.py:770` (was :791) · `cmd_activity` → `cli.py:6749` (the discarded `False`
> return is inside it; was cited as :6771) · the activity-hook recipe → `docs/operations.md:721`
> (was :708-720; p1 inserted a cadence caution above it). `config.py:150`
> (`stuck_tool_threshold_seconds`) is unmoved.
>
> **Also new since this shard was written:** p1 added four `worker_idle_*` thresholds to
> `ProjectConfig` and a `_validate_positive_int` helper beside `_validate_positive_float`. If
> this phase adds a threshold of its own, use those validators — a detector bound of zero is
> what crashed the tick in p1's review.

- `end_of_line/cli.py` — **no new flag.** The probe removed the reason for one: there is no failure event to wire it to. `clu activity` keeps `--start-bash` / `--end-bash` exactly as they are, `--token` stays required and validated against the live claim. One change only: `cmd_activity` currently DISCARDS `stamp_activity_marker`'s `False` return (`:6771`), so a stamp dropped under store contention leaves no trace at all — log it rather than continuing silently.

- `end_of_line/plan_store.py` — `op_activity` (`:791-792`) is the real write site. Two changes: (a) on a START, invalidate the accumulated idle window by clearing `cpu_samples` and re-arming `worker_idle_notified` — a tool call is positive proof of activity, so the window it would otherwise contribute to is void; (b) the CAS set must include whatever new field gates suppression, per the master's Background findings, or a state change mid-tick stops voiding a stale emit.

- `end_of_line/state.py` — bound the marker's age at the read site. `_emit_worker_idle` returns early whenever `active_tool_started_at` is set (`supervisor.py:665-666`) with no upper bound; a marker older than the sibling stuck-tool window must stop suppressing. Derive the bound from `config.stuck_tool_threshold_seconds` (`config.py:150`, default 300) rather than minting an unrelated number — the two watchdogs must not disagree about whether a tool is still running. Prefer bounding where it is READ rather than adding a sweeper — one predicate, no new write path.

- `end_of_line/state.py` — DELETE `mark_active_tool_start` (`:968`) and `clear_active_tool` (`:981`). Both have zero production callers (grepped across `end_of_line/`); the live path is raw SQL at `plan_store.py:792`. Their docstrings assert that `clu activity --start-bash` / `--end-bash` call them, which is false and actively misleading — this is the exact "comment as decision record" trap that #118 was filed for elsewhere in the codebase. If a test depends on them, port the test to the real path rather than keeping the functions alive to satisfy it.

- `end_of_line/skills/clu-phase/SKILL.md` — the hook block itself is unchanged (there is no event to add). Document what the probe established, because a future reader will otherwise "fix" this by wiring the failure event: a nonzero-exit Bash command fires no closing event, so the marker is stale until the next Bash call re-stamps it, and the age bound is what makes that safe. **Correct the false claim at `:350`**: it states "Claude Code's subagent contexts don't inherit parent env, so `CLU_TOKEN` is unset inside subagent hooks → they short-circuit." Disproved by probe this session on Claude Code 2.1.238 — a `PreToolUse` hook fired from inside an Explore subagent (payload carried `agent_id`, which per the hook docs is present only inside a subagent call) received `CLU_TOKEN` intact. Replace with what is actually true, and drop the "lease expiry remains the safety net for wedges inside subagents" conclusion that rests on it.

- `docs/operations.md` — the activity-hook recipe (`:708-720`) states the coverage limit: the marker is cleared only on a successful Bash call, and the age bound is what prevents a failing one from silencing the watchdog. The recipe's hook block itself does not change.

- `docs/contract.md` — document the claim fields this phase touches, including the marker's new bounded meaning ("suppresses only while fresh") rather than its current unbounded one.

- `tests/test_activity_marker.py` *(new)* — a marker past its age bound stops suppressing; a START voids an accumulated window; a dropped stamp is logged rather than silent.

- `end_of_line/supervisor.py` — **added at execution.** The phase says to bound the marker "at the read site", and the read site is `_emit_worker_idle`'s early return; the predicate lives in `state.py` but the call site is here. Also carries the new `cpu_samples` precondition (see findings).
- `end_of_line/activity_hook.py` — **added at execution.** The OTHER entry point discarding the same `False` return, and the one the bundled recipe actually wires — logging only in `cmd_activity` would leave the hot path silent.
- `tests/test_state_stuck_tool.py` — **added at execution.** Four tests called the deleted helpers; their behaviours are ported to the real `op_activity` path, per this phase's own instruction not to resurrect the functions to keep a test green.
- `docs/reference.md` — **added at execution.** Named both deleted symbols, and described the idle gate as "no active Bash tool" rather than "no FRESH marker".
- `docs/architecture.md` — **added at execution.** Asserted that the activity stamp "touch[es] only their own claim columns, so they cannot false-trip a precondition" — the opposite of true once a START clears `cpu_samples`, and deliberately so.
- `end_of_line/skills_manifest.json` — **added at execution.** `tests/test_skill_sync.py` hard-fails when a bundled SKILL.md's hash is missing; regenerated via `scripts/gen_skill_manifest.py`.
- `tests/test_activity_marker.py` — **also carries the review fix**: the zero-bound test as first written pinned the defect as intended behaviour.

- Consumes: `plan_store.op_activity(...)` (`plan_store.py:791`); `state.stamp_activity_marker(state_path, *, token: str, phase: str, action: str, timeout_seconds: float | None) -> bool`; `state.worker_idle_window_satisfied(claim, now, *, min_samples: int, window_min: float, max_sample_gap: float, cpu_delta_threshold: float) -> bool` (produced by p1)
- Produces: a bounded-suppression predicate consumed by `_emit_worker_idle`

## Decisions & findings

### Decision: bound the marker at the read site rather than sweeping it  *(status: active)*
- **Rationale:** the leak is that a clearing event may never arrive; adding a second writer to clear it inherits the same fragility one level up, and every write to a claim row contends with the tick. Bounding where the value is READ means the suppression expires by construction with no new write path and no new failure mode.
- **Alternatives considered:** a sweeper in the tick that clears stale markers (rejected — a new writer on the hot claim row, and "one tick = one action" makes it awkward to place); wiring `PostToolUseFailure` to clear it (rejected on PROBE evidence, not on judgment — it did not fire for a nonzero exit, a failed command, or a permission denial, so it would close nothing).
- **Evidence:** `supervisor.py:665-666`; `plan_store.py:791-792`; the four-case hook probe recorded in the master's Diagnosis.

### Decision: delete the two uncalled state helpers  *(status: active)*
- **Rationale:** they are not merely unused — their docstrings describe them as the live activity write path, so a future contributor reading `state.py` to understand the marker learns something false. That is worse than absence.
- **Alternatives considered:** keep them and correct the docstrings (rejected — dead code with accurate comments is still dead code that the next reader must rule out); route `op_activity` through them to make the docstrings true (rejected — it would add an indirection whose only purpose is to justify keeping the functions).
- **Evidence:** `state.py:968,981`; grep for callers across `end_of_line/` returns nothing outside `state.py`; `plan_store.py:792` is the raw-SQL write.

### Finding: the plan predicted the wrong compare-and-set need, in the wrong direction  *(status: active)*
- The Work list said "the CAS set must include whatever new field gates suppression". Checked at execution: there IS no new field — suppression is still gated on `active_tool_started_at`, and that was already in both watchdogs' CAS sets (`supervisor.py:611`, `:750`). The real need ran the other way. `op_activity`'s START became a **second writer of `cpu_samples`**, so the tick's own sample append needed a precondition of its own or a tick already in flight would write the cleared history straight back and resurrect the window the START had just voided.
- p1 shipped a comment asserting "the tick is the only writer of `cpu_samples`". True when written, false the moment this phase landed — rewritten in the same commit.
- **Cost, recorded because it is real:** `_emit_worker_idle` runs on every tick with a live claim, so nearly every such tick now carries a `cpu_samples` precondition, and a Bash START landing inside the tick's think-window discards that whole tick (`idle / concurrent_write`, re-derived on the next cron tick). It cannot affect dispatch — `_emit_worker_idle` returns before recording anything when there is no claim.

### Finding: review caught a silence switch the phase had reintroduced by inheritance  *(status: active)*
- The age bound is derived from `stuck_tool_threshold_seconds`, which `config.py:148` documents as "Setting threshold to 0 disables detection" and which the non-negative validator accepts. As first written, `activity_marker_suppresses` read `max_age_seconds <= 0` as "no bound" and returned True for any stamped marker — **probed: a 30-day-old marker suppressed with `bound=0` and did not with `bound=300`.** So an operator disabling stuck-tool detection silently disabled the idle watchdog's protection too, restoring the exact deafness this phase exists to close, in violation of the master's plan-level invariant that no suppression this plan adds can be left open indefinitely.
- The first-written test PINNED the defect as intended behaviour, which is why the suite stayed green.
- **Fixed in the same commit:** a disabled sibling detector falls back to `state.ACTIVITY_MARKER_FALLBACK_BOUND_SECONDS` (300, equal to the config default) rather than dropping the bound. The shard's "do not mint an unrelated number" rule was written so the two watchdogs cannot disagree about whether a tool is still running — with the sibling detector switched off there is no second watchdog to disagree with, so that rationale does not reach this branch. Four doc claims stating the old behaviour were corrected with it.

### Finding: dropped-stamp logging lands where the recipes discard it  *(status: active)*
- Both shipped hook recipes end in `2>/dev/null` (a locked decision — a hook exiting 2 BLOCKS the Bash call), so the new stderr trace is invisible during normal worker operation. It reaches an operator who runs `clu activity` or the hook module by hand. This is the intended shape, recorded so nobody later concludes the logging is broken.

## Failure modes to anticipate

- **Deleting the helpers breaks tests that call them directly**, which will read as a regression. Port those tests to the real path; do not resurrect the functions to keep a test green.
- **The age bound is set too tight** and starts suppressing nothing, re-opening p1's false alarms from a different direction. It must be at least the stuck-tool threshold so the two watchdogs cannot disagree about whether a tool is still running.
- **Clearing `cpu_samples` on tool START interacts with p1's contiguity rule** — after a clear, the window rebuilds from scratch, which is intended, but a test asserting "N samples after a tool call" will need to know that.
- **Carried in by the p1 sweep — the clear is NOT redundant with p1's contiguity rule, and must not be dropped as such.** p1 shipped a rule that voids a window containing any adjacent sample gap wider than `worker_idle_max_sample_gap_seconds` (default 60s). That covers a LONG tool call, whose suppressed sampling leaves a wide hole. It does NOT cover a SHORT one: a Bash call lasting less than the max gap leaves a hole contiguity accepts, so the window survives and the samples either side of a real tool call get treated as one uninterrupted quiet span. p2's clear is what voids the window for a tool call of ANY length. The two mechanisms look interchangeable and are not — one is bounded by the gap threshold and the other is not.
- **No hook-config change ships, so no operator machine needs updating** — this is what dropping the new flag buys, and it is worth stating because the original scoping assumed the opposite. If implementation discovers a case where a closing event DOES fire that the probe missed, that is new information about the API, not licence to re-add the flag without re-probing.
- **The probe covered four cases, not every case.** Nonzero exit, command failure, permission denial, success. A timeout, an interrupt, or a worker killed mid-call were not tested; all three plausibly also leave the marker stamped, and all three are covered by the age bound regardless — which is the argument for the age bound being the mechanism rather than event coverage.
- **`clu activity` is on the worker's hot path** — every Bash call pays it. The 2-second budget at `cli.py:6774` exists because freezing a worker's Bash call is worse than dropping a marker update.

## Done criteria

- **Carried in by the p1 sweep.** A test drives a Bash tool call SHORTER than `worker_idle_max_sample_gap_seconds` and asserts the idle window is void afterwards — proving the clear covers the case p1's contiguity rule accepts. Without this the two mechanisms are indistinguishable in the suite, and the clear looks safe to delete.

- **Observable, and it is this phase's gate:** a claim carrying a marker older than the age bound, with a worker whose cumulative CPU is not moving, emits `worker_idle` — read the emitted event log, not the predicate. Today that claim is silent forever, which is the deafness this phase exists to remove.
- **Observable:** a marker stamped and never cleared (the nonzero-exit shape the probe reproduced) stops suppressing once past the bound, while a marker stamped seconds ago still suppresses. Both read back from `clu state dump`, not from an internal return value.
- `mark_active_tool_start` and `clear_active_tool` do not exist in `end_of_line/`.
- `skills/clu-phase/SKILL.md` contains no claim that subagent hooks lack `CLU_TOKEN`, and it states the actual coverage limit — cleared on success only, with the age bound as what makes that safe — so the next reader does not "fix" it by wiring an event that never fires.
- `docs/operations.md` and `docs/contract.md` describe the marker as bounded rather than absolute; no doc line implies a failing Bash call clears it.
- **Observable:** a tool START voids an accumulated idle window — assert the sample set is empty afterwards, read back from `clu state dump`. Without this a window built before a long build survives it.
- The new suppression field appears in BOTH watchdogs' compare-and-set sets, asserted by a test where the field changes mid-tick and the emit is correctly discarded (the contract at `plan_store.py:1537-1541`).
- A stamp dropped under store contention is logged rather than silently discarded — asserted against `stamp_activity_marker` returning `False`.
- Commit message REFERENCES #115 without a closing keyword. #115 is completed by p1+p2+p3 together, so only p3 — the last of the three — carries `Fixes #115`; a closing keyword here would close the issue while a third of the fix is still unwritten.
- The worker contract is unchanged: a phase still ends by calling `clu complete` or `clu block`, and no callback skips token validation.
- `python3 -m unittest discover -s tests` green; `clu verify` green.
