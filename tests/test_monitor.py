"""Tests for the marker primitive (v2 — written by `clu install-hook`).

Marker file lives at `$XDG_CONFIG_HOME/clu/monitor.json` (default
`~/.config/clu/monitor.json`) and signals that the UserPromptSubmit
hook for surfacing clu inbox events is installed. Account-wide, not
per-project; mirrors the `registry.registry_path()` XDG resolution
pattern.

v1 markers (legacy `/schedule` install) are covered separately by
`test_monitor_migration.py` — they read as "not scheduled" so the CLI
hint fires and reinstall runs cleanly.

Tolerance contract: load_marker / is_scheduled treat "missing", "corrupt
JSON", "schema mismatch", and v1 markers the same way — no exception,
returns None/False — so callers can branch on a single "do we need to
install?" predicate without exception handling.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import monitor
from tests import isolate_monitor_marker, must


class MarkerPathTests(unittest.TestCase):
    def test_marker_path_respects_xdg_config_home(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(
                monitor.marker_path(),
                Path("/tmp/xdg") / "clu" / "monitor.json",
            )

    def test_marker_path_defaults_to_home_dotconfig(self) -> None:
        env = dict(os.environ)
        env.pop("XDG_CONFIG_HOME", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                monitor.marker_path(),
                Path.home() / ".config" / "clu" / "monitor.json",
            )


class MarkerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        self.path = monitor.marker_path()

    def _record(self) -> None:
        monitor.record_hook_installed(
            "/abs/hook.py",
            "/home/x/.claude/settings.json",
        )

    def test_is_scheduled_returns_false_when_absent(self) -> None:
        self.assertFalse(self.path.exists())
        self.assertFalse(monitor.is_scheduled())

    def test_is_scheduled_returns_true_when_present(self) -> None:
        self._record()
        self.assertTrue(monitor.is_scheduled())

    def test_is_scheduled_returns_false_when_corrupt(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("not json {{{")
        self.assertFalse(monitor.is_scheduled())

    def test_is_scheduled_returns_false_when_schema_mismatch(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 999,
                    "hook_installed_at": "2026-05-12T00:00:00Z",
                    "hook_path": "/x",
                    "settings_json_path": "/y",
                }
            )
        )
        self.assertFalse(monitor.is_scheduled())

    def test_record_hook_installed_writes_marker(self) -> None:
        monitor.record_hook_installed("/abs/hook.py", "/home/x/settings.json")
        data = json.loads(self.path.read_text())
        self.assertEqual(data["schema_version"], monitor.SCHEMA_VERSION)
        self.assertEqual(data["hook_path"], "/abs/hook.py")
        self.assertEqual(data["settings_json_path"], "/home/x/settings.json")
        self.assertTrue(data["hook_installed_at"].endswith("Z"))

    def test_record_hook_installed_overwrites_existing(self) -> None:
        monitor.record_hook_installed("/old/hook.py", "/old/settings.json")
        monitor.record_hook_installed("/new/hook.py", "/new/settings.json")
        data = json.loads(self.path.read_text())
        self.assertEqual(data["hook_path"], "/new/hook.py")
        self.assertEqual(data["settings_json_path"], "/new/settings.json")

    def test_clear_marker_removes_file(self) -> None:
        self._record()
        self.assertTrue(self.path.exists())
        monitor.clear_marker()
        self.assertFalse(self.path.exists())

    def test_clear_marker_idempotent_when_absent(self) -> None:
        self.assertFalse(self.path.exists())
        monitor.clear_marker()  # must not raise
        self.assertFalse(self.path.exists())

    def test_load_marker_returns_dict_when_present(self) -> None:
        monitor.record_hook_installed("/abs/hook.py", "/home/x/settings.json")
        loaded = must(monitor.load_marker())
        self.assertEqual(loaded["hook_path"], "/abs/hook.py")

    def test_load_marker_returns_none_when_absent(self) -> None:
        self.assertIsNone(monitor.load_marker())

    def test_load_marker_returns_none_when_corrupt(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("garbage")
        self.assertIsNone(monitor.load_marker())


if __name__ == "__main__":
    unittest.main()
