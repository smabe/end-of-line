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


class TestReleaseClaimAndEmit(TempStateMixin, unittest.TestCase):
    """The wrapper that delegates to release_claim and fires coolant.emit_stop.

    Snapshots phase_id + claimed_by BEFORE the release so the emit has
    stable fields to hand to coolant.
    """

    def test_emits_with_snapshot_fields_on_clean_release(self) -> None:
        data = st.empty_state("foo", "plans")
        token = st.claim_phase(data, "phase-a", lease_minutes=30)
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            st.release_claim_and_emit(
                data,
                expected_token=token, expected_phase="phase-a",
            )
        self.assertIsNone(data["current_claim"])
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        self.assertEqual(kwargs["session_id"], token)
        self.assertEqual(kwargs["agent_id"], "clu-foo-phase-a")
        self.assertEqual(kwargs["agent_type"], "clu-worker")

    def test_unconditional_release_still_emits(self) -> None:
        data = st.empty_state("foo", "plans")
        st.claim_phase(data, "phase-a", lease_minutes=30)
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            st.release_claim_and_emit(data)
        self.assertIsNone(data["current_claim"])
        emit.assert_called_once()

    def test_no_claim_no_emit(self) -> None:
        data = st.empty_state("foo", "plans")
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            st.release_claim_and_emit(data)
        emit.assert_not_called()

    def test_claim_mismatch_does_not_emit(self) -> None:
        data = st.empty_state("foo", "plans")
        st.claim_phase(data, "phase-a", lease_minutes=30)
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            with self.assertRaises(st.ClaimMismatch):
                st.release_claim_and_emit(
                    data,
                    expected_token="wrong-token", expected_phase="phase-a",
                )
        # Release was rejected; the claim still belongs to the right token.
        # Decrementing coolant here would lie about the worker's status.
        emit.assert_not_called()
        self.assertIsNotNone(data["current_claim"])

    def test_malformed_claim_skips_emit(self) -> None:
        """A claim missing phase_id or claimed_by is unsalvageable for coolant —
        prefer a silent skip over polluting the events log with empty fields."""
        data = st.empty_state("foo", "plans")
        data["current_claim"] = {"phase_id": "", "claimed_by": "tok"}
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            st.release_claim_and_emit(data)
        emit.assert_not_called()
        self.assertIsNone(data["current_claim"])

    def test_coolant_disabled_skips_emit_but_still_releases(self) -> None:
        data = st.empty_state("foo", "plans")
        st.claim_phase(data, "phase-a", lease_minutes=30)
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            st.release_claim_and_emit(data, coolant_enabled=False)
        # Release happened regardless of coolant config.
        self.assertIsNone(data["current_claim"])
        emit.assert_not_called()

    def test_coolant_script_override_passed_through(self) -> None:
        data = st.empty_state("foo", "plans")
        st.claim_phase(data, "phase-a", lease_minutes=30)
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            st.release_claim_and_emit(
                data, coolant_script_override="/opt/coolant/scripts",
            )
        emit.assert_called_once()
        self.assertEqual(
            emit.call_args.kwargs["script_override"], "/opt/coolant/scripts",
        )


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

    def test_add_emits_event_with_question(self) -> None:
        """Regression guard for #46: the EVENT_PHASE_BLOCKED payload
        must carry the question text so the --task-list projector
        renders the full BLOCKED msg, not just the blocker_id."""
        data = st.empty_state("foo", "plans")
        st.add_blocker(data, "phase-a", "Postgres or sqlite?", ["yes", "no"])
        event = data["events"][-1]
        self.assertEqual(event["type"], st.EVENT_PHASE_BLOCKED)
        self.assertEqual(event["question"], "Postgres or sqlite?")
        self.assertEqual(event["phase"], "phase-a")
        self.assertEqual(event["blocker_id"], "q-1")

    def test_add_emits_event_with_empty_question(self) -> None:
        """Empty question still serializes as an empty string on the
        event so projector code (which uses `event.get('question') or
        ''`) handles both None and '' uniformly."""
        data = st.empty_state("foo", "plans")
        st.add_blocker(data, "phase-a", "", [])
        event = data["events"][-1]
        self.assertEqual(event["question"], "")


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


class TestLockedJson(TempStateMixin, unittest.TestCase):
    """The generic lock+load+yield+save primitive (factored out of state.mutate
    and registry._mutate). state.mutate and registry._mutate are both thin
    wrappers around it now."""

    def test_works_with_custom_empty_factory(self) -> None:
        path = self.tmp / "custom.json"
        empty = lambda: {"schema_version": 1, "payload": "fresh"}
        with st.locked_json(path, expected_version=1, empty=empty) as data:
            self.assertEqual(data["payload"], "fresh")
            data["payload"] = "modified"
        reloaded = json.loads(path.read_text())
        self.assertEqual(reloaded["payload"], "modified")

    def test_raises_schema_mismatch(self) -> None:
        path = self.tmp / "wrong.json"
        path.write_text('{"schema_version": 7, "payload": "x"}')
        with self.assertRaises(st.SchemaVersionMismatch):
            with st.locked_json(
                path, expected_version=1, empty=lambda: {"schema_version": 1},
            ):
                pass

    def test_missing_file_without_empty_factory_raises(self) -> None:
        # Preserves state.mutate's pre-extraction behavior: callers that
        # don't pass an empty factory want FileNotFoundError on missing.
        path = self.tmp / "missing.json"
        with self.assertRaises(FileNotFoundError):
            with st.locked_json(path, expected_version=1):
                pass

    def test_atomic_rename_leaves_no_tmp_on_success(self) -> None:
        path = self.tmp / "atomic.json"
        empty = lambda: {"schema_version": 1, "rows": []}
        with st.locked_json(path, expected_version=1, empty=empty) as data:
            data["rows"].append("x")
        leftover = list(path.parent.glob("atomic.json.*.tmp"))
        self.assertEqual(leftover, [])

    def test_creates_parent_dir(self) -> None:
        path = self.tmp / "nested" / "deep" / "file.json"
        empty = lambda: {"schema_version": 1}
        with st.locked_json(path, expected_version=1, empty=empty):
            pass
        self.assertTrue(path.exists())


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
