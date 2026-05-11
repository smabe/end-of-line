"""Host-level plan registry (Day-2 Cliff 2 dependency)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import registry, state as st


class RegistryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.reg_path = self.tmp / "registry.json"
        # Stand up a real directory so register()'s is_dir() check passes.
        self.project = self.tmp / "myproject"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_load_missing_file_returns_empty(self) -> None:
        data = registry._load(self.reg_path)
        self.assertEqual(data["schema_version"], registry.SCHEMA_VERSION)
        self.assertEqual(data["plans"], [])

    def test_register_creates_file(self) -> None:
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

    def test_register_multiple_projects(self) -> None:
        other = self.tmp / "other-project"
        other.mkdir()
        registry.register(self.project, "plan-a", path=self.reg_path)
        registry.register(other, "plan-a", path=self.reg_path)
        rows = registry.entries(self.reg_path)
        self.assertEqual(len(rows), 2)
        roots = {row.project_root for row in rows}
        self.assertEqual(roots, {str(self.project.resolve()), str(other.resolve())})

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

    def test_unregister_when_file_absent(self) -> None:
        self.assertFalse(registry.unregister(self.project, "plan-a", path=self.reg_path))

    def test_schema_mismatch_raises(self) -> None:
        self.reg_path.parent.mkdir(parents=True, exist_ok=True)
        self.reg_path.write_text(json.dumps({"schema_version": 99, "plans": []}))
        with self.assertRaises(st.SchemaVersionMismatch):
            registry._load(self.reg_path)

    def test_default_path_honors_xdg_config_home(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/clu-xdg"}):
            self.assertEqual(
                registry.registry_path(),
                Path("/tmp/clu-xdg/clu/registry.json"),
            )

    def test_default_path_falls_back_to_dot_config(self) -> None:
        # Pop XDG_CONFIG_HOME only — clear=True nukes PATH/HOME and breaks
        # tests that run later. mock.patch.dict(os.environ) snapshots and
        # restores everything on exit.
        with mock.patch.dict(os.environ):
            os.environ.pop("XDG_CONFIG_HOME", None)
            self.assertEqual(
                registry.registry_path(),
                Path.home() / ".config" / "clu" / "registry.json",
            )


if __name__ == "__main__":
    unittest.main()
