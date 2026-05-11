"""Unit tests for end_of_line.state."""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from end_of_line import state as st


class TempStateMixin:
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state_path = self.tmp / "test.state.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestEmptyState(unittest.TestCase):
    def test_has_required_top_level_keys(self) -> None:
        data = st.empty_state("foo", "plans")
        for key in (
            "schema_version", "plan_slug", "plan_dir", "status",
            "current_claim", "blockers", "spawned_tasks",
            "config", "events", "created_at",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["schema_version"], st.SCHEMA_VERSION)
        self.assertEqual(data["status"], "running")
        self.assertIsNone(data["current_claim"])


class TestAtomicWrite(TempStateMixin, unittest.TestCase):
    def test_save_load_roundtrip(self) -> None:
        data = st.empty_state("foo", "plans")
        with st.locked(self.state_path):
            st.save_atomic(self.state_path, data)
        loaded = st.load(self.state_path)
        self.assertEqual(loaded["plan_slug"], "foo")

    def test_save_atomic_leaves_no_tmp_on_success(self) -> None:
        data = st.empty_state("foo", "plans")
        with st.locked(self.state_path):
            st.save_atomic(self.state_path, data)
        leftover = list(self.state_path.parent.glob("test.state.json.*.tmp"))
        self.assertEqual(leftover, [])


class TestClaim(TempStateMixin, unittest.TestCase):
    def test_claim_when_idle(self) -> None:
        data = st.empty_state("foo", "plans")
        token = st.claim_phase(data, "phase-a", lease_minutes=30)
        self.assertIsNotNone(token)
        self.assertEqual(data["current_claim"]["phase_id"], "phase-a")
        self.assertEqual(data["current_claim"]["attempts"], 1)
        self.assertEqual(
            data["events"][-1]["type"], "phase_started",
        )

    def test_claim_raises_when_active(self) -> None:
        data = st.empty_state("foo", "plans")
        st.claim_phase(data, "phase-a", lease_minutes=30)
        with self.assertRaises(RuntimeError):
            st.claim_phase(data, "phase-b", lease_minutes=30)

    def test_claim_reclaims_after_lease_expiry(self) -> None:
        data = st.empty_state("foo", "plans")
        st.claim_phase(data, "phase-a", lease_minutes=30)
        # Manually expire the lease
        data["current_claim"]["lease_expires"] = "2020-01-01T00:00:00Z"
        token = st.claim_phase(data, "phase-a", lease_minutes=30)
        self.assertIsNotNone(token)
        self.assertEqual(data["current_claim"]["attempts"], 2)
        types = [e["type"] for e in data["events"]]
        self.assertIn("lease_expired", types)


class TestBlockers(TempStateMixin, unittest.TestCase):
    def test_add_and_answer(self) -> None:
        data = st.empty_state("foo", "plans")
        blocker_id = st.add_blocker(
            data, "phase-a", "Which one?", ["A", "B"], context="…",
        )
        self.assertEqual(blocker_id, "q-1")
        self.assertTrue(st.phase_has_open_blocker(data, "phase-a"))
        st.answer_blocker(data, blocker_id, "A")
        self.assertFalse(st.phase_has_open_blocker(data, "phase-a"))
        self.assertEqual(data["blockers"][0]["answer"], "A")

    def test_answer_unknown_raises(self) -> None:
        data = st.empty_state("foo", "plans")
        with self.assertRaises(KeyError):
            st.answer_blocker(data, "q-999", "A")

    def test_double_answer_raises(self) -> None:
        data = st.empty_state("foo", "plans")
        bid = st.add_blocker(data, "phase-a", "Q?", ["X"])
        st.answer_blocker(data, bid, "X")
        with self.assertRaises(KeyError):
            st.answer_blocker(data, bid, "Y")


class TestLockfileSymlink(TempStateMixin, unittest.TestCase):
    def test_refuses_symlink_lockfile(self) -> None:
        victim = self.tmp / "victim.txt"
        victim.write_text("don't truncate me")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        os.symlink(victim, lock_path)
        with self.assertRaises(OSError):
            with st.locked(self.state_path):
                pass
        self.assertEqual(victim.read_text(), "don't truncate me")

    def test_lockfile_created_with_600_mode(self) -> None:
        with st.locked(self.state_path):
            pass
        lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        mode = lock_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class TestSchemaVersion(TempStateMixin, unittest.TestCase):
    def test_load_rejects_future_version(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text('{"schema_version": 999, "events": []}')
        with self.assertRaises(st.SchemaVersionMismatch):
            st.load(self.state_path)

    def test_load_rejects_missing_version(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text('{"events": []}')
        with self.assertRaises(st.SchemaVersionMismatch):
            st.load(self.state_path)

    def test_load_accepts_current_version(self) -> None:
        with st.mutate(self.state_path) if False else st.locked(self.state_path):
            st.save_atomic(self.state_path, st.empty_state("foo", "plans"))
        loaded = st.load(self.state_path)
        self.assertEqual(loaded["plan_slug"], "foo")


class TestEvents(unittest.TestCase):
    def test_append_event(self) -> None:
        data = st.empty_state("foo", "plans")
        st.append_event(data, "custom", phase="a", note="hi")
        evt = data["events"][-1]
        self.assertEqual(evt["type"], "custom")
        self.assertEqual(evt["phase"], "a")

    def test_completed_phase_ids(self) -> None:
        data = st.empty_state("foo", "plans")
        st.append_event(data, "phase_completed", phase="a")
        st.append_event(data, "phase_completed", phase="b")
        st.append_event(data, "phase_started", phase="c")
        self.assertEqual(st.completed_phase_ids(data), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
