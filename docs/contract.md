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
    {"ts": "ISO8601", "type": "plan_completed"},
    {"ts": "ISO8601", "type": "queue_popped",   "slug": "...", "added_at": "...", "added_by": "operator", "position": 1}
  ]
}
```

`queue_popped` is the provenance event written as the **first** event in any state.json that the supervisor's per-project queue-advancement step creates. The worker that gets dispatched on the next tick reads it as part of its initial state. See "Queue schema" below for the matching queue.json fields it carries forward.

## Invariants

- `events` is append-only. Never edit or remove past events.
- `current_claim` is null OR has a non-expired lease OR is in the same tick as a `lease_expired` event being written.
- A phase is "done" iff there is a `phase_completed` event with its `phase` id. Status is derived, not stored.
- Atomic writes only: tmp + fsync + rename, under a sibling lock file.
- Schema version mismatch halts the supervisor. No silent migrations.

## Queue schema

Per-project queue file at `<project_root>/<plan_dir>/.orchestrator/queue.json`. One queue per project (not per plan). Schema, after `queue._empty()`:

```jsonc
{
  "schema_version": 1,
  "queue": [
    {
      "slug": "next-plan-slug",
      "added_at": "ISO8601",
      "added_by": "operator",
      "position_at_add": "tail | front"
    }
  ],
  "history": [
    {
      "slug": "previous-plan-slug",
      "added_at": "ISO8601",
      "added_by": "operator",
      "position_at_add": "tail | front",
      "ended_at": "ISO8601",
      "outcome": "abandoned | removed | absorbed"
    }
  ]
}
```

Field semantics:

- `queue` is the pending list in dispatch order; the head pops first.
- `history` is forensic. Three terminal outcomes are recorded:
  - `removed` — operator ran `clu queue remove <slug>`.
  - `abandoned` — supervisor popped a head whose plan file was missing; written under the queue lock alongside a `KIND_QUEUE_SKIPPED` ping.
  - `absorbed` — supervisor popped a head whose state.json already existed in a non-freeze status (`done`/`running`); the queue entry is retired without re-`init`-ing.
- A successful normal pop produces no history entry — the popped slug becomes the active plan instead.
- Reads/writes go through `queue.mutate` (which uses `state.locked_json`); the same lock+atomic-write primitive that protects state.json.

Two sibling files share the queue's directory:

- `queue.json.lock` — the flock under `state.locked` (managed automatically).
- `queue.json.corrupt-<UTCstamp>` — bytes-for-bytes backup written before any auto-repair attempt. Kept on disk after the attempt; the operator can diff old vs new to see what the worker rewrote.
- `queue.json.repair-attempts` — per-diagnosis-hash throttle counter. `{"attempts": N, "last_at": "...", "diagnosis_hash": "..."}`. Reset on a successful repair; resets to 0 on a hash mismatch.

## Auto-repair contract

When `queue.load` raises (catastrophic JSON / schema corruption) inside the supervisor's post-loop pass, clu may dispatch a headless Claude repair worker. This is opt-in via `ProjectConfig.dispatch.repair_command`; unset → fall back to plain `KIND_QUEUE_CORRUPT` notification and increment the throttle.

**clu's responsibilities** (the safety boundary — these run regardless of worker behavior):

1. Read the original bytes, write `queue.json.corrupt-<UTCstamp>` backup.
2. Check the per-diagnosis-hash throttle. ≥ 3 attempts → notify + return (no dispatch).
3. Spawn the repair worker via `dispatch.dispatch_repair_worker` synchronously (default 60s timeout).
4. After the worker exits (rc ignored), run `queue.validate_repair(backup_bytes, queue_path)`.
   The validator is intentionally regex-based over the backup bytes so a worker that truncated the JSON before writing can't slip slugs past us.
5. Validation failed → revert bytes from backup, fire `KIND_QUEUE_REPAIR_FAILED`, increment throttle.
6. Validation passed → fire `KIND_QUEUE_REPAIRED`, reset throttle.

**Worker's responsibilities** (the prompt is advisory; clu's validation is authoritative):

- Read both the corrupt file and the backup, diagnose, atomic-write the repaired file in place (`tmp + fsync + os.replace`).
- Preserve every pending slug from the backup. Empty `queue` array only if the original was provably empty.
- Treat `history` as append-only. Add if needed, never remove.
- If safe repair is impossible without violating the above, exit `ExitCode.REPAIR_DECLINED = 9` — this is the legibility-only signal that the worker chose not to touch the file. clu's validation rejects bad output regardless of rc.

The hard rules clu enforces (in `queue.validate_repair`):

1. The repaired file must `load()` cleanly (parseable JSON, correct schema version).
2. Every slug `queue.best_effort_extract_slugs` found in the backup must appear in the repaired `queue` OR `history`. Pending-only slugs (backup-all minus backup-history, computed via `queue.best_effort_extract_history_slugs`) must specifically be in the repaired `queue`.
3. Every history slug from the backup must still be in the repaired history.

A validator-rejected repair surfaces `would drop slugs: [...]` or `history entries removed: [...]` in the `KIND_QUEUE_REPAIR_FAILED` body.

## Notification kinds

The outbound iMessage adapter (`notify.py`) classifies every send by kind. Quiet hours (default 22:00–08:00 local) gate every kind not in `notify.QUIET_HOURS_BYPASS_KINDS`.

| Kind | Trigger | Quiet hours |
|---|---|---|
| `KIND_BLOCKER` | Worker called `clu block` | Gated |
| `KIND_STALLED` | Live claim past `stalled_heartbeat_minutes` | Gated |
| `KIND_COMPLETED` | Plan finished cleanly (`plan_completed`) | Gated |
| `KIND_HALTED` | Plan halted (max-attempts / replan / systemic failure) | **Bypass** |
| `KIND_QUEUE_SKIPPED` | Queue head popped + abandoned (plan file missing) | Gated |
| `KIND_QUEUE_REPAIRED` | Auto-repair succeeded + validation passed | Gated |
| `KIND_QUEUE_REPAIR_FAILED` | Auto-repair failed validation (file reverted) | **Bypass** |
| `KIND_QUEUE_CORRUPT` | Queue corrupt + auto-repair disabled OR throttle exhausted | **Bypass** |

Bypass set: `{KIND_HALTED, KIND_QUEUE_REPAIR_FAILED, KIND_QUEUE_CORRUPT}`. These are unrecoverable-without-operator states; deferring them past quiet hours would let the chain sit silently broken until morning.

## Exit codes

`end_of_line.cli.ExitCode` — IntEnum, returned by every CLI command via `_die`. Cron and the inbound poller key off these codes.

| Code | Name | Meaning |
|---|---|---|
| 0 | `OK` | Success |
| 1 | `GENERIC` | Catch-all error |
| 2 | `INVALID_SLUG` | Slug failed `state.validate_slug` |
| 3 | `BAD_SHA` | `--commit` SHA not in the project's git repo |
| 4 | `CLAIM_MISMATCH` | Worker token didn't match the live claim |
| 5 | `SPAWN_CAP` | `--max-spawns-per-phase` exceeded |
| 6 | `UNKNOWN_TASK` | Named task / blocker / queue entry not found |
| 7 | `STATUS_TRANSITION` | Refused state change (pause → resume on `done`, etc.) |
| 9 | `REPAIR_DECLINED` | Repair worker refusing to touch the file (legibility-only — clu still validates) |

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
