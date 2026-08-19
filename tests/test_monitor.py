"""Tests for the marker primitive (written by `clu install-hook`).

The marker lives in the `monitor` table of the host database at
`$XDG_CONFIG_HOME/clu/clu.db` (default `~/.config/clu/clu.db`) and signals
that the UserPromptSubmit hook for surfacing clu inbox events is installed.
Account-wide, not per-project.

Tolerance contract: `load_marker` / `is_scheduled` treat "no rows", "no
database", and "a database a newer clu wrote" the same way — no exception,
returns None/False — so callers can branch on a single "do we need to
install?" predicate without exception handling.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import db, monitor
from tests import isolate_monitor_marker, must


class MarkerStorePathTests(unittest.TestCase):
    def test_store_path_respects_xdg_config_home(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(db.host_db_path(), Path("/tmp/xdg") / "clu" / "clu.db")

    def test_store_path_defaults_to_home_dotconfig(self) -> None:
        env = dict(os.environ)
        env.pop("XDG_CONFIG_HOME", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                db.host_db_path(),
                Path.home() / ".config" / "clu" / "clu.db",
            )


class MarkerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)

    def _record(self) -> None:
        monitor.record_hook_installed(
            "/abs/hook.py",
            "/home/x/.claude/settings.json",
        )

    def test_is_scheduled_returns_false_when_absent(self) -> None:
        self.assertFalse(db.host_db_path().exists())
        self.assertFalse(monitor.is_scheduled())

    def test_is_scheduled_returns_true_when_present(self) -> None:
        self._record()
        self.assertTrue(monitor.is_scheduled())

    def test_is_scheduled_returns_false_when_the_store_is_unreadable(self) -> None:
        path = db.host_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not a database")
        self.assertFalse(monitor.is_scheduled())

    def test_is_scheduled_returns_false_when_the_schema_is_newer(self) -> None:
        self._record()
        conn = sqlite3.connect(str(db.host_db_path()))
        conn.execute(f"PRAGMA user_version = {db.HOST_SCHEMA_VERSION + 1}")
        conn.close()
        self.assertFalse(monitor.is_scheduled())

    def test_record_hook_installed_writes_marker(self) -> None:
        monitor.record_hook_installed("/abs/hook.py", "/home/x/settings.json")
        data = must(monitor.load_marker())
        self.assertEqual(data["schema_version"], monitor.SCHEMA_VERSION)
        self.assertEqual(data["hook_path"], "/abs/hook.py")
        self.assertEqual(data["settings_json_path"], "/home/x/settings.json")
        self.assertTrue(data["hook_installed_at"].endswith("Z"))

    def test_record_hook_installed_overwrites_existing(self) -> None:
        monitor.record_hook_installed("/old/hook.py", "/old/settings.json")
        monitor.record_hook_installed("/new/hook.py", "/new/settings.json")
        data = must(monitor.load_marker())
        self.assertEqual(data["hook_path"], "/new/hook.py")
        self.assertEqual(data["settings_json_path"], "/new/settings.json")

    def test_session_start_stamp_survives_a_reinstall(self) -> None:
        # #70: the two install paths are separate commands and neither may
        # erase the other's stamp.
        self._record()
        monitor.record_session_start_installed("/abs/session_start.py")
        monitor.record_hook_installed("/new/hook.py", "/new/settings.json")
        data = must(monitor.load_marker())
        self.assertEqual(data["session_start_hook_path"], "/abs/session_start.py")
        self.assertEqual(data["hook_path"], "/new/hook.py")
        self.assertTrue(data["session_start_installed_at"].endswith("Z"))

    def test_clear_marker_makes_is_scheduled_false(self) -> None:
        self._record()
        self.assertTrue(monitor.is_scheduled())
        monitor.clear_marker()
        self.assertFalse(monitor.is_scheduled())
        self.assertIsNone(monitor.load_marker())

    def test_clear_marker_idempotent_when_absent(self) -> None:
        self.assertFalse(monitor.is_scheduled())
        monitor.clear_marker()  # must not raise
        self.assertFalse(monitor.is_scheduled())

    def test_load_marker_returns_dict_when_present(self) -> None:
        monitor.record_hook_installed("/abs/hook.py", "/home/x/settings.json")
        loaded = must(monitor.load_marker())
        self.assertEqual(loaded["hook_path"], "/abs/hook.py")

    def test_load_marker_returns_none_when_absent(self) -> None:
        self.assertIsNone(monitor.load_marker())


if __name__ == "__main__":
    unittest.main()
