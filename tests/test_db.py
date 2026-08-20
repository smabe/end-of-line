"""SQLite core (`end_of_line.db`): contention, bounded wait, schema/versioning.

Three families, in the order the phase gates them:

1. **Contention acceptance** — the gate. Eight OS processes each do 25
   contended read-modify-writes of one row through `db.write_txn`. The plan's
   whole storage design rests on `BEGIN IMMEDIATE` + `busy_timeout` being
   lossless under real cross-process contention on THIS host, so the numbers
   are asserted with the counts in the message rather than as a bare green dot.
2. **Bounded wait** — a child holds a write transaction open; the parent's
   `write_txn(timeout_s=0.2)` must give up quickly with `DbBusy`. This is the
   replacement for today's `LockTimeout` drop-on-contention contract
   (`state.py:1090-1122`, the activity hook's 2s budget).
3. **Schema / versioning** — DDL idempotency, `PRAGMA user_version` stamping,
   the newer-than-me refusal, 0600 file mode, and the "no implicit transaction
   is open after connect" invariant that `isolation_level=None` buys.

`multiprocessing` runs under the **spawn** context: fork is unsafe with open
SQLite connections, and every child here opens its connection after the spawn.
"""

from __future__ import annotations

import multiprocessing as mp
import sqlite3
import stat
import sys
import threading
import time
from pathlib import Path

from end_of_line import db
from tests import CluTestCase

# The plan-time probe's shape, reproduced exactly: 8 processes x 25 increments.
CONTENTION_PROCESSES = 8
CONTENTION_INCREMENTS = 25
CONTENTION_TOTAL = CONTENTION_PROCESSES * CONTENTION_INCREMENTS  # 200

# Generous on purpose: the failure this must not have is a flake under load.
CHILD_TIMEOUT_S = 10.0
CHILD_JOIN_S = 120.0


def _make_counter_db(path: Path) -> None:
    """One-row counter table, committed, connection closed."""
    conn = db.connect(path, timeout_s=CHILD_TIMEOUT_S)
    try:
        with db.write_txn(conn) as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY, n INTEGER)")
            cur.execute("INSERT OR REPLACE INTO counter (id, n) VALUES (1, 0)")
    finally:
        conn.close()


def _read_counter(path: Path) -> int:
    conn = db.connect(path, readonly=True, timeout_s=CHILD_TIMEOUT_S)
    try:
        with db.read_txn(conn) as cur:
            row = cur.execute("SELECT n FROM counter WHERE id = 1").fetchone()
    finally:
        conn.close()
    assert row is not None
    return int(row[0])


def _increment_worker(db_path: str, result_path: str, increments: int) -> None:
    """Child: `increments` contended read-modify-writes; records busy escapes.

    Module-level and picklable so the spawn context can import it. Deliberately
    has NO retry loop — a `DbBusy` escape here means the increment was lost,
    which is exactly what the assertion in the parent is measuring.
    """
    busy = 0
    conn = db.connect(Path(db_path), timeout_s=CHILD_TIMEOUT_S)
    try:
        for _ in range(increments):
            try:
                with db.write_txn(conn) as cur:
                    row = cur.execute("SELECT n FROM counter WHERE id = 1").fetchone()
                    cur.execute("UPDATE counter SET n = ? WHERE id = 1", (int(row[0]) + 1,))
            except db.DbBusy:
                busy += 1
    finally:
        conn.close()
    Path(result_path).write_text(str(busy))


def _holder_worker(db_path: str, ready_path: str, release_path: str, max_hold_s: float) -> None:
    """Child: take a write transaction, announce it, hold until released."""
    conn = db.connect(Path(db_path), timeout_s=CHILD_TIMEOUT_S)
    try:
        with db.write_txn(conn) as cur:
            cur.execute("UPDATE counter SET n = n + 1 WHERE id = 1")
            Path(ready_path).write_text("1")
            deadline = time.monotonic() + max_hold_s
            while time.monotonic() < deadline and not Path(release_path).exists():
                time.sleep(0.02)
    finally:
        conn.close()


