# sqlite-migration-p7 — dashboards on native reads; delete the facade and the file primitives

You are phase `p7` of the `sqlite-migration` plan. This phase delivers, as one commit, the read side rebuilt on native queries — `clu watch` on event-rowid cursors with `data_version` change detection, top/serve/fleet/locator on snapshots — and then deletes the scaffolding the migration no longer needs: the `mutate_compat` facade and `state.py`'s file-lock primitives. After this commit no clu code path opens a `.state.json`, takes a flock, or rewrites a whole store.

## Locked decisions (do NOT re-litigate)
See the master `plans/sqlite-migration.md`. The decisions binding this phase:
- Output formats are frozen: `clu watch` line formats and TASK protocol, the top/serve row dict (D10 append-only wire contract), fleet's table. This phase changes HOW data is read, never what renders.
- Pollers hold ONE connection across frames and check `PRAGMA data_version` before re-querying (probed: it moves only on other connections' commits, so a held connection is required for it to mean anything) — but they NEVER hold a transaction across frames (probed: a held read txn pins the WAL unboundedly).
- The facade and file primitives are deleted HERE, per the master's Files-touched deletion entries: `plan_store.mutate_compat`, and `state.py`'s `locked`, `locked_json`, `mutate` (file engine), `save_atomic`, `LockTimeout`. Deleting them is the proof no caller remains.
- Remaining `mutate_compat` callers at phase start (operator commands in cli.py, cross-plan `_apply`, demo seeding — everything p4 deliberately left) convert to native ops or snapshot+ops in THIS phase; that conversion is Task 1's bulk.

## Work

### Task 1 — convert the remaining writers, then delete the seam
- end_of_line/cli.py — the operator-command writers p4 left on the facade: `cmd_pause`/`cmd_resume`/`cmd_retry`/`cmd_extend_lease`/`cmd_release_claim`/`cmd_force_complete`/`cmd_prior_blocker`'s read path (`cli.py:4095-4130`, read-only — confirm it needs no op), `cmd_ship`'s state stamps, `cmd_archive`'s status flip, worktree attach/gc state edits — each becomes snapshot-read + the matching `plan_store.op_*` (new small ops where none fits: `op_set_worktree`, `op_extend_lease`, `op_reset_attempts_event` — same one-txn shape as p4's).
- end_of_line/cross_plan_rules.py — `_apply` (`cross_plan_rules.py:93-100`) → `op_append_events` + `op_set_fields` per plan; rule reads use `plan_store.snapshot`.
- end_of_line/demo.py — seeding via ops.
- end_of_line/plan_store.py — delete `mutate_compat` **and `write_full`**; add the small ops above. `write_full` did not exist when this shard was written: p3 added it as the store-side meaning of `save_atomic` (which this phase deletes on the next line), purely so 68 test-seeding sites across 34 files kept working through the engine swap. It has no production caller and nothing to serve once `save_atomic` is gone, so leaving it is the orphaned-helper trap p2 deleted `registry_path()` to avoid. `exists_for_path` STAYS — it is the path-keyed gate 21 live call sites use.
- end_of_line/state.py — delete `locked`, `locked_json`, `mutate`, `save_atomic`, `load`'s file branch, `LockTimeout`, `stamp_activity_marker`'s file plumbing (its op form lives since p4). What REMAINS in state.py: the domain layer over dicts (event constants, `validate_slug`/`SLUG_PATTERN`, projection helpers `attempts_for_phase`/`completed_phase_ids`/`status_reason`/`latest_event`/`open_blockers`, claim predicates `claim_is_stalled`/`heartbeat_age_seconds`, process liveness `claim_worker_alive`/`reap_orphan_pgroup`/`_cmdline_marker_present`, git probes, `utcnow` helpers) — these all operate on snapshot dicts and stay byte-compatible.
- Consumes: `plan_store.snapshot(orch_dir, slug) -> dict` (p3); `plan_store.op_append_events`, `op_release_claim`, `op_set_status`, `op_stamp_claim_fields`, `op_archive_events` (p4)
- Produces: `plan_store.op_set_fields(orch_dir, slug, fields: dict)`; `plan_store.op_set_worktree(orch_dir, slug, worktree: dict | None)`; `plan_store.op_extend_lease(orch_dir, slug, *, phase, minutes) -> str`

