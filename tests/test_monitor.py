"""Tests for the `/clu-monitor` marker primitive.

Marker file lives at `$XDG_CONFIG_HOME/clu/monitor.json` (default
`~/.config/clu/monitor.json`) and signals that background notification
monitoring is already scheduled. Account-wide, not per-project; mirrors
the `registry.registry_path()` XDG resolution pattern.

Tolerance contract: load_marker / is_scheduled treat "missing", "corrupt
JSON", and "schema mismatch" the same way — no exception, returns
None/False — so callers can branch on a single "do we need to schedule?"
predicate without exception handling.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import monitor

from tests import isolate_monitor_marker


class MarkerPathTests(unittest.TestCase):
    def test_marker_path_respects_xdg_config_home(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(
                monitor.marker_path(), Path("/tmp/xdg") / "clu" / "monitor.json",
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

    def test_is_scheduled_returns_false_when_absent(self) -> None:
        self.assertFalse(self.path.exists())
        self.assertFalse(monitor.is_scheduled())

    def test_is_scheduled_returns_true_when_present(self) -> None:
        monitor.record_scheduled("sch-123", "*/15 * * * *")
        self.assertTrue(monitor.is_scheduled())

    def test_is_scheduled_returns_false_when_corrupt(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("not json {{{")
        self.assertFalse(monitor.is_scheduled())

    def test_is_scheduled_returns_false_when_schema_mismatch(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "schema_version": 999,
            "scheduled_at": "2026-05-12T00:00:00Z",
            "schedule_id": "sch-x",
            "cadence": "*/15 * * * *",
        }))
        self.assertFalse(monitor.is_scheduled())

    def test_record_scheduled_writes_marker(self) -> None:
        monitor.record_scheduled("sch-abc", "*/15 8-21 * * *")
        data = json.loads(self.path.read_text())
        self.assertEqual(data["schema_version"], monitor.SCHEMA_VERSION)
        self.assertEqual(data["schedule_id"], "sch-abc")
        self.assertEqual(data["cadence"], "*/15 8-21 * * *")
        self.assertTrue(data["scheduled_at"].endswith("Z"))

    def test_record_scheduled_overwrites_existing(self) -> None:
        monitor.record_scheduled("sch-old", "*/30 * * * *")
        monitor.record_scheduled("sch-new", "*/15 * * * *")
        data = json.loads(self.path.read_text())
        self.assertEqual(data["schedule_id"], "sch-new")
        self.assertEqual(data["cadence"], "*/15 * * * *")

    def test_clear_marker_removes_file(self) -> None:
        monitor.record_scheduled("sch-abc", "*/15 * * * *")
        self.assertTrue(self.path.exists())
        monitor.clear_marker()
        self.assertFalse(self.path.exists())

    def test_clear_marker_idempotent_when_absent(self) -> None:
        self.assertFalse(self.path.exists())
        monitor.clear_marker()  # must not raise
        self.assertFalse(self.path.exists())

    def test_load_marker_returns_dict_when_present(self) -> None:
        monitor.record_scheduled("sch-abc", "*/15 * * * *")
        loaded = monitor.load_marker()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["schedule_id"], "sch-abc")

    def test_load_marker_returns_none_when_absent(self) -> None:
        self.assertIsNone(monitor.load_marker())

    def test_load_marker_returns_none_when_corrupt(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("garbage")
        self.assertIsNone(monitor.load_marker())


if __name__ == "__main__":
    unittest.main()
