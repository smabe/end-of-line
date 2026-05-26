"""Tests for marker schema v1 → v2 migration.

v1 (legacy `/schedule` install): {schema_version: 1, schedule_id, cadence,
scheduled_at}.

v2 (current hook install): {schema_version: 2, hook_installed_at,
hook_path, settings_json_path}.

`is_scheduled()` must return True only for v2 markers — v1 markers were
written by the broken `/schedule` skill and represent "needs reinstall."
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from end_of_line import monitor
from tests import isolate_monitor_marker


class V1MarkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        self.path = monitor.marker_path()

    def _write_v1(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "scheduled_at": "2026-05-12T00:00:00Z",
                    "schedule_id": "sch-legacy",
                    "cadence": "*/15 * * * *",
                }
            )
        )

    def test_is_scheduled_returns_false_for_v1_marker(self) -> None:
        self._write_v1()
        self.assertFalse(monitor.is_scheduled())

    def test_load_marker_returns_none_for_v1_marker(self) -> None:
        self._write_v1()
        self.assertIsNone(monitor.load_marker())

    def test_record_hook_installed_writes_v2(self) -> None:
        monitor.record_hook_installed(
            hook_path="/abs/path/to/clu_inbox_surface.py",
            settings_json_path="/home/x/.claude/settings.json",
        )
        data = json.loads(self.path.read_text())
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["hook_path"], "/abs/path/to/clu_inbox_surface.py")
        self.assertEqual(
            data["settings_json_path"],
            "/home/x/.claude/settings.json",
        )
        self.assertTrue(data["hook_installed_at"].endswith("Z"))

    def test_v1_marker_overwritten_by_v2_install(self) -> None:
        self._write_v1()
        monitor.record_hook_installed(
            hook_path="/abs/path/to/clu_inbox_surface.py",
            settings_json_path="/home/x/.claude/settings.json",
        )
        # No stale v1 data — fields fully replaced.
        data = json.loads(self.path.read_text())
        self.assertEqual(data["schema_version"], 2)
        self.assertNotIn("schedule_id", data)
        self.assertNotIn("cadence", data)
        self.assertTrue(monitor.is_scheduled())

    def test_is_scheduled_returns_true_for_v2_marker(self) -> None:
        monitor.record_hook_installed(
            hook_path="/a/b/c.py",
            settings_json_path="/home/x/.claude/settings.json",
        )
        self.assertTrue(monitor.is_scheduled())


if __name__ == "__main__":
    unittest.main()
