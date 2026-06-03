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
    "attempts": 1,
    // pid: stamped by dispatch._stamp_pid after Popen. pgid == pid because
    // the worker is spawned start_new_session=True (it leads its own process
    // group); reapers killpg(pgid) to take the worker + heartbeat loop
    // together. pre-#75 state files have pid but no pgid — reapers fall back.
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

  "events": [
    {"ts": "ISO8601", "type": "phase_started",   "phase": "a-foundation", "claimed_by": "..."},
    {"ts": "ISO8601", "type": "phase_completed", "phase": "a-foundation", "commits": ["abc123"]},
    {"ts": "ISO8601", "type": "phase_blocked",   "phase": "...", "blocker_id": "q-1"},
    {"ts": "ISO8601", "type": "blocker_answered","blocker_id": "q-1", "answer": "..."},
    {"ts": "ISO8601", "type": "lease_expired",   "phase": "..."},
    {"ts": "ISO8601", "type": "phase_worker_dead", "phase": "...", "pid": 12345},
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
    {"ts": "ISO8601", "type": "operator_skip_simplify", "phase": "..."}
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
- `worktree` and `in_conflict_with` are **additive optional** — readers use `state.get_worktree(data)` and `data.get("in_conflict_with") or []`. No `schema_version` bump on introduction.

### Worktree event semantics

- `worktree_missing` — emitted by `dispatch_for_tick` when `state.worktree` exists but `path` is either gone from disk or no longer a valid git working dir (operator deleted the dir, or ran `git worktree prune`). The plan is paused (status → PAUSED), the just-made claim is released without burning a phase attempt, and a KIND_HALTED iMessage names the path. Recovery: restore the dir or hand-edit `state.worktree`, then `clu resume`.
- `worktree_conflict_warning` — emitted by `clu tick-all`'s post-loop conflict scan when two active plans in the same project both lack a worktree record. Only the lexicographically-smaller slug in the pair emits the event (`other_slug` names the peer); both plans update their `in_conflict_with` field. Auto-clears when one side transitions out of "active" (claim ends or status leaves RUNNING).

### Cleanup / terminalization semantics

- `plan_abandoned` — emitted by `state.terminalize` when a non-terminal (`running`) plan is torn down: `clu unregister` of a still-running plan, or the registry-independent zombie sweep (`supervisor.sweep_zombie_states`). The status flips to `halted` (no new `abandoned` status — the event carries the provenance) and the worker process group is best-effort reaped. `terminalize` is compare-and-set: a no-op on an already-terminal plan, so a cron tick racing a manual cleanup can't double-fire it. The `reason` field distinguishes `"unregister"` from `"zombie_sweep"`. Additive-optional: no `schema_version` bump.

### Operator claim-control event semantics

- `lease_extended` — emitted by `clu extend-lease` (operator-only; no `--token` required). Fields: `phase` (current phase id), `extended_by_minutes` (the argument passed), `new_expires` (ISO-8601 UTC string of the new expiry), `operator: true`. Semantics: `new_expires = max(now, current_lease_expires) + timedelta(minutes=N)`, so extending an already-expired (stalled) claim anchors from `now`, never backwards.
- `attempts_reset` — emitted alongside `claim_force_released` when `clu release-claim --reset-attempts` is passed. Fields: `phase`, `operator: true`. Resets the attempt floor so the next dispatch starts fresh. `attempts_for_phase()` counts `phase_started` events after the most-recent of EITHER `retry_requested` OR `attempts_reset` — both act as floor markers; most-recent wins.

### Quality-attestation event semantics

- `verify_stamped` — emitted by `clu verify` on rc=0. Fields: `phase`, `commit_sha` (the HEAD SHA captured before the command ran). Stamps `current_claim.attestations.verify`.
- `simplify_stamped` — emitted by `clu attest --simplify`. Fields: `phase`, `commit_sha` (current HEAD at attest time). Stamps `current_claim.attestations.simplify`.
- `operator_skip_verify` — emitted by `clu complete --skip-verify`. Audit event; phase still completes. Fields: `phase`.
- `operator_skip_simplify` — emitted by `clu complete --skip-simplify`. Audit event; phase still completes. Fields: `phase`.
- `attestation_refused` — emitted by `clu complete` when the verify or simplify quality gate refuses. Fires once per refusal call (no dedup; #70 operator-dashboard wants every gate hit visible). Fields: `phase`, `gate` (`"verify"` | `"simplify"`, matching `ATTESTATION_VERIFY` / `ATTESTATION_SIMPLIFY`), `stamped_at` (last stamp SHA or `null`), `head_sha` (HEAD that was refused; worktree HEAD if a worktree is active). Re-read under lock at emit time — if a concurrent stamp made the gate pass between the snapshot read and the lock acquire, no event is emitted.

### Worker-enqueue event semantics

- `queue_appended` — emitted in the **source plan's** `events` array (not a separate project-level log) when a worker successfully appends a slug to the project queue. Fields: `slug` (the enqueued plan), `source_plan` (the worker's plan), `source_phase` (the worker's phase), `source_token_fp` (sha256 fingerprint of the token, first 8 hex chars — raw token never persisted), `reason` (optional free-text from `--reason`), `position` (1-based queue position at append time).
- `queue_rejected` — emitted in the **source plan's** `events` array when a worker-enqueue attempt is refused. Fields: `slug`, `source_plan`, `source_phase`, `reason` — either `"cap"` (per-phase add cap reached) or `"missing_plan_file"` (the target `<plan_dir>/<slug>.md` does not exist).

Both events ride in the **source plan's** state file so the worker's audit trail is co-located with the rest of its phase actions.

### Stall-detector guard

`phase_stalled` is suppressed when `last_heartbeat_at == started_at` (the canonical `claude --print` case: stdout buffers until exit, so the bundled `/clu-phase` skill never calls `clu heartbeat` between tool calls and the heartbeat timestamp never advances). The lease-expiry path (`lease_expired`) still fires on genuinely-silent workers via `_detect_lease_expired` — the guard only mutes the chatty per-threshold ping, not the final timeout.

## Queue schema

Per-project queue file at `<project_root>/<plan_dir>/.orchestrator/queue.json`. One queue per project (not per plan). Schema, after `queue._empty()`:

```jsonc
{
  "schema_version": 1,
  "queue": [
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
- A successful normal pop produces no history entry — the popped slug becomes the active plan instead. `queue.history` records only failures (`removed | absorbed | abandoned`); successful pops live only in the popped plan's state.json. The `clu queue list` in-flight footer derives from the registry, not `queue.history`.
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

## Background-monitoring marker

`$XDG_CONFIG_HOME/clu/monitor.json` (default `~/.config/clu/monitor.json`). Account-wide, not per-project — one `UserPromptSubmit` hook covers every plan on the host. Absent file = monitoring not set up; `clu init` and `clu queue add` emit a one-line tip recommending `/clu-monitor` when this file is absent and stdout is a TTY.

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
| `hook_installed_at` | ISO UTC timestamp of marker write |
| `hook_path` | Absolute path to the bundled hook script resolved at install time |
| `settings_json_path` | Absolute path to the `settings.json` the installer wrote into |

Idempotency: `clu install-hook` (which `/clu-monitor` shells out to) checks `settings.json` for an existing entry whose command matches `hook_path` before adding a new one, then writes this marker on success. A failed install leaves the marker absent so the next attempt retries cleanly. To reset (e.g. after a manual edit to `settings.json`), `clu uninstall-hook && rm ~/.config/clu/monitor.json` and re-run `/clu-monitor`.

v1 markers (pre-#20, contained `schedule_id`/`cadence` from the broken `/schedule`-based install) are treated as "needs reinstall" — `is_scheduled()` returns False so `/clu-monitor` re-runs and overwrites the marker in v2 form. No data migrated; the v1 `schedule_id` was never read by anything beyond the original routine creation.

Helpers in `end_of_line/monitor.py`: `marker_path`, `is_scheduled`, `load_marker`, `record_hook_installed`, `clear_marker`. Reads/writes go through `state.locked_json` so the marker shares the lock + atomic-write primitive with state.json, registry.json, and queue.json.

## Inbox event files

`$XDG_CONFIG_HOME/clu/inbox/<safe_ts>-<time_ns>-<kind>-<short>.json` (default `~/.config/clu/inbox/...`). One file per clu notification event. Surfaced into the active Claude Code session by the `UserPromptSubmit` hook, then moved to `~/.config/clu/inbox/processed/`.

```jsonc
{
  "id": "evt-<8hex>",
  "schema_version": 1,
  "type": "halted | blocked | plan_completed | queue_skipped | queue_corrupt | queue_repaired | queue_repair_failed | stuck_blocker | stalled_claim",
  "plan_slug": "...",
  "project_root": "/abs/path",
  "timestamp": "ISO UTC",
  "summary": "one-line human summary",
  "details": { "...kind-specific...": "..." }
}
```

| Field | Meaning |
|---|---|
| `id` | `evt-` + 8 hex chars; the handle `mark_processed` keys off |
| `type` | Matches the `KIND_*` constant in `notify.py` (without the prefix) |
| `project_root` | Resolved absolute path; the hook filter compares against `git rev-parse --show-toplevel` / `os.getcwd()` |
| `timestamp` | Same `%Y-%m-%dT%H:%M:%SZ` format as state.json events |
| `summary` | What surfaces verbatim into Claude's context (≤200 chars by convention) |
| `details` | Free-form kind-specific payload — see the renderer in `notify.py` for the shape |

Filenames are sort-friendly: `<safe_ts>` is the second-resolution UTC timestamp with separators stripped, `<time_ns>` is `time.time_ns()` zero-padded to 19 digits (strict monotonic ordering under tight-loop writes), and `<short>` is an 8-hex tiebreaker against simultaneous writes from multiple processes. Reading the inbox lexically equals reading by arrival time.

Mark-and-sweep dedup: the hook moves a surfaced event into `processed/` after emitting it. To reset (e.g. clear debug noise), `rm -rf ~/.config/clu/inbox/ ~/.config/clu/inbox/processed/` — the next event write recreates the directory.

Helpers in `end_of_line/inbox.py`: `inbox_root`, `write_event`, `read_unprocessed`, `mark_processed`, `list_for_project`. Writes use atomic `tmp + rename` (the dirs are short-lived per event — no flock). Corrupt files are silently skipped on read so a malformed sibling can't kill the hook.

## `.orchestrator.json` top-level schema

Optional fields alongside `dispatch` and `notify`:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `plan_dir` | string | `"plans"` | Subdirectory under `project_root` that holds plan files and `.orchestrator/` |
| `test_command` | string \| null | null | Shell command run inside the scratch worktree by `dry_merge.attempt_merge` and `clu integrate`. Absent or null → textual-merge-only mode (no suite run). Treated as `shell=True`; the operator owns trust. Example: `"python3 -m unittest discover -s tests"` |
| `auto_archive` | bool | `true` | When `true`, clu automatically archives every `STATUS_DONE` plan whose worktree branch is an ancestor of `origin/main` on the next cron tick. Set `false` to require manual `clu archive` + `clu unregister`. Non-bool values (strings, integers) raise `ConfigError` at load time. |

### `quality` (optional)

Controls the quality gates enforced by `clu complete`. Absent block = defaults apply.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `verify_command` | string \| null | null | Command run by `clu verify`. Falls back to top-level `test_command` if absent or null. Single-string + `shlex.split`; wrap multi-step verify in a script. |
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

The outbound router (`notify.py`) classifies every send by kind. Quiet hours (default 22:00–08:00 local) gate every kind not in `notify.QUIET_HOURS_BYPASS_KINDS`.

| Kind | Trigger | Quiet hours |
|---|---|---|
| `KIND_BLOCKER` | Worker called `clu block` | Gated |
| `KIND_STALLED` | Live claim past heartbeat threshold (explicit `stalled_heartbeat_minutes` or derived `min(25, max(15, lease_ttl//2))`) | Gated |
| `KIND_COMPLETED` | Plan finished cleanly (`plan_completed`) | Gated |
| `KIND_HALTED` | Plan halted (max-attempts / replan / systemic failure) | **Bypass** |
| `KIND_QUEUE_SKIPPED` | Queue head popped + abandoned (plan file missing) | Gated |
| `KIND_QUEUE_REPAIRED` | Auto-repair succeeded + validation passed | Gated |
| `KIND_QUEUE_REPAIR_FAILED` | Auto-repair failed validation (file reverted) | **Bypass** |
| `KIND_QUEUE_CORRUPT` | Queue corrupt + auto-repair disabled OR throttle exhausted | **Bypass** |
| `KIND_STUCK_BLOCKER` | Open blocker un-consumed for >30 min; re-pings every 30 min | Gated |
| `KIND_STALLED_CLAIM` | Live claim's lease expired with plan status `running`; one-shot per claim | Gated |
| `KIND_GATE_CLEAN` | Dry-merge gate ran; all batch branches textually/suite-clean | Gated |
| `KIND_GATE_DIRTY` | Dry-merge gate ran; textual conflict or suite failure found | **Bypass** |
| `KIND_PLAN_AUTO_ARCHIVED` | `auto_archive_rule` detected a merged branch and completed cleanup | Gated |

Bypass set: `{KIND_HALTED, KIND_QUEUE_REPAIR_FAILED, KIND_QUEUE_CORRUPT}`. These are unrecoverable-without-operator states; deferring them past quiet hours would let the chain sit silently broken until morning.

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
| 9 | `REPAIR_DECLINED` | Repair worker refusing to touch the file (legibility-only — clu still validates) |
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
   - Acquires state lock first, queue lock second (never reverse — see architecture.md).
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
