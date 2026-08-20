"""Tests for `end_of_line.inbox` — per-event inbox surfaced to active
Claude Code sessions via the UserPromptSubmit hook.

The inbox is the `inbox` table of the host database at
`$XDG_CONFIG_HOME/clu/clu.db` (default `~/.config/clu/clu.db`). One row per
event, ordered by autoincrement id; a `processed` flag replaces the
move-into-`processed/` protocol the directory version used.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import db, inbox
from tests import isolate_monitor_marker


class InboxStorePathTests(unittest.TestCase):
    """The inbox reads and writes the host database, wherever XDG points."""

    def test_store_path_respects_xdg_config_home(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(db.host_db_path(), Path("/tmp/xdg") / "clu" / "clu.db")


class InboxLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)

    def _write(
        self,
        *,
        type: str = "halted",
        plan_slug: str = "foo",
        project_root: str = "/x",
        summary: str = "s",
        details: dict | None = None,
    ) -> str:
        return inbox.write_event(
            type=type,
            plan_slug=plan_slug,
            project_root=project_root,
            summary=summary,
            details=details,
        )

    def test_write_event_records_the_full_shape(self) -> None:
        event_id = self._write(
            summary="phase X halted at attempt 3",
            details={"reason": "max_attempts", "phase": "impl"},
        )
        self.assertTrue(event_id.startswith("evt-"))
        events = inbox.read_unprocessed()
        self.assertEqual(len(events), 1)
        payload = events[0]
        self.assertEqual(payload["id"], event_id)
        self.assertEqual(payload["schema_version"], inbox.SCHEMA_VERSION)
        self.assertEqual(payload["type"], "halted")
        self.assertEqual(payload["plan_slug"], "foo")
        self.assertEqual(payload["project_root"], "/x")
        self.assertEqual(payload["summary"], "phase X halted at attempt 3")
        self.assertEqual(payload["details"]["reason"], "max_attempts")
        self.assertTrue(payload["timestamp"].endswith("Z"))

    def test_write_event_ids_are_collision_free(self) -> None:
        ids = {self._write() for _ in range(10)}
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(inbox.read_unprocessed()), 10)

    def test_read_unprocessed_returns_events_in_arrival_order(self) -> None:
        ids = [self._write(type=kind) for kind in ("halted", "blocked", "plan_completed")]
        events = inbox.read_unprocessed()
        self.assertEqual([e["id"] for e in events], ids)

    def test_read_unprocessed_excludes_processed(self) -> None:
        ids = [self._write() for _ in range(3)]
        inbox.mark_processed(ids[1])
        events = inbox.read_unprocessed()
        self.assertEqual(len(events), 2)
        self.assertNotIn(ids[1], [e["id"] for e in events])

    def test_read_unprocessed_handles_a_missing_store(self) -> None:
        self.assertFalse(db.host_db_path().exists())
        self.assertEqual(inbox.read_unprocessed(), [])

    def test_read_unprocessed_tolerates_an_unreadable_store(self) -> None:
        path = db.host_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{{{ not a database")
        self.assertEqual(inbox.read_unprocessed(), [])

    def test_read_unprocessed_skips_a_newer_schema(self) -> None:
        self._write()
        conn = sqlite3.connect(str(db.host_db_path()))
        conn.execute(f"PRAGMA user_version = {db.HOST_SCHEMA_VERSION + 1}")
        conn.close()
        self.assertEqual(inbox.read_unprocessed(), [])

    def test_mark_processed_flags_the_row_without_deleting_it(self) -> None:
        event_id = self._write()
        inbox.mark_processed(event_id)
        self.assertEqual(inbox.read_unprocessed(), [])
        with db.host_conn() as conn:
            row = conn.execute(
                "SELECT processed, processed_at FROM inbox WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        self.assertEqual(row[0], 1)
        self.assertTrue(row[1].endswith("Z"))

    def test_mark_processed_idempotent_when_missing(self) -> None:
        # Empty inbox → no error.
        inbox.mark_processed("evt-nonexistent")
        # Inbox with content but id absent → no error, no mutation.
        kept = self._write()
        inbox.mark_processed("evt-nonexistent")
        self.assertEqual([e["id"] for e in inbox.read_unprocessed()], [kept])

    def test_marking_twice_is_a_no_op(self) -> None:
        event_id = self._write()
        inbox.mark_processed(event_id)
        inbox.mark_processed(event_id)  # must not raise
        self.assertEqual(inbox.read_unprocessed(), [])

    def test_list_for_project_filters_by_root(self) -> None:
        # Use real paths so Path.resolve() is well-defined.
        a = self.tmp / "proj-a"
        b = self.tmp / "proj-b"
        a.mkdir()
        b.mkdir()
        self._write(project_root=str(a), summary="s")
        self._write(project_root=str(a), summary="t")
        self._write(plan_slug="bar", project_root=str(b), summary="u")
        a_events = inbox.list_for_project(str(a))
        self.assertEqual(len(a_events), 2)
        for e in a_events:
            self.assertEqual(e["project_root"], str(a.resolve()))

    def test_list_for_project_handles_a_missing_store(self) -> None:
        self.assertFalse(db.host_db_path().exists())
        self.assertEqual(inbox.list_for_project("/anywhere"), [])


class ClaimForProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        self.proj = self.tmp / "proj-a"
        self.proj.mkdir()

    def _write(self, n: int, root: Path | None = None) -> list[str]:
        return [
            inbox.write_event(
                type="halted",
                plan_slug="foo",
                project_root=str(root or self.proj),
                summary=f"event {i:02d}",
            )
            for i in range(n)
        ]

    def test_claim_flags_everything_it_returns_when_under_the_cap(self) -> None:
        ids = self._write(3)
        claimed = inbox.claim_for_project(str(self.proj), limit=20)
        self.assertEqual([e["id"] for e in claimed], ids)
        self.assertEqual(inbox.read_unprocessed(), [])

    def test_claim_is_scoped_to_the_project(self) -> None:
        other = self.tmp / "proj-b"
        other.mkdir()
        mine = self._write(2)
        theirs = self._write(1, root=other)
        claimed = inbox.claim_for_project(str(self.proj), limit=20)
        self.assertEqual([e["id"] for e in claimed], mine)
        self.assertEqual([e["id"] for e in inbox.read_unprocessed()], theirs)

    def test_claim_flags_only_the_newest_limit_but_returns_all(self) -> None:
        # The caller renders the newest `limit` and reports the count of the
        # rest. Claiming everything would consume events nobody saw; returning
        # only the claimed set would erase the count.
        ids = self._write(25)
        claimed = inbox.claim_for_project(str(self.proj), limit=20)
        self.assertEqual([e["id"] for e in claimed], ids)
        self.assertEqual(
            [e["id"] for e in inbox.read_unprocessed()],
            ids[:5],
        )

    def test_a_second_claim_surfaces_the_event_exactly_once(self) -> None:
        self._write(2)
        first = inbox.claim_for_project(str(self.proj), limit=20)
        second = inbox.claim_for_project(str(self.proj), limit=20)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])

    def test_claim_on_an_empty_inbox_returns_empty(self) -> None:
        self.assertEqual(inbox.claim_for_project(str(self.proj), limit=20), [])


if __name__ == "__main__":
    unittest.main()
