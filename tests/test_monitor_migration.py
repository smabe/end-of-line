"""Tests for what the marker's move into the host database left behind.

The marker used to be `~/.config/clu/monitor.json`, in two schemas: v1 (the
broken legacy `/schedule` install) and v2 (the hook install). Both are now
inert files — the marker is a set of rows in the host database, and "no rows"
is the entire encoding of "not installed".

That makes the v1 branch's job fall out for free, and these tests pin it: a
host carrying EITHER leftover file still reads as un-monitored, so the CLI
hint fires and `/clu-monitor` reinstalls cleanly. A file that resurrected the
marker would be the failure — it would suppress the hint on a host where the
hook is not actually wired to anything.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from end_of_line import monitor
from end_of_line._xdg_guard import clu_config_dir
from tests import isolate_monitor_marker, must


class LegacyMarkerFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        self.legacy = clu_config_dir() / monitor.LEGACY_MARKER_FILENAME
        self.legacy.parent.mkdir(parents=True, exist_ok=True)

    def _write_v1(self) -> None:
        self.legacy.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "scheduled_at": "2026-05-12T00:00:00Z",
                    "schedule_id": "sch-legacy",
                    "cadence": "*/15 * * * *",
                }
            )
        )

    def _write_v2(self) -> None:
        self.legacy.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "hook_installed_at": "2026-05-12T00:00:00Z",
                    "hook_path": "/legacy/hook.py",
                    "settings_json_path": "/legacy/settings.json",
                }
            )
        )

    def test_a_leftover_v1_file_does_not_make_the_host_monitored(self) -> None:
        self._write_v1()
        self.assertIsNone(monitor.load_marker())

    def test_a_leftover_v2_file_does_not_make_the_host_monitored(self) -> None:
        # No JSON import: the file is inert, and the fleet-quiet precondition
        # makes "reinstall the hook" the correct answer on first run.
        self._write_v2()
        self.assertIsNone(monitor.load_marker())

    def test_installing_over_a_legacy_file_carries_none_of_its_fields(self) -> None:
        self._write_v1()
        monitor.record_hook_installed(
            hook_path="/abs/path/to/clu_inbox_surface.py",
            settings_json_path="/home/x/.claude/settings.json",
        )
        data = must(monitor.load_marker())
        self.assertEqual(data["hook_path"], "/abs/path/to/clu_inbox_surface.py")
        self.assertEqual(data["settings_json_path"], "/home/x/.claude/settings.json")
        self.assertNotIn("schedule_id", data)
        self.assertNotIn("cadence", data)


if __name__ == "__main__":
    unittest.main()
