# Design brief: SQLite migration — full inventory of the JSON storage layer

Scope pass for replacing the hand-rolled storage engine (flock + tmp/fsync/rename
+ schema_version + whole-file polling) with SQLite. This documents **every store,
every writer, every reader, the lock discipline between them, and the invariants a
migration must preserve**. Not a plan yet — the decision points at the end need
settling first.

Verified against source 2026-08-19; all `path:line` citations are from that read.

---

## 1. The primitives being replaced (`state.py`)

| Primitive | Where | What it does |
|---|---|---|
| `locked()` | `state.py:521` | Sibling `<file>.lock` + `fcntl.flock(LOCK_EX)`, `O_NOFOLLOW`. Optional `timeout_seconds` → 50 ms `LOCK_NB` poll, raises `LockTimeout`. Lock files are never deleted (the `.lock` litter in `~/.config/clu/`). |
| `save_atomic()` | `state.py:627` | `mkstemp` + `fsync` + `rename`. **Every mutation rewrites the whole file**, including the full event log. |
| `locked_json()` | `state.py:562` | lock + load + yield-for-mutation + **unconditional** save (rewrites even when unchanged). `empty=` factory for tolerate-missing stores. |
| `mutate()` | `state.py:594` | `locked_json` pinned to the plan-state schema. The canonical read-modify-write. |
| `load()` | `state.py:615` | `json.loads` + `schema_version` equality check → `SchemaVersionMismatch`. |
| `LockTimeout` | `state.py:506` | Bounded-wait variant for hot paths: activity hook passes 2.0 s and **drops the write** on contention (`state.py:1090-1122`); heartbeat-daemon death report passes a bounded budget so a contended lock can't strand the detached daemon. |

