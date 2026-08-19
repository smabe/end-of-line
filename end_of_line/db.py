"""SQLite core: connection factories, transaction helpers, and both schemas.

clu keeps two databases, never one:

- **per-project** `<project>/plans/.orchestrator/clu.db` — plan state, claims,
  blockers, spawned tasks, events, queue, quota. One write lock per project, so
  two projects' ticks never contend.
- **host** `~/.config/clu/clu.db` — registry, monitor marker, the iMessage /
  Discord sidecars, the inbox, and the skill-install receipt.

Three rules hold the concurrency story up, and all three are load-bearing:

1. **Every write transaction opens `BEGIN IMMEDIATE`.** A deferred transaction
   that reads first and writes later fails the moment another connection has
   committed in between — instantly, with a `database is locked` that
   `busy_timeout` does NOT absorb (SQLITE_BUSY_SNAPSHOT; sqlite.org's busy
   handler is skipped whenever SQLite judges the wait to be a deadlock). Taking
   the write lock up front turns that unrecoverable failure into an ordinary
   bounded wait. A snapshot-then-write across a subprocess gap cannot be one
   transaction, so it guards its write with a compare-and-set instead.
2. **`isolation_level=None` everywhere.** Python's legacy transaction control
   issues an implicit deferred `BEGIN` before DML, which is exactly the shape
   rule 1 exists to avoid. (The 3.12+ `autocommit` attribute is not used — it
   does not exist on this project's 3.11 floor.)
3. **`busy_timeout` on every connection, readers included.** A WAL reader can
   still see a brief SQLITE_BUSY during last-connection-close cleanup and crash
   recovery (sqlite.org/wal.html, "Sometimes Queries Return SQLITE_BUSY").

`synchronous=FULL` is deliberate: every clu write fsyncs today (tmp + fsync +
rename), and a storage migration whose premise is "no behavior change" must not
quietly weaken durability on the way through.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ._xdg_guard import assert_xdg_safe, clu_config_dir

PROJECT_SCHEMA_VERSION = 1
HOST_SCHEMA_VERSION = 1

# Both databases use the same filename; the directory is what distinguishes
# them (`plans/.orchestrator/` per project, `clu_config_dir()` for the host).
DB_FILENAME = "clu.db"

# State files are 0600 today (mkstemp) and carry claim tokens — the token IS
# the worker security boundary, so the database that inherits them is no more
# readable than the files it replaces.
DB_FILE_MODE = 0o600

DEFAULT_TIMEOUT_S = 5.0


class DbBusy(RuntimeError):
    """A bounded wait for the write lock expired.

    The direct replacement for `state.LockTimeout`: callers that would rather
    drop a write than freeze a worker (the activity hook's 2s budget) catch
    this and move on.
    """


class SchemaTooNew(RuntimeError):
    """The database's `user_version` is newer than this clu understands.

    Upstream decision #6: a database written by a newer clu is skipped, never
    read optimistically and never downgraded. Callers walking a fleet catch
    this per project and keep walking.
    """


# --- paths ------------------------------------------------------------------


def project_db_path(orchestrator_dir: Path) -> Path:
    """The project database inside an already-resolved `.orchestrator/` dir."""
    return Path(orchestrator_dir) / DB_FILENAME


def host_db_path() -> Path:
    """The host database under `$XDG_CONFIG_HOME/clu` (default `~/.config/clu`).

    Goes through `assert_xdg_safe` for the same reason the JSON stores it
    replaces did: under `CLU_TEST_MODE=1` a test that forgot its isolation
    fails loudly instead of writing into the operator's real config dir.
    """
    path = clu_config_dir() / DB_FILENAME
    assert_xdg_safe(path)
    return path


# --- connections ------------------------------------------------------------


def _busy_error(exc: sqlite3.OperationalError) -> bool:
    """True for SQLite's lock-contention errors, whatever the wording."""
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _ensure_private(path: Path) -> None:
    """Make the database file exist at 0600 before SQLite ever opens it.

    SQLite would create it with the process umask (typically 0644), and there
    is no window in which a claim token should be world-readable — so the file
    exists at the right mode before any row is written. `O_NOFOLLOW` refuses a
    pre-seeded symlink, matching `state.locked`'s lockfile handling.

    The mode is re-established on EVERY open, not only on create. The files
    this database replaces are 0600 on every write — `save_atomic` mkstemps a
    fresh file (0600 by construction) and renames it over the old one — so a
    create-only chmod would silently weaken that guarantee the first time a
    `clu.db` arrives from anywhere else: a restored backup, a `cp`, or an
    operator who opened it with the `sqlite3` CLI. The symlink refusal has to
    run on every open for the same reason.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, DB_FILE_MODE)
    os.close(fd)
    # os.open's mode is masked by the umask, and applies only on create; chmod
    # is neither, which is what makes this correct for a pre-existing file.
    os.chmod(path, DB_FILE_MODE)


def connect(
    path: Path,
    *,
    readonly: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> sqlite3.Connection:
    """Open `path` with clu's pragmas. Creates the file (0600) unless readonly.

    `readonly=True` opens through a `mode=ro` URI and skips every write pragma
    — `journal_mode=WAL` is a write to the database header and fails outright
    on a read-only handle — but still sets `busy_timeout`, which readers need.
    """
    path = Path(path)
    if readonly:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout_s, isolation_level=None)
    else:
        _ensure_private(path)
        conn = sqlite3.connect(str(path), timeout=timeout_s, isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(timeout_s * 1000)}")
        if not readonly:
            _require_wal(conn, path)
            conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        conn.close()
        raise
    return conn


@contextmanager
def host_conn(
    path: Path | None = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Iterator[sqlite3.Connection]:
    """Open the host database, ensure its schema, and close it on the way out.

    Every host-DB store — registry, monitor marker, the iMessage / Discord
    sidecars, the inbox, the skill receipt — needs the same three lines, and
    all of them want the connection SHORT-LIVED: the Discord notifier is
    constructed once per notification, and a reader that keeps a handle across
    frames pins the WAL (see `read_txn`). One helper so none of them invents
    its own lifetime.

    `path` overrides `host_db_path()` and exists for the same reason the old
    `path=` keyword on these functions did: a test points it at its own file.
    """
    conn = connect(path or host_db_path(), timeout_s=timeout_s)
    try:
        ensure_host_schema(conn)
        yield conn
    finally:
        conn.close()


# What a caller catches when it would rather DEGRADE than fail — a read that
# returns empty (registry, marker, cursor, inbox) or a best-effort write that
# is skipped (the inbox notes beside a durable state change). Both used to be
# `except OSError` against a file; against a database the same failure arrives
# as `sqlite3.Error`, and contention as `DbBusy`, so a guard that named only
# OSError would convert a busy store into a crashed tick. `SchemaTooNew` is in
# the set because upstream decision #6 says a newer database is SKIPPED.
#
# Deliberately NOT in the set: the plain `RuntimeError` raised when WAL did not
# take. That one means the concurrency model is not in force, and `connect`
# raises it precisely so somebody hears about it.
DEGRADABLE_ERRORS: tuple[type[BaseException], ...] = (
    SchemaTooNew,
    DbBusy,
    sqlite3.Error,
    OSError,
)


def _require_wal(conn: sqlite3.Connection, path: Path) -> None:
    """Switch to WAL and refuse to continue if the switch did not take.

    `PRAGMA journal_mode` does not raise when it cannot honour the request — it
    returns the mode actually in force, so a database that stays on the
    rollback journal is indistinguishable from one that switched unless the
    answer is read. Everything this module promises rests on WAL: readers that
    never block behind the writer, and the lossless contention the acceptance
    test measures. On a rollback journal both quietly stop being true and
    nothing anywhere reports it — every dashboard read would serialize behind
    the tick and be blamed on something else. The usual cause is a filesystem
    without the shared memory WAL needs, which is the network-filesystem case
    the plan declares unsupported; this is where "unsupported" becomes audible.
    """
    row = conn.execute("PRAGMA journal_mode = WAL").fetchone()
    mode = str(row[0]).lower() if row else "unknown"
    if mode != "wal":
        raise RuntimeError(
            f"{path}: journal_mode is {mode!r}, not 'wal' — clu's concurrency "
            f"model requires WAL. The usual cause is a filesystem that cannot "
            f"provide WAL's shared memory (network mounts); clu is same-host, "
            f"local-filesystem only."
        )


# --- transactions -----------------------------------------------------------


def _set_busy_timeout(conn: sqlite3.Connection, ms: int) -> None:
    conn.execute(f"PRAGMA busy_timeout = {ms}")


def _current_busy_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    return int(row[0]) if row else 0


@contextmanager
def write_txn(
    conn: sqlite3.Connection,
    *,
    timeout_s: float | None = None,
) -> Iterator[sqlite3.Cursor]:
    """`BEGIN IMMEDIATE` … COMMIT, rolling back on any exception.

    `timeout_s` overrides the connection's `busy_timeout` for the duration of
    the attempt and is restored afterwards, so one impatient caller cannot
    change how patient the next one is. Contention that outlives the budget
    raises `DbBusy` rather than SQLite's generic `OperationalError`, which is
    what makes drop-on-contention a decision a caller can express.
    """
    restore: int | None = None
    if timeout_s is not None:
        restore = _current_busy_timeout(conn)
        _set_busy_timeout(conn, int(timeout_s * 1000))
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if _busy_error(exc):
                raise DbBusy(str(exc)) from exc
            raise
        cur = conn.cursor()
        try:
            yield cur
        except BaseException:
            _rollback_quietly(conn)
            raise
        finally:
            cur.close()
        try:
            conn.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            _rollback_quietly(conn)
            if _busy_error(exc):
                raise DbBusy(str(exc)) from exc
            raise
    finally:
        if restore is not None:
            _set_busy_timeout(conn, restore)


@contextmanager
def read_txn(conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
    """A deferred read transaction that is ALWAYS rolled back on exit.

    Never hold one across frames: a reader with an open transaction pins the
    WAL past its autocheckpoint and the file grows without bound (probed:
    819KB to 12.8MB) until the reader lets go. Pollers read in short bursts.

    Contention on the BEGIN itself surfaces as `DbBusy`, the same currency
    `write_txn` deals in, so a caller that drops a read rather than waiting
    catches one exception type rather than two. Note where that does NOT reach:
    a deferred BEGIN acquires nothing, so the read snapshot is taken by the
    caller's FIRST statement inside the block — a busy there (WAL's
    last-connection-close cleanup, per `connect`) raises SQLite's own
    `OperationalError`, because it comes from a statement this helper never
    sees.
    """
    try:
        conn.execute("BEGIN")
    except sqlite3.OperationalError as exc:
        if _busy_error(exc):
            raise DbBusy(str(exc)) from exc
        raise
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()
        _rollback_quietly(conn)


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    """Roll back if a transaction is open; never mask the original error."""
    try:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


# --- schema -----------------------------------------------------------------

# JSON-valued columns are noted per table. They are the compat seam: the plan
# swaps the storage engine under today's dict-shaped API first and grows native
# columns for the hot paths afterwards, so the shapes that no writer contends
# on stay whole documents.

_PROJECT_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS plans (
        slug        TEXT PRIMARY KEY,
        status      TEXT,
        plan_dir    TEXT,
        created_at  TEXT,
        batch_id    TEXT,
        -- Reader-facing change hint, bumped by every writer: one granularity
        -- finer than PRAGMA data_version, which is DB-wide and cannot say
        -- WHICH plan moved. Deliberately NOT the tick's compare-and-set
        -- predicate — that guard is a precondition set.
        version     INTEGER NOT NULL DEFAULT 0,
        config      TEXT,   -- JSON
        phases      TEXT,   -- JSON
        worktree    TEXT,   -- JSON
        extra       TEXT    -- JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claims (
        plan_slug              TEXT PRIMARY KEY REFERENCES plans(slug),
        phase_id               TEXT,
        claimed_by             TEXT,
        lease_expires          TEXT,
        started_at             TEXT,
        last_heartbeat_at      TEXT,
        attempts               INTEGER,
        pid                    INTEGER,
        pgid                   INTEGER,
        log_path               TEXT,
        session_id             TEXT,
        active_tool_started_at TEXT,
        flags                  TEXT,   -- JSON
        attestations           TEXT,   -- JSON
        cpu_samples            TEXT    -- JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS blockers (
        plan_slug        TEXT,
        blocker_id       TEXT,
        phase_id         TEXT,
        type             TEXT,
        question         TEXT,
        options          TEXT,   -- JSON
        context          TEXT,
        asked_at         TEXT,
        answer           TEXT,
        answered_at      TEXT,
        consumed         INTEGER DEFAULT 0,
        last_repinged_at TEXT,
        notify_metadata  TEXT,   -- JSON
        PRIMARY KEY (plan_slug, blocker_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS spawned_tasks (
        plan_slug TEXT,
        task_id   TEXT,
        status    TEXT,
        payload   TEXT,   -- JSON
        PRIMARY KEY (plan_slug, task_id)
    )
    """,
    # The hot event log. `id` is monotonic and replaces today's "cursor on
    # len(events)" contract for every streaming reader.
    """
    CREATE TABLE IF NOT EXISTS events (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_slug TEXT,
        ts        TEXT,
        type      TEXT,
        payload   TEXT   -- JSON
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_plan_id ON events (plan_slug, id)",
    "CREATE INDEX IF NOT EXISTS idx_events_plan_type ON events (plan_slug, type)",
    # Terminal plans' events move here on explicit archival so the hot table
    # stays lean. No AUTOINCREMENT: ids are carried over, not minted.
    """
    CREATE TABLE IF NOT EXISTS events_archive (
        id          INTEGER,
        plan_slug   TEXT,
        ts          TEXT,
        type        TEXT,
        payload     TEXT,   -- JSON
        archived_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS queue (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        slug     TEXT,
        added_at TEXT,
        added_by TEXT,
        batch_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS queue_history (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        slug     TEXT,
        added_at TEXT,
        added_by TEXT,
        batch_id TEXT,
        ended_at TEXT,
        outcome  TEXT
    )
    """,
    # Single-row by construction: "no row" is the whole encoding of "not
    # paused", which only reads unambiguously if a second row cannot exist.
    """
    CREATE TABLE IF NOT EXISTS quota (
        id              INTEGER PRIMARY KEY CHECK (id = 1),
        paused_until    TEXT,
        signature       TEXT,
        line            TEXT,
        canary_plan     TEXT,
        canary_deadline TEXT,
        created_at      TEXT
    )
    """,
)

_HOST_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS registry (
        project_root  TEXT,
        plan_slug     TEXT,
        registered_at TEXT,
        PRIMARY KEY (project_root, plan_slug)
    )
    """,
    # Key/value rather than columns: the monitor marker and the inbound cursor
    # are single scalars whose set of names changes more often than their
    # shape, and neither is ever queried by anything but its own key.
    "CREATE TABLE IF NOT EXISTS monitor (k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE IF NOT EXISTS inbound_state (k TEXT PRIMARY KEY, v TEXT)",
    """
    CREATE TABLE IF NOT EXISTS outbound_floors (
        chat_id     TEXT PRIMARY KEY,
        floor_rowid INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outbound_marks (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        sent_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discord_dm_cache (
        user_id    TEXT PRIMARY KEY,
        channel_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discord_cursor (
        channel_id TEXT PRIMARY KEY,
        message_id TEXT
    )
    """,
    # `event_id UNIQUE` is the dedupe the move-to-`processed/` directory
    # protocol used to get from the filename.
    """
    CREATE TABLE IF NOT EXISTS inbox (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     TEXT UNIQUE,
        ts           TEXT,
        type         TEXT,
        plan_slug    TEXT,
        project_root TEXT,
        summary      TEXT,
        details      TEXT,   -- JSON
        processed    INTEGER DEFAULT 0,
        processed_at TEXT
    )
    """,
    "CREATE TABLE IF NOT EXISTS skills (name TEXT PRIMARY KEY, digest TEXT)",
)


def _user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _refuse_if_newer(found: int, version: int) -> None:
    if found > version:
        raise SchemaTooNew(
            f"database schema is version {found}; this clu understands {version} "
            f"— skipping (upgrade clu to read it)"
        )


def _ensure_schema(conn: sqlite3.Connection, ddl: tuple[str, ...], version: int) -> None:
    """Create the tables if absent and stamp `user_version`, idempotently.

    Two paths, and the split matters more than it looks. The common case by far
    is a database that is already at the right version, and it must not take
    the write lock to discover that: callers run this on open, so a lock here
    is a lock every process pays on every open — a dashboard refreshing once a
    second would contend with the tick and the heartbeats forever, and could
    fail with `DbBusy` on what is otherwise a pure read path.

    A database that is NEWER than this clu is refused on the fast path too, and
    that placement is the point rather than an optimization: the refusal is
    what a fleet walk catches per project and keeps walking, so it has to be
    reachable on a read-only connection — where taking the write lock first
    would raise "attempt to write a readonly database" and bury it.

    When work IS needed, the version check is repeated INSIDE the transaction,
    because the fast path's read and the DDL are not atomic together: a
    concurrent clu on a newer schema could commit in between.
    """
    found = _user_version(conn)
    _refuse_if_newer(found, version)
    if found == version:
        return
    with write_txn(conn) as cur:
        _refuse_if_newer(_user_version(conn), version)
        for stmt in ddl:
            cur.execute(stmt)
        # Not parameterizable: PRAGMA takes a literal. `version` is a module
        # constant, never caller input.
        cur.execute(f"PRAGMA user_version = {int(version)}")


def ensure_project_schema(conn: sqlite3.Connection) -> None:
    """Create/verify the per-project schema. Raises `SchemaTooNew` if newer."""
    _ensure_schema(conn, _PROJECT_DDL, PROJECT_SCHEMA_VERSION)


def ensure_host_schema(conn: sqlite3.Connection) -> None:
    """Create/verify the host schema. Raises `SchemaTooNew` if newer."""
    _ensure_schema(conn, _HOST_DDL, HOST_SCHEMA_VERSION)
