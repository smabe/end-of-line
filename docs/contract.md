# State Schema + Worker Contract

## State file location

`<project_root>/<plan_dir>/.orchestrator/<plan_slug>.state.json`

Sibling lock file: `<plan_slug>.state.json.lock` (managed automatically).

## Host-level registry

`$XDG_CONFIG_HOME/clu/registry.json` (default `~/.config/clu/registry.json`) indexes every `(project_root, plan_slug)` pair clu knows about on this host. `clu init` auto-registers; `clu register / unregister / list` manage entries explicitly. Multi-plan features (inbound reply routing, fleet view) walk the registry to find state files. Reads + writes go through `state.locked` + `state.save_atomic`.

## State schema (v1)

```jsonc
{
  "schema_version": 1,
  "plan_slug": "watch-start-workout",
  "plan_dir": "plans",
  "status": "running | paused | halted | halted_for_replan | done",
  "created_at": "ISO8601",

  "current_claim": {
    "phase_id": "a-foundation",
    "claimed_by": "session-abcd1234",
    "lease_expires": "ISO8601",
    "started_at": "ISO8601",
    "last_heartbeat_at": "ISO8601",
    "attempts": 1
  },

  "blockers": [
    {
      "id": "q-1",
      "phase_id": "a-foundation",
      "type": "blocked_input | blocked_replan",
      "question": "Snapshot includes startDate, or only kind?",
      "options": ["startDate+kind", "kind only", "full HKWorkout summary"],
      "context": "Plan says minimal; tests assert kind only; RecoveryService needs startDate.",
      "asked_at": "ISO8601",
      "answer": null,
      "answered_at": null,
      "consumed": false
    }
  ],

  "spawned_tasks": [
    {
      "id": "task-1",
      "source": "simplify",
      "spawned_by_phase": "b-extract",
      "title": "Dedupe AM/PM helpers at DataQueryViewModel:180",
      "description": "...",
      "depends_on_phases": ["b-extract"],
      "status": "pending | done",
      "spawned_at": "ISO8601"
    }
  ],

  "config": {
    "lease_ttl_minutes": 30,
    "blocked_question_sla_hours": 24,
    "max_attempts_per_phase": 3,
    "max_spawns_per_phase": 10,
    "stalled_heartbeat_minutes": 10
  },

  "events": [
    {"ts": "ISO8601", "type": "phase_started",   "phase": "a-foundation", "claimed_by": "..."},
    {"ts": "ISO8601", "type": "phase_completed", "phase": "a-foundation", "commits": ["abc123"]},
    {"ts": "ISO8601", "type": "phase_blocked",   "phase": "...", "blocker_id": "q-1"},
    {"ts": "ISO8601", "type": "blocker_answered","blocker_id": "q-1", "answer": "..."},
    {"ts": "ISO8601", "type": "lease_expired",   "phase": "..."},
    {"ts": "ISO8601", "type": "task_spawned",    "task": "task-1", "source": "simplify"},
    {"ts": "ISO8601", "type": "plan_completed"}
  ]
}
```

## Invariants

- `events` is append-only. Never edit or remove past events.
- `current_claim` is null OR has a non-expired lease OR is in the same tick as a `lease_expired` event being written.
- A phase is "done" iff there is a `phase_completed` event with its `phase` id. Status is derived, not stored.
- Atomic writes only: tmp + fsync + rename, under a sibling lock file.
- Schema version mismatch halts the supervisor. No silent migrations.

## Plan markdown contract

End of Line reads the master plan at `<project>/<plan_dir>/<plan_slug>.md` to learn phase identity and order.

### Multi-session plan: `## Sessions index` table

The master plan declares phases via a markdown table directly under `## Sessions index`:

```markdown
## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A — Foundation | `plan-slug-a-foundation.md` | Phase 0 + Phase 1 | 2-3 hr |
| B — Extract | `plan-slug-b-extract.md` | Phase 1.5 | 2-3 hr |
```

Each row = one phase. The phase **id** is derived from the plan-file stem with the master plan's stem stripped: `plan-slug-a-foundation.md` → `a-foundation`.

### Single-phase plan

A plan with no `## Sessions index` table is treated as a single phase. (Not yet wired in v0.1 — parser returns `[]` and supervisor reports `error`. Add an explicit synthesis step here when needed.)

## Worker contract

A worker is a fresh process that runs ONE phase. It must:

1. **Read minimally.** The phase plan file at `<phase_plan_file>`. Any prior phases' commit SHAs from the most recent `phase_completed` events for those phases. The blockers for *this* phase that have been answered (treat them as facts).
2. **Execute the phase plan.** Follow project conventions (TDD, `/review`, `/commit`).
   While running, ping the supervisor every ~2 minutes so it knows the worker
   is still alive (default stalled threshold: 10 min):
   ```bash
   clu heartbeat --project P --plan S --phase X --token <token>
   ```
   Without heartbeats the supervisor can't tell "running" from "dead" until
   the 30-min lease expires.
3. **On success**, before exit:
   ```bash
   clu complete --project P --plan S --phase X --commit <sha> [...]
   ```
4. **On a /simplify finding the worker chooses NOT to fix in this phase**, before completing:
   ```bash
   clu spawn --project P --plan S --source simplify --phase X --title "..." --description "..."
   ```
   Never file as a GH issue. Spawned tasks are first-class members of the plan.
5. **On blocked ambiguity**:
   ```bash
   clu block --project P --plan S --phase X \
     --question "..." --option A --option B --context "..." \
     [--type blocked_replan]
   ```
   This releases the claim and writes the blocker.
6. **On unrecoverable failure**: just exit. The lease expires and the supervisor retries (up to `max_attempts_per_phase`).

## Cron snippet

```cron
# Every 5 min, advance any in-progress plans
*/5 * * * * /usr/local/bin/clu tick --project /Users/me/projects/HealthData --plan watch-start-workout >> /tmp/clu-watch.log 2>&1
```

## What End of Line is NOT

- Not a /plan replacement. It calls /plan; it doesn't reinvent it.
- Not a code reviewer. Workers run `/review` and `/simplify` themselves per project rules.
- Not a parallel scheduler. v0.1 dispatches sequentially (`max_concurrent_phases: 1`). Fan-out across plans is fine (run multiple cron lines).
- Not a CI replacement. Test runs happen in the worker session, not the supervisor.
