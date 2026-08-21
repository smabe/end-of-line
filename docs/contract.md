# State Schema + Worker Contract

## Where state lives

clu keeps **two SQLite databases, never one**:

| Database | Path | Holds |
|---|---|---|
| per-project | `<project_root>/<plan_dir>/.orchestrator/clu.db` | plan state, claims, blockers, spawned tasks, events, the queue, the quota pause |
| host | `$XDG_CONFIG_HOME/clu/clu.db` (default `~/.config/clu/clu.db`) | registry, monitor marker, iMessage / Discord sidecars, the inbox, the skill-install receipt |

Both run in WAL mode, so each has two short-lived siblings (`clu.db-wal`, `clu.db-shm`). Both are created 0600 — a claim token is a credential. Splitting them per project means two projects' ticks never contend for one write lock.

**There is no per-plan file.** A path of the form `<orch_dir>/<slug>.state.json` is still passed around inside clu, but it is a **key**, not a file: it names *(the database in `<orch_dir>`, plan `<slug>`)*, and `config.state_path`'s slug validation + traversal guard is what makes an external slug safe to turn into one. Nothing opens it.

To read raw plan state, use **`clu state dump [--project PATH] [--plan SLUG]`** — with `--plan` the output *is* the plan's state document (archived events folded back in); without it, an object keyed by slug.

### Host-level registry

The host database's `registry` table indexes every `(project_root, plan_slug)` pair clu knows about on this host. `clu init` auto-registers; `clu register / unregister / list` manage entries explicitly. Multi-plan features (inbound reply routing, fleet view) walk the registry to find plans.

## Plan state: the tables

Plan state is normalized across six tables in the project database. The full DDL is in `end_of_line/db.py` (`_PROJECT_DDL`); the shape is:

| Table | Key | Notes |
|---|---|---|
| `plans` | `slug` | The head row: `status`, `plan_dir`, `created_at`, `batch_id`, plus a reader-facing `version` counter every writer bumps. `config`, `phases`, `worktree` and `extra` are JSON-valued columns; `extra` is the catch-all that lets a writer add a field without a migration. |
| `claims` | `plan_slug` | At most one row per plan — "no row" *is* "no claim". Scalars are columns (`phase_id`, `claimed_by`, `lease_expires`, `pid`, `pgid`, `session_id`, …); `flags`, `attestations` and `cpu_samples` are JSON. |
| `blockers` | `(plan_slug, blocker_id)` | `options` and `notify_metadata` are JSON; `consumed` is 0/1. |
| `spawned_tasks` | `(plan_slug, task_id)` | `payload` is JSON. |
| `events` | `id` (autoincrement) | The hot append-only log. `id` is monotonic and is the cursor every streaming reader uses. Indexed by `(plan_slug, id)` and `(plan_slug, type)`. |
| `events_archive` | — | Terminal plans' events, moved here on explicit archival so the hot table stays lean. Ids are carried over, never re-minted. `clu state dump` reads both; `watch` / `top` / the locator read the hot table only. |

`queue`, `queue_history` and `quota` share the same database — see "Queue schema" and "Quota pause" below.

## State document (what readers are handed)

`plan_store.snapshot` (and therefore `clu state dump`) assembles those rows into the dict every consumer has always projected from, inside ONE read transaction — so a writer committing halfway through cannot produce a claim from before it and events from after it. That document is the reader contract:

