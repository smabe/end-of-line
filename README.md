# End of Line

Plan orchestrator for the `/plan` skill. Cron-driven supervisor, file-state, cold-context phase workers.

> "End of line."  
> — Master Control Program

## What it does

Your `/plan` skill encodes a multi-phase plan as a master `.md` file plus per-phase plan files. Today, each phase requires manual intervention — you `/clear` between phases and prompt the next one. End of Line automates the gap: a tiny supervisor wakes up on a cron, reads a durable state file, and dispatches the next phase to a fresh Claude session. Workers run cold (no crusty context), report back via the CLI (`clu complete`, `clu block`), and the supervisor advances the plan.

When a worker hits genuine ambiguity, it writes a blocker question to state and exits clean. You answer async (`clu answer`); the supervisor unblocks on the next tick. Blockers older than the SLA escalate.

`/simplify` findings that get deferred during a phase are appended as `spawned_tasks` and processed before the plan can finish — tech debt never escapes the plan.

## Design

- **State lives outside sessions.** `<project>/plans/.orchestrator/<slug>.state.json` is the single source of orchestration truth. Workers are stateless.
- **Atomic writes.** Every mutation is `tmp + fsync + rename` under a `flock`-serialized lock.
- **Append-only event log.** Phase completion, claims, lease expirations, blockers — all derivable from `events[]`. Corruption-resistant.
- **/plan is the contract.** Phase declarations come from the master plan's `## Sessions index` table. No reinvention.
- **System cron is the heartbeat.** No long-running orchestrator process. Each tick is ~50ms of Python; the supervisor itself burns zero LLM tokens.

## Install

```bash
cd ~/projects/end-of-line
pip install -e .
# Or: pipx install .
```

## Bootstrap a project

In your project repo, drop a `.orchestrator.json`:

```json
{
  "plan_dir": "plans",
  "dispatch": {
    "kind": "shell",
    "command": "claude --print '/plan {plan_slug}'"
  },
  "notify": {"push": true}
}
```

Then:

```bash
# Initialize state for a plan
clu init --project /path/to/project --plan my-plan

# Run the cron loop manually
clu tick --project /path/to/project --plan my-plan --dispatch

# Inspect state
clu status --project /path/to/project --plan my-plan
```

Add cron:

```cron
*/5 * * * * /usr/local/bin/clu tick --project /path/to/project --plan my-plan --dispatch >> /tmp/clu.log 2>&1
```

## Worker contract

A phase worker is a fresh Claude session (or any process) that:

1. Reads its phase plan file from `<project>/<plan_dir>/<phase_plan_file>.md`
2. Runs `/plan` (Mode 2 / resume) or equivalent
3. On success, calls:  
   `clu complete --project P --plan S --phase X --commit <sha> [--commit <sha>...]`
4. On a /simplify finding it can't fix this phase:  
   `clu spawn --project P --plan S --source simplify --phase X --title "…"`
5. On blocked ambiguity:  
   `clu block --project P --plan S --phase X --question "…" --option A --option B --context "…"`
6. On unrecoverable failure: just exit — lease expires, supervisor retries.

## Commands

| Command | Side | Purpose |
|---|---|---|
| `clu init` | bootstrap | Create state.json for a new plan |
| `clu tick` | supervisor (cron) | One decision step |
| `clu status` | human | Pretty-print current state |
| `clu answer <id> <text\|index>` | human | Unblock a worker |
| `clu complete --phase X` | worker | Mark phase done + record commits |
| `clu block --phase X` | worker | Record blocker + release claim |
| `clu spawn` | worker | Append a dynamic follow-up task |

## State schema

See `docs/contract.md`. Minimal sketch:

```json
{
  "schema_version": 1,
  "plan_slug": "watch-start-workout",
  "status": "running",
  "current_claim": {"phase_id": "...", "claimed_by": "...", "lease_expires": "..."},
  "blockers": [{"id": "q-1", "phase_id": "...", "question": "...", "answer": null}],
  "spawned_tasks": [{"id": "task-1", "source": "simplify", "title": "...", "status": "pending"}],
  "events": [{"ts": "...", "type": "phase_completed", "phase": "..."}],
  "config": {"lease_ttl_minutes": 30, "blocked_question_sla_hours": 24}
}
```

## Status

v0.1 — working MVP. Tested against the `/plan` skill's Sessions-index convention. Notification adapters are stubs (print to stderr); plug real channels (osascript, Pushover, iMessage) in `notify.py`.
