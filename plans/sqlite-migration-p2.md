# sqlite-migration-p2 — host DB: registry, monitor, sidecars, inbox, skill receipt

You are phase `p2` of the `sqlite-migration` plan. This phase delivers, as one commit, every `~/.config/clu/*.json` store moved into the host database (`~/.config/clu/clu.db`). Public function signatures are preserved wherever a caller depends on them, so the consumer edits are limited to the four named in Work: the callers passing `inbox=`, the callers of the deleted `registry_path()`, the inbox hook (which moves to the single-transaction claim call), and the Discord notifier's constructor plumbing. The per-project stores (plan state, queue, quota) are later phases.

## Locked decisions (do NOT re-litigate)
See the master `plans/sqlite-migration.md`. The decisions binding this phase:
- Host DB at `~/.config/clu/clu.db` via `db.host_db_path()` / `db.ensure_host_schema` (p1).
- **Inbox migrates to a table** — operator decision (upstream #3), with a `processed` flag replacing the move-to-`processed/` protocol, and the hook reading + flagging in ONE transaction (the decision says so literally): `claim_for_project` is the API that does both, and the hook uses it instead of the read-then-mark pair. Research counterpoint recorded in Decisions below; the operator decision stands.
- No JSON import: existing `registry.json`, `monitor.json`, `inbound_state.json`, `outbound_pending.json`, discord sidecars become inert (quarantined in p8). Fleet-quiet precondition means an empty registry at first run is correct, not data loss.
- Every write is `db.write_txn` (BEGIN IMMEDIATE); reads are short `db.read_txn` or single statements.
- Tolerant-read contract: a missing DB row degrades exactly like a missing file did (empty registry, `None` marker, cursor 0); `db.SchemaTooNew` degrades like `SchemaVersionMismatch` does today (skip / None, never crash a fleet walk).

## Work

### Task 1 — registry, monitor, skill receipt
- end_of_line/registry.py — keep `PlanEntry`, `entries()`, `entries_for_project()`, `register()`, `unregister()`, `load_entry_state()` signatures byte-identical; internals become host-DB queries (`registry` table). `register` keeps returning False on duplicate (INSERT OR IGNORE + rowcount), keeps `validate_slug` + `project_root.resolve()` + is_dir check. `registry_path()` is deleted (its only external caller set is enumerated by grep in this phase and re-pointed; tests use `isolate_registry` which re-points env, still valid because `host_db_path` is XDG-derived).
- end_of_line/monitor.py — `load_marker`/`is_scheduled`/`record_hook_installed`/`record_session_start_installed`/`clear_marker` same signatures over the `monitor` k/v table. The v1-marker legacy branch dies with the file (no marker rows == not scheduled).
- end_of_line/skill_sync.py — the install receipt over the `skills` table. NOTE: this landed a batch writer while this plan was being drafted (commit 79e2258) — `record_install` (`skill_sync.py:218-224`) now delegates to `_record_installs` (`skill_sync.py:227+`), which takes ONE lock and ONE rewrite for a whole `repair()` run. Both keep their signatures; `_record_installs` becomes one `db.write_txn` over N rows, which is the same batch guarantee it has today.
- Consumes: `db.connect(path, *, readonly=False, timeout_s=5.0)`, `db.write_txn(conn, *, timeout_s=None)`, `db.read_txn(conn)`, `db.host_db_path() -> Path`, `db.ensure_host_schema(conn)`, `db.SchemaTooNew` (p1); `st.validate_slug(slug, *, kind)` (exists, `state.py:46`)
- Produces: the preserved public surface later phases call — `registry.entries() -> list[PlanEntry]`, `registry.entries_for_project(project_root) -> list[PlanEntry]`, `registry.register(project_root, plan_slug) -> bool`, `registry.unregister(project_root, plan_slug) -> bool`, `registry.load_entry_state(entry) -> dict | None` (all signature-identical to today, now host-DB backed; p5 and p7 consume them)

### Task 2 — iMessage + Discord sidecars
- end_of_line/notify_imessage_inbound.py — `read_inbound_state`/`write_inbound_state` become row ops on `inbound_state` + `outbound_floors` (the dict shape `{last_inbound_rowid, outbound_rowids}` is preserved for the poller's in-memory cache at `notify_imessage_inbound.py:489-537`); `append_outbound_mark`/`drain_outbound_marks` move to the `outbound_marks` table — append stays multi-writer-safe (single INSERT txn), drain deletes resolved/expired marks in the same txn it reads them. `inbound_state_path()`/`outbound_pending_path()` die; the legacy `_drop_legacy_seen` cleanup stays (it targets a pre-JSON file).
- end_of_line/notify_discord.py — `_load_dm_cache`/`_save_dm_cache` (today unlocked read-modify-write, `notify_discord.py:113-130`) → `discord_dm_cache` row; the constructor's `state_path` plumbing becomes a host-DB handle (test seam: accept an injected db path).
- end_of_line/notify_discord_inbound.py — same DM cache + `_read_cursor`/`_write_cursor` (`notify_discord_inbound.py:178-188`) → `discord_cursor` rows. This closes the unlocked-RMW race A3 ranked most silent.
- Consumes: `db.write_txn(conn, *, timeout_s=None)`, `db.read_txn(conn)`, `db.host_db_path() -> Path` (p1)
- Produces: none (public signatures preserved)

### Task 3 — inbox + its hook consumer
- end_of_line/inbox.py — `write_event(type, plan_slug, project_root, summary, details, inbox=None) -> str`, `read_unprocessed`, `mark_processed(event_id)`, `list_for_project(project_root)` keep signatures; rows in the `inbox` table, ordered by autoincrement id (replaces nanosecond-filename lexical ordering); `mark_processed` = `UPDATE inbox SET processed=1, processed_at=? WHERE event_id=? AND processed=0` — atomic, closing the two-sessions-race on `os.rename` (A3: `inbox.py:124` uncaught). NEW `claim_for_project(project_root, *, limit) -> list[dict]` selects this project's unprocessed rows AND flags them processed inside one `db.write_txn`, which is what upstream decision #3 specifies. **The `limit` is load-bearing, not decoration:** the hook renders at most `MAX_EVENTS = 20` (`clu_inbox_surface.py:31`) and today marks processed ONLY the events it actually surfaced (`:265`) — a claim-everything call would silently consume events the operator never saw. The hook passes its own cap, and the rows it does not claim stay unprocessed for the next turn. The `inbox=` keyword arg is re-typed as a host-DB override handle for test isolation. **Verified this session: nothing in `end_of_line/` or `tests/` actually passes it** — what `notify.py:128,141` injects is `inbox_writer`, a callable seam, not a path — so this is a signature-compatibility change with zero call sites to update, not a migration.
- end_of_line/hooks/clu_inbox_surface.py — switch from `inbox.list_for_project` + per-event `mark_processed` (`clu_inbox_surface.py:238-265`) to one `claim_for_project(project_root, limit=MAX_EVENTS)` call, preserving today's cap-and-only-mark-what-was-shown behavior (`:31`, `:188-193`, `:265`). The fail-open contract (exit 0 on any error) is unchanged.
- end_of_line/hooks/clu_session_start.py — consumes `registry.entries`/`load_entry_state` only; verify-only.
- tests: `tests/` files exercising registry/monitor/inbox/sidecar behavior update their seeding to go through the (unchanged) public APIs instead of writing JSON files; the master's blast-radius counts name the raw-read offenders — the ones in THIS phase's stores are updated here, plan-state test files wait for p3.
- Consumes: `db.write_txn(conn, *, timeout_s=None)`, `db.read_txn(conn)`, `db.host_db_path() -> Path` (p1)
- Produces: `inbox.claim_for_project(project_root, *, limit: int) -> list[dict]` (read + flag the capped set in one transaction)

## Decisions & findings

### Decision: inbox migrates to a table despite A2/A3 recommending it stay a directory  *(status: active)*
- **Rationale:** operator decision (upstream #3). The research concerns are answerable: (a) "consumers are foreign-session hooks" — the hooks import `end_of_line` (`clu_inbox_surface.py:27`) and reach the host DB exactly as they reach `~/.config/clu/inbox/` today; (b) "rename-dedup is its contract" — the dir version's dedup has an uncaught race when two sessions mark the same event (`inbox.py:124`, A3 finding); `UPDATE … WHERE processed=0` is strictly stronger.
- **Alternatives considered:** keep dir-of-files (A2: "already crash-safe and ordering-correct"; true, but it is also the only store left un-migrated and the ordering trick — 19-digit ns filenames — is exactly the hand-rolled machinery this plan retires).
- **Evidence:** inbox.py:43-134; A2/A3 reports (plan-time research, transcribed in master Background findings).

### Decision: `registry_path()` deleted rather than aliased  *(status: active)*
- **Rationale:** a path-returning function whose file no longer exists is a trap for future callers; the tolerant contract lives in `entries()` now.
- **Alternatives considered:** keep returning the legacy path for doctor display — doctor output is updated instead (p8 owns the doc sweep; the code reference dies here).
- **Evidence:** registry.py:31-34 and its callers (grep this phase).

## Failure modes to anticipate
- `tests.isolate_registry` (per CLAUDE.md conventions) currently isolates the registry FILE via env; confirm it isolates `clu_config_dir()` itself (it does — XDG env), so the host DB lands in the tmp dir. A missed seam pollutes the real `~/.config/clu/clu.db` — `assert_xdg_safe` is the backstop.
- The Discord notifier is constructed per send (`notify.py:175`); opening a host-DB connection per notification is fine (short-lived writers are the supported shape) but the DM cache read must not hold a txn across the HTTP request.
- The inbound poller daemon caches inbound state in memory and writes once per poll (`notify_imessage_inbound.py:497-537`) — preserve the cache; do not turn every poll into a write.
- `entries()` is the hottest read in the system (top/serve read it every 1.5s frame) — it must be a single SELECT on a short-lived or held read connection, never a txn held across frames (WAL-pinning probe, master Background findings).
- Hooks run in arbitrary Claude sessions possibly under an OLD installed clu while this phase is uncommitted — covered by the fleet-quiet + LaunchAgents-unloaded precondition (master Status).

## Done criteria
- Full suite green.
- Observable: a scripted end-to-end in tests — `inbox.write_event` → `clu_inbox_surface` hook main() over a seeded host DB prints the rendered system-reminder payload and claims the event in ONE transaction (asserted via `read_unprocessed` going empty); the same event is NOT re-surfaced on a second hook run, and two hook runs racing the same event surface it exactly once.
- Observable: `registry.register` → `entries_for_project` → `unregister` round-trip leaves `~/.config/clu` (test-isolated) containing `clu.db` and NO `registry.json` / `*.lock`.
- Observable: `clu install-hook` writes the monitor marker to the host DB and `monitor.is_scheduled()` reads True from a fresh process; `clear_marker` makes it False. (Without this the monitor migration ships with no exit condition at all.)
- `notify_discord_inbound` cursor survives a poller restart (write cursor, new instance reads it back) — the regression the unlocked file version could lose.
