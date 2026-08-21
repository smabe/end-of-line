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


_BASE = _dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=_dt.UTC)


def _cum_samples(
    count: int,
    *,
    step_seconds: float = 30.0,
    start_cpu: float = 100.0,
    per_step: float = 0.0,
    base: _dt.datetime = _BASE,
) -> list[dict]:
    """Contiguous CUMULATIVE-CPU samples: `count` stamps `step_seconds` apart.

    `per_step` is how much processor time the tree accrues between samples —
    0.0 is the dormant worker the detector exists to catch.
    """
    return [
        {
            "ts": (base + _dt.timedelta(seconds=i * step_seconds)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "cpu": start_cpu + i * per_step,
        }
        for i in range(count)
    ]


class AppendCpuSampleTestCase(unittest.TestCase):
    def _claim(self) -> dict:
        return {}

    def test_appends_sample(self) -> None:
        claim = self._claim()
        st.append_cpu_sample(claim, 0.5, _BASE, retain_seconds=660.0)
        self.assertEqual(len(claim["cpu_samples"]), 1)
        self.assertEqual(claim["cpu_samples"][0]["cpu"], 0.5)

    def test_retires_samples_older_than_the_retention_window(self) -> None:
        claim = self._claim()
        for i in range(40):
            st.append_cpu_sample(
                claim,
                float(i),
                _BASE + _dt.timedelta(seconds=30 * i),
                retain_seconds=660.0,
            )
        stamps = [st.parse_iso(s["ts"]) for s in claim["cpu_samples"]]
        newest = stamps[-1]
        self.assertTrue(all((newest - t).total_seconds() <= 660.0 for t in stamps))
        # 660s of 30s-spaced history is 23 samples (t-660 .. t inclusive) —
        # more than the 20 the old count cap allowed, which is the whole point.
        self.assertEqual(len(stamps), 23)

    def test_retention_is_independent_of_tick_cadence(self) -> None:
        # Halving the cadence must not halve the history: 15s spacing keeps
        # twice as many samples covering the SAME wall-clock span.
        claim = self._claim()
        for i in range(80):
            st.append_cpu_sample(
                claim,
                float(i),
                _BASE + _dt.timedelta(seconds=15 * i),
                retain_seconds=660.0,
            )
        stamps = [st.parse_iso(s["ts"]) for s in claim["cpu_samples"]]
        self.assertEqual((stamps[-1] - stamps[0]).total_seconds(), 660.0)

    def test_absolute_cap_guards_unbounded_growth(self) -> None:
        # Every sample inside the retention window, so only the growth guard
        # can bound the list.
        claim = self._claim()
        for i in range(st.WORKER_IDLE_SAMPLE_CAP + 60):
            st.append_cpu_sample(claim, float(i), _BASE, retain_seconds=660.0)
        self.assertEqual(len(claim["cpu_samples"]), st.WORKER_IDLE_SAMPLE_CAP)
        self.assertEqual(claim["cpu_samples"][-1]["cpu"], float(st.WORKER_IDLE_SAMPLE_CAP + 59))


class WorkerIdleWindowSatisfiedTestCase(unittest.TestCase):
    """The window predicate: span, contiguity, recency, cumulative delta."""

    def _satisfied(self, samples: list[dict], now: _dt.datetime, **over) -> bool:
        kwargs: dict = {
            "min_samples": 3,
            "window_min": 10.0,
            "max_sample_gap": 60.0,
            "cpu_delta_threshold": 1.0,
        }
        kwargs.update(over)
        return st.worker_idle_window_satisfied({"cpu_samples": samples}, now, **kwargs)

    def _now(self, seconds_after_base: float) -> _dt.datetime:
        return _BASE + _dt.timedelta(seconds=seconds_after_base)

    def test_satisfied_when_dormant_across_a_contiguous_window(self) -> None:
        samples = _cum_samples(21)  # 21 × 30s = 600s of span
        self.assertTrue(self._satisfied(samples, self._now(600)))

    def test_not_satisfied_too_few_samples(self) -> None:
        samples = _cum_samples(2, step_seconds=600.0)
        self.assertFalse(self._satisfied(samples, self._now(600)))

    def test_span_is_measured_between_observed_samples_not_to_now(self) -> None:
        # 20 samples at 30s span only 570s — the arithmetic that made the old
        # predicate unsatisfiable under continuous sampling. `now` running on
        # past the newest sample must NOT stretch the observed span.
        samples = _cum_samples(20)
        self.assertFalse(self._satisfied(samples, self._now(570)))

    def test_not_satisfied_when_a_gap_breaks_contiguity(self) -> None:
        # The historical false-alarm shape: quiet samples, an interruption
        # longer than the max gap (positive evidence the worker was WORKING),
        # then more quiet samples. The union spans the window; the history
        # does not.
        head = _cum_samples(11)  # t=0 .. t=300
        tail = _cum_samples(11, base=_BASE + _dt.timedelta(seconds=420))  # t=420 .. 720
        self.assertFalse(self._satisfied(head + tail, self._now(720)))
        # Same shape with the gap closed IS satisfied — proving the gap, not
        # the sample count or the span, is what rejected it.
        self.assertTrue(self._satisfied(_cum_samples(25), self._now(720)))

    def test_not_satisfied_when_the_newest_sample_is_stale(self) -> None:
        # Sampling stopped two minutes ago: whatever the worker is doing now,
        # this window is no longer evidence about it.
        samples = _cum_samples(21)
        self.assertFalse(self._satisfied(samples, self._now(600 + 120)))

    def test_not_satisfied_when_cumulative_cpu_advanced(self) -> None:
        # 0.25s of processor time per 30s tick — the measured live-worker rate.
        samples = _cum_samples(21, per_step=0.25)
        self.assertFalse(self._satisfied(samples, self._now(600)))

    def test_boundary_delta_exactly_at_threshold(self) -> None:
        samples = _cum_samples(21, per_step=1.0 / 20)
        self.assertTrue(self._satisfied(samples, self._now(600)))

    def test_boundary_delta_just_above_threshold(self) -> None:
        samples = _cum_samples(21, per_step=1.01 / 20)
        self.assertFalse(self._satisfied(samples, self._now(600)))

    def test_negative_delta_is_cannot_judge_not_very_quiet(self) -> None:
        # A recycled pid or a descendant exiting mid-window shrinks the
        # cumulative sum. That reads as "less than zero CPU used", which is
        # not evidence of idleness — it is evidence the sum changed meaning.
        samples = _cum_samples(21)
        samples[-1]["cpu"] = 1.0
        self.assertFalse(self._satisfied(samples, self._now(600)))

    def test_not_satisfied_empty_samples(self) -> None:
        self.assertFalse(st.worker_idle_window_satisfied(
            {},
            _BASE,
            min_samples=3,
            window_min=10.0,
            max_sample_gap=60.0,
            cpu_delta_threshold=1.0,
        ))

    def test_empty_history_is_refused_even_with_a_zero_minimum(self) -> None:
        # The predicate indexes the newest stamp, so an empty history must be
        # refused on its own terms and not via the caller's minimum. Config
        # rejects a zero minimum; this pins the function itself.
        self.assertFalse(self._satisfied([], self._now(600), min_samples=0))

    def test_corrupt_timestamp_is_not_satisfied(self) -> None:
        samples = _cum_samples(21)
        samples[5]["ts"] = "not-a-timestamp"
        self.assertFalse(self._satisfied(samples, self._now(600)))


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