### Task 2 — watch on rowid cursors + data_version
- end_of_line/watch.py — `stream_loop` (`watch.py:452-542`): the max-event-id cursor is already in place (p3); this phase makes the read native — each poll holds the plan-DB connection open, checks `PRAGMA data_version`, and only when it moved runs `SELECT id, ts, type, payload FROM events WHERE plan_slug=? AND id>? ORDER BY id`. Baseline snapshot + TASK bootstrap read `plan_store.snapshot`. Formatters/`project_event`/`project_event_task` untouched. The tolerant skip on unreadable state (cursor pop, `watch.py:516-518`) maps to too-new-schema / missing-DB skip.
- Consumes: `db.connect(path, *, readonly=True, timeout_s=5.0)`, `db.read_txn(conn)` (p1); `plan_store.snapshot` (p3); events table columns `(id, plan_slug, ts, type, payload)` (p1 DDL)
- Produces: none (line formats frozen)

### Task 3 — top / serve / fleet / locator / registry reads
- end_of_line/top.py, end_of_line/top_registry.py — `gather_rows` (`top.py:495-560`) reads plan state via `registry.load_entry_state` (below) — row assembly unchanged; transcript reading untouched.
- end_of_line/webserver.py — `/api/plans`-side reads via the same path (`webserver.py:502,530`); `/api/feed` transcript resolution unchanged.
- end_of_line/fleet.py, end_of_line/state_locator.py — `summarize_plan` / `_load_open_blockers` consume `registry.load_entry_state`, which internally becomes `plan_store.snapshot` (its tolerant None-on-any-failure contract, `registry.py:87-107`, preserved for missing project dir / missing plan row / too-new schema).
- end_of_line/registry.py — `load_entry_state` re-pointed at snapshot (one seam carries fleet, top, serve, locator, hooks).
- tests — reader families re-point seeding; the golden-output test re-runs the scripted demo sequence and diffs `clu watch`'s lines against `tests/goldens/watch-demo.txt` (recorded at p1 from the pre-migration JSON backend).
- Consumes: `plan_store.snapshot(orch_dir, slug) -> dict` (p3)
- Produces: none (row dict D10 contract frozen)

## Decisions & findings