SQLite equivalents exist for all four jobs: WAL journal (atomic commit),
`busy_timeout` (advisory locking incl. the bounded-wait variant),
`PRAGMA user_version` (schema versioning), and `PRAGMA data_version` /
rowid cursors (change detection — the one job the current layer *doesn't* do).

Precedent for stdlib `sqlite3` already in-tree: the iMessage poller reads
`chat.db` read-only via URI (`notify_imessage_inbound.py:468-471`). Zero-dep
constraint holds.

---

## 2. Store inventory

### 2.1 Per-plan state — `<project>/plans/.orchestrator/<slug>.state.json`

Schema v1 (`state.py:96`). The center of gravity. One JSON document holding
**two different kinds of data fused together**:

- **Mutable head state**: `status`, `current_claim` (token, lease, pid/pgid,
  heartbeat, attestations, dedup markers, cpu_samples), `blockers[]`,
  `spawned_tasks[]`, `config`, `phases[]`, `worktree`, plus ad-hoc rule fields
  (`gate_result`, `ready_to_ship_announced`, `in_conflict_with`, `ship_pending`,
  `batch_id`).
- **Append-only event log**: `events[]` — ~40 `EVENT_*` types, the audit trail
  AND the projection source (`attempts_for_phase`, `completed_phase_ids`,
  `status_reason`, `latest_event` all re-scan the array per call,
  `state.py:1183-1317`).

Because both live in one document, **every heartbeat ping rewrites the entire
event history to disk**, and the rewrite cost grows with plan age. Live host
sample: 57 state files, ~150 KB in this repo's `.orchestrator/` alone.

**Writers** (all under the flock, via `mutate`/`locked`):

| Writer | Site | Cadence / trigger |
|---|---|---|
| Supervisor tick | `supervisor.py:614` | cron `tick-all` every 30 s (`examples/clu.tick.plist`), per plan. **Holds the lock across the whole 10-priority chain** — see §3. |
| Dispatch | `dispatch.py:668,761,795,825` | pid/pgid/log_path/session_id stamping, dispatch-failure release, systemic/quota pause, per dispatch. |
| Worker callbacks | `cli.py`: complete 4805/4849/4883, block 6281, spawn 4603/4612, task-done 4649, heartbeat 5992-6021, verify 6358, attest 6387, notify-heartbeat-failure 6062, notify-worker-dead 6150, activity 6221 | Token-validated; heartbeat every 120 s per live worker; activity on **every Bash tool call** (Pre+Post hooks, 2 s LockTimeout budget). |
| Heartbeat daemon | `heartbeat_daemon.py:63` | 120 s per live worker, plus the death-report path. |
| Operator commands | `cli.py`: init 1985-2021, pause 4451, resume 4464, retry 4483, extend-lease 4508, release-claim 4537, answer 4562, force-complete 4941, archive 5774, ship 3174/5652, worktree attach/gc 3627/3710 | Interactive. |
| Inbound reply (iMessage) | `notify_imessage_inbound.py:159` | Writes the blocker answer directly on message match. |
| Cross-plan rules | `cross_plan_rules.py:96` (`_apply`), 204-217 (queue-pop state-create) | Per tick post-loop. |
| Discord notifier | `notify_discord.py:102` | Stamps `notify_metadata` onto a blocker after send. |
| Zombie sweep | `supervisor.py:999-1007` | `locked` + re-check + conditional save. |
| Demo workers | `demo.py:107` | Demo mode only. |

**Readers** (no lock — they rely on rename atomicity for a consistent snapshot):

| Reader | Site | Cadence |
|---|---|---|
| `clu watch` | `watch.py:515` | **Full `json.loads` of every watched state file every 1.0 s**, keeps an event-array-length cursor per file, emits the tail slice. This is the 1 Hz whole-file reparse. Persistent operator Monitors run this indefinitely. |
| `clu top` | `top.py:510-514` via `registry.load_entry_state` | Every frame (default 1.5 s): registry + every plan state + transcript tails. |
| `clu serve` | `webserver.py:502,530` | Same `gather_rows` per `/api/workers` HTTP poll (~1.5 s per browser tab); `/api/feed` resolves claim→transcript per poll. |
| Fleet view (bare `clu`) | `fleet.py:24-58` | Per invocation, all plans. |
| Blocker locator | `state_locator.py:102` | **All registered plans' state files re-loaded per inbound message** (iMessage poll 4 s when rows arrive; Discord same). |
| Cross-plan rules load | `cross_plan_rules.py:68` | All plans re-loaded per project per tick post-loop. |
| SessionStart hook | `hooks/clu_session_start.py:112` | Every Claude session start walks every entry's state. |
| CLI reads | status 4392, logs, blockers 6421/6446, prior-blocker 4129, doctor, validate | Interactive. |

### 2.2 Host registry — `~/.config/clu/registry.json`

Schema v1 (`registry.py`). List of `(project_root, plan_slug, registered_at)`.
Writers: `register` (init + **inside the queue-pop lock**, `cross_plan_rules.py:218`),
`unregister` (archive, unregister commands). Readers are the hottest in the
system: top/serve re-read it **every 1.5 s frame** (`top.py:510`,
`webserver.py:502`), tick-all reads it twice per run (`cli.py:3960,3983`),
inbound pollers per routed message, the SessionStart hook, and a dozen CLI
commands. It is tiny, but every read is open+parse+close.

### 2.3 Per-project queue — `plans/.orchestrator/queue.json`

Schema v1 (`queue.py`). `queue[]` + append-only `history[]`. Writers:
`queue add` (`cli.py:3325`), `--batch` (3192), `remove` (3550), and the
advancement rule's pop/absorb/abandon (`cross_plan_rules.py:162,177,201`).
Carries a whole **corruption-repair subsystem** that exists because JSON files
get corrupted: raw-bytes slug extraction (`queue.py:56-103`), repair-worker
dispatch + `validate_repair` safety boundary, `queue.json.repair-attempts`
throttle sidecar (**raw unlocked `write_text`**, `queue.py:182`), and
`queue.json.corrupt-<ts>` backups. Much of this machinery is retirable
post-migration.

### 2.4 Quota pause — `plans/.orchestrator/quota.json`

(`quota.py:167-296`.) Written by three death sites via `record_quota_pause`
(locked_json); read + canary-CAS-written + **unlinked** by `gate_decision`
under `locked` on the dispatch path. Two semantics a migration must keep:
**file-absence == not paused** (the unlink-under-lock resume), and the
operator escape hatch is literally `rm quota.json` (baked into the notify
body, `notify.py:334-343`).

### 2.5 Monitor marker — `~/.config/clu/monitor.json`

Schema v2 (`monitor.py`). Advisory idempotence marker only; locked_json
writes, tolerant reads, v1 markers read as "reinstall".

### 2.6 iMessage inbound state — `~/.config/clu/inbound_state.json` + `outbound_pending.json`

- `inbound_state.json`: chat.db ROWID high-water + per-chat outbound floors.
  `save_atomic` **without a lock** — single-writer assumption (the poller
  daemon caches it in memory, `notify_imessage_inbound.py:489-537`).
- `outbound_pending.json`: genuinely multi-writer — **any process that sends
  an iMessage** appends a mark (`append_outbound_mark:374`, locked_json);
  the poller drains marks against chat.db (`drain_outbound_marks:397`).

### 2.7 Discord sidecars — `~/.config/clu/discord_state.json`, `discord_cursor.json`

DM-channel cache has **two independent writers with no lock** (outbound
`notify_discord.py:113-130`, inbound `notify_discord_inbound.py:93-107`) —
last-writer-wins on a single-key dict, benign today, but it's an existing
unlocked-concurrency hole the migration absorbs for free. Cursor file is
single-writer `save_atomic`.

### 2.8 Inbox — `~/.config/clu/inbox/*.json`

Directory-as-queue: one file per event, tmp+rename, ns-timestamped monotonic
filenames (`inbox.py:43-79`); the UserPromptSubmit hook consumes by moving
files to `processed/`. Lock-free **by design** and it works. Candidate to
migrate (events table + consumed flag) or deliberately keep — see §5.

### 2.9 Skill-install receipt (in-flight skill-drift work)

`skill_sync.record_install` (`skill_sync.py:196`) uses `locked` + `save_atomic`
on the receipt sidecar — two clu processes can race there. Uses the same
primitives; migrates or keeps trivially.

### 2.10 Explicit non-targets

Human-edited config stays as files: `.orchestrator.json` (read raw
`config.py:385`, written by init `cli.py:249-255,1924-1930`), global
`~/.config/clu/config.json` (fail-open raw read), `worker-settings.json`,
Claude `settings.json` hook edits, worker/attempt logs, plan markdown.

---

## 3. How they interact — the parts that constrain the design

### Lock-nesting orders that exist today (implicit, unenforced)

1. **queue → state → (registry)**: the pop sequence holds the queue lock, takes
   the state lock inside it to create the state file, then calls
   `registry.register` (registry lock) still inside the queue lock
   (`cross_plan_rules.py:201-219`). Deliberate: crash-replayable pop.
2. **state → quota**: `quota.record_quota_death` acquires the quota.json lock
   inside the supervisor's open state `mutate` window (`supervisor.py:639,717`).

No inverse orders exist, so no deadlock today — but nothing checks this. In a
single per-project SQLite DB, orders 1 (minus registry) and 2 collapse into
ordinary transactions.

### Long lock holds vs. bounded-wait writers

`supervisor.tick` holds the per-plan state lock across the **entire** priority
chain, including subprocess work: `ps -eo` snapshot (5 s timeout,
`supervisor.py:125-142`), `lsof` (1 s, :540), the killpg reap escalation loop
(up to ~5 s of sleep-polls, `state.py:436-457`), and coolant script shell-outs
(2 s each, inside the mutate at `supervisor.py:654-663`). Meanwhile the
activity hook waits at most 2 s and **drops its write** on contention, and the
heartbeat daemon strikes. Worst-case tick = several seconds of exclusive hold.

A naive port that opens one SQLite write transaction around `tick()` recreates
this exactly (and WAL allows only one writer). **The tick needs restructuring
to read-snapshot → act → short compare-and-set write transactions.** This is
the one genuinely behavioral piece of the migration; everything else is
mechanical.

Counter-pattern already in-tree worth copying: `cmd_verify` snapshots state,
runs the 600 s gate **unlocked**, then re-locks briefly to stamp
(`cli.py:6327-6358`). And notify sends already happen strictly after the
mutate window exits (`TickResult.notify_body` / `side_notifies` contract,
`supervisor.py:204-212`).

### Change detection: the missing feature

There is no way to ask "did anything change?" — so:

- `watch` reparses every file at 1 Hz and diffs event-array length.
- `top`/`serve` reload registry + all states per 1.5 s frame.
- `state_locator` reloads all states per inbound message.

An `events` table keyed by autoincrement rowid turns all three into
`SELECT ... WHERE id > ?` — the watch cursor becomes a rowid, top/serve can
select the head-state columns without deserializing event history, and
`PRAGMA data_version` gives a free "nothing changed, skip the frame" check.

### Derived state recomputed per call

`attempts_for_phase` (retry-floor + forgiveness scan), `completed_phase_ids`,
`status_reason`, `latest_event` are O(events) Python scans, called multiple
times per tick. These become indexed queries or stay as Python over a
`SELECT type, phase, ... FROM events WHERE plan=?` — either way stop paying
full-file JSON parse first.

### Tolerant-read contract

Every fleet-walking reader treats missing/corrupt/schema-mismatched state as
"skip, never raise" (`registry.load_entry_state:87-107`,
`cross_plan_rules.py:68-72`, `state_locator.py:102-108`, watch, hooks). The
migration must preserve *reader never takes down the fleet* — including the
new failure mode "DB file from a newer clu version".

### Operator hand-editing contract

Documented affordances assume inspectable files: `rm quota.json` (stuck-pause
escape), hand-editing `state.worktree` (`notify.py:279-284`), reading state
JSON in an editor while debugging. A migration needs replacements:
`clu state dump [--plan]` (JSON out), `clu quota clear`, and doctor-level
introspection — or those docs/notify bodies rot into lies.

### Process / environment facts

- All writers are **short-lived processes** (cron ticks, CLI callbacks, hooks)
  plus three daemons (heartbeat daemons, inbound poller, `clu serve`). WAL
  handles this shape well; connections are per-invocation.
- Workers run Seatbelt-sandboxed but every state write already flows through
  the sandbox-exempt `clu` CLI (locked config decision #90) — the DB gets the
  same exemption for free.
- All stores live on local APFS (repo dirs + `~/.config/clu`). WAL's
  shm/wal sidecars are safe there; the "no network FS" caveat is worth one
  line in operations.md.
- Tests: whole suite builds state via tmp_path JSON files and `CluTestCase`
  HOME/XDG isolation; `docs/contract.md` freezes the JSON schema as the wire
  contract; `clu demo` drives the real pipeline. Test-helper migration is a
  large mechanical chunk of the work.

---

## 4. What the migration retires

- The four hand-rolled DBMS features (flock discipline, tmp+fsync+rename,
  per-file schema_version checks, 1 Hz/1.5 s whole-file polling).
- `.lock` sibling litter (never-deleted lock files across `~/.config/clu/`
  and every `.orchestrator/`).
- Most of the queue corruption-repair subsystem (§2.3) — regex-over-bytes slug
  rescue, repair-worker dispatch, throttle sidecar — SQLite's atomicity makes
  the "well-formed-prefix-then-garbage" failure mode it exists for
  structurally impossible. (Keep `validate_repair`'s tests as history.)
- The unlocked Discord DM-cache race (§2.7).
- Whole-event-log rewrite on every heartbeat.

## 5. Decisions (settled with operator 2026-08-19)

1. **DB topology — DECIDED: per-project + host pair.** Per-project DB at
   `plans/.orchestrator/clu.db` (state + queue + quota); host DB at
   `~/.config/clu/clu.db` (registry, monitor, inbound/outbound marks, discord
   cursors, inbox, skill receipt). The pop sequence stays two coordinated
   transactions (project txn creates state + pops queue; host txn registers),
   same recovery shape as today's nesting but with real atomicity inside each
   side.
2. **Tick restructuring — NEEDS RESEARCH** (operator-confirmed). The plan must
   carry a stage-zero research/probe phase before any tick code changes:
   - SQLite behavior to verify by probe, not memory: WAL single-writer +
     `busy_timeout` semantics under our short-lived-process shape;
     `PRAGMA data_version` cross-connection semantics (it only moves for
     *other* connections' commits); fsync durability defaults
     (`synchronous=NORMAL` vs `FULL` in WAL) against the current
     tmp+fsync+rename guarantee; behavior of a reader holding a long read txn
     while the tick writes.
   - Design question: snapshot → act → per-priority CAS writes vs one short
     end-of-tick transaction; how claim-token CAS (`assert_claim_match`)
     generalizes to "write succeeds only if claim unchanged since snapshot".
   - Survey how comparable single-host orchestrators structure this
     (prior-art pass, per the new-to-repo-API rule).
3. **Inbox — DECIDED: migrate to a table** (consumed flag replaces the
   move-to-`processed/` protocol; the UserPromptSubmit hook reads + flags in
   one txn).
4. **Event retention — DECIDED: archive table.** Terminal plans' event rows
   move to an `events_archive` table (same schema + archived_at) so the hot
   `events` table stays lean; watch/top/locator only ever query the hot table.
5. **Human affordances — proposed default (operator: no strong preference).**
   Ship in the same change: `clu state dump [--plan <slug>]` (JSON to stdout,
   replaces opening the state file in an editor), `clu quota clear` (replaces
   `rm quota.json`), and sweep every doc/notify body that names a file path as
   an operator action (`notify.py:334-343` render_quota_stuck, worktree-missing
   body, operations.md troubleshooting). Cheap to adjust later; the invariant
   is "no documented escape hatch names a raw file that no longer exists".
6. **Migration mechanics — DECIDED: start from scratch.** No JSON→DB
   auto-migration and no dual-read compat layer (single-operator install).
   Cutover = ship with the fleet quiet (no running plans/claims); existing
   JSON state files become inert legacy (archived plans stay frozen JSON,
   reference-only). Still required: `PRAGMA user_version` stamping from day
   one, and the tolerant-read contract extends to "DB from a newer clu
   version → skip, never crash the fleet walk".

## 6. Execution hazard to carry into the plan

**Dogfooding cutover risk:** clu orchestrates this repo using the exact
storage layer being replaced. Phases that rewrite `state.py`/callback plumbing
must not be executed by clu workers whose own claims live in that layer
mid-rewrite — either run this plan in-session (manual `/plan` execution) or
accept that each phase's worker runs on the *old* installed clu while editing
the new code in a worktree, with the pipx reinstall happening only at ship.
Tests are already HOME-isolated (p1, `853a7f4`), so the suite itself can't
touch live state.