class ContentionAcceptanceTest(CluTestCase):
    """The phase gate: `BEGIN IMMEDIATE` + `busy_timeout` is lossless here."""

    def test_eight_processes_lose_no_increments(self) -> None:
        # Arrange — a counter at zero and one result file per child.
        db_path = self.tmp_path / "contention.db"
        _make_counter_db(db_path)
        ctx = mp.get_context("spawn")
        results = [self.tmp_path / f"busy-{i}.txt" for i in range(CONTENTION_PROCESSES)]

        # Act — every child hammers the same row at once.
        procs = [
            ctx.Process(
                target=_increment_worker,
                args=(str(db_path), str(res), CONTENTION_INCREMENTS),
            )
            for res in results
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(CHILD_JOIN_S)

        # Assert — every child exited clean, nothing was dropped, nothing waited
        # long enough to give up. The counts are IN the messages: the phase
        # requires them observable in the test output, not just a green dot.
        for i, p in enumerate(procs):
            self.assertEqual(
                p.exitcode,
                0,
                f"contention child {i} exited {p.exitcode} (expected 0)",
            )
        missing = [str(r) for r in results if not r.exists()]
        self.assertEqual(missing, [], f"contention children left no result file: {missing}")
        busy_escapes = sum(int(r.read_text()) for r in results)
        final = _read_counter(db_path)
        # Printed, not merely asserted: the phase wants these numbers visible
        # in a passing run, because "green" alone cannot distinguish a gate
        # that held from a gate that never ran. Measured control for contrast:
        # the identical workload on DEFERRED transactions loses ~half the
        # increments on this host (93/200, 107 busy escapes).
        print(
            f"\nCONTENTION ACCEPTANCE: {final}/{CONTENTION_TOTAL} increments "
            f"({CONTENTION_PROCESSES} processes x {CONTENTION_INCREMENTS}), "
            f"{busy_escapes} unhandled busy escapes",
            file=sys.stderr,
        )
        self.assertEqual(
            (final, busy_escapes),
            (CONTENTION_TOTAL, 0),
            f"CONTENTION ACCEPTANCE: {final}/{CONTENTION_TOTAL} increments "
            f"({CONTENTION_PROCESSES} processes x {CONTENTION_INCREMENTS}), "
            f"{busy_escapes} unhandled busy escapes "
            f"(required: {CONTENTION_TOTAL}/{CONTENTION_TOTAL}, 0)",
        )


class BoundedWaitTest(CluTestCase):
    """`write_txn(timeout_s=...)` drops on contention instead of hanging."""

    def test_write_txn_raises_dbbusy_within_budget(self) -> None:
        # Arrange — a child holds the write lock for longer than our budget.
        db_path = self.tmp_path / "bounded.db"
        _make_counter_db(db_path)
        ready = self.tmp_path / "held.flag"
        release = self.tmp_path / "release.flag"
        ctx = mp.get_context("spawn")
        holder = ctx.Process(
            target=_holder_worker,
            args=(str(db_path), str(ready), str(release), 30.0),
        )
        holder.start()
        self.addCleanup(holder.join, CHILD_JOIN_S)
        self.addCleanup(release.touch)
        deadline = time.monotonic() + CHILD_JOIN_S
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), "holder child never took the write transaction")

        # Act — ask for the write lock with a 0.2s budget.
        conn = db.connect(db_path, timeout_s=CHILD_TIMEOUT_S)
        self.addCleanup(conn.close)
        started = time.monotonic()
        with self.assertRaises(db.DbBusy):
            with db.write_txn(conn, timeout_s=0.2) as cur:
                cur.execute("UPDATE counter SET n = n + 1 WHERE id = 1")
        elapsed = time.monotonic() - started

        # Assert — gave up fast, and left no transaction dangling.
        self.assertLess(elapsed, 1.0, f"DbBusy took {elapsed:.3f}s with a 0.2s budget")
        self.assertFalse(conn.in_transaction, "a failed write_txn left a transaction open")

    def test_readonly_connection_reads_while_a_writer_holds_the_lock(self) -> None:
        # WAL's point: a reader never blocks behind the write lock. This is the
        # invariant every dashboard read in later phases depends on.
        db_path = self.tmp_path / "reader.db"
        _make_counter_db(db_path)
        ready = self.tmp_path / "held.flag"
        release = self.tmp_path / "release.flag"
        ctx = mp.get_context("spawn")
        holder = ctx.Process(
            target=_holder_worker,
            args=(str(db_path), str(ready), str(release), 30.0),
        )
        holder.start()
        self.addCleanup(holder.join, CHILD_JOIN_S)
        self.addCleanup(release.touch)
        deadline = time.monotonic() + CHILD_JOIN_S
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), "holder child never took the write transaction")

        # The reader sees the pre-commit value, and sees it promptly.
        self.assertEqual(_read_counter(db_path), 0)


