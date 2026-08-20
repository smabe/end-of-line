"""Unit tests for end_of_line.state."""

from __future__ import annotations

import datetime as _dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from end_of_line import db
from end_of_line import state as st
from tests import write_state


class TempStateMixin:
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # The slug in the filename is the KEY the store is read back by, so
        # it matches the `plan_slug` these tests seed.
        self.state_path = self.tmp / "foo.state.json"

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


class TestStoreRoundTrip(TempStateMixin, unittest.TestCase):
    def test_seed_load_roundtrip(self) -> None:
        data = st.empty_state("foo", "plans")
        write_state(self.state_path, data)
        loaded = st.load(self.state_path)
        self.assertEqual(loaded["plan_slug"], "foo")

    def test_load_raises_file_not_found_for_a_plan_that_does_not_exist(self) -> None:
        # The contract every tolerant reader in the fleet catches by name: a
        # path that names no row answers the same way a missing file did.
        with self.assertRaises(FileNotFoundError):
            st.load(self.state_path)


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


class TestSchemaVersion(TempStateMixin, unittest.TestCase):
    """One loader, one version check — the database's own `user_version`.

    There is no longer a document with a `schema_version` field to compare, so
    `load` takes no expected version: a store written by a newer clu is refused
    by the store itself, and it still arrives as the `SchemaVersionMismatch`
    every tolerant reader catches (upstream decision #6 — skip, never
    downgrade).
    """

    def test_load_rejects_a_store_from_a_newer_clu(self) -> None:
        write_state(self.state_path, st.empty_state("foo", "plans"))
        conn = sqlite3.connect(str(db.project_db_path(self.state_path.parent)))
        conn.execute(f"PRAGMA user_version = {db.PROJECT_SCHEMA_VERSION + 1}")
        conn.close()
        with self.assertRaises(st.SchemaVersionMismatch):
            st.load(self.state_path)

    def test_load_accepts_the_current_version(self) -> None:
        write_state(self.state_path, st.empty_state("foo", "plans"))
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

    def _alive_with_cmdline(self, stdout: str, marker: str) -> bool:
        """Run claim_worker_alive against a faked `ps` cmdline (#76)."""
        from subprocess import CompletedProcess

        with (
            patch("end_of_line.state.os.kill", return_value=None),
            patch(
                "end_of_line.state.subprocess.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout=stdout, stderr=""
                ),
            ),
        ):
            return st.claim_worker_alive({"pid": 1}, cmdline_match=marker)

    def test_cmdline_match_prefix_collision_returns_false(self) -> None:
        # #76: slug `w1` died; a recycled PID now runs plan `w1-foo`'s worker.
        # The hyphen must NOT count as a token boundary that lets `w1` match.
        self.assertFalse(
            self._alive_with_cmdline("claude /clu-phase w1-foo a", "w1")
        )

    def test_cmdline_match_underscore_collision_returns_false(self) -> None:
        # `_` is in the slug charset too — `w1` must not match `w1_foo`.
        self.assertFalse(
            self._alive_with_cmdline("claude /clu-phase w1_foo a", "w1")
        )

    def test_cmdline_match_equals_separator_returns_true(self) -> None:
        # Operator templates may emit `--plan=<slug>`; `=` is a valid boundary,
        # so the whole-token match must still fire.
        self.assertTrue(
            self._alive_with_cmdline("clu heartbeat --plan=w1 --phase a", "w1")
        )

    def test_cmdline_match_exact_token_returns_true(self) -> None:
        # The slug as its own whitespace-bounded token is the common case.
        self.assertTrue(
            self._alive_with_cmdline("clu heartbeat --plan w1 --phase a", "w1")
        )


class AppendCpuSampleTestCase(unittest.TestCase):
    def _claim(self) -> dict:
        return {}

    def test_appends_sample(self) -> None:
        claim = self._claim()
        now = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)
        st.append_cpu_sample(claim, 0.5, now)
        self.assertEqual(len(claim["cpu_samples"]), 1)
        self.assertEqual(claim["cpu_samples"][0]["cpu"], 0.5)

    def test_trims_to_cap(self) -> None:
        claim = self._claim()
        cap = st.WORKER_IDLE_SAMPLE_CAP
        for i in range(cap + 5):
            now = _dt.datetime(2026, 1, 1, 12, i, 0, tzinfo=_dt.UTC)
            st.append_cpu_sample(claim, float(i), now)
        self.assertEqual(len(claim["cpu_samples"]), cap)
        # Last sample should be the most recent
        self.assertEqual(claim["cpu_samples"][-1]["cpu"], float(cap + 4))

    def test_keeps_most_recent_on_trim(self) -> None:
        claim = self._claim()
        cap = st.WORKER_IDLE_SAMPLE_CAP
        for i in range(cap + 3):
            now = _dt.datetime(2026, 1, 1, 12, i, 0, tzinfo=_dt.UTC)
            st.append_cpu_sample(claim, float(i), now)
        # Oldest samples (0, 1, 2) should be gone
        cpus = [s["cpu"] for s in claim["cpu_samples"]]
        self.assertNotIn(0.0, cpus)
        self.assertNotIn(1.0, cpus)
        self.assertNotIn(2.0, cpus)


class WorkerIdleWindowSatisfiedTestCase(unittest.TestCase):
    def _samples(self, count: int, span_minutes: float, cpu: float = 0.5) -> list[dict]:
        base = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)
        if count == 1:
            return [{"ts": base.isoformat(), "cpu": cpu}]
        step = (span_minutes * 60) / (count - 1)
        return [
            {
                "ts": (_dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)
                       + _dt.timedelta(seconds=i * step)).isoformat(),
                "cpu": cpu,
            }
            for i in range(count)
        ]

    def _now(self, span_minutes: float = 12.0) -> _dt.datetime:
        return _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC) + _dt.timedelta(
            minutes=span_minutes
        )

    def test_satisfied_with_sufficient_samples_and_span(self) -> None:

        claim = {"cpu_samples": self._samples(6, 12.0)}
        now = self._now(12.0)
        self.assertTrue(st.worker_idle_window_satisfied(claim, now))

    def test_not_satisfied_too_few_samples(self) -> None:

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
        now = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)
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


class TestWorkerDeathMarker(unittest.TestCase):
    """The dedup marker the daemon stamps so the supervisor doesn't re-notify."""

    def test_unset_reads_false(self) -> None:
        self.assertFalse(st.worker_death_already_reported({}))

    def test_mark_then_read_true(self) -> None:
        claim: dict = {}
        st.mark_worker_death_reported(claim, st._now_utc())
        self.assertTrue(st.worker_death_already_reported(claim))
        self.assertIn("worker_death_reported_at", claim)


if __name__ == "__main__":
    unittest.main()
