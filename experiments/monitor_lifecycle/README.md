# Monitor lifecycle experiment (#69)

One-off empirical test of the Claude Code `Monitor` tool. Answers four
undocumented questions:

1. **Concurrency cap** — how many Monitors can a session hold at once?
2. **`/clear` survival** — does an armed Monitor (and its producer process)
   survive `/clear`?
3. **`/compact` survival** — same for `/compact`.
4. **SessionStart-hook arming** — can a SessionStart hook reliably cause the
   session to arm a Monitor at conversation start?

Findings land in `docs/research/monitor-lifecycle.md`; this directory is the
scratch + protocol for running the experiment.

## Files

- `producer.py` — tick generator. Each tick writes to stdout (the Monitor
  stream) AND appends to `~/.cache/clu-monitor-lifecycle/{label}.log` so
  liveness is observable from any session.
- `check_state.py` — reports producer PID liveness + latest tick age. Run
  this from a fresh session after `/clear` / `/compact` to see whether the
  producers survived.

## Protocol

All commands assume cwd `~/projects/end-of-line`.

### Phase A — concurrency floor

In one Claude session:

1. Start 5 producers as background bash tasks, one per label A..E.
2. Arm `Monitor` on each producer's stdout (5 calls).
3. Observe: do all 5 fire notifications? Does the Nth call error?
4. Record the cap (or "≥5, no observed limit") in findings.

### Phase B — `/clear` survival

1. Start producers A, B. Arm Monitor on each.
2. Confirm both Monitors fire at least once (note tick numbers).
3. Operator types `/clear`.
4. In the cleared session, run `python3 experiments/monitor_lifecycle/check_state.py`.
   - If producers are ALIVE: Claude did NOT reap child processes on /clear.
     The Monitor itself is gone (no listener in the new context) but the
     producer is orphaned.
   - If producers are DEAD: Claude reaped them on /clear.
5. Try re-arming Monitor on the same producer stdout. Does it see backlog
   or start from the latest line?

### Phase C — `/compact` survival

Same as Phase B with `/compact` instead of `/clear`.

### Phase D — SessionStart-hook arming

1. Add a temporary entry to `~/.claude/settings.local.json` under
   `hooks.SessionStart` that outputs the instruction to arm Monitor on a
   producer stream as additional context.
2. Start a fresh session (new conversation).
3. Observe: does the new session call Monitor? Does the Monitor fire?
4. Remove the temp hook entry.

## Cleanup

```bash
rm -rf ~/.cache/clu-monitor-lifecycle/
pkill -f experiments/monitor_lifecycle/producer.py
```

## Why this experiment exists

The `worker-watchdog` plan (#67 #68) deferred long-running-Monitor delivery
because Monitor lifecycle is undocumented. Re-checked 2026-05-23 via
`claude-code-guide`: still undocumented for /clear, /compact, concurrency,
and hook arming. This experiment derisks #70 — green outcome means
operator-dashboard Monitor delivery is viable; red outcome means stick
with inbox-hook delivery and close #70.