class ConnectTest(CluTestCase):
    def test_creates_the_file_0600(self) -> None:
        path = self.tmp_path / "nested" / db.DB_FILENAME
        conn = db.connect(path)
        self.addCleanup(conn.close)
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_no_implicit_transaction_after_connect(self) -> None:
        # isolation_level=None: the module must not open a deferred BEGIN for
        # us — a deferred read that later upgrades to a write is precisely the
        # SQLITE_BUSY_SNAPSHOT deadlock shape this plan is built to avoid.
        conn = db.connect(self.tmp_path / "x.db")
        self.addCleanup(conn.close)
        self.assertIsNone(conn.isolation_level)
        self.assertFalse(conn.in_transaction)

    def test_pragmas_are_set(self) -> None:
        conn = db.connect(self.tmp_path / "pragma.db", timeout_s=3.0)
        self.addCleanup(conn.close)
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(str(journal).lower(), "wal")
        self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)  # FULL
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 3000)

    def test_readonly_connection_skips_write_pragmas_and_refuses_writes(self) -> None:
        path = self.tmp_path / "ro.db"
        _make_counter_db(path)
        conn = db.connect(path, readonly=True, timeout_s=2.0)
        self.addCleanup(conn.close)
        # busy_timeout is set on readers too — WAL readers can hit a brief
        # SQLITE_BUSY during last-connection-close cleanup.
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 2000)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("UPDATE counter SET n = 99 WHERE id = 1")

    def test_readonly_connect_on_a_missing_file_raises(self) -> None:
        with self.assertRaises(sqlite3.OperationalError):
            db.connect(self.tmp_path / "nope.db", readonly=True)

    def test_reopening_a_widened_database_restores_0600(self) -> None:
        # The files this replaces are 0600 on EVERY write (save_atomic mkstemps
        # a fresh file and renames it), so create-only enforcement would be a
        # quiet weakening the first time a clu.db arrives from a backup, a `cp`,
        # or the `sqlite3` CLI. The database holds claim tokens.
        path = self.tmp_path / "widened.db"
        _make_counter_db(path)
        path.chmod(0o644)
        conn = db.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_connect_refuses_a_symlinked_database_path(self) -> None:
        # A pre-seeded symlink would divert plan state — claim tokens included —
        # into a file of someone else's choosing.
        real = self.tmp_path / "real.db"
        _make_counter_db(real)
        link = self.tmp_path / "link.db"
        link.symlink_to(real)
        with self.assertRaises(OSError):
            db.connect(link)


