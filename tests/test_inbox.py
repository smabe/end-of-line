"""Tests for `end_of_line.inbox` — per-event inbox surfaced to active
Claude Code sessions via the UserPromptSubmit hook.

Inbox lives at `$XDG_CONFIG_HOME/clu/inbox/` (default `~/.config/clu/inbox/`).
One JSON file per event; `processed/` subdirectory holds mark-and-sweep
dedup history. Filenames carry a random short id so concurrent writers
can't collide on filename.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import inbox

from tests import isolate_monitor_marker


class InboxPathTests(unittest.TestCase):
    def test_inbox_path_respects_xdg_config_home(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(
                inbox.inbox_root(),
                Path("/tmp/xdg") / "clu" / "inbox",
            )

    def test_inbox_path_defaults_to_home_dotconfig(self) -> None:
        env = dict(os.environ)
        env.pop("XDG_CONFIG_HOME", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                inbox.inbox_root(),
                Path.home() / ".config" / "clu" / "inbox",
            )


class InboxLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        self.root = inbox.inbox_root()

    def test_write_event_creates_file_with_correct_shape(self) -> None:
        event_id = inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root="/x",
            summary="phase X halted at attempt 3",
            details={"reason": "max_attempts", "phase": "impl"},
        )
        self.assertTrue(event_id.startswith("evt-"))
        files = list(self.root.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertIn("halted", files[0].name)
        payload = json.loads(files[0].read_text())
        self.assertEqual(payload["id"], event_id)
        self.assertEqual(payload["schema_version"], inbox.SCHEMA_VERSION)
        self.assertEqual(payload["type"], "halted")
        self.assertEqual(payload["plan_slug"], "foo")
        self.assertEqual(payload["project_root"], "/x")
        self.assertEqual(payload["summary"], "phase X halted at attempt 3")
        self.assertEqual(payload["details"]["reason"], "max_attempts")
        self.assertTrue(payload["timestamp"].endswith("Z"))

    def test_write_event_race_free_filenames(self) -> None:
        ids = {
            inbox.write_event(
                type="halted",
                plan_slug="foo",
                project_root="/x",
                summary="s",
            )
            for _ in range(10)
        }
        files = list(self.root.glob("*.json"))
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(files), 10)

    def test_read_unprocessed_returns_all_in_inbox(self) -> None:
        for kind in ("halted", "blocked", "plan_completed"):
            inbox.write_event(
                type=kind,
                plan_slug="foo",
                project_root="/x",
                summary=kind,
            )
        events = inbox.read_unprocessed()
        self.assertEqual(len(events), 3)
        timestamps = [e["timestamp"] for e in events]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_read_unprocessed_excludes_processed(self) -> None:
        ids = [
            inbox.write_event(
                type="halted",
                plan_slug="foo",
                project_root="/x",
                summary="s",
            )
            for _ in range(3)
        ]
        inbox.mark_processed(ids[1])
        events = inbox.read_unprocessed()
        self.assertEqual(len(events), 2)
        self.assertNotIn(ids[1], [e["id"] for e in events])

    def test_read_unprocessed_handles_missing_inbox(self) -> None:
        # No inbox dir at all (no write_event called).
        self.assertFalse(self.root.exists())
        self.assertEqual(inbox.read_unprocessed(), [])

    def test_read_unprocessed_tolerates_corrupt_file(self) -> None:
        # Corrupt JSON should be skipped, not raise.
        inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root="/x",
            summary="s",
        )
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "garbage.json").write_text("{{{ not json")
        events = inbox.read_unprocessed()
        self.assertEqual(len(events), 1)

    def test_mark_processed_moves_file_to_subdir(self) -> None:
        event_id = inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root="/x",
            summary="s",
        )
        inbox.mark_processed(event_id)
        unprocessed = list(self.root.glob("*.json"))
        self.assertEqual(unprocessed, [])
        processed = list((self.root / "processed").glob("*.json"))
        self.assertEqual(len(processed), 1)

    def test_mark_processed_idempotent_when_missing(self) -> None:
        # Empty inbox → no error.
        inbox.mark_processed("evt-nonexistent")
        # Inbox with content but id absent → no error, no mutation.
        kept = inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root="/x",
            summary="s",
        )
        inbox.mark_processed("evt-nonexistent")
        self.assertEqual(len(list(self.root.glob("*.json"))), 1)
        self.assertEqual(inbox.read_unprocessed()[0]["id"], kept)

    def test_list_for_project_filters_by_root(self) -> None:
        # Use real paths so Path.resolve() is well-defined.
        a = self.tmp / "proj-a"
        b = self.tmp / "proj-b"
        a.mkdir()
        b.mkdir()
        inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root=str(a),
            summary="s",
        )
        inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root=str(a),
            summary="t",
        )
        inbox.write_event(
            type="halted",
            plan_slug="bar",
            project_root=str(b),
            summary="u",
        )
        a_events = inbox.list_for_project(str(a))
        self.assertEqual(len(a_events), 2)
        for e in a_events:
            self.assertEqual(e["project_root"], str(a.resolve()))

    def test_list_for_project_handles_missing_inbox(self) -> None:
        self.assertFalse(self.root.exists())
        self.assertEqual(inbox.list_for_project("/anywhere"), [])


if __name__ == "__main__":
    unittest.main()