```jsonc
{
  // A constant projected onto every snapshot, not a stored field. The
  // durable version is the database's `PRAGMA user_version`.
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
    "attempts": 1,
    // pid: stamped by dispatch._stamp_pid after Popen. pgid == pid because
    // the worker is spawned start_new_session=True (it leads its own process
    // group); reapers killpg(pgid) to take the worker + heartbeat loop
    // together. Claims written before #75 have pid but no pgid — reapers
    // fall back.
    "pid": 12345,
    "pgid": 12345,
    // Optional. Stamped by dispatch._stamp_pid ONLY when dispatch.command
    // includes a real {session_id} placeholder (e.g. `claude --session-id
    // {session_id} ...`): clu generates the uuid, hands it to Claude Code,
    // and records it so `clu top` reads the worker's transcript by exact
    // filename. Absent when the command omits the placeholder (Claude Code
    // then picks its own id and `clu top` falls back to cwd-matching).
    "session_id": "bb35bdb6-70d5-46f7-8b3c-2c8a686566ea",
    // Optional, lazy-init. Absent until the worker stamps via `clu verify`
    // or `clu attest`. Each entry: {"at": ISO8601_Z, "commit_sha": str}.
    // Stamp is "stale" if commit_sha != current HEAD.
    "attestations": {
      "verify":   {"at": "ISO8601", "commit_sha": "<40-char SHA>"},
      "simplify": {"at": "ISO8601", "commit_sha": "<40-char SHA>"}
    }
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
    "lease_ttl_minutes": 60,
    "blocked_question_sla_hours": 24,
    "max_attempts_per_phase": 3,
    "max_spawns_per_phase": 10
    // `stalled_heartbeat_minutes` is optional. When absent, threshold
    // derives as min(25, max(15, lease_ttl_for_phase // 2)). Set an
    // int to pin an explicit override (bypasses both bounds).
  },

  // One record per phase in the master plan's Sessions index, written at
  // init time. `lease_ttl_minutes` is present only when the Effort column
  // parsed into minutes; it is that plan's per-phase lease override.
  // Empty list when the master file is absent or has no Sessions index.
  "phases": [
    {"id": "a-foundation", "lease_ttl_minutes": 90}
  ],

  // Optional, additive (no schema_version bump). Present iff the plan was
  // init'd with `--worktree`. `base_ref` is the resolved commit SHA at
  // init time, not the symbolic ref the operator passed.
  "worktree": {
    "path": "/absolute/path/to/worktree-dir",
    "branch": "clu/<slug>",
    "base_ref": "<40-char SHA>"
  },

  // Optional. Set by the tick-time worktree conflict scan. List with
  // set semantics, stored sorted. Cleared automatically when the other
  // plan transitions out of "active" (claim ends or status leaves
  // RUNNING). Future code MUST NOT `.add()` to it — read into a set,
  // rewrite the list.
  "in_conflict_with": ["<other-slug>", "..."],

  // Optional, additive. Set at queue-pop time when the queue entry
  // carries a batch_id (from `clu queue add --batch <name>`). Null for
  // plans not tagged to a batch. Used by dry_merge_gate_rule to group
  // sibling plans for integration testing.
  "batch_id": "my-batch | null",

  // Optional, additive. Stamped by dry_merge_gate_rule after running
  // attempt_merge. Null until the gate fires for this plan.
  "gate_result": {
    "sha_key": "<sorted HEAD SHAs joined by |>",
    "ts": "ISO8601",
    "batch_id": "my-batch",
    "outcome": "clean | textual_conflict | suite_failed",
    // Present only on dirty outcomes:
    "follow_up_plan": "merge-resolve-<batch>-<YYYYMMDDhhmm>.md"
  },

  // Every event also carries an `id` — the `events` table's autoincrement
  // rowid, monotonic per project and the cursor streaming readers resume
  // from. Omitted below for readability.
  "events": [
    {"id": 1, "ts": "ISO8601", "type": "phase_started",   "phase": "a-foundation", "claimed_by": "..."},
    {"ts": "ISO8601", "type": "phase_completed", "phase": "a-foundation", "commits": ["abc123"]},
    {"ts": "ISO8601", "type": "phase_blocked",   "phase": "...", "blocker_id": "q-1"},
    {"ts": "ISO8601", "type": "blocker_answered","blocker_id": "q-1", "answer": "..."},
    {"ts": "ISO8601", "type": "lease_expired",   "phase": "..."},
    {"ts": "ISO8601", "type": "phase_worker_dead", "phase": "...", "pid": 12345},
    {"ts": "ISO8601", "type": "phase_worker_dead_reported", "phase": "...", "pid": 12345, "log_path": "plans/.orchestrator/logs/<phase>.<session>.log", "reporter": "heartbeat_daemon"},
    {"ts": "ISO8601", "type": "task_spawned",    "task": "task-1", "source": "simplify"},
    {"ts": "ISO8601", "type": "plan_completed"},
    {"ts": "ISO8601", "type": "queue_popped",   "slug": "...", "added_at": "...", "added_by": "operator | worker", "position": 1},
    {"ts": "ISO8601", "type": "queue_appended", "slug": "...", "source_plan": "...", "source_phase": "...", "source_token_fp": "...", "reason": "...", "position": 1},
    {"ts": "ISO8601", "type": "queue_rejected", "slug": "...", "source_plan": "...", "source_phase": "...", "reason": "cap | missing_plan_file"},
    {"ts": "ISO8601", "type": "worktree_missing", "phase": "...", "token": "...", "worktree_path": "..."},
    {"ts": "ISO8601", "type": "worktree_conflict_warning", "other_slug": "..."},
    {"ts": "ISO8601", "type": "lease_extended", "phase": "...", "extended_by_minutes": 15, "new_expires": "...", "operator": true},
    {"ts": "ISO8601", "type": "attempts_reset",         "phase": "...", "operator": true},
    {"ts": "ISO8601", "type": "verify_stamped",         "phase": "...", "commit_sha": "..."},
    {"ts": "ISO8601", "type": "simplify_stamped",       "phase": "...", "commit_sha": "..."},
    {"ts": "ISO8601", "type": "operator_skip_verify",   "phase": "..."},
    {"ts": "ISO8601", "type": "operator_skip_simplify", "phase": "..."},
    {"ts": "ISO8601", "type": "quota_death",   "phase": "...", "token": "...", "signature": "session_limit", "line": "You've hit your session limit · resets 1:50am (America/New_York)"},
    {"ts": "ISO8601", "type": "quota_paused",  "paused_until": "ISO8601 | null", "signature": "session_limit"},
    {"ts": "ISO8601", "type": "quota_resumed"}
  ]
}
```

`queue_popped` is the provenance event written as the **first** event of any plan the supervisor's per-project queue-advancement step creates. The worker that gets dispatched on the next tick reads it as part of its initial state. See "Queue schema" below for the matching queue-entry fields it carries forward.

**Event types and worker callback shapes are unchanged by the move to SQLite.** Every `EVENT_*` type, every field on every event, and every `clu complete / block / spawn / task-done / heartbeat / verify / attest` invocation mean exactly what they meant when this was a JSON file. What changed is where the bytes land.

## Invariants

- `events` is append-only. Never edit or remove past events.
- `current_claim` is null OR has a non-expired lease OR is in the same tick as a `lease_expired` event being written.
- A phase is "done" iff there is a `phase_completed` event with its `phase` id. Status is derived, not stored.
- Every write is one `BEGIN IMMEDIATE` transaction that names the rows it changes. There is no read-whole-document-and-write-it-back path; a heartbeat touches the claim row and nothing else.
- A decision that spans a subprocess gap (the tick) cannot be one transaction, so it snapshots, decides holding nothing, and applies under **re-asserted preconditions** — a compare-and-set that raises `plan_store.TickConflict` if anything it decided on moved. See architecture.md "The tick".
- A database whose `PRAGMA user_version` is NEWER than this clu understands is **skipped, never read optimistically and never downgraded** (`db.SchemaTooNew`, surfaced to plan callers as `state.SchemaVersionMismatch`). A fleet walk skips that project and keeps walking. No silent migrations.
- `worktree` and `in_conflict_with` are **additive optional** — readers use `state.get_worktree(data)` and `data.get("in_conflict_with") or []`. No `schema_version` bump on introduction.

### Worktree event semantics

- `worktree_missing` — emitted by `dispatch_for_tick` when `state.worktree` exists but `path` is either gone from disk or no longer a valid git working dir (operator deleted the dir, or ran `git worktree prune`). The plan is paused (status → PAUSED), the just-made claim is released without burning a phase attempt, and a KIND_HALTED iMessage names the path. Recovery: restore the dir or hand-edit `state.worktree`, then `clu resume`.
- `worktree_conflict_warning` — emitted by `clu tick-all`'s post-loop conflict scan when two active plans in the same project both lack a worktree record. Only the lexicographically-smaller slug in the pair emits the event (`other_slug` names the peer); both plans update their `in_conflict_with` field. Auto-clears when one side transitions out of "active" (claim ends or status leaves RUNNING).

### Cleanup / terminalization semantics

- `plan_abandoned` — emitted by `state.terminalize` when a non-terminal (`running`) plan is torn down: `clu unregister` of a still-running plan, or the registry-independent zombie sweep (`supervisor.sweep_zombie_states`). The status flips to `halted` (no new `abandoned` status — the event carries the provenance) and the worker process group is best-effort reaped. `terminalize` is compare-and-set: a no-op on an already-terminal plan, so a cron tick racing a manual cleanup can't double-fire it. The `reason` field distinguishes `"unregister"` from `"zombie_sweep"`. Additive-optional: no `schema_version` bump.

### Operator claim-control event semantics

- `lease_extended` — emitted by `clu extend-lease` (operator-only; no `--token` required). Fields: `phase` (current phase id), `extended_by_minutes` (the argument passed), `new_expires` (ISO-8601 UTC string of the new expiry), `operator: true`. Semantics: `new_expires = max(now, current_lease_expires) + timedelta(minutes=N)`, so extending an already-expired (stalled) claim anchors from `now`, never backwards.
- `attempts_reset` — emitted alongside `claim_force_released` when `clu release-claim --reset-attempts` is passed. Fields: `phase`, `operator: true`. Resets the attempt floor so the next dispatch starts fresh. `attempts_for_phase()` counts `phase_started` events after the most-recent of EITHER `retry_requested` OR `attempts_reset` — both act as floor markers; most-recent wins.

### Worker-death event semantics

Two distinct events record a dead worker, by two different processes with two
different evidences — collapsing them would make the plan's state lie about who
saw what:

- `phase_worker_dead` — emitted by the **supervisor tick** (`_detect_dead_pid`)
  when the claim's worker PID is gone or PID-recycled to an unrelated process
  (cmdline mismatch) but the lease hasn't expired. The supervisor releases the
  claim and reaps in the same lock window, so its idempotency is structural.
  Fields: `phase`, `pid`.
- `phase_worker_dead_reported` — emitted by the **per-worker heartbeat daemon**
  (`clu notify-worker-dead`) when its cmdline-anchored liveness probe finds the
  worker PID dead, ~120s after death rather than at the next tick. Token-validated
  (the daemon holds the claim token; `append_event` itself does no claim check).
  Fields: `phase`, `pid`, `log_path` (the ATTEMPT log the dispatcher stamped — the
  post-mortem target, not the daemon's `.hb.log` sidecar), `reporter`
  (`"heartbeat_daemon"`). Deduped via the claim's `worker_death_reported` marker:
  the daemon stamps it, and the supervisor's own worker-dead branch consults it to
  suppress a duplicate operator notification while still emitting its own event,
  releasing, and reaping. The reporter also RELEASES the claim in the same lock
  window (death-recovery, #104): it classifies quota from `log_path` FIRST — a
  quota death still records the pause and forgives the attempt — then calls the
  token-validated `release_claim_and_emit`, so the phase is redispatchable by the
  next tick rather than sitting claimed until one re-derives a death already on
  record. It does NOT reap the process group (the daemon's `setsid` puts it
  outside that group; the supervisor's reap stays the backstop). It is
  default-visible in `clu watch` because #104's complaint is precisely that live
  watch streams saw nothing when the worker died.

### Quality-attestation event semantics

- `verify_stamped` — emitted by `clu verify` on rc=0. Fields: `phase`, `commit_sha` (the HEAD SHA captured before the command ran). Stamps `current_claim.attestations.verify`.
- `simplify_stamped` — emitted by `clu attest --simplify`. Fields: `phase`, `commit_sha` (current HEAD at attest time). Stamps `current_claim.attestations.simplify`.
- `operator_skip_verify` — emitted by `clu complete --skip-verify`. Audit event; phase still completes. Fields: `phase`.
- `operator_skip_simplify` — emitted by `clu complete --skip-simplify`. Audit event; phase still completes. Fields: `phase`.
- `attestation_refused` — emitted by `clu complete` when the verify or simplify quality gate refuses. Fires once per refusal call (no dedup; #70 operator-dashboard wants every gate hit visible). Fields: `phase`, `gate` (`"verify"` | `"simplify"`, matching `ATTESTATION_VERIFY` / `ATTESTATION_SIMPLIFY`), `stamped_at` (last stamp SHA or `null`), `head_sha` (HEAD that was refused; worktree HEAD if a worktree is active). Re-read under lock at emit time — if a concurrent stamp made the gate pass between the snapshot read and the lock acquire, no event is emitted.

### Worker-enqueue event semantics

- `queue_appended` — emitted in the **source plan's** `events` array (not a separate project-level log) when a worker successfully appends a slug to the project queue. Fields: `slug` (the enqueued plan), `source_plan` (the worker's plan), `source_phase` (the worker's phase), `source_token_fp` (sha256 fingerprint of the token, first 8 hex chars — raw token never persisted), `reason` (optional free-text from `--reason`), `position` (1-based queue position at append time).
- `queue_rejected` — emitted in the **source plan's** `events` array when a worker-enqueue attempt is refused. Fields: `slug`, `source_plan`, `source_phase`, `reason` — either `"cap"` (per-phase add cap reached) or `"missing_plan_file"` (the target `<plan_dir>/<slug>.md` does not exist).

Both events ride in the **source plan's** event log so the worker's audit trail is co-located with the rest of its phase actions.

### Quota-death event semantics (#94)

A worker killed by the operator's Claude subscription limit prints a recognizable line (`You've hit your session limit · resets 1:50am (America/New_York)`) and exits — indistinguishable, on PID/exit-code alone, from a real crash. `end_of_line.quota` classifies the worker-log tail at all three death sites (supervisor dead-PID probe, supervisor lease-expiry, dispatch fast-fail) and records three events:

