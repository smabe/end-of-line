"""Unit tests for end_of_line.state."""

from __future__ import annotations

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
            "schema_version",
            "plan_slug",
            "plan_dir",
            "status",
            "current_claim",
            "blockers",
            "spawned_tasks",
            "config",
            "events",
            "created_at",
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
            data["events"][-1]["type"],
            "phase_started",
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
                expected_token=token,
                expected_phase="phase-a",
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
                    expected_token="wrong-token",
                    expected_phase="phase-a",
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
                data,
                coolant_script_override="/opt/coolant/scripts",
            )
        emit.assert_called_once()
        self.assertEqual(
            emit.call_args.kwargs["script_override"],
            "/opt/coolant/scripts",
        )


class TestBlockers(TempStateMixin, unittest.TestCase):
    def test_add_and_answer(self) -> None:
        data = st.empty_state("foo", "plans")
        blocker_id = st.add_blocker(
            data,
            "phase-a",
            "Which one?",
            ["A", "B"],
            context="…",
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
        with st.locked_json(
            path,
            expected_version=1,
            empty=lambda: {"schema_version": 1, "payload": "fresh"},
        ) as data:
            self.assertEqual(data["payload"], "fresh")
            data["payload"] = "modified"
        reloaded = json.loads(path.read_text())
        self.assertEqual(reloaded["payload"], "modified")

    def test_raises_schema_mismatch(self) -> None:
        path = self.tmp / "wrong.json"
        path.write_text('{"schema_version": 7, "payload": "x"}')
        with self.assertRaises(st.SchemaVersionMismatch):
            with st.locked_json(
                path,
                expected_version=1,
                empty=lambda: {"schema_version": 1},
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
        with st.locked_json(
            path,
            expected_version=1,
            empty=lambda: {"schema_version": 1, "rows": []},
        ) as data:
            data["rows"].append("x")
        leftover = list(path.parent.glob("atomic.json.*.tmp"))
        self.assertEqual(leftover, [])

    def test_creates_parent_dir(self) -> None:
        path = self.tmp / "nested" / "deep" / "file.json"
        with st.locked_json(
            path,
            expected_version=1,
            empty=lambda: {"schema_version": 1},
        ):
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


class TestClaimWorkerAlive(unittest.TestCase):
    """Liveness probe used by the supervisor's dead-PID rule:
    ESRCH → dead, EPERM → alive, plus the cmdline-match guard.
    """

    def test_pid_none_returns_true(self) -> None:
        # Popen-to-_stamp_pid race: claim active but pid not yet stamped.
        # Default to alive so the supervisor doesn't kill a freshly-claimed phase.
        self.assertTrue(st.claim_worker_alive({}))
        self.assertTrue(st.claim_worker_alive({"pid": None}))

    def test_dead_pid_returns_false(self) -> None:
        # 99999 is well above any plausible live PID on a typical macOS / Linux
        # box and even if it happens to be live, cmdline_match would fail.
        with patch("end_of_line.state.os.kill", side_effect=ProcessLookupError):
            self.assertFalse(st.claim_worker_alive({"pid": 99999}))

    def test_live_pid_permission_error_treated_as_alive(self) -> None:
        # EPERM means the process exists but we lack signaling permission
        # (cross-user / sandboxed). Treat as alive — the process is there.
        with patch("end_of_line.state.os.kill", side_effect=PermissionError):
            self.assertTrue(st.claim_worker_alive({"pid": 1}))

    def test_cmdline_match_mismatch_returns_false(self) -> None:
        # PID is alive but cmdline doesn't match the expected /clu-phase
        # invocation → PID was reused. Treat as dead.
        from subprocess import CompletedProcess

        with (
            patch("end_of_line.state.os.kill", return_value=None),
            patch(
                "end_of_line.state.subprocess.run",
                return_value=CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="some other command",
                    stderr="",
                ),
            ),
        ):
            self.assertFalse(
                st.claim_worker_alive(
                    {"pid": 1},
                    cmdline_match="/clu-phase foo bar",
                )
            )

    def test_cmdline_match_hit_returns_true(self) -> None:
        from subprocess import CompletedProcess

        with (
            patch("end_of_line.state.os.kill", return_value=None),
            patch(
                "end_of_line.state.subprocess.run",
                return_value=CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="claude /clu-phase foo bar token",
                    stderr="",
                ),
            ),
        ):
            self.assertTrue(
                st.claim_worker_alive(
                    {"pid": 1},
                    cmdline_match="/clu-phase foo bar",
                )
            )


