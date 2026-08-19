# sqlite-migration — replace JSON-files-plus-flock with SQLite

## Phase map

**Phase p1 — SQLite core: `db.py`, both schemas, cross-process contention acceptance test** — SHIPPED `e95dd60`
- Enters when: start here (plan approved, fleet-quiet precondition met — see Status)
- Done signal: contention acceptance test green with counted numbers; see `plans/sqlite-migration-p1.md`
- If it fails: STOP the plan — if `BEGIN IMMEDIATE` + busy_timeout cannot produce N×M lossless increments on this host, the research was wrong and the plan re-enters EXPLORE
- Shard: `plans/sqlite-migration-p1.md`

**Phase p2 — host DB: registry, monitor, iMessage/Discord sidecars, inbox, skill receipt**
- Enters when: p1 committed
- Done signal: host stores DB-backed behind unchanged public signatures; see `plans/sqlite-migration-p2.md`
- If it fails: no gate — fix-forward
- Shard: `plans/sqlite-migration-p2.md`

**Phase p3 — project DB: plan-state engine swap under the existing `st.mutate`/`st.load` API**
- Enters when: p2 committed
- Done signal: `clu demo` fleet runs end-to-end on DB-resident plan state, zero `.state.json` writes; see `plans/sqlite-migration-p3.md`
- If it fails: the compat-facade approach is the load-bearing assumption — if the dict round-trip cannot preserve semantics, STOP and re-plan (the data-model fork's evidence changing)
- Shard: `plans/sqlite-migration-p3.md`

**Phase p4 — native write ops for hot paths: heartbeat, activity stamps, worker callbacks, dispatch stamps**
- Enters when: p3 committed
- Done signal: a heartbeat is a single-row UPDATE inside one `BEGIN IMMEDIATE` txn, proven by SQL-trace test; see `plans/sqlite-migration-p4.md`
- If it fails: no gate — fix-forward
- Shard: `plans/sqlite-migration-p4.md`

**Phase p5 — queue + quota tables; retire the corruption-repair subsystem; `clu quota clear`**
- Enters when: p4 committed
- Done signal: queue add→pop→dispatch round-trip transactional on the project DB; quota pause row-based with `clu quota clear`; see `plans/sqlite-migration-p5.md`
- If it fails: no gate — fix-forward
- Shard: `plans/sqlite-migration-p5.md`

**Phase p6 — supervisor tick restructure: snapshot → detect (unlocked) → precondition-guarded apply (stop gate — a failure here halts the plan rather than fixing forward)**
- Enters when: p5 committed
- Done signal: trace-proven "no subprocess runs inside an open transaction" + the precondition-selectivity test (a mid-tick heartbeat must NOT abort a lease-expiry apply, but MUST abort a stalled-emit); see `plans/sqlite-migration-p6.md`
- If it fails: the chosen tick shape is wrong — STOP, re-read the p6 Decisions entry (it records the four rejected alternatives with why), and take the per-priority-write alternative to the operator before writing more code
- Shard: `plans/sqlite-migration-p6.md`

**Phase p7 — dashboards + watch on native reads; delete the compat facade and the file-lock primitives**
- Enters when: p6 committed
- Done signal: `clu watch` output byte-identical on a demo run; the only `import fcntl` left is the PTY shim's terminal-sizing use; see `plans/sqlite-migration-p7.md`
- If it fails: no gate — fix-forward
- Shard: `plans/sqlite-migration-p7.md`

**Phase p8 — docs, bundled skills, operations, cutover checklist, legacy quarantine**
- Enters when: p7 committed
- Done signal: no doc or skill names a raw JSON store that no longer exists; legacy files quarantined; see `plans/sqlite-migration-p8.md`
- If it fails: no gate — fix-forward
- Shard: `plans/sqlite-migration-p8.md`

## Status & cold-start
**Approval: APPROVED 2026-08-19**
**Authored at: 5aab1ed**
**Upstream: docs/design-briefs/sqlite-migration-scope.md (uncommitted at authoring; on disk this session)**

Verification pass 2026-08-19 (rerun — the first attempt's grounding and executability auditors both died on an API credit error before reporting; all three axes ran on the second attempt). **62 claims checked: 43 resolve, 8 do not, 8 partially, 3 uncheckable (the multiprocess probe numbers — the script lived in a session scratchpad, and p1 re-runs the load-bearing probe as an executable test precisely so the finding is re-proved on the host), 4 uncited, 4 hedged · 44 done criteria across 8 shards, ~75 symbols in 30 interface bullets, 11 tasks in 4 tiered phases · 37 locked-decision entries walked against their mechanisms, 24 characterizations, ~20 cross-file restatements, 8 upstream entries.**

**Two findings changed the work, not just the citations.** (A) **The `.exists()` gate — the defect that would have taken the fleet down with a green suite.** p3 swapped the engine under `st.load`/`st.mutate` and asserted consumers were unchanged, but 21 sites never reach `st.load`: they ask `state_path.exists()` first. `supervisor.py:601` is `tick()`'s first line and `registry.py:102` is the seam behind every dashboard, so the moment state files stopped being written every plan would have read as absent — idle ticks, empty dashboards, no error anywhere. p3's Work now enumerates all 21 and carries a grep as its own mechanical gate. (B) **The `fcntl` exit criterion could never pass** — `_pty_spawn_shim.py:70` imports it for terminal sizing, untouched by any phase; both the master's and p7's criteria now name that one legitimate survivor.

**Fixed, from the coherence axis:** p1's contention criterion said 300/300 while its Work said 8×25/200 (300 is the naive control's number) → 200/200; p6's opening paragraph and p1's schema comment still described the version-counter guard the amendment replaced → both rewritten; the master named `snapshot_with_version` after p6 renamed it, counted three rejected alternatives where p6 lists four, and kept an absolute "every read-modify-write must be BEGIN IMMEDIATE" that the amendment had narrowed → all three corrected; p3's demo criterion demanded a directory its own Work still populates (queue and quota files live there until p5) → scoped to state files, with p5 taking the full zero-file criterion; p2 claimed two consumer edits against four in its own Work; the p6 phase-map tag named a "kill-switch gate" no shard defines.

**Fixed, from the executability axis:** the master's zero-file criterion had no owner after p5 → p5 now owns it; `monitor.py`'s migration had no done criterion at all → added; four criteria passed vacuously when both sides were empty (p6 emitter parity, p6 watch sequence, p4 archive counts, p7 dashboard rows) → each now asserts presence before agreement; p2 declared `Produces: none` while p5 and p7 call five of its functions → the preserved surface is enumerated; p4 under-declared `op_stamp_claim_fields`; p5's `create_in_txn` was "expose if needed" while its pop cannot be one transaction without it → promoted to Produces; seven unsourced referents (`TickResult`, `GateDecision`, `load_plans_for_project`, the auto-archive rule, the absorb/abandon outcomes, the store map, the demo script) → sourced, and the demo script became a real p1 deliverable since p3, p5, p6 and p7 all replay it; the bundled-skills non-goal gained its asymmetry sentence.

**Fixed, from the grounding axis, beyond A and B:** the inbox claim would have consumed events the hook never displayed (it caps at 20 and today marks only what it showed) → `claim_for_project` takes the cap; the `inbox=` keyword has zero call sites anywhere, so what looked like a migration is a no-op (what `notify.py` injects is a callable, not a path); p3's failure-mode note claimed callers distinguish missing state from corrupt, and the cited code does the opposite; five citation ranges were wrong (`cmd_verify`, `cmd_queue_add`, `_handle_corrupt_queue`, the tick-all guard, `_perform_archive`); "137 test files" is 135, "three lock tests" is two (the other two test atomic rename), "five gap-fill emitters" is four plus `_detect_stalled`, "eight `$STATE` sites" is nine in two distinct groups; the activity hook is operator-installed and token-guarded, so p6's write-rate argument now rests on the unconditional heartbeat at minimum; and no example config carries `repair_command`, so p8 has nothing to annotate there.

**Not fixed, by choice:** the ~20 behavioral characterizations the coherence auditor flags on principle — each cites a file I read this session, and the standing caveat is that a resolving citation proves a symbol exists, never that a description of its behavior is right.
**p1 SHIPPED 2026-08-19 (`e95dd60`).** Fleet-quiet precondition established first, as p1's opening act: queue empty, the one registered plan on this host (`bench-row-hardening`) done with no live claim, and `com.clu.tick` booted out of the user LaunchAgent domain (no inbound agent was loaded). It stays out until p8 ships.

**Spec check at p1** — one task (no task tier), so run in the dispatching session · 4/4 Work items evidenced · interfaces conform: all nine `Produces` symbols shipped with the declared signatures, and both schemas' tables match the DDL contract exactly (11 project incl. 2 indexes, 9 host) · 1 file unclaimed by the Work list, `tests/test_demo_script.py`, reported by the worker as required by the described work → applied and recorded as a planning defect in p1's Work.

**Review at p1** — `/code-review` at high: 5 findings, all fixed in the phase commit, none parked. `journal_mode=WAL`'s result was discarded (a database that failed to switch would run on the rollback journal with readers serializing behind the tick and nothing reporting why); 0600 and the symlink refusal were enforced only on create, while the state files being replaced re-establish 0600 on every write; the schema check took the write lock even when already current, which would have had every dashboard poll contend with the tick and made the newer-schema refusal unreachable from a read-only handle; `read_txn` raised SQLite's own error instead of `DbBusy`; and the demo driver ignored the exit codes of the commands producing most of the golden. Four new tests cover the behavior changes. Gate: **2199/2199 green** (2168 before the phase), basedpyright 0 errors, ruff clean.

Downstream sweep at p1 — p2 clean (its five `Consumes` symbols shipped as declared) · p3 golden criterion pinned to the text projection · p4 1 criterion added (`attempts` key) · p5 clean · p6 1 criterion added (`attempts` key) · p7 2 criteria added (`attempts` residual on reads, golden projection) · p8 clean · code: p1 pinned both schemas and the transaction discipline, but it is the FIRST phase — no earlier phase's shipped source exists to have been built against the freedoms it removed, so nothing was obsoleted · upstream: re-read `docs/design-briefs/sqlite-migration-scope.md`, unchanged since transcription, no conflict with p1 as shipped or p2 as scoped.

Three findings from p1 bind later phases and are recorded in full in its shard: the golden is `clu watch`'s TEXT projection and `--json` will never match it; `clu watch`'s always-`attempt 1` bug is FROZEN into the golden rather than fixed, so p4 and p6 must not add an `attempts` key to the start event; and a busy on a reader's first statement inside `read_txn` still arrives as SQLite's own error, which p7's pollers must catch alongside `DbBusy`.

NEXT: p2 — read `plans/sqlite-migration-p2.md` FIRST.
Binding decisions for p2 (inline copy): the host DB is reached through `db.host_db_path()` / `db.ensure_host_schema` and every write is one `db.write_txn`, reads short `db.read_txn` or single statements; the tolerant-read contract is that a missing DB row degrades exactly as a missing file did (empty registry, `None` marker, cursor 0) and `db.SchemaTooNew` degrades like today's `SchemaVersionMismatch` — skip or None, never crash a fleet walk; every migrated store keeps its public signatures, so consumers are unchanged. Note for the inbox work: `claim_for_project` takes the hook's own cap (`MAX_EVENTS = 20`), because a claim-everything call would consume events the operator never saw.

**Execution arrangement (confirmed by the operator at approval 2026-08-19 — "this will be the only plan running"):** this repo's clu is an editable pipx install (`pipx install -e .`, CLAUDE.md "Stack + run/test"), so edits to this checkout change the live cron-driven clu immediately. The plan executes in the main checkout with the fleet quiet: no running or queued plans on this host, and the tick + inbound LaunchAgents unloaded (`launchctl bootout gui/$UID/com.clu.tick`, same for inbound) from p1's first edit until p8 ships. Re-load at ship.

## Upstream decisions (transcribed)

- "**DB topology — DECIDED: per-project + host pair.** Per-project DB at `plans/.orchestrator/clu.db` (state + queue + quota); host DB at `~/.config/clu/clu.db` (registry, monitor, inbound/outbound marks, discord cursors, inbox, skill receipt)." — docs/design-briefs/sqlite-migration-scope.md:266-269
- "The pop sequence stays two coordinated transactions (project txn creates state + pops queue; host txn registers), same recovery shape as today's nesting but with real atomicity inside each side." — docs/design-briefs/sqlite-migration-scope.md:269-272
- "**Tick restructuring — NEEDS RESEARCH** (operator-confirmed). The plan must carry a stage-zero research/probe phase before any tick code changes" — docs/design-briefs/sqlite-migration-scope.md:273-275 (discharged at plan time rather than by a phase, and here is the evidence rather than the assertion: the SQLite semantics were probed on BOTH the production 3.14 interpreter and the 3.11 floor before any shard was written — the numbers, including the busy-retry counts and the WAL-pinning sizes, are in `## Background findings` below; the doc half is cited there too; and the tick shape those probes settled is written up with four rejected alternatives in `plans/sqlite-migration-p6.md` `## Decisions & findings`. p1 re-runs the load-bearing probe as an executable acceptance test so the finding is re-proved on the host at execution time, not trusted from this document.)
- "**Inbox — DECIDED: migrate to a table** (consumed flag replaces the move-to-`processed/` protocol; the UserPromptSubmit hook reads + flags in one txn)." — docs/design-briefs/sqlite-migration-scope.md:287-289
- "**Event retention — DECIDED: archive table.** Terminal plans' event rows move to an `events_archive` table (same schema + archived_at) so the hot `events` table stays lean; watch/top/locator only ever query the hot table." — docs/design-briefs/sqlite-migration-scope.md:290-292 (scope clarified by the operator at approval 2026-08-19: "halt or paused should stay in the live table so they can be resumed or archived later" — so the move fires on explicit archival only, never on a halt or pause)
- "**Human affordances — proposed default (operator: no strong preference).** Ship in the same change: `clu state dump [--plan <slug>]` … `clu quota clear` … the invariant is 'no documented escape hatch names a raw file that no longer exists'." — docs/design-briefs/sqlite-migration-scope.md:293-299
- "**Migration mechanics — DECIDED: start from scratch.** No JSON→DB auto-migration and no dual-read compat layer (single-operator install). Cutover = ship with the fleet quiet (no running plans/claims); existing JSON state files become inert legacy (archived plans stay frozen JSON, reference-only). Still required: `PRAGMA user_version` stamping from day one, and the tolerant-read contract extends to 'DB from a newer clu version → skip, never crash the fleet walk'." — docs/design-briefs/sqlite-migration-scope.md:300-308
- "**Dogfooding cutover risk:** clu orchestrates this repo using the exact storage layer being replaced." — docs/design-briefs/sqlite-migration-scope.md:310-311 (operational rule; resolved by the fleet-quiet execution arrangement in Status)

## Decisions settled at approval

All three of the surface-level questions the read-back surfaced are closed; none remains open.

1. **A tick that hits a precondition conflict stays quiet that round** (p6) — operator-confirmed 2026-08-19. Nothing was written, so nothing pings; if the trouble is real the next tick 30s later re-detects and pings then. The alternative (notify anyway) would re-ping every 30s for as long as the condition looked true, because the dedup marker lives in the discarded write. Cost is a bounded ~30s delay on a wedge alert in the uncommon case where a worker write lands inside the tick's inspection window.
2. **`synchronous=FULL`, not NORMAL** (p1) — settled by the plan's own premise rather than by preference: every write fsyncs today (`state.py:save_atomic` is tmp+fsync+rename), and a storage migration whose stated non-goal is behavior change should not quietly weaken a durability guarantee on the way through. NORMAL stays in the Parking lot as a documented, reversible perf option, to be taken deliberately if ever.
3. **Operator-facing surface defaults** — `clu state dump` with no `--plan` dumps every plan in the project (p3); `clu quota clear` prints the cleared signature or "no quota pause recorded" (p5); a `.orchestrator.json` still carrying `repair_command` gets a one-line stderr deprecation note rather than silence (p5); `clu doctor`'s registry line names the host database instead of `registry.json` (p2). Each mirrors what the surface it replaces already did.

## Non-goals

- **No third-party dependencies.** Stdlib `sqlite3` only (project rule, CLAUDE.md "Stack + run/test"). SQLite is already stdlib; precedent: chat.db is read via `sqlite3` today (`notify_imessage_inbound.py:468-471`).
- **Config files stay plain files**: per-project `.orchestrator.json`, global `~/.config/clu/config.json`, `worker-settings.json`. Safety sentence for this peer-set exclusion (exclusion specialist's invariant, verified against every writer): *every writer of these files is a one-shot operator command, never a tick* — no cron path, callback, or daemon writes them, so they never contend and never need transactions.
- **Logs, plan markdown, worker transcripts, and `.corrupt-*` historical backups stay files.** Same invariant: written once by one process, read by humans and `tail`.
- **No JSON data import.** Operator decision (upstream #6). Live and archived `*.state.json` are quarantined, not imported (p8); nothing post-p3 reads them.
- **No change to the worker-token security model.** The token stays the boundary (`--token` on every callback, CLAUDE.md conventions); its enforcement moves from `assert_claim_match` on a loaded dict to a `WHERE claimed_by=? AND phase_id=?` compare-and-set, which is the same predicate evaluated atomically.
- **No dashboard feature or output-format changes.** `clu top`/`clu serve` row dict is a frozen wire contract (D10, append-only — memory: clu-top TUI); `clu watch` line formats and the TASK_CREATE/TASK_UPDATE protocol are unchanged. Only the read mechanism changes.
- **No behavioral redesign of bundled skills.** clu-phase/clu-plan SKILL.md get path/mechanism reference updates only (p8). Safety sentence for the asymmetry against the docs, which ARE rewritten in that same phase: a doc is read by a human who can notice a stale sentence, while a bundled SKILL.md is EXECUTED verbatim by a cold worker — so changing a skill's instructions beyond the storage references it names would alter worker behavior in a plan whose whole premise is that behavior is unchanged.
- **Same-host only, local filesystem only.** WAL requires shared memory; network filesystems are explicitly unsupported (documented in p8's operations.md update).
- **The inbound chat.db reader is untouched.** It is already SQLite, read-only, and Apple's schema — not ours.

## Files touched (overview)

- end_of_line/db.py — p1 — NEW: connection factories, txn helpers, schema DDL, `DbBusy`
- tests/test_db.py — p1 — NEW: contention acceptance tests
- tests/demo_script.py — p1 — NEW: the deterministic demo sequence p3, p5, p6, p7 all replay
- tests/goldens/watch-demo.txt — p1 — NEW: pre-migration `clu watch` capture; diffed at p3, p6 and p7
- tests/test_demo_script.py — p1 — NEW: guards the driver's determinism and the golden's non-vacuity (added during p1; the approved Work list named the fixture and the golden but no home for the assertions about them)
- end_of_line/registry.py — p2 (internals → host DB; public signatures preserved), p3 (`load_entry_state`'s exists-gate), p7 (`load_entry_state` → snapshot)
- end_of_line/monitor.py — p2 — marker → host DB row
- end_of_line/notify_imessage_inbound.py — p2 (inbound cursor + outbound marks → host DB), p4 (`_shell_clu_answer` → blocker-answer op)
- end_of_line/notify_discord.py — p2 (DM cache → host DB), p3 (state-path handling in `_persist_metadata`), p4 (blocker-metadata op)
- end_of_line/notify_discord_inbound.py — p2 — DM cache + poll cursor → host DB
- end_of_line/inbox.py — p2 — dir-of-files → host-DB table; signatures preserved
- end_of_line/skill_sync.py — p2 — install receipt → host DB (batch writer `_record_installs`, shipped 79e2258 mid-draft)
- end_of_line/hooks/clu_inbox_surface.py — p2 — consumes inbox API (verify no file-path assumptions)
- end_of_line/hooks/clu_session_start.py — p2 — consumes registry API (verify only)
- end_of_line/plan_store.py — p3 (NEW: snapshot / mutate-compat facade / create), p4 (native write ops), p5 (`create_in_txn` for the pop), p6 (`snapshot_with_preconditions`, `apply_tick_delta`, `TickConflict`, `TickPreconditions`), p7 (remaining ops; facade deleted)
- end_of_line/state.py — p3 (engine swap under `mutate`/`load`; `stamp_activity_marker`), p4 (native ops), p7 (file primitives deleted)
- end_of_line/cli.py — p3 (`clu state dump`, init state-create), p4 (callback handlers → native ops), p5 (queue cmds, `clu quota clear`, retire `_handle_corrupt_queue`), p6 (`_tick_one_plan` / `cmd_tick` / `cmd_tick_all` verify-only), p7 (operator commands → ops)
- end_of_line/supervisor.py — p3 (zombie sweep → DB enumeration), p6 (tick restructure)
- end_of_line/dispatch.py — p3 (raw state read at :612), p4 (stamp/release ops), p5 (retire `dispatch_repair_worker`)
- end_of_line/cross_plan_rules.py — p3 (queue-pop state-create), p5 (queue ops, transactional pop), p7 (`_apply` → ops)
- end_of_line/notify.py — p2 (inbox-writer call sites), p5 (queue-corruption kinds/renderers deleted; `render_quota_stuck` escape hatch reworded)
- end_of_line/queue.py — p5 — ops on queue tables; repair machinery deleted
- end_of_line/quota.py — p5 (pause file → quota row; "row absent == not paused"), p6 (`record_quota_death`'s plan-event half becomes tick-delta events)
- end_of_line/watch.py — p3 (bootstrap raw read + event-id cursor), p7 (native queries + data_version gating)
- end_of_line/top.py — p7 — reads via snapshot; row contract unchanged
- end_of_line/top_registry.py — p7 — same
- end_of_line/webserver.py — p7 — same
- end_of_line/fleet.py — p7 — reads via snapshot
- end_of_line/state_locator.py — p7 — reads via snapshot
- end_of_line/heartbeat_daemon.py — p4 — `_ping` → native heartbeat op
- end_of_line/demo.py — p3 (state seeding via store), p7 (seeding via ops)
- end_of_line/config.py — p5 — `repair_command` accepted-but-inert note
- end_of_line/activity_hook.py — p3 — DbBusy drop-on-contention path (the 2s bounded window routes through `stamp_activity_marker`, which p3 re-points; p4 changes the op beneath it, not this file)
- tests/__init__.py — p3 — `state_path`/`_claim`/`_read` seam re-pointed at the store
- tests/ (15 raw-read files + 11 existence-assert files per the blast-radius report; the `_read` seam itself spans 18 files) — p2, p3, p4, p5 — mechanical updates named in each shard
- docs/contract.md, docs/architecture.md, docs/reference.md, docs/operations.md, docs/conventions.md — p8
- CLAUDE.md, CONTEXT.md — p8 — conventions ("with st.mutate…" bullet) and vocabulary
- README.md, examples/*.orchestrator.json — p8 — store-vocabulary sweep; `repair_command` deprecation note
- end_of_line/skills/clu-phase/SKILL.md, end_of_line/skills/clu-plan/SKILL.md — p8 — state-file path references
- Deleted: end_of_line/queue.py repair half (`best_effort_extract_*`, `validate_repair`, throttle fns) — p5; `cli.py:_handle_corrupt_queue` — p5; `dispatch.dispatch_repair_worker` — p5; `state.py` file primitives (`locked`, `locked_json`, `save_atomic`, `mutate` file engine, `LockTimeout`) — p7; `plan_store` mutate-compat facade — p7 (created p3, worked p3-p6, deleted p7: justification — it is the strangler seam that lets p3-p6 land as separate green commits; a one-phase big-bang port is the alternative it exists to avoid)

## Background findings

**Probed this session** (probe script in session scratchpad; run on BOTH the production interpreter Python 3.14.3/pipx and floor 3.11.15, SQLite 3.53.4, identical results):
- Deferred read→write upgrade after another connection's commit fails in 0.00s with `database is locked` — busy_timeout (2s and 5s tested) is NOT consumed. This is `SQLITE_BUSY_SNAPSHOT`; sqlite.org/c3ref/busy_handler.html: the busy handler is skipped when SQLite judges deadlock. **Every read-modify-write contained in one transaction must open it with `BEGIN IMMEDIATE`** (a snapshot-then-write across a subprocess gap cannot be, and is guarded by preconditions instead — p6).
- 8 processes × 25 contended read-modify-writes: `BEGIN IMMEDIATE` → exact total, zero busy retries; naive deferred → 1,506-3,316 busy escapes (and the specialist's 6×50 naive control lost 172/300 without a retry loop).
- `PRAGMA data_version` moves only on OTHER connections' commits (own commit does not move it) — pollers must hold a connection across frames.
- A reader holding an open transaction pinned the WAL past the autocheckpoint (819KB-12.8MB in probes); `wal_checkpoint(TRUNCATE)` returns it to 0 after release. **Pollers read in short transactions, never across frames.**
- Python `sqlite3` defaults: `connect(timeout=5.0)` ⇒ `busy_timeout=5000`; `journal_mode=delete`; `synchronous=FULL` (stays FULL after switching to WAL); `wal_autocheckpoint=1000`; `fullfsync=0`. 3.11 has no `Connection.autocommit` (3.12+; default `LEGACY_TRANSACTION_CONTROL`); with legacy control the module issues plain deferred `BEGIN` before DML only — so explicit `BEGIN IMMEDIATE` via `isolation_level=None` is required on every supported version.
- `mode=ro` URI connections read a WAL database fine on this host (shm present), and see consistent pre-commit state while a writer is mid-transaction.

**Doc/citation findings (C1/C2/specialist):** `synchronous=NORMAL` under WAL loses only power-loss durability, never consistency (sqlite.org/pragma.html §synchronous); macOS `fullfsync` off by default so even FULL does not force the drive cache (pragma.html; bonsaidb.io/blog/acid-on-apple/). Readers can hit brief SQLITE_BUSY in WAL during last-connection-close cleanup/crash-recovery — every connection gets a busy_timeout, readers included (sqlite.org forum; wal.html "Sometimes Queries Return SQLITE_BUSY"). Claim-via-UPDATE + lease expiry is the settled SQLite job-queue pattern (litequeue). Per-tenant DB files are favored precisely because each DB gets its own write lock (highperformancesqlite.com/watch/multi-tenancy) — supports the per-project split.

**Cross-phase code findings (Teams A/B):**
- Lock-free readers today depend on rename atomicity + an append-only, never-compacted `events` array: `watch.py:486,521` cursors on `len(events)`; `supervisor.py` and `cross_plan_rules.py:68` snapshot without the lock. The DB equivalents are WAL snapshot isolation + monotonic `events.id`.
- The implicit lock-nesting orders (queue→state→registry in `cross_plan_rules.py:201-219`; state→quota in `supervisor.py:614→637`) are convention, not mechanism; the DB replaces them with transactions per upstream decision #1.
- Two divergent attempt counters exist: `claim_phase` stores raw `phase_started` count (`state.py:745-752`) while `attempts_for_phase` applies retry floors + forgiveness (`state.py:1256-1276`); `clu top`'s ATT column shows the former, dispatch uses the latter. The migration preserves BOTH as-is (p3 facade) — unifying them is parked, not silently changed.
- `LockTimeout` is caught by name at the activity hook (2s budget, drop-on-contention: `state.py:1090-1122`) and the heartbeat-daemon death path; the DB layer must preserve bounded-wait-then-drop semantics.
- The state files are 0600 via mkstemp (`state.py:630`) and carry claim tokens; the DB files must be created 0600.
- Cold-context workers are handed the literal state.json path to inspect (`end_of_line/skills/clu-phase/SKILL.md:22`) — `clu state dump` (p3) + SKILL.md updates (p8) replace that read path.
- Archived plans' state files live in the SAME directory as live ones — archive moves only markdown — `_perform_archive` git-mv's `<plan>.md` and `<plan>-*.md` and nothing else (`cli.py:5844-5900`; `cmd_archive` at `:5922`), and its own docstring calls what stays behind "the orphaned state file after archive"; the zombie sweep globs `*.state.json` skipping registered slugs (`supervisor.py:977-1007`), so post-cutover it would chew legacy files: the sweep switches to DB enumeration in p3, and p8 quarantines the legacy files.
- Test blast radius (specialist, counted; re-counted this session): 135 `tests/test_*.py` modules; choke points `tests/__init__.py:272,286-291` (`state_path`, `_claim`, `_read` — 105 `_read` call sites across 18 files); 35 files call `st.load`, 33-34 seed via `save_atomic`; 15 files raw-`read_text()` state/registry/queue/quota (heaviest `test_dispatch.py` ×10, `test_supervisor.py` ×4, `test_zombie_sweep.py` ×3); 11 files assert file existence (`test_queue_add.py` ×6, `test_queue_worker_dispatch.py` ×4). Only TWO tests exercise the locking mechanism itself — `test_state.py:552-573` (LockTimeout via a real flock) and `test_activity_callback.py:166-186` (a subprocess holds the flock; the update is dropped). Two more, `test_state.py:55-59` and `:305-313`, test atomic-rename rather than locking and were miscounted as lock tests in the research report. All four pass-or-vanish, so p1's contention tests are the replacement coverage, not an optional extra.
- `scripts/partest.py` shards one subprocess per module and relies on per-test XDG/tmp isolation; a per-tmp-dir DB preserves that (a global DB would break it — the design has none).

## Done criteria

- Full canonical gate green: `python3 -m unittest discover -s tests` (report the count), and `clu verify`'s basedpyright gate clean.
- A fresh `clu demo up` fleet runs its full lifecycle (dispatch → heartbeats → blocker → completion) with **zero** `*.state.json`, `*.lock`, `queue.json`, or `quota.json` files created anywhere — verified by find over the demo project and `~/.config/clu` (test-isolated).
- `clu watch` on the demo emits the same event lines the JSON backend emitted for the same script — diffed against the golden capture p1 records from the pre-migration backend.
- The only surviving `import fcntl` under `end_of_line/` is `_pty_spawn_shim.py:70`, which uses it for terminal sizing (`ioctl(TIOCSWINSZ)`), never for locking — `state.py`'s import is gone. (Stated this way because a bare "no fcntl anywhere" criterion can never pass: the shim's use is legitimate and untouched by any phase.)
- Every documented operator escape hatch resolves: `clu state dump`, `clu quota clear` exist and are named in the docs that used to name raw files (p8 sweep).
- Legacy stores quarantined: `plans/.orchestrator/` on this repo contains `clu.db` (plus its `-wal`/`-shm` siblings when a connection is or was open), `logs/`, and `legacy/` — nothing else.

## Parking lot

- Unify the two attempt counters (`claim_phase` stored field vs `attempts_for_phase` projection) — surfaced by B1/B2, deliberately NOT changed in this plan.
- `synchronous=NORMAL` perf option (documented-only; plan ships FULL for parity with today's fsync guarantee).
- **`clu watch` always prints `attempt 1`** — surfaced at p1, deliberately NOT fixed in this plan. `claim_phase` computes the attempt count onto the claim but never puts it in the `phase_started` event (`state.py:762`), while the formatter falls back to 1 (`watch.py:121,334`). Fixing it is a behavior change in a plan whose premise is that behavior is unchanged, and it would contaminate the pre-migration golden that p3, p6 and p7 diff against. p4 and p6 carry Done criteria keeping the event as-is; this is the follow-up after the plan ships.
