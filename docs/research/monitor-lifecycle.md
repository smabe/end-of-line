# Monitor lifecycle (Claude Code)

Empirical findings on Claude Code's `Monitor` tool behavior across
`/clear`, `/compact`, and process boundaries. Run 2026-05-23 as the
research session for #69; this note is what informs production design
in #70 and any future long-running-Monitor surface.

The raw experiment + protocol live under
`experiments/monitor_lifecycle/`. This doc is the curated takeaway.

## TL;DR

- **`/clear` does not reap producer processes or cancel armed Monitors.**
  Pre-/clear Monitor tasks keep delivering events into the post-/clear
  session until their own `timeout_ms` boundary.
- **`/compact` behaves identically and a notch cleaner.** Pre-/compact
  Monitor tasks deliver events all the way through producer exit; the
  stream-ended notification also arrives in-session.
- **Concurrency floor ≥5.** Five Monitors armed in parallel all fired
  normally; no observed cap at that size.
- **Producer child processes are session-orphan-tolerant.** Once spawned
  under a Monitor, the child keeps running independent of the session
  that started it.
- **Monitor armings serialize at the harness** (~1-2s between consecutive
  calls), even when sent as a parallel-tool-call batch.

## What this means for clu

`Monitor` is safe to use as a long-lived signaling channel for
clu-worker observation. Session control commands (`/clear`, `/compact`)
do not touch the harness's background task table, so an armed Monitor
on `clu watch` survives an operator-driven session reset until its own
timeout expires.

Two implications for #70 (operator-dashboard long-running Monitor):

1. **Continuation case is free.** The Monitor a session armed before
   `/clear` or `/compact` keeps delivering into the new context. The
   operator doesn't need to re-arm to keep getting events for the
   remainder of the prior `timeout_ms` window.
2. **Cold-start case still needs arming.** A brand-new conversation
   that never armed a Monitor sees nothing. A SessionStart hook (or
   first-message instruction) is the right surface for that — separate
   experiment, not a blocker.

## Method (so future-you can re-run)

Four phases against three minimal Python helpers under
`experiments/monitor_lifecycle/`:

- `producer.py` — a tick generator that writes one stdout line per
  interval and mirrors to `~/.cache/clu-monitor-lifecycle/<label>.log`
  plus a `<label>.pid` file. Liveness is observable from any session.
- `check_state.py` — inspects the cache dir and prints per-label PID
  alive + tick count + last-tick age.
- `findings.md` — raw observation notebook with the full per-phase
  tables and conclusions.

Phase summaries:

| Phase | Probe | Outcome |
|---|---|---|
| A | Concurrency floor (5 parallel Monitors) | All 5 fired, no error. Cap ≥5. |
| B | `/clear` survival of producer + Monitor | Both survive. Pre-clear Monitor keeps delivering. |
| C | `/compact` survival of producer + Monitor | Both survive, cleaner than /clear. |
| D | SessionStart-hook arming for cold-start | Deferred. Not a blocker; B+C cover the continuation case the dashboard needs. |

Full per-phase tables in `experiments/monitor_lifecycle/findings.md`.

## Caveats

- Single host, single Claude Code build (May 2026). The Monitor surface
  is officially undocumented; behavior could change without notice.
  Re-run the experiment before designing on it again if a major Claude
  Code version ships.
- Tests ran with `timeout_ms` ≥ 300000 (default) and ≤ 1800000 (30 min).
  Behavior at the 1-hour `timeout_ms` ceiling was not exercised.
- The producer interval was 5s. Sub-second tick rates were not tested
  for delivery loss or batching pressure.

## Recommendation

`#70` is **GREEN**. Proceed with long-running-Monitor delivery for the
operator dashboard. Suggested shape:

- `clu watch --all --operator` filter mode emitting only cross-plan
  events worth interrupting for.
- Monitor armed `persistent: true` so it runs until session end.
- SessionStart-hook arming as a small follow-up for cold-start; not on
  the critical path.
