"""Host-level plan registry (Day-2 Cliff 2 dependency).

The registry lives in the `registry` table of the host database. The `path`
keyword on every function names that DATABASE — it is the same test seam the
JSON-file version had, pointed at a different kind of store.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import db, registry
from end_of_line import state as st


class RegistryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.reg_path = self.tmp / "clu.db"
        # Stand up a real directory so register()'s is_dir() check passes.
        self.project = self.tmp / "myproject"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_fresh_store_reads_as_empty(self) -> None:
        self.assertFalse(self.reg_path.exists())
        self.assertEqual(registry.entries(self.reg_path), [])

    def test_register_creates_the_database(self) -> None:
        added = registry.register(self.project, "plan-a", path=self.reg_path)
        self.assertTrue(added)
        self.assertTrue(self.reg_path.exists())
        rows = registry.entries(self.reg_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].plan_slug, "plan-a")
        self.assertEqual(rows[0].project_root, str(self.project.resolve()))

    def test_register_is_idempotent(self) -> None:
        registry.register(self.project, "plan-a", path=self.reg_path)
        added_again = registry.register(self.project, "plan-a", path=self.reg_path)
        self.assertFalse(added_again)
        self.assertEqual(len(registry.entries(self.reg_path)), 1)

    def test_register_multiple_plans_per_project(self) -> None:
        registry.register(self.project, "plan-a", path=self.reg_path)
        registry.register(self.project, "plan-b", path=self.reg_path)
        slugs = {row.plan_slug for row in registry.entries(self.reg_path)}
        self.assertEqual(slugs, {"plan-a", "plan-b"})

    def test_entries_preserve_registration_order(self) -> None:
        for slug in ("plan-c", "plan-a", "plan-b"):
            registry.register(self.project, slug, path=self.reg_path)
        self.assertEqual(
            [row.plan_slug for row in registry.entries(self.reg_path)],
            ["plan-c", "plan-a", "plan-b"],
        )

    def test_register_multiple_projects(self) -> None:
        other = self.tmp / "other-project"
        other.mkdir()
        registry.register(self.project, "plan-a", path=self.reg_path)
        registry.register(other, "plan-a", path=self.reg_path)
        rows = registry.entries(self.reg_path)
        self.assertEqual(len(rows), 2)
        roots = {row.project_root for row in rows}
        self.assertEqual(roots, {str(self.project.resolve()), str(other.resolve())})

    def test_entries_for_project_filters(self) -> None:
        other = self.tmp / "other-project"
        other.mkdir()
        registry.register(self.project, "plan-a", path=self.reg_path)
        registry.register(other, "plan-b", path=self.reg_path)
        rows = registry.entries_for_project(self.project, self.reg_path)
        self.assertEqual([r.plan_slug for r in rows], ["plan-a"])

    def test_register_validates_slug(self) -> None:
        with self.assertRaises(st.InvalidSlug):
            registry.register(self.project, "../escape", path=self.reg_path)

    def test_register_rejects_missing_project_dir(self) -> None:
        bogus = self.tmp / "does-not-exist"
        with self.assertRaises(FileNotFoundError):
            registry.register(bogus, "plan-a", path=self.reg_path)

    def test_unregister_removes_entry(self) -> None:
        registry.register(self.project, "plan-a", path=self.reg_path)
        registry.register(self.project, "plan-b", path=self.reg_path)
        removed = registry.unregister(self.project, "plan-a", path=self.reg_path)
        self.assertTrue(removed)
        slugs = {row.plan_slug for row in registry.entries(self.reg_path)}
        self.assertEqual(slugs, {"plan-b"})

    def test_unregister_missing_returns_false(self) -> None:
        registry.register(self.project, "plan-a", path=self.reg_path)
        self.assertFalse(registry.unregister(self.project, "plan-z", path=self.reg_path))

    def test_unregister_when_store_absent(self) -> None:
        self.assertFalse(registry.unregister(self.project, "plan-a", path=self.reg_path))

    def test_unregister_many_removes_a_batch_and_counts_it(self) -> None:
        for slug in ("plan-a", "plan-b", "plan-c"):
            registry.register(self.project, slug, path=self.reg_path)
        root = str(self.project.resolve())
        removed = registry._unregister_many(
            {(root, "plan-a"), (root, "plan-c")},
            path=self.reg_path,
        )
        self.assertEqual(removed, 2)
        self.assertEqual(
            [row.plan_slug for row in registry.entries(self.reg_path)],
            ["plan-b"],
        )

    def test_a_newer_schema_reads_as_empty_rather_than_raising(self) -> None:
        # Upstream decision #6: a database a newer clu wrote is SKIPPED. A
        # fleet walk must survive it, so `entries` degrades exactly the way a
        # missing file used to.
        registry.register(self.project, "plan-a", path=self.reg_path)
        conn = sqlite3.connect(str(self.reg_path))
        conn.execute(f"PRAGMA user_version = {db.HOST_SCHEMA_VERSION + 1}")
        conn.close()
        self.assertEqual(registry.entries(self.reg_path), [])


class RegistryStoreShapeTestCase(unittest.TestCase):
    """A round-trip leaves ONE file behind: the host database."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.tmp)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.project = self.tmp / "myproject"
        self.project.mkdir()

    def test_round_trip_leaves_only_clu_db_in_the_config_dir(self) -> None:
        self.assertTrue(registry.register(self.project, "plan-a"))
        self.assertEqual(
            [e.plan_slug for e in registry.entries_for_project(self.project)],
            ["plan-a"],
        )
        self.assertTrue(registry.unregister(self.project, "plan-a"))

        config_dir = self.tmp / "clu"
        names = sorted(p.name for p in config_dir.iterdir())
        # WAL sidecars are SQLite's own; anything else would be a store this
        # phase was supposed to retire.
        self.assertIn("clu.db", names)
        self.assertNotIn("registry.json", names)
        self.assertEqual([n for n in names if n.endswith(".lock")], [])
        self.assertEqual([n for n in names if n.endswith(".json")], [])


class RegistryPathTestCase(unittest.TestCase):
    """The store's default location still follows XDG."""

    def test_default_path_honors_xdg_config_home(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/clu-xdg"}):
            self.assertEqual(db.host_db_path(), Path("/tmp/clu-xdg/clu/clu.db"))

    def test_default_path_falls_back_to_dot_config(self) -> None:
        # Pop XDG_CONFIG_HOME only — clear=True nukes PATH/HOME and breaks
        # tests that run later. mock.patch.dict(os.environ) snapshots and
        # restores everything on exit.
        with mock.patch.dict(os.environ):
            os.environ.pop("XDG_CONFIG_HOME", None)
            self.assertEqual(
                db.host_db_path(),
                Path.home() / ".config" / "clu" / "clu.db",
            )


if __name__ == "__main__":
    unittest.main()
