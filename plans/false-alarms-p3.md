# false-alarms-p3 — workers declare their quiet spans instead of being guessed at

You are phase `p3` of the `false-alarms` plan. p1 and p2 make the supervisor's *inference* correct. This phase removes most of the need for it: a worker about to go quiet for a long stretch — a code review, a full test gate — says so, through a token-validated callback the supervisor reads, and tells the operator's live session if one is running. One commit.

## Locked decisions (do NOT re-litigate)

See the master `plans/false-alarms.md`. The decisions binding this phase:

- **The worker knows what it is doing; the supervisor should not have to guess.** Every signal p1 builds is a proxy for a fact the worker holds directly. A declared span is that fact, stated once, with no inference in between.
- **A declared span is a LEASE, not a pair.** It carries an expected duration and expires on its own. This is the single most expensive lesson of this plan's research: the same "trust the close event" design was proposed via subagent hooks and disqualified because close events are not guaranteed ([#27755](https://github.com/anthropics/claude-code/issues/27755), [#33049](https://github.com/anthropics/claude-code/issues/33049)), then found ALREADY BROKEN in the shipped activity marker, where a nonzero-exit Bash call fires no close event at all (master Diagnosis, second test). A span that can be left open forever is a silence switch. Do not build one.
- **This does NOT replace p1's inference — it sits in front of it.** A worker that declares nothing must still be judged, because old skill versions, crashed workers, and non-`/clu-phase` dispatches all exist. p1 is the floor; this phase is the fast path.
- **The ceiling is operator-signed-off at 45 minutes** (2026-08-21, recorded in the master's Status), and it is operator-configurable. It is the only bound on how much silence a worker can claim, so changing it during implementation is an approach switch, not tuning.
- **Messaging the operator is BEST-EFFORT and never load-bearing.** No session running is the normal case (clu exists for the operator being away), and a failed or skipped send must never fail a phase, block a callback, or change what the supervisor decides.
- **`SendMessage` from a dispatched worker is proven, including under the hardened recipe.** Probed this session: a headless `claude -p` sees `ListAgents` and `SendMessage`, resolves this repo's interactive session by name, and delivers — with `--permission-mode dontAsk` and the hardened `--allowedTools` list applied, because that list gates permission prompts rather than tool availability. Two probe messages were received in the drafting session.

## Work

- `end_of_line/cli.py` — a new token-validated worker callback declaring a quiet span. `--token` required and validated against the live claim, exactly like every other callback; `state.validate_slug` on the phase id before any path join; `ExitCode` for every exit.

  ```
  clu quiet-span --plan <slug> --phase <id> --token <tok> \
                 --reason code-review --expected-minutes 20
  clu quiet-span --plan <slug> --phase <id> --token <tok> --end
  ```

  `--end` is a courtesy, not the mechanism — the span expires on `expected-minutes` regardless, and a worker that dies mid-review must not leave the watchdog deaf.

- `end_of_line/plan_store.py` — ONE `op_*` writing the span onto the claim. It lands in the `flags` JSON catch-all (`plan_store.py:558` routes any key outside `_CLAIM_OWN_KEYS` there), so `PROJECT_SCHEMA_VERSION` stays 1 and no migration is needed. Never read-modify-write: name the row being changed.

  ```python
  claim["quiet_span"] = {"reason": str, "started_at": iso, "expires_at": iso}
  # expires_at = started_at + min(expected_minutes, ceiling) — a worker
  # cannot buy unlimited silence by declaring a 10-hour review.
  ```

- `end_of_line/supervisor.py` — `_emit_worker_idle` suppresses while a quiet span is open AND unexpired, alongside the existing active-tool check. Per the master's Background findings, whatever gates suppression must also join BOTH watchdogs' compare-and-set sets, or a span opening mid-tick stops voiding a stale emit.

- `end_of_line/state.py` — the span predicate (`quiet_span_active(claim, now) -> bool`) and the ceiling constant. One predicate, read-site bounded, same discipline as p2's marker age bound.

- `end_of_line/config.py` — `worker_quiet_span_ceiling_minutes` (default 45), so an operator can cap how much silence a worker may declare.

- `end_of_line/skills/clu-phase/SKILL.md` — the worker contract gains one step, placed immediately before the per-phase `/code-review`: declare the span, then run the review, then end it. Plus the best-effort operator notice, written so a worker with no peer session does nothing and says nothing about it.

  The instruction must be explicit that the notice is fire-and-forget: `ListAgents`, and if an interactive session for this project is listed, one short `SendMessage` naming the plan, the phase, and the expected quiet duration. If nothing is listed, skip silently. Never retry, never block, never treat a send failure as a phase failure.

- `docs/contract.md` — the new callback joins the documented worker-callback list; the `quiet_span` claim field joins the state document.

- `docs/architecture.md` — the tick priority chain's idle band gains the span check. Keep "one tick = one action" intact: this is a suppression condition inside an existing band, not a new action.

- `tests/test_quiet_span.py` *(new)* — an open unexpired span suppresses; an EXPIRED span does not (the leak this design refuses to build); a span with no `--end` still expires; a declared duration above the ceiling is clamped; a forged or stale token is rejected.

- Consumes: `plan_store` claim-write helpers and the `_CLAIM_OWN_KEYS` / `flags` split (`plan_store.py:558`); `state.validate_slug`; `ExitCode`; `state.worker_idle_window_satisfied(claim, now, *, min_samples: int, window_min: float, max_sample_gap: float, cpu_delta_threshold: float) -> bool` (produced by p1); the bounded-suppression predicate produced by p2
- Produces: `clu quiet-span` worker callback; `state.quiet_span_active(claim: dict, now: datetime) -> bool`; `current_claim.quiet_span` record

## Decisions & findings

### Decision: the span expires on its own clock rather than on an end callback  *(status: active)*
- **Rationale:** every close-event design examined in this plan's research has failed in the field or in probe. Subagent stop events are missing in a reported 42% of traces; the shipped activity marker's close event does not fire at all for a nonzero-exit command. A suppression that depends on a message arriving is a suppression that eventually becomes permanent, and permanent suppression of a wedge detector is strictly worse than the false alarms this plan set out to remove.
- **Alternatives considered:** require `--end` and treat a missing end as an error (rejected — the error arrives in the same silence it is trying to report); sweep expired spans from the tick (rejected — a new writer on the hot claim row, and the read-site bound needs no writer at all).
- **Evidence:** master Diagnosis, second test (the four-case hook probe); the two GitHub issues cited in Locked decisions; `plan_store.py:558` for the storage seam.

### Decision: notify the operator's session, but never depend on it  *(status: active)*
- **Rationale:** it is the highest-signal channel available when the operator is present — it carries the reason and the expected duration into the session they are actually looking at — and it is worthless when they are absent, which is clu's design centre. Treating it as an enhancement rather than a mechanism is what lets it be both.
- **Alternatives considered:** route the notice through the existing notify channels instead (rejected — Discord already covers away, and duplicating a live-session notice there is the noise this phase is trying not to create); make the send mandatory (rejected — no peer session is the normal case, so a mandatory send fails constantly by design).
- **Evidence:** probes recorded in the master's Background findings; `ListAgents` returned no interactive peer for other machines' sessions, and returned this repo's session by name.

## Failure modes to anticipate

- **Fleet noise.** Several workers announcing every review turns the operator's session into a ticker. Scope the notice to spans above a meaningful duration, and keep it to one line.
- **A worker declares a span and then wedges inside it.** Covered by the ceiling and the expiry, which is the entire reason both exist — but the detection latency for that wedge is now the span length, not the idle window. That is a deliberate trade and it should be stated in the docs rather than discovered.
- **The span becomes a way to silence a watchdog.** A worker (or a future skill edit) that declares a span at phase start and never ends it gets `ceiling_minutes` of guaranteed silence. The ceiling is the only thing bounding that; it must not be configurable to an unbounded value.
- **`ListAgents` resolves the wrong session.** It lists peers by name across the machine; a worker could message an unrelated session that happens to match. The notice must name the plan and phase so a misrouted message is obviously identifiable rather than confusing.
- **The message interrupts the operator mid-task.** Two probe sends during drafting demonstrated exactly this. It is acceptable for a deliberate, low-frequency notice and unacceptable for anything chatty.
- **Skill and code ship together or the contract lies.** A `/clu-phase` that declares spans against a clu without the callback fails every declaration; a clu expecting declarations from an old skill silently gets none. The callback must be additive and its absence must degrade to p1's inference.

## Done criteria

- **Observable, and this phase's gate:** drive a claim through the idle window with a quiet span open and unexpired — assert NO `worker_idle` event. Re-drive it with the span EXPIRED and the worker still quiet — assert the event fires. Read the emitted event log, not the predicate. The second half is what proves this did not build a silence switch.
- **Observable:** a span declared with no `--end` and a dead worker still expires, and the watchdog then reports — proving the design does not depend on a close callback arriving.
- A declared duration above the ceiling is clamped to the ceiling, asserted directly.
- The callback rejects a forged token and a token from a released claim, like every other worker callback.
- A worker with no interactive peer session completes its phase normally and emits no error, warning, or log noise about the absent notice.
- `docs/contract.md` lists the new callback; `docs/architecture.md` describes the span as a suppression condition within the existing idle band, with the detection-latency trade stated.
- Commit message carries `Fixes #115` — p3 is the last of the three phases (p1, p2, p3) that together complete it, so the closing keyword belongs here and nowhere earlier.
- `python3 -m unittest discover -s tests` green; `clu verify` green.