class TxnTest(CluTestCase):
    def test_write_txn_commits_on_clean_exit(self) -> None:
        path = self.tmp_path / "commit.db"
        _make_counter_db(path)
        conn = db.connect(path)
        self.addCleanup(conn.close)
        with db.write_txn(conn) as cur:
            cur.execute("UPDATE counter SET n = 7 WHERE id = 1")
        self.assertFalse(conn.in_transaction)
        self.assertEqual(_read_counter(path), 7)

    def test_write_txn_rolls_back_on_exception(self) -> None:
        path = self.tmp_path / "rollback.db"
        _make_counter_db(path)
        conn = db.connect(path)
        self.addCleanup(conn.close)
        with self.assertRaises(ValueError):
            with db.write_txn(conn) as cur:
                cur.execute("UPDATE counter SET n = 7 WHERE id = 1")
                raise ValueError("boom")
        self.assertFalse(conn.in_transaction)
        self.assertEqual(_read_counter(path), 0)

    def test_write_txn_restores_the_connection_busy_timeout(self) -> None:
        path = self.tmp_path / "restore.db"
        _make_counter_db(path)
        conn = db.connect(path, timeout_s=4.0)
        self.addCleanup(conn.close)
        with db.write_txn(conn, timeout_s=0.1) as cur:
            self.assertEqual(cur.execute("PRAGMA busy_timeout").fetchone()[0], 100)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 4000)

    def test_read_txn_never_holds_the_transaction(self) -> None:
        path = self.tmp_path / "read.db"
        _make_counter_db(path)
        conn = db.connect(path)
        self.addCleanup(conn.close)
        with db.read_txn(conn) as cur:
            self.assertEqual(cur.execute("SELECT n FROM counter WHERE id = 1").fetchone()[0], 0)
            self.assertTrue(conn.in_transaction)
        # A reader that keeps its transaction open pins the WAL past the
        # autocheckpoint (probed: 819KB-12.8MB). read_txn always rolls back.
        self.assertFalse(conn.in_transaction)


class NestedWriteTxnTest(CluTestCase):
    """A write transaction is never nested on one database, so it never joins.

    p5 gave `write_txn` a thread-local registry: a nested call on the same
    database JOINED the transaction already open, because SQLite's write lock
    is per-connection and the second `BEGIN IMMEDIATE` would otherwise wait for
    a lock only the waiter can release. The one caller that needed it was the
    supervisor consulting the quota gate from inside its own state window; the
    tick restructure removed that window, and with it the join.

    What these pin is the state after the removal: a genuine nest fails LOUDLY
    as contention rather than silently sharing a stranger's transaction, and
    `with write_txn(...)` means "committed at block exit" again.
    """

    def test_nesting_on_one_database_now_surfaces_as_contention(self) -> None:
        path = self.tmp_path / "nested.db"
        _make_counter_db(path)
        outer = db.connect(path, timeout_s=1.0)
        self.addCleanup(outer.close)
        inner = db.connect(path, timeout_s=1.0)
        self.addCleanup(inner.close)

        with db.write_txn(outer) as cur:
            cur.execute("UPDATE counter SET n = 1 WHERE id = 1")
            with self.assertRaises(db.DbBusy):
                with db.write_txn(inner, timeout_s=0.2):
                    pass
        self.assertEqual(_read_counter(path), 1)

    def test_the_block_commits_at_its_own_exit(self) -> None:
        # The join's real cost: with it, an inner block's work was still
        # uncommitted when the block ended. Without it, leaving the block means
        # the write is durable — which is what every caller reads it as.
        path = self.tmp_path / "commit-at-exit.db"
        _make_counter_db(path)
        conn = db.connect(path)
        self.addCleanup(conn.close)

        with db.write_txn(conn) as cur:
            cur.execute("UPDATE counter SET n = 7 WHERE id = 1")
        self.assertEqual(_read_counter(path), 7)
        self.assertFalse(conn.in_transaction)

    def test_two_different_databases_still_take_their_own_locks(self) -> None:
        # Two projects' databases have independent write locks — an open
        # transaction on one must not delay or swallow a write meant for
        # another.
        a, b = self.tmp_path / "a.db", self.tmp_path / "b.db"
        _make_counter_db(a)
        _make_counter_db(b)
        conn_a = db.connect(a)
        self.addCleanup(conn_a.close)
        conn_b = db.connect(b)
        self.addCleanup(conn_b.close)

        with db.write_txn(conn_a) as cur_a:
            cur_a.execute("UPDATE counter SET n = 3 WHERE id = 1")
            with db.write_txn(conn_b) as cur_b:
                cur_b.execute("UPDATE counter SET n = 4 WHERE id = 1")
            # B committed on its own, while A is still open.
            self.assertEqual(_read_counter(b), 4)
            self.assertEqual(_read_counter(a), 0)
        self.assertEqual(_read_counter(a), 3)

    def test_a_separate_thread_still_contends(self) -> None:
        # Two threads holding write transactions on one database are genuine
        # contention, and the loser waits out its budget and gives up.
        path = self.tmp_path / "threaded.db"
        _make_counter_db(path)
        holder = db.connect(path, timeout_s=CHILD_TIMEOUT_S)
        self.addCleanup(holder.close)
        outcome: list[str] = []
        release = threading.Event()
        acquired = threading.Event()

        def _other() -> None:
            conn = db.connect(path, timeout_s=CHILD_TIMEOUT_S)
            try:
                with db.write_txn(conn, timeout_s=0.2):
                    outcome.append("acquired")
            except db.DbBusy:
                outcome.append("busy")
            finally:
                conn.close()
                acquired.set()

        with db.write_txn(holder) as cur:
            cur.execute("UPDATE counter SET n = 1 WHERE id = 1")
            worker = threading.Thread(target=_other)
            worker.start()
            acquired.wait(timeout=CHILD_JOIN_S)
        release.set()
        worker.join(timeout=CHILD_JOIN_S)
        self.assertEqual(outcome, ["busy"], "another thread did not contend for the lock")


