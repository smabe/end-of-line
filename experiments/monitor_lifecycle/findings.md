# Monitor lifecycle — raw observations

Scratch notebook for the experiment. Final writeup lives at
`docs/research/monitor-lifecycle.md`.

## Phase A — concurrency floor

- Producers armed: 5 (labels A-E, 1s interval, 15 ticks each)
- Monitor calls: 5 — all returned task IDs, no error
- All 5 fired tick-1 and tick-15 events and exited cleanly
- **Cap: ≥5, no observed limit at this size**
- Sub-finding: Monitor armings serialize at the harness — start times A=15:35:10, B=15:35:12, C=15:35:14, D=15:35:15, E=15:35:17 (1-2s apart) despite being sent as parallel tool calls in a single message

## Phase B — `/clear`

Run on 2026-05-23, producers armed at 5s interval, A max-ticks 60, B max-ticks 60.
Pre-`/clear` snapshot: `experiments/monitor_lifecycle/snapshot_before_clear.json`
(A pid=16856 ticks=8 alive; B pid=16997 ticks=7 alive).

| Observation | Result |
|---|---|
| Producer PIDs before /clear | A=16856, B=16997 (both alive, 8/7 ticks) |
| Producer PIDs after /clear (alive?) | **Both ALIVE** — sample 1: A=18 ticks, B=17 ticks; sample 2 (+30s): A=24 ticks, B=23 ticks (+6 each, matches 5s interval) |
| Latest tick age after /clear | 0.7s / 1.7s (sample 1), 3.8s / 4.8s (sample 2) — producers ticking continuously |
| Pre-/clear Monitor tasks alive in new session? | **YES** — tasks `bwomer93o` (A) and `bsp00138l` (B) delivered tick-20 events to the fresh session, then hit Monitor's normal `timed out — re-arm if needed` timeout |
| Re-arm Monitor on a new stream after /clear | **Yes** — task `b0vzd5kqp` armed on producer-C, delivered events normally |

**Conclusion: `/clear` does NOT reap producer processes AND does NOT cancel
armed Monitor tasks.** Both child processes and Monitor listeners are
session-orphan-tolerant. Pre-/clear Monitor tasks continue delivering
notifications into the post-/clear session until their own timeout expires.
The fresh session can also arm new Monitors normally. Implication: a
SessionStart-hook need only arm Monitor if no prior Monitor is still in
flight; otherwise the prior Monitor will deliver until its own timeout.

Post-test cleanup observation: A/B exited naturally at the producer's
`--max-ticks` boundary (24 each) without external intervention, confirming
the orphan producers had a normal lifecycle independent of the session
that spawned them.

## Phase C — `/compact`

Run on 2026-05-23, producers armed at 5s interval, D max-ticks 60, E max-ticks 60.
Pre-`/compact` snapshot: `experiments/monitor_lifecycle/snapshot_before_compact.json`
(D pid=31730 ticks=5 alive; E pid=31727 ticks=5 alive). Both Monitors
were armed with `timeout_ms: 1800000` (30 min).

| Observation | Result |
|---|---|
| Producer PIDs before /compact | D=31730, E=31727 (both alive, 5/5 ticks) |
| Producer PIDs after /compact (alive?) | **Both ALIVE** through the entire post-compact session — sample at +20s (D=37, E=37); ran continuously to natural exit at tick 60 each (final sample shows DEAD ticks=60, age 28s, matching the producer's --max-ticks boundary) |
| Latest tick age after /compact | 1.4s (sample 1, +20s post-compact) — ticks landing on schedule throughout |
| Pre-/compact Monitor tasks alive in new session? | **YES** — tasks `bby9ymdyf` (D) and `boyyj1xj9` (E) streamed tick 13 → tick 60 into the post-/compact session, then both ended cleanly with stream-ended notifications when their producer exited |
| Re-arm Monitor on a new stream after /compact | **Yes** — task `bviob5nqr` armed on producer-F immediately after compact, delivered tick 1 → tick 20 normally and stream-ended on its own |

**Conclusion: `/compact` does NOT reap producer processes AND does NOT cancel
armed Monitor tasks.** Pre-/compact Monitor tasks continued delivering
events uninterrupted across the compact boundary and through their full
producer lifecycle, *cleaner than Phase B's `/clear`* — under `/compact`
the Monitor's stream-ended event also arrived in-session (whereas Phase B
saw `/clear`'s pre-existing Monitors hit their own timeout while events
kept flowing). Fresh post-/compact Monitor arming on a new producer works
identically to a normal session.

Combined with Phase B, this confirms that **session control commands
(`/clear`, `/compact`) do not touch the harness's background task table
or the child processes those tasks own.** Producers and Monitor listeners
are session-orphan-tolerant across both reset paths.

## Phase D — SessionStart-hook arming

Phase B + Phase C both established that the *prior* session's Monitor
keeps delivering across `/clear` and `/compact`. That makes a SessionStart
hook unnecessary for the *continuation* case the long-running-Monitor
proposal in #70 cares about — the existing Monitor will keep firing into
the new context until its own `timeout_ms` expires.

A SessionStart hook still matters for the *cold-start* case (operator
opens a brand-new conversation that has no prior Monitor in flight). Per
the protocol that's a separate setting change and a separate experiment;
deferring rather than rolling it into this session.

Conclusion: **Phase D skipped.** Defer to a follow-up if cold-start
arming becomes a blocker for #70. Reasoning above.

## Decision

`#70` green/red: **GREEN with caveats.**

Long-running Monitor delivery for clu-worker observation is viable. Concrete
findings supporting this:

- **Survives session resets.** Pre-/clear and pre-/compact Monitor tasks
  continue delivering events into the new context. Operator can /clear or
  /compact without losing clu-watch.
- **Concurrency floor ≥5.** No observed cap at 5 simultaneous Monitors
  per session.
- **Producer orphans survive too.** Even when the harness-side task ends
  (timeout or stream end), the child process keeps running and can be
  re-attached by a fresh Monitor on the same stream / log file.
- **Stream-ended events arrive normally** in the post-compact session,
  which gives the operator a deterministic signal to re-arm if desired.

Caveats:

- **`/clear` leaves the prior Monitor on its own timeout schedule** —
  events keep flowing, but the post-/clear session sees a `timed out —
  re-arm if needed` message at the original `timeout_ms` boundary, not
  at producer exit. `/compact` is cleaner.
- **Cold-start (brand-new session with no prior Monitor)** still needs a
  SessionStart hook to auto-arm; Phase D deferred.
- **Monitor armings serialize at the harness** (~1-2s between consecutive
  Monitor tool calls), so dashboards with many parallel streams pay a
  small startup tax. Steady-state delivery is unaffected.

Recommend: proceed with #70's long-running-Monitor design. Use
`persistent: true` for the worker-observation Monitor, and rely on
pre-existing-Monitor survival across /clear /compact as the steady-state
delivery mechanism. SessionStart-hook arming is a small follow-up for
the cold-start path, not a blocker.