- `quota_death` — the classification. Fields: `phase`, `token` (the dead claim's `claimed_by`), `signature` (the matched table key, e.g. `session_limit` | `weekly_limit` | `model_limit` | `usage_credits` | `extra_usage`), `line` (the verbatim matched log line). **This event is a forgiveness marker:** `attempts_for_phase()` subtracts the matching `phase_started` for any phase named by a `quota_death` (or `systemic_failure`), so a quota kill never advances the 3-attempt halt counter. The plan status is **not** touched — quota pause is project-level, not a plan halt.
- `quota_paused` — the project entered the quota pause. Fields: `paused_until` (ISO-8601 UTC of `reset + 120s`, or **`null`** for the stuck bucket — a quota match whose reset time didn't parse), `signature`. **Carries no `phase` key** — consumers iterating `events` must not assume one.
- `quota_resumed` — the supervisor's dispatch gate cleared the pause after the canary survived its window. No fields. Rides the resuming plan's event log.

`quota_death` + `quota_paused` are appended together by `quota.record_quota_death` onto the snapshot the death site is holding, and land when that site persists its events; the pause ROW is written first, in its own transaction, so a pause is never lost to a later failure. `quota_resumed` is appended by the gate during the resume tick (see architecture.md "Quota pause gate"). The pause itself lives in the project database's single `quota` row (schema below), not in any plan's state.

### Stall-detector guard

`phase_stalled` is suppressed when `last_heartbeat_at == started_at` (the canonical `claude --print` case: stdout buffers until exit, so the bundled `/clu-phase` skill never calls `clu heartbeat` between tool calls and the heartbeat timestamp never advances). The lease-expiry path (`lease_expired`) still fires on genuinely-silent workers via `_detect_lease_expired` — the guard only mutes the chatty per-threshold ping, not the final timeout.

## Queue schema

Two tables in the project database, `queue` (pending) and `queue_history` (departed). One queue per project, not per plan.

`queue.id` is the ORDER, not just an identity: the head is the smallest id, a tail-add takes the next autoincrement, and `clu queue add --front` inserts *below* the current minimum (explicit, possibly negative rowids), so a front-insert renumbers nothing already queued.

Columns of their own: `slug`, `added_at`, `added_by`, `batch_id`. Everything else on an entry rides in the JSON `extra` column — `position_at_add`, and the worker-enqueue provenance (`source_plan`, `source_phase`, `source_token_fp`, `reason`) — so an entry dict round-trips whole. `queue_history` carries the same columns plus `ended_at` and `outcome`.

One entry, as `queue.pending()` hands it back:

```jsonc
{
  "slug": "next-plan-slug",
  "added_at": "ISO8601",
  "added_by": "operator | worker",
  "position_at_add": "tail | front",
  // Worker-enqueue fields — nullable; operator-side entries leave all four as null.
  "source_plan": "source-plan-slug | null",
  "source_phase": "source-phase-id | null",
  "source_token_fp": "sha256(token)[:8] | null",
  "reason": "free-text string | null",
  // Set by `clu queue add --batch <name>` (operator-only). Null when not
  // part of a batch. Propagated to plan state at queue-pop time so the
  // dry-merge gate can group sibling plans without re-reading queue history.
  "batch_id": "my-batch | null"
}
```

Field semantics:

- `queue` is the pending list in dispatch order; the head pops first.
- `queue_history` is forensic. Every entry that leaves `queue` lands there — including successful pops — with one of four outcomes:
  - `popped` — the normal pop. Written in the same transaction that creates the plan's rows.
  - `absorbed` — supervisor popped a head whose plan already existed in a non-freeze status (`done`/`running`); the queue entry is retired without re-`init`-ing.
  - `abandoned` — supervisor popped a head whose plan file was missing; written in the same transaction as the pop, alongside a `KIND_QUEUE_SKIPPED` ping.
  - `removed` — operator ran `clu queue remove <slug>`.
- `clu queue list`'s in-flight footer derives from the registry, not the history table.
- **The pop is two transactions, deliberately.** Creating the plan and retiring the queue head is one write transaction; dispatching the worker is a subprocess and cannot be inside it. See architecture.md "Queue advancement".

## Quota pause (#94)

One row in the project database's `quota` table, single-row by construction (`CHECK (id = 1)`), shared by every plan in the project. Written by `quota.record_quota_pause`, read by the supervisor dispatch gate (`quota.gate_decision`). `quota.read_pause` hands it back as:

```jsonc
{
  // reset + 120s buffer (ISO-8601 UTC), or null for the STUCK bucket
  // (a quota match whose reset time didn't parse — no auto-resume).
  "paused_until": "2026-06-12T05:52:00Z | null",
  "signature": "session_limit",
  "line": "You've hit your session limit · resets 1:50am (America/New_York)",
  // Canary-resume bookkeeping. Stamped by the FIRST plan to tick past
  // paused_until; that plan dispatches as the survival probe while the
  // rest of the fleet idles until canary_deadline. Both null at rest.
  "canary_plan": "plan-slug | null",
  "canary_deadline": "2026-06-12T05:55:00Z | null",
  "created_at": "2026-06-12T03:00:00Z"
}
```

**The single invariant: row absent == not paused.** That is why the table is single-row by construction — a second row would make "absent" ambiguous. A resume *deletes* the row (never writes a "cleared" sentinel), and the operator escape hatch for a stuck pause is **`clu quota clear [--project PATH]`**. A field-malformed row degrades to "dispatch" (a malformed pause must never freeze the fleet), with a stderr note. See operations.md "Recovering from a quota pause" for the operator runbook and architecture.md "Quota pause gate" for the gate state machine.

## Auto-repair contract — deleted

There is none, and the absence is deliberate. The queue's auto-repair subsystem (corrupt-file backup, throttle counter, headless repair worker, `queue.validate_repair`) existed because a JSON file interrupted mid-write is recoverable text, and rescuing slugs out of one with a regex was worth doing. A WAL database never hands a reader a half-written store: the pop either committed or it did not. What is left — a genuinely corrupt database file — is not something a headless worker edits back to health, so it surfaces to the operator instead.

Deleted with it: the `KIND_QUEUE_CORRUPT` / `KIND_QUEUE_REPAIRED` / `KIND_QUEUE_REPAIR_FAILED` notify kinds, `ExitCode.REPAIR_DECLINED`, and `dispatch.dispatch_repair_worker`. A `dispatch.repair_command` left in an `.orchestrator.json` is **ignored**, with a one-line stderr deprecation note — see "`.orchestrator.json` top-level schema" below.

## Background-monitoring marker

Rows in the host database's `monitor` table — a key/value table, because the marker is a handful of scalars whose names change more often than their shape. Account-wide, not per-project: one `UserPromptSubmit` hook covers every plan on the host. **No rows = monitoring not set up**; `clu init` and `clu queue add` emit a one-line tip recommending `/clu-monitor` when the marker is absent and stdout is a TTY.

`monitor.load_marker()` returns:

```jsonc
{
  "schema_version": 2,
  "hook_installed_at": "2026-05-12T19:00:00Z",
  "hook_path": "/abs/path/to/end_of_line/hooks/clu_inbox_surface.py",
  "settings_json_path": "/Users/you/.claude/settings.json"
}
```

| Field | Meaning |
|---|---|
| `hook_installed_at` | ISO UTC timestamp of the marker write |
| `hook_path` | Absolute path to the bundled hook script resolved at install time |
| `settings_json_path` | Absolute path to the `settings.json` the installer wrote into |

`schema_version` is projected onto the read, not stored — the durable version is the host database's `PRAGMA user_version`.

Idempotency: `clu install-hook` (which `/clu-monitor` shells out to) checks `settings.json` for an existing entry whose command matches `hook_path` before adding a new one, then writes these rows on success. A failed install leaves the marker absent so the next attempt retries cleanly. To reset (e.g. after a manual edit to `settings.json`), run `clu uninstall-hook` — it clears the rows — and re-run `/clu-monitor`.

A leftover `~/.config/clu/monitor.json` from before the migration is **inert**: nothing reads it, in either the v1 (`/schedule`-based) or v2 (hook) shape, so a host carrying one still reads as un-monitored and `/clu-monitor` reinstalls cleanly. The quarantine sweep in operations.md moves it aside.

Helpers in `end_of_line/monitor.py`: `is_scheduled`, `load_marker`, `record_hook_installed`, `clear_marker`.

## Inbox events

Rows in the host database's `inbox` table — one row per clu notification event. Surfaced into the active Claude Code session by the `UserPromptSubmit` hook, then marked `processed = 1`. `event_id` is `UNIQUE`: that column is the dedupe the old move-to-`processed/`-directory protocol used to get from the filename.

`inbox.read_unprocessed()` / `list_for_project()` hand back:

```jsonc
{
  "id": "evt-<8hex>",
  "schema_version": 1,
  "type": "halted | blocked | plan_completed | queue_skipped | stuck_blocker | stalled_claim | ...",
  "plan_slug": "...",
  "project_root": "/abs/path",
  "timestamp": "ISO UTC",
  "summary": "one-line human summary",
  "details": { "...kind-specific...": "..." }
}
```

| Field | Meaning |
|---|---|
| `id` | `evt-` + 8 hex chars; the handle `mark_processed` keys off (the `event_id` column) |
| `type` | Matches the `KIND_*` constant in `notify.py` (without the prefix) |
| `project_root` | Resolved absolute path; the hook filter compares against `git rev-parse --show-toplevel` / `os.getcwd()` |
| `timestamp` | Same `%Y-%m-%dT%H:%M:%SZ` format as plan-state events |
| `summary` | What surfaces verbatim into Claude's context (≤200 chars by convention) |
| `details` | Free-form kind-specific payload — see the renderer in `notify.py` for the shape |

Ordering is by the table's autoincrement `id`, which is arrival order — no filename-timestamp encoding is needed any more, and two processes writing simultaneously cannot collide.

Mark-and-sweep dedup: the hook marks a surfaced event processed after emitting it. A leftover `~/.config/clu/inbox/` directory (including its `processed/` subdirectory) from before the migration is inert — nothing reads it, and unreadable files in it cannot affect the hook. The quarantine sweep in operations.md moves it aside.

Helpers in `end_of_line/inbox.py`: `write_event`, `read_unprocessed`, `mark_processed`, `list_for_project`. Reads degrade to empty on `db.DEGRADABLE_ERRORS` (absent, busy, or newer-schema store) so a broken host database cannot kill the hook.

## `.orchestrator.json` top-level schema

Optional fields alongside `dispatch` and `notify`:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `plan_dir` | string | `"plans"` | Subdirectory under `project_root` that holds plan files and `.orchestrator/` |
| `test_command` | string \| null | null | Shell command run inside the scratch worktree by `dry_merge.attempt_merge` and `clu integrate`. Absent or null → textual-merge-only mode (no suite run). Treated as `shell=True`; the operator owns trust. Example: `"python3 -m unittest discover -s tests"` |
| `auto_archive` | bool | `true` | When `true`, clu automatically archives every `STATUS_DONE` plan whose worktree branch is an ancestor of `origin/main` on the next cron tick. Set `false` to require manual `clu archive` + `clu unregister`. Non-bool values (strings, integers) raise `ConfigError` at load time. |

### `dispatch.repair_command` — deprecated and ignored

Set it and clu prints a one-line stderr note at config load, then carries on as if it were absent. It named the headless worker that repaired a corrupt `queue.json`; the queue is now rows in a transactional database, so there is nothing to repair. Remove it from your `.orchestrator.json`. (No shipped example config carries it.)

### `quality` (optional)

Controls the quality gates enforced by `clu complete`. Absent block = defaults apply.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `verify_command` | string \| null | null | Command run by `clu verify`. Falls back to top-level `test_command` if absent or null. Single string, run through the shell (same operator-trust model as `test_command`); chained gates like `typecheck && tests` work directly. |
| `simplify_threshold` | object \| null | `{files: 1, lines: 30}` | Threshold for the simplify gate. Format: `{files: int, lines: int}` — exceeding EITHER triggers the gate. Set both to 0 to gate every phase. Null restores the default. |

The verify gate always fires (unless `--skip-verify`). The simplify gate fires only when the cumulative phase diff (from branch base to current HEAD) exceeds the threshold.

## Notify config schema

`notify` in `.orchestrator.json`:

```jsonc
"notify": {
  "channels": [
    // iMessage (macOS only)
    {"kind": "imessage", "to": "+1...", "kinds": null, "enabled": true},
    // Discord (any OS)
    {"kind": "discord", "bot_token": "...", "user_id": "...", "kinds": null, "enabled": true}
  ],
  "quiet_hours": ["22:00", "08:00"],  // local wall-clock, wraps overnight; null = never quiet
  "inbound_auto_tick": true           // trigger a tick on inbound reply
}
```

**ChannelSpec fields:**

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `kind` | string | yes | — | `"imessage"` or `"discord"` |
| `kinds` | string[] \| null | no | null | Notification kinds to send (null = all) |
| `enabled` | bool | no | true | `false` silences the channel without deleting it |
| `to` | string | if `kind=imessage` | — | iMessage self-chat handle |
| `bot_token` | string | if `kind=discord` | — | Discord bot token |
| `user_id` | string | if `kind=discord` | — | Discord user ID to DM |

**Auto-migration:** if `notify.imessage.to` is present and `notify.channels` is absent,
clu synthesizes `channels: [{kind: "imessage", to: <value>}]` at config load. No file
is rewritten — existing flat-shape configs continue to work transparently.

**Default:** `channels: []` (empty or omitted) = clu-watch-only mode. Inbox hook still
works; no outbound sends. Not an error — operators who only use the in-session surface
opt in to this mode intentionally.

## Notification kinds

The outbound router (`notify.py`) classifies every send by kind. Quiet hours, when configured, gate every kind not in `notify.QUIET_HOURS_BYPASS_KINDS`; `quiet_hours` is unset by default, so nothing is gated. A gated send is DROPPED — `notify.notify` returns without sending and nothing re-sends it.

| Kind | Trigger | Quiet hours |
|---|---|---|
| `KIND_BLOCKER` | Worker called `clu block` | Gated |
| `KIND_STALLED` | Live claim past heartbeat threshold (explicit `stalled_heartbeat_minutes` or derived `min(25, max(15, lease_ttl//2))`) | Gated |
| `KIND_COMPLETED` | Plan finished cleanly (`plan_completed`) | Gated |
| `KIND_HALTED` | Plan halted (max-attempts / replan / systemic failure) | **Bypass** |
| `KIND_QUEUE_SKIPPED` | Queue head popped + abandoned (plan file missing) | Gated |
| `KIND_STUCK_BLOCKER` | Open blocker un-consumed for >30 min; re-pings every 30 min | Gated |
| `KIND_STALLED_CLAIM` | Live claim's lease expired with plan status `running`; one-shot per claim | Gated |
| `KIND_GATE_CLEAN` | Dry-merge gate ran; all batch branches textually/suite-clean | Gated |
| `KIND_GATE_DIRTY` | Dry-merge gate ran; textual conflict or suite failure found | Gated |
| `KIND_PLAN_AUTO_ARCHIVED` | `auto_archive_rule` detected a merged branch and completed cleanup | Gated |
| `KIND_QUOTA_PAUSED` | Quota death with a parseable reset; project pauses until reset, then auto-resumes | Gated |
| `KIND_QUOTA_RESUMED` | Dispatch gate cleared the quota pause after the canary survived | Gated |
| `KIND_QUOTA_STUCK` | Quota death whose reset didn't parse; no auto-resume horizon | **Bypass** |

Bypass set: `{KIND_HALTED, KIND_QUOTA_STUCK}` — two members since the queue's repair kinds were deleted along with the repair subsystem. These are unrecoverable-without-operator states; deferring them past quiet hours would let the chain sit silently broken until morning. `KIND_QUOTA_PAUSED`/`KIND_QUOTA_RESUMED` stay gated because the pause self-heals via auto-resume — there's nothing for the operator to do overnight, and a live `clu watch` surfaces the events regardless.

Inbox-vs-iMessage asymmetry: every `notify()` call with `plan_slug` + `project_root` in scope writes an inbox event regardless of quiet-hours gating. Quiet hours suppress only the iMessage send — the inbox is for the next Claude turn, not for waking the operator, so it can't be deferred. The two new "gap-fill" kinds (`KIND_STUCK_BLOCKER`, `KIND_STALLED_CLAIM`) ride on the same wire alongside whatever primary action the supervisor's tick already produces, via `TickResult.side_notifies`.

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
| 9 | `REPAIR_DECLINED` | **Retired.** The repair worker it belonged to is gone (see "Auto-repair contract — deleted"). The number stays reserved so the other codes keep their values. |
| 10 | `WORKTREE_SETUP_FAILED` | `clu init --worktree` rolled back: git worktree add succeeded but a downstream step (state save) failed, and we tore the worktree + branch back down |
| 11 | `QUEUE_CAP` | Worker tried `clu queue add` but exceeded `max_queue_adds_per_phase` (default 3). Operator path is uncapped. |

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
   is still alive (default stalled threshold: derived `max(15, lease_ttl//2)`
   — 30 min at the 60-min lease default):
   ```bash
   clu heartbeat --project P --plan S --phase X --token <token>
   ```
   Without heartbeats the supervisor can't tell "running" from "dead" until
   the 60-min lease expires.
3. **On success**, before exit:
   ```bash
   clu complete --project P --plan S --phase X --commit <sha> [...]
   ```
4. **On a /code-review finding the worker chooses NOT to fix in this phase**, before completing:
   ```bash
   clu spawn --project P --plan S --source simplify --phase X --title "..." --description "..."
   ```
   Never file as a GH issue. Spawned tasks are first-class members of the plan.
5. **To chain a follow-up plan into the project queue mid-phase** (v2 worker-enqueue):
   ```bash
   clu queue add <slug> --project P --plan S --phase X --token <token> [--reason "..."]
   ```
   The `--token` flag switches `clu queue add` into worker mode. Worker mode:
   - Requires `--plan` + `--phase`; forbids `--front`; accepts exactly one slug.
   - Validates the slug (syntax, plan-file existence, registered-project check).
   - Runs as three separate transactions, never nested: the claim is checked against a snapshot, the cap + idempotency + insert run in one queue transaction, and the outcome event lands through a plan-store op that re-checks the claim in its own `WHERE` clause. Plan state and the queue share one database, so a nested write would wait on a lock only the waiter could release.
   - Checks `max_queue_adds_per_phase` (default 3; counts over `queue + history` where `source_plan == S AND source_phase == X`). Exceeds cap → emits `EVENT_QUEUE_REJECTED` + exits `ExitCode.QUEUE_CAP` (11).
   - Idempotency: if the slug is already pending or in-flight → silently no-op (prints position); if in history → exits `STATUS_TRANSITION` (7) — hitting this is a worker bug.
   - On success: emits `EVENT_QUEUE_APPENDED` in the source plan's events; fingerprints the token (`sha256(token)[:8]`) onto the queue entry (raw token never persisted); exits 0.
   - `@_translate_claim_mismatch` wraps the worker path so a bad token exits 4 (`CLAIM_MISMATCH`).
6. **On blocked ambiguity**:
   ```bash
   clu block --project P --plan S --phase X \
     --question "..." --option A --option B --context "..." \
     [--type blocked_replan]
   ```
   This releases the claim and writes the blocker.
7. **On unrecoverable failure**: just exit. The lease expires and the supervisor retries (up to `max_attempts_per_phase`).

## Cron snippet

```cron
# Every 5 min, advance any in-progress plans
*/5 * * * * /usr/local/bin/clu tick --project /Users/me/projects/HealthData --plan watch-start-workout >> /tmp/clu-watch.log 2>&1
```

## What End of Line is NOT

- Not a /plan replacement. It calls /plan; it doesn't reinvent it.
- Not a code reviewer. Workers run `/review` and `/code-review` themselves per project rules.
- Not a parallel scheduler. v0.1 dispatches sequentially (`max_concurrent_phases: 1`). Fan-out across plans is fine (run multiple cron lines).
- Not a CI replacement. Test runs happen in the worker session, not the supervisor.