class AppendCpuSampleTestCase(unittest.TestCase):
    def _claim(self) -> dict:
        return {}

    def test_appends_sample(self) -> None:
        import datetime as _dt

        claim = self._claim()
        now = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
        st.append_cpu_sample(claim, 0.5, now)
        self.assertEqual(len(claim["cpu_samples"]), 1)
        self.assertEqual(claim["cpu_samples"][0]["cpu"], 0.5)

    def test_trims_to_cap(self) -> None:
        import datetime as _dt

        claim = self._claim()
        cap = st.WORKER_IDLE_SAMPLE_CAP
        for i in range(cap + 5):
            now = _dt.datetime(2026, 1, 1, 12, i, 0, tzinfo=_dt.timezone.utc)
            st.append_cpu_sample(claim, float(i), now)
        self.assertEqual(len(claim["cpu_samples"]), cap)
        # Last sample should be the most recent
        self.assertEqual(claim["cpu_samples"][-1]["cpu"], float(cap + 4))

    def test_keeps_most_recent_on_trim(self) -> None:
        import datetime as _dt

        claim = self._claim()
        cap = st.WORKER_IDLE_SAMPLE_CAP
        for i in range(cap + 3):
            now = _dt.datetime(2026, 1, 1, 12, i, 0, tzinfo=_dt.timezone.utc)
            st.append_cpu_sample(claim, float(i), now)
        # Oldest samples (0, 1, 2) should be gone
        cpus = [s["cpu"] for s in claim["cpu_samples"]]
        self.assertNotIn(0.0, cpus)
        self.assertNotIn(1.0, cpus)
        self.assertNotIn(2.0, cpus)


class WorkerIdleWindowSatisfiedTestCase(unittest.TestCase):
    def _samples(self, count: int, span_minutes: float, cpu: float = 0.5) -> list[dict]:
        import datetime as _dt

        base = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
        if count == 1:
            return [{"ts": base.isoformat(), "cpu": cpu}]
        step = (span_minutes * 60) / (count - 1)
        return [
            {
                "ts": (_dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
                       + _dt.timedelta(seconds=i * step)).isoformat(),
                "cpu": cpu,
            }
            for i in range(count)
        ]

    def _now(self, span_minutes: float = 12.0) -> "_dt.datetime":
        import datetime as _dt

        return _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.timezone.utc) + _dt.timedelta(
            minutes=span_minutes
        )

    def test_satisfied_with_sufficient_samples_and_span(self) -> None:
        import datetime as _dt

        claim = {"cpu_samples": self._samples(6, 12.0)}
        now = self._now(12.0)
        self.assertTrue(st.worker_idle_window_satisfied(claim, now))

    def test_not_satisfied_too_few_samples(self) -> None:
        import datetime as _dt

        claim = {"cpu_samples": self._samples(3, 12.0)}
        now = self._now(12.0)
        self.assertFalse(st.worker_idle_window_satisfied(claim, now))

    def test_not_satisfied_span_too_short(self) -> None:
        # 5 samples but only 8 minutes of span — below the 10-min window
        claim = {"cpu_samples": self._samples(5, 8.0)}
        now = self._now(8.0)
        self.assertFalse(st.worker_idle_window_satisfied(claim, now))

    def test_not_satisfied_high_cpu(self) -> None:
        # One sample above the threshold poisons the window
        samples = self._samples(6, 12.0, cpu=0.5)
        samples[3]["cpu"] = 30.0
        claim = {"cpu_samples": samples}
        now = self._now(12.0)
        self.assertFalse(st.worker_idle_window_satisfied(claim, now))

    def test_not_satisfied_empty_samples(self) -> None:
        claim: dict = {}
        import datetime as _dt

        now = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
        self.assertFalse(st.worker_idle_window_satisfied(claim, now))

    def test_boundary_exactly_at_threshold(self) -> None:
        # cpu exactly at threshold (1.0) should satisfy
        claim = {"cpu_samples": self._samples(6, 12.0, cpu=1.0)}
        now = self._now(12.0)
        self.assertTrue(st.worker_idle_window_satisfied(claim, now))

    def test_boundary_just_above_threshold(self) -> None:
        # cpu just above threshold should NOT satisfy
        claim = {"cpu_samples": self._samples(6, 12.0, cpu=1.01)}
        now = self._now(12.0)
        self.assertFalse(st.worker_idle_window_satisfied(claim, now))


if __name__ == "__main__":
    unittest.main()
