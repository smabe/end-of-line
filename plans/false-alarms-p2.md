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

- `end_of_line/cli.py` — **no new flag.** The probe removed the reason for one: there is no failure event to wire it to. `clu activity` keeps `--start-bash` / `--end-bash` exactly as they are, `--token` stays required and validated against the live claim. One change only: `cmd_activity` currently DISCARDS `stamp_activity_marker`'s `False` return (`:6771`), so a stamp dropped under store contention leaves no trace at all — log it rather than continuing silently.

- `end_of_line/plan_store.py` — `op_activity` (`:791-792`) is the real write site. Two changes: (a) on a START, invalidate the accumulated idle window by clearing `cpu_samples` and re-arming `worker_idle_notified` — a tool call is positive proof of activity, so the window it would otherwise contribute to is void; (b) the CAS set must include whatever new field gates suppression, per the master's Background findings, or a state change mid-tick stops voiding a stale emit.

- `end_of_line/state.py` — bound the marker's age at the read site. `_emit_worker_idle` returns early whenever `active_tool_started_at` is set (`supervisor.py:665-666`) with no upper bound; a marker older than the sibling stuck-tool window must stop suppressing. Derive the bound from `config.stuck_tool_threshold_seconds` (`config.py:150`, default 300) rather than minting an unrelated number — the two watchdogs must not disagree about whether a tool is still running. Prefer bounding where it is READ rather than adding a sweeper — one predicate, no new write path.

- `end_of_line/state.py` — DELETE `mark_active_tool_start` (`:968`) and `clear_active_tool` (`:981`). Both have zero production callers (grepped across `end_of_line/`); the live path is raw SQL at `plan_store.py:792`. Their docstrings assert that `clu activity --start-bash` / `--end-bash` call them, which is false and actively misleading — this is the exact "comment as decision record" trap that #118 was filed for elsewhere in the codebase. If a test depends on them, port the test to the real path rather than keeping the functions alive to satisfy it.

- `end_of_line/skills/clu-phase/SKILL.md` — the hook block itself is unchanged (there is no event to add). Document what the probe established, because a future reader will otherwise "fix" this by wiring the failure event: a nonzero-exit Bash command fires no closing event, so the marker is stale until the next Bash call re-stamps it, and the age bound is what makes that safe. **Correct the false claim at `:350`**: it states "Claude Code's subagent contexts don't inherit parent env, so `CLU_TOKEN` is unset inside subagent hooks → they short-circuit." Disproved by probe this session on Claude Code 2.1.238 — a `PreToolUse` hook fired from inside an Explore subagent (payload carried `agent_id`, which per the hook docs is present only inside a subagent call) received `CLU_TOKEN` intact. Replace with what is actually true, and drop the "lease expiry remains the safety net for wedges inside subagents" conclusion that rests on it.

- `docs/operations.md` — the activity-hook recipe (`:708-720`) states the coverage limit: the marker is cleared only on a successful Bash call, and the age bound is what prevents a failing one from silencing the watchdog. The recipe's hook block itself does not change.

- `docs/contract.md` — document the claim fields this phase touches, including the marker's new bounded meaning ("suppresses only while fresh") rather than its current unbounded one.

- `tests/test_activity_marker.py` *(new)* — a marker past its age bound stops suppressing; a START voids an accumulated window; a dropped stamp is logged rather than silent.

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
