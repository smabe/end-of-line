# sqlite-migration-p1 — SQLite core: `db.py`, both schemas, contention acceptance test

You are phase `p1` of the `sqlite-migration` plan. This phase delivers, as one commit, the shared SQLite layer every later phase builds on: connection factories with the correct pragmas, `BEGIN IMMEDIATE` transaction helpers with bounded-wait semantics, the DDL for both databases, and — FIRST, before any of that is trusted — the cross-process contention acceptance test that proves the discipline works on this host.

## Locked decisions (do NOT re-litigate)
See the master `plans/sqlite-migration.md`. The decisions binding this phase:
- Stdlib `sqlite3` only; no third-party deps.
- Two databases: per-project `plans/.orchestrator/clu.db`, host `~/.config/clu/clu.db` (upstream decision #1).
- Every WRITE transaction opens with `BEGIN IMMEDIATE`; a read-modify-write contained in one transaction takes it directly, while a snapshot-then-write pattern (the p6 tick, `cmd_complete`) reads outside the transaction and guards its write with a compare-and-set. Probed this session: a deferred read→write upgrade after a concurrent commit fails in 0.00s with a busy_timeout-immune `database is locked` (SQLITE_BUSY_SNAPSHOT), while BEGIN IMMEDIATE + busy_timeout gave 8×25 lossless contended increments with zero busy retries (master "Background findings").
- `journal_mode=WAL`, `synchronous=FULL` (parity with today's per-write fsync in `state.py:save_atomic`; NORMAL is parked), `busy_timeout` explicit on every connection **including read-only ones** (WAL readers can hit brief SQLITE_BUSY during last-connection-close cleanup — sqlite.org wal.html "Sometimes Queries Return SQLITE_BUSY").
- `isolation_level=None` on every connection: Python 3.11's legacy transaction control issues implicit deferred BEGINs before DML, which is exactly the upgrade-deadlock shape; the plan controls transactions explicitly. 3.12+ `autocommit` attribute is NOT used (absent on the 3.11 floor — probed).
- `PRAGMA user_version` stamped at schema creation; a DB whose user_version is NEWER than the running clu's schema constant is treated as unreadable-tolerant (skip, never crash a fleet walk) — upstream decision #6.
- DB files created 0600 (state files are 0600 today via mkstemp, `state.py:630`, and carry claim tokens).

## Work

- tests/test_db.py — NEW. Written FIRST (TDD + the load-test placement rule). Three test families:
  1. **Contention acceptance (the phase gate):** spawn 8 OS processes (`multiprocessing` spawn context, matching the probe) each doing 25 read-modify-write increments of one row through `db.write_txn`; assert the final value is exactly 200 and zero unhandled `DbBusy` escapes. This reproduces the plan-time probe: 8×25 through BEGIN IMMEDIATE gave 200/200 with zero busy retries, while the same workload on deferred transactions needed 1,506-3,316 busy retries to reach the same total and, without a retry loop, loses increments outright (master "Background findings").
  2. **Bounded-wait drop:** process A holds a write txn open; `write_txn(timeout_s=0.2)` in the parent raises `DbBusy` in <1s — the replacement for today's `LockTimeout` drop-on-contention contract (`state.py:1090-1122`).
  3. **Schema/versioning:** `ensure_project_schema` / `ensure_host_schema` are idempotent; `user_version` stamped; opening a DB with `user_version` greater than the code's constant raises `SchemaTooNew`; file mode is 0600.
- end_of_line/db.py — NEW. The shared core. Work-shape sketch (interface half is contract):
  ```python
  PROJECT_SCHEMA_VERSION = 1
  HOST_SCHEMA_VERSION = 1
  DB_FILENAME = "clu.db"          # lives in plans/.orchestrator/ (project) or clu_config_dir() (host)

  class DbBusy(RuntimeError): ...        # bounded-wait exceeded (busy_timeout expired)
  class SchemaTooNew(RuntimeError): ...  # user_version > code constant

  def project_db_path(orchestrator_dir: Path) -> Path
  def host_db_path() -> Path             # clu_config_dir()/clu.db, assert_xdg_safe'd
  def connect(path: Path, *, readonly: bool = False, timeout_s: float = 5.0) -> sqlite3.Connection
      # isolation_level=None; PRAGMA busy_timeout, journal_mode=WAL (rw only),
      # synchronous=FULL, foreign_keys=ON; os.chmod 0600 on create
  @contextmanager
  def write_txn(conn, *, timeout_s: float | None = None) -> Iterator[sqlite3.Cursor]
      # BEGIN IMMEDIATE; commit on exit, rollback on exception;
      # sqlite3.OperationalError 'database is locked' -> DbBusy
  @contextmanager
  def read_txn(conn) -> Iterator[sqlite3.Cursor]   # plain BEGIN; always rollback (never hold)
  def ensure_project_schema(conn) -> None
  def ensure_host_schema(conn) -> None
  ```
  Grounding: `isolation_level=None` + explicit BEGIN per docs.python.org/3.11 §Transaction control (specialist report, probed); `clu_config_dir`/`assert_xdg_safe` exist at `end_of_line/_xdg_guard.py` (used by `registry.py:19,32-34`).
  Project DDL (body illustrative; table/PK names are contract because later shards' Consumes lines repeat them):
  ```sql
  plans(slug TEXT PRIMARY KEY, status TEXT, plan_dir TEXT, created_at TEXT,
        batch_id TEXT, version INTEGER NOT NULL DEFAULT 0,   -- reader-facing change hint (NOT the tick's guard; see p6)
        config TEXT, phases TEXT, worktree TEXT, extra TEXT) -- JSON-valued columns
  claims(plan_slug TEXT PRIMARY KEY REFERENCES plans(slug), phase_id TEXT, claimed_by TEXT,
         lease_expires TEXT, started_at TEXT, last_heartbeat_at TEXT, attempts INTEGER,
         pid INTEGER, pgid INTEGER, log_path TEXT, session_id TEXT,
         active_tool_started_at TEXT, flags TEXT, attestations TEXT, cpu_samples TEXT)
  blockers(plan_slug TEXT, blocker_id TEXT, phase_id TEXT, type TEXT, question TEXT,
           options TEXT, context TEXT, asked_at TEXT, answer TEXT, answered_at TEXT,
           consumed INTEGER DEFAULT 0, last_repinged_at TEXT, notify_metadata TEXT,
           PRIMARY KEY (plan_slug, blocker_id))
  spawned_tasks(plan_slug TEXT, task_id TEXT, status TEXT, payload TEXT,
                PRIMARY KEY (plan_slug, task_id))
  events(id INTEGER PRIMARY KEY AUTOINCREMENT, plan_slug TEXT, ts TEXT, type TEXT, payload TEXT)
    + INDEX (plan_slug, id), INDEX (plan_slug, type)
  events_archive(id INTEGER, plan_slug TEXT, ts TEXT, type TEXT, payload TEXT, archived_at TEXT)
  queue(id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT, added_at TEXT, added_by TEXT, batch_id TEXT)
  queue_history(id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT, added_at TEXT, added_by TEXT,
                batch_id TEXT, ended_at TEXT, outcome TEXT)
  quota(id INTEGER PRIMARY KEY CHECK (id = 1), paused_until TEXT, signature TEXT, line TEXT,
        canary_plan TEXT, canary_deadline TEXT, created_at TEXT)   -- row absent == not paused
  ```
  Host DDL:
  ```sql
  registry(project_root TEXT, plan_slug TEXT, registered_at TEXT, PRIMARY KEY (project_root, plan_slug))
  monitor(k TEXT PRIMARY KEY, v TEXT)                    -- marker fields as rows
  inbound_state(k TEXT PRIMARY KEY, v TEXT)              -- last_inbound_rowid
  outbound_floors(chat_id TEXT PRIMARY KEY, floor_rowid INTEGER)
  outbound_marks(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, sent_at REAL)
  discord_dm_cache(user_id TEXT PRIMARY KEY, channel_id TEXT)
  discord_cursor(channel_id TEXT PRIMARY KEY, message_id TEXT)
  inbox(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, ts TEXT, type TEXT,
        plan_slug TEXT, project_root TEXT, summary TEXT, details TEXT,
        processed INTEGER DEFAULT 0, processed_at TEXT)
  skills(name TEXT PRIMARY KEY, digest TEXT)
  ```
- tests/demo_script.py — NEW. The scripted demo sequence itself, since p3, p6 and p7 all replay it and none of them can define it: a deterministic driver that brings up a demo project, dispatches one phase, emits a heartbeat, opens and answers a blocker, and completes the plan — built on the existing `clu demo` machinery (`end_of_line/demo.py`, `demo_worker.py`) with fixed slugs and no wall-clock dependence, so two runs differ only where a timestamp is deliberately elided from the golden.
- tests/test_demo_script.py — NEW. **Not in the Work list as approved; added during the phase.** The driver above is a fixture, not a test, and unittest only discovers `test*.py` — so the two claims this phase makes about it (that it is deterministic, and that the golden is non-vacuous) had nowhere to be asserted. The planning defect: the Work list named a fixture and a golden and assumed the Done criteria asserting them would live somewhere, without naming where. `test_db.py` was not the answer either — the phase defines it as exactly three families.
- tests/goldens/watch-demo.txt — NEW. The pre-migration golden: run that scripted sequence against the CURRENT JSON backend (untouched at this point in the phase — `db.py` is additive and nothing routes through it yet) and record `clu watch`'s emitted lines. This is the only moment in the plan where the JSON backend still exists to be captured; p3 and p7 diff against this file.
- Consumes: `clu_config_dir() -> Path`, `assert_xdg_safe(path) -> None` (both exist; used identically by `registry.registry_path`)
- Produces: `db.connect(path, *, readonly=False, timeout_s=5.0) -> sqlite3.Connection`; `db.write_txn(conn, *, timeout_s=None)` ctx manager; `db.read_txn(conn)` ctx manager; `db.project_db_path(orchestrator_dir) -> Path`; `db.host_db_path() -> Path`; `db.ensure_project_schema(conn)`; `db.ensure_host_schema(conn)`; `db.DbBusy`; `db.SchemaTooNew`; both schemas' tables as sketched

## Decisions & findings

### Decision: `synchronous=FULL`, not NORMAL  *(status: active)*
- **Rationale:** today every write fsyncs (`state.py:save_atomic` tmp+fsync+rename); FULL preserves that durability class while still being cheaper than today (WAL fsync vs whole-file rewrite+fsync). NORMAL would newly introduce power-loss rollback of committed orchestration events.
- **Alternatives considered:** NORMAL (sqlite.org recommends for WAL; parked in master Parking lot as a documented option).
- **Evidence:** probe — `synchronous` stays 2 (FULL) after `journal_mode=WAL`; sqlite.org/pragma.html §synchronous.

### Decision: `plans.version` is a reader-facing change hint, not a correctness guard  *(status: active)*
- **Rationale:** every writer bumps it, so a poller can ask "did THIS plan change" one granularity finer than `PRAGMA data_version` (which is DB-wide and covers every plan in the project). It is in the schema from day one because retrofitting a column mid-plan means a schema migration. It is deliberately NOT the p6 tick's compare-and-set predicate — that guard is a precondition set, for reasons recorded in p6's Decisions entry (the two highest-frequency writers would otherwise abort most watchdog ticks).
- **Alternatives considered:** using it as the tick's CAS predicate (rejected in p6 — see there); omitting it entirely and leaning on `PRAGMA data_version` alone (loses per-plan granularity, so a dashboard would re-read every plan whenever any one changed).
- **Evidence:** probe — data_version moves only on other connections' commits and is DB-wide (master Background findings).

### Decision: the golden is `clu watch`'s TEXT projection, never `--json`  *(status: active — binds p3, p6, p7)*
- **Rationale:** the master's non-goals freeze `clu watch`'s LINE FORMATS as the contract the migration must preserve, so the text projection is the thing under test. `--json` also varies run to run in the claim token as well as the timestamp, which would contradict this phase's own determinism requirement; the text projection prints neither, and two independent runs came out byte-identical.
- **Consequence for later phases:** capture through `demo_script.capture_watch_lines` and diff the lines. A phase that captures `--json` and diffs it against this golden is comparing two different projections.
- **Evidence:** `watch.py:121,334` render `started (attempt {n})`; the event type names appear only in the JSON mode.

### Decision: `clu watch`'s always-`attempt 1` bug is FROZEN into the golden, not fixed  *(status: active — binds p4 and p6)*
- **Rationale:** `clu watch` has always printed `attempt 1` for every attempt, including genuine retries. `claim_phase` computes the attempt count onto the claim but never puts it in the `phase_started` event (`state.py:762` appends `phase=`/`claimed_by=` only), while the watch formatter reads `e.get('attempts', 1)` from the event — so the fallback always wins. The golden's re-dispatch line therefore reads `started (attempt 1)` where the truth is attempt 2. Fixing it here would have been a behavior change in a plan whose whole premise is that behavior is unchanged, AND would have baked a fix into the pre-migration baseline, destroying its value as a baseline.
- **Consequence for later phases:** any phase that puts `attempts` into the `phase_started` event — p4's native write ops and p6's tick-delta events are both in a position to do it incidentally — breaks the golden diff, and it will present as a migration regression when it is really a latent bug being fixed. Both shards now carry this as a Done criterion.
- **Alternatives considered:** fix it in this phase (rejected: behavior change, and it contaminates the baseline); capture the golden after fixing it (same objection, one step later).
- **Evidence:** `state.py:762`, `watch.py:121,334`; observed in the recorded golden, whose two `started` lines are identical.

### Decision: `connect` re-establishes 0600 and the symlink refusal on EVERY open  *(status: active)*
- **Rationale:** review finding. As first written the mode was enforced only when `connect` created the file, and because `.exists()` follows symlinks the `O_NOFOLLOW` guard was skipped on the same path. Probed: a `clu.db` chmod'd to 0644 stays 0644 through reopen-and-write. The files this database replaces are 0600 on *every* write — `save_atomic` mkstemps a fresh file, 0600 by construction, and renames it over the old one — so create-only enforcement would have been a silent weakening of a property this migration promises to preserve, on a database holding claim tokens. Triggered by a restored backup, a `cp`, or an operator opening the DB with the `sqlite3` CLI.
- **Evidence:** probe this session; `state.py:630` (mkstemp), CLAUDE.md "the token is the entire security boundary".

### Decision: the schema check is lock-free when the schema is already current  *(status: active — binds every later phase's read paths)*
- **Rationale:** review finding. `_ensure_schema` originally took `BEGIN IMMEDIATE` unconditionally. Callers run it on open, so that is a write lock every process pays on every open — a dashboard refreshing once a second would contend with the tick and every heartbeat forever, and could fail with `DbBusy` on a pure read path. The newer-schema refusal moved to the fast path for a second reason: it is what a fleet walk catches per project before moving on, so it has to be reachable from a read-only handle, where taking the write lock first raises "attempt to write a readonly database" and buries the real cause. The in-transaction re-check stays for the create path, since the fast read and the DDL are not atomic together.
- **Consequence for later phases:** `ensure_*_schema` is safe to call on open, including on read-only connections, which is what lets p7's dashboards use it.
- **Evidence:** `db.py` `_ensure_schema`; tests assert both (one holds the write lock from another process, one refuses a newer schema through a read-only handle).

### Decision: `connect` refuses to continue if `journal_mode=WAL` did not take  *(status: active)*
- **Rationale:** review finding. `PRAGMA journal_mode` does not raise when it cannot honour the request — probed: it returns the mode actually in force. Every guarantee in this layer rests on WAL (readers that never block behind the writer; the losslessness the contention gate measures), and on a rollback journal both quietly stop being true with nothing reporting it: every dashboard read would serialize behind the tick and be blamed on something else. The usual cause is a filesystem without WAL's shared memory, which is the network-filesystem case the master's non-goals declare unsupported — this is where "unsupported" becomes audible.
- **Evidence:** probe this session; sqlite.org/wal.html.

### Finding: the contention gate has a measured control  *(empirical, this phase)*
The identical 8×25 workload run on DEFERRED transactions on this host: **93/200 increments, 107 busy escapes.** Through `BEGIN IMMEDIATE`: **200/200, 0 escapes.** The plan-time probe reproduces on the execution host. This matters beyond a green dot: without the control, a passing acceptance test cannot distinguish "the discipline held" from "the workload never actually contended." The control's numbers are recorded in `tests/test_db.py` beside the assertion rather than run on every suite pass.

### Finding: WAL sidecar files inherit 0600 from the main database  *(empirical, this phase)*
Under a 022 umask, `clu.db-wal` and `clu.db-shm` both come out 0600 because SQLite matches them to the main file's mode. Since the WAL holds uncommitted rows — claim tokens included — pre-creating only the main database at 0600 genuinely covers the whole set. Verified, not assumed.

### Finding: two pins the demo driver needs that the phase did not anticipate  *(empirical, this phase — p3, p6, p7 inherit them)*
`tick_on_action` defaults to ON, so `clu block` and `clu complete` each fire a detached background tick that races the script and reorders its events; the driver turns it off in the scaffolded config. And `clu complete` needs `--skip-verify --skip-simplify`, because the throwaway demo project is not a git repo and there is no HEAD for a quality stamp to be measured against. Both are pinned inside `demo_script.py`, so later phases get them for free — but a phase that drives the lifecycle by hand instead of through that helper will hit both.

### Finding: `clu watch` drops state paths that do not exist when the stream starts  *(empirical, this phase)*
A path absent at stream start is dropped from the poll set and never picked up. That is why `scaffold_project` is split from `run_sequence`: scaffold and `clu init` must complete BEFORE the stream opens, with the rest of the sequence running inside `stream_loop`'s `_before_first_tick` seam. This is what makes a single poll tick sufficient and keeps the capture free of `time.sleep`.

## Failure modes to anticipate
- The multiprocess test flakes under CI-like load if the busy_timeout is too tight — use generous timeouts (10s) in the acceptance test; the bounded-wait test uses its own dedicated DB.
- `multiprocessing` fork vs spawn on macOS: fork is unsafe with open SQLite connections; the test must open connections AFTER spawn (probe used spawn successfully).
- `PRAGMA journal_mode=WAL` on a read-only connection fails — `connect(readonly=True)` must skip write pragmas.
- Leaving an implicit transaction open by accident (legacy isolation_level) — guarded by `isolation_level=None` everywhere + a test asserting `conn.in_transaction` is False after `connect`.
- tests must isolate `HOME`/XDG (suite already enforces this — `tests/__init__.py` isolation, p1 commit `853a7f4`); `host_db_path` goes through `assert_xdg_safe` which fails loud on missed isolation.

## Done criteria
- Contention acceptance test green and its numbers printed in the test output: **200/200 increments (8 processes × 25), 0 unhandled busy escapes** (observable, not just a green dot — the assertion message carries the counts). 200 is the figure the plan-time probe produced and the figure p1's Work specifies; the 300-increment number that appears in the master's Background findings belongs to the specialist's separate 6×50 NAIVE control, which is not what this test runs.
- Bounded-wait test observes `DbBusy` in under 1s wall-clock with a 0.2s budget.
- A `clu.db` created by `ensure_project_schema` has mode 0600 and `user_version=1` (asserted).
- `tests/goldens/watch-demo.txt` exists, records the whole lifecycle — the phase's start line, its blocker, the operator's answer, its completion, and the plan-done line — and was produced by the JSON backend before any routing change. Non-vacuity is the point: an empty or truncated golden would let every later comparison pass while proving nothing. **Criterion corrected at execution:** as approved it asked for literal `phase_started` / `phase_completed` lines, which `clu watch` never emits — its default projection renders events as prose (`demo-block/c: started (attempt 1)`), and the type names appear only under `--json`. The criterion asked for the event types when what it protects is lifecycle coverage, and the text projection is the one the master's non-goals freeze as the contract p7 must preserve.
- Full suite green (`python3 -m unittest discover -s tests`).