### Decision: `registry.load_entry_state` stays the single fleet-read seam  *(status: active)*
- **Rationale:** fleet, top, serve, locator, and the SessionStart hook all funnel through it today (A1 fan-in); re-pointing one function migrates five surfaces with one tolerance contract to test.
- **Alternatives considered:** per-surface native queries (five copies of the tolerance rules; premature optimization — snapshot cost is one indexed read per plan, vs today's full-file JSON parse).
- **Evidence:** registry.py:87-107; top.py:514; webserver.py:530; fleet.py:30; hooks/clu_session_start.py:112.

### Decision: watch keeps 1s polling cadence (data_version-gated), not push  *(status: active)*
- **Rationale:** the Monitor-tool contract is a polling stream; data_version makes the idle poll two PRAGMA statements instead of N full-file parses, which is the entire performance complaint. Push (WAL-hook, honker-style) needs a resident process clu doesn't have.
- **Alternatives considered:** file-watching the -wal (fragile, undocumented); shorter/adaptive cadence (out of scope, formats frozen).
- **Evidence:** probe — data_version semantics; C2 prior-art (honker polls data_version at 1ms; 1s is generous).

## Failure modes to anticipate
- Deleting `save_atomic`/`locked` breaks any straggler caller the greps missed — the deletion IS the detector (import errors), but check non-imported textual references too: docs (p8), and `webserver.py:147`'s comment citing `state.save_atomic`.
- `watch` holding a read-only connection per plan DB across a long stream: connections are cheap but N plans × 1 connection must be bounded — one connection per PROJECT DB (plans share it), keyed like the cursors.
- A plan archived mid-stream moves its events to `events_archive` (p4) — the watch cursor sees `events` shrink to zero; max-id cursors are monotonic and must never rewind on a shrink (the reason p3 moved watch off length cursors; add a test that archival mid-stream emits nothing).
- `top`'s 1.5s loop constructs registry reads per frame — the host-DB connection should also be held per process with data_version gating (same rule as watch; do it in `registry.entries` callers or accept per-frame SELECT — one small indexed table, acceptable; decide by measurement in-phase and record).
- The SessionStart hook runs in foreign sessions with whatever clu is installed — after this phase the editable install serves the new read path to every hook invocation; fleet-quiet precondition still stands until p8 ships.

## Done criteria
- **Carried in from p4's sweep — a test asserting a timestamp column equals a just-computed value MUST backdate first.** `st.utcnow()` is second-resolution: three consecutive calls return the identical string, so a test that seeds a row and immediately asserts `row[field] == op(...)` compares a value to itself and passes whatever the op wrote, including nothing and including the wrong column. p4 proved this rather than supposing it — breaking `op_heartbeat` to write the neighbouring column left all 53 tests in its own file green and the full suite green. Observable: any new test in this phase asserting on a written timestamp seeds a distinctly old value first, asserts the value ADVANCED, and asserts the neighbouring column is untouched.
- **Carried in from p4's sweep — no git subprocess runs inside an open transaction.** `_maybe_cleanup_worktree` shells out to git several times from inside a `st.mutate` window, reached from `cmd_complete` and `_perform_archive`; p4 moved the `cmd_complete` copy after its commit but could not restructure the archive path without a plan-field op it did not own. That is the project-wide lock held across foreign work — p2's finding, and the exact shape this migration exists to remove. Observable: a trace or a read of the archive path shows no `subprocess` call between BEGIN and COMMIT.
- **Escalated at p3 — every tolerant `except` clause this phase touches or adds is checked against what the STORE can raise.** This class has now recurred twice: p2 found eight `except OSError` guards that a database failure would have walked straight through, and p3 widened four more and STILL missed the zombie sweep's two, which its review caught. The shape is always the same — a clause that was complete for a FILE is incomplete for a database, because a broken store arrives as `sqlite3.Error`, contention as `db.DbBusy` (a `RuntimeError`, so `except OSError` misses it), and a newer schema as `db.SchemaTooNew`. The check: for every `except` guarding a store read or write in this phase's files, say which clauses were examined, which were widened, and which were deliberately left narrow and why. `db.DEGRADABLE_ERRORS` is the tuple for "degrade rather than fail"; it deliberately excludes the `RuntimeError` raised when WAL did not take.
- Observable: golden-diff test — `clu watch` output for the scripted demo sequence is byte-identical to `tests/goldens/watch-demo.txt`, the pre-migration capture recorded at p1. Capture through `demo_script.capture_watch_lines`, which pins the projection: the golden is the DEFAULT TEXT output, and `--json` is a different projection that will never match it (p1 decision).
- Observable: an idle-poll trace shows the watch loop executing only PRAGMA data_version (no events SELECT) on ticks where nothing changed.
- `grep -rn "import fcntl" end_of_line/` returns exactly ONE hit — `_pty_spawn_shim.py:70`, which uses it for `ioctl(TIOCSWINSZ)` terminal sizing and has nothing to do with locking; `state.py`'s import is gone. `grep -rn "save_atomic\|locked_json\|LockTimeout" end_of_line/ --include="*.py"` → no live references (comments cleaned).
- `clu top` and `clu serve` render the scripted demo fleet with the same rows/columns as before: the capture must show at least one worker row with a non-empty plan, phase and status cell (a dashboard rendering zero rows also 'matches' a comparison that never asserts presence). Manual capture, noted in Status.
- **Carried in from p1's sweep — a poller that drops on contention catches BOTH `db.DbBusy` and `sqlite3.OperationalError`.** `read_txn` translates a busy at its own `BEGIN`, but a deferred BEGIN acquires nothing: the read snapshot is taken by the caller's FIRST statement inside the block, so a busy there (WAL's last-connection-close cleanup, which `db.connect`'s own contract warns readers about) arrives as SQLite's error and `read_txn` never sees it. Every reader this phase re-points is a poller. Assert it: a read path that meets a busy on its first statement degrades rather than raising.
- Full suite green; basedpyright clean.