class SchemaTest(CluTestCase):
    def _project_conn(self, name: str = "p.db") -> sqlite3.Connection:
        conn = db.connect(self.tmp_path / name)
        self.addCleanup(conn.close)
        return conn

    def test_project_schema_stamps_user_version_and_is_idempotent(self) -> None:
        conn = self._project_conn()
        db.ensure_project_schema(conn)
        db.ensure_project_schema(conn)  # second call must be a no-op, not an error
        self.assertEqual(
            conn.execute("PRAGMA user_version").fetchone()[0], db.PROJECT_SCHEMA_VERSION
        )
        self.assertEqual(db.PROJECT_SCHEMA_VERSION, 1)

    def test_project_schema_creates_every_declared_table(self) -> None:
        conn = self._project_conn()
        db.ensure_project_schema(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual(
            {
                "plans",
                "claims",
                "blockers",
                "spawned_tasks",
                "events",
                "events_archive",
                "queue",
                "queue_history",
                "quota",
            },
            names,
        )

    def test_project_schema_indexes_the_event_read_paths(self) -> None:
        conn = self._project_conn()
        db.ensure_project_schema(conn)
        idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("idx_events_plan_id", idx)
        self.assertIn("idx_events_plan_type", idx)

    def test_quota_table_holds_at_most_one_row(self) -> None:
        # "row absent == not paused" only reads unambiguously if a second row
        # can't exist.
        conn = self._project_conn()
        db.ensure_project_schema(conn)
        with db.write_txn(conn) as cur:
            cur.execute("INSERT INTO quota (id, paused_until) VALUES (1, 'x')")
        with self.assertRaises(sqlite3.IntegrityError):
            with db.write_txn(conn) as cur:
                cur.execute("INSERT INTO quota (id, paused_until) VALUES (2, 'y')")

    def test_claims_reference_plans(self) -> None:
        conn = self._project_conn()
        db.ensure_project_schema(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            with db.write_txn(conn) as cur:
                cur.execute("INSERT INTO claims (plan_slug, phase_id) VALUES ('ghost', 'a')")

    def test_host_schema_stamps_user_version_and_is_idempotent(self) -> None:
        conn = db.connect(self.tmp_path / "h.db")
        self.addCleanup(conn.close)
        db.ensure_host_schema(conn)
        db.ensure_host_schema(conn)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.HOST_SCHEMA_VERSION)

    def test_host_schema_creates_every_declared_table(self) -> None:
        conn = db.connect(self.tmp_path / "h.db")
        self.addCleanup(conn.close)
        db.ensure_host_schema(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual(
            {
                "registry",
                "monitor",
                "inbound_state",
                "outbound_floors",
                "outbound_marks",
                "discord_dm_cache",
                "discord_cursor",
                "inbox",
                "skills",
            },
            names,
        )

    def test_a_newer_schema_refuses_rather_than_crashing(self) -> None:
        # Upstream decision #6: a DB written by a newer clu is skipped, never
        # silently downgraded — so the refusal has to be a nameable exception a
        # fleet walk can catch.
        conn = self._project_conn()
        db.ensure_project_schema(conn)
        conn.execute(f"PRAGMA user_version = {db.PROJECT_SCHEMA_VERSION + 1}")
        with self.assertRaises(db.SchemaTooNew):
            db.ensure_project_schema(conn)

    def test_host_schema_refuses_a_newer_user_version(self) -> None:
        conn = db.connect(self.tmp_path / "h.db")
        self.addCleanup(conn.close)
        db.ensure_host_schema(conn)
        conn.execute(f"PRAGMA user_version = {db.HOST_SCHEMA_VERSION + 99}")
        with self.assertRaises(db.SchemaTooNew):
            db.ensure_host_schema(conn)

    def test_an_up_to_date_schema_check_takes_no_write_lock(self) -> None:
        # Callers run ensure_*_schema on open. If the already-current case took
        # the write lock, every open would contend with the tick and the
        # heartbeats — a dashboard polling once a second would fight them
        # forever and could fail with DbBusy on a pure read path.
        db_path = self.tmp_path / "nolock.db"
        conn = self._project_conn("nolock.db")
        db.ensure_project_schema(conn)

        ready = self.tmp_path / "held.flag"
        release = self.tmp_path / "release.flag"
        ctx = mp.get_context("spawn")
        holder = ctx.Process(
            target=_holder_worker,
            args=(str(db_path), str(ready), str(release), 30.0),
        )
        # The holder needs a row to update; the schema check must survive it.
        with db.write_txn(conn) as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY, n INTEGER)")
            cur.execute("INSERT OR REPLACE INTO counter (id, n) VALUES (1, 0)")
        holder.start()
        self.addCleanup(holder.join, CHILD_JOIN_S)
        self.addCleanup(release.touch)
        deadline = time.monotonic() + CHILD_JOIN_S
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), "holder child never took the write transaction")

        # With the write lock held elsewhere, this must still return at once.
        started = time.monotonic()
        db.ensure_project_schema(conn)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0, f"an up-to-date schema check blocked for {elapsed:.3f}s")

    def test_a_readonly_connection_can_still_refuse_a_newer_schema(self) -> None:
        # The refusal is what a fleet walk catches per project before moving on,
        # so it has to be reachable from a read-only handle. Taking the write
        # lock first would raise "attempt to write a readonly database" instead,
        # burying the real reason.
        path = self.tmp_path / "newer.db"
        rw = db.connect(path)
        db.ensure_project_schema(rw)
        rw.execute(f"PRAGMA user_version = {db.PROJECT_SCHEMA_VERSION + 1}")
        rw.close()
        ro = db.connect(path, readonly=True)
        self.addCleanup(ro.close)
        with self.assertRaises(db.SchemaTooNew):
            db.ensure_project_schema(ro)

    def test_created_project_db_is_0600(self) -> None:
        path = db.project_db_path(self.tmp_path / "plans" / ".orchestrator")
        conn = db.connect(path)
        self.addCleanup(conn.close)
        db.ensure_project_schema(conn)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)


class PathTest(CluTestCase):
    def test_project_db_path_is_the_orchestrator_dir(self) -> None:
        orch = self.tmp_path / "plans" / ".orchestrator"
        self.assertEqual(db.project_db_path(orch), orch / db.DB_FILENAME)

    def test_host_db_path_follows_xdg(self) -> None:
        # CluTestCase points XDG_CONFIG_HOME at tmp_path and HOME at a sibling,
        # so this resolves inside the test sandbox and assert_xdg_safe passes.
        self.assertEqual(db.host_db_path(), self.tmp_path / "clu" / db.DB_FILENAME)


if __name__ == "__main__":
    import unittest

    unittest.main()
