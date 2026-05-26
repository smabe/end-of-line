"""Tests for watch.stream_loop — polling loop, cursor, snapshot baseline."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.watch import stream_loop
from tests import CluTestCase

TS = "2026-05-17T10:00:00Z"


def _evt(type_: str, **fields) -> dict:
    return {"type": type_, "ts": TS, **fields}


def _make_state(
    path: Path,
    slug: str,
    *,
    status: str = "running",
    claim_phase: str | None = None,
    events: list | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    claim = None
    if claim_phase:
        claim = {
            "phase_id": claim_phase,
            "token": "tok-1",
            "expires": "2099-01-01T00:00:00Z",
            "attempts": 1,
        }
    data = {
        "schema_version": st.SCHEMA_VERSION,
        "plan_slug": slug,
        "plan_dir": str(path.parent.parent.parent),
        "status": status,
        "current_claim": claim,
        "blockers": [],
        "spawned_tasks": [],
        "config": {
            "lease_ttl_minutes": 30,
            "blocked_question_sla_hours": 24,
            "max_attempts_per_phase": 3,
            "max_spawns_per_phase": 5,
            "max_queue_adds_per_phase": 5,
            "stalled_heartbeat_minutes": 10,
        },
        "events": events or [],
        "created_at": TS,
    }
    path.write_text(json.dumps(data))


def _append_event(path: Path, event: dict) -> None:
    data = json.loads(path.read_text())
    data["events"].append(event)
    path.write_text(json.dumps(data))


class StreamLoopSnapshotTest(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.state_path = self.project / "plans" / ".orchestrator" / "my-plan.state.json"
        _make_state(self.state_path, "my-plan", claim_phase="foundation")

    def test_snapshot_baseline_emitted_on_start(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            json_mode=False,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=0,
        )
        out = sink.getvalue()
        self.assertIn("[snapshot]", out)
        self.assertIn("my-plan", out)
        self.assertIn("running", out)
        self.assertIn("active=foundation", out)

    def test_snapshot_baseline_active_none_when_no_claim(self) -> None:
        state_path = self.project / "plans" / ".orchestrator" / "no-claim.state.json"
        _make_state(state_path, "no-claim")
        sink = io.StringIO()
        stream_loop(
            [state_path],
            json_mode=False,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=0,
        )
        self.assertIn("active=none", sink.getvalue())


class StreamLoopCursorTest(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.state_path = self.project / "plans" / ".orchestrator" / "my-plan.state.json"
        _make_state(self.state_path, "my-plan", claim_phase="foundation")

    def test_new_event_emitted_after_baseline(self) -> None:
        sink = io.StringIO()

        def inject():
            _append_event(self.state_path, _evt(st.EVENT_PHASE_COMPLETED, phase="foundation"))

        stream_loop(
            [self.state_path],
            json_mode=False,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=1,
            _before_first_tick=inject,
        )
        self.assertIn("completed", sink.getvalue())

    def test_cursor_advances_no_duplicate_emit(self) -> None:
        sink = io.StringIO()
        injected = [False]

        def inject():
            if not injected[0]:
                _append_event(self.state_path, _evt(st.EVENT_PHASE_COMPLETED, phase="p"))
                injected[0] = True

        stream_loop(
            [self.state_path],
            json_mode=False,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=2,
            _before_first_tick=inject,
        )
        lines = [line for line in sink.getvalue().splitlines() if "completed" in line]
        self.assertEqual(len(lines), 1)

    def test_multiple_events_in_one_tick_all_emit(self) -> None:
        sink = io.StringIO()

        def inject():
            for e in [
                _evt(st.EVENT_PHASE_STARTED, phase="a", attempts=1),
                _evt(st.EVENT_PHASE_COMPLETED, phase="a"),
                _evt(st.EVENT_PLAN_COMPLETED),
            ]:
                _append_event(self.state_path, e)

        stream_loop(
            [self.state_path],
            json_mode=False,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=1,
            _before_first_tick=inject,
        )
        out = sink.getvalue()
        self.assertIn("started", out)
        self.assertIn("completed", out)
        self.assertIn("PLAN DONE", out)


class StreamLoopVerboseTest(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.state_path = self.project / "plans" / ".orchestrator" / "vp.state.json"
        _make_state(self.state_path, "vp")

    def _inject_lease_extended(self) -> None:
        _append_event(
            self.state_path,
            _evt(
                st.EVENT_LEASE_EXTENDED,
                phase="p",
                extended_by_minutes=30,
                new_expires="2099-01-01T01:00:00Z",
            ),
        )

    def test_verbose_only_event_filtered_default(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            json_mode=False,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=1,
            _before_first_tick=self._inject_lease_extended,
        )
        self.assertNotIn("lease extended", sink.getvalue())

    def test_verbose_flag_passes_through(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            json_mode=False,
            verbose=True,
            sink=sink,
            poll_interval=0,
            max_ticks=1,
            _before_first_tick=self._inject_lease_extended,
        )
        self.assertIn("lease extended", sink.getvalue())


class StreamLoopJsonModeTest(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.state_path = self.project / "plans" / ".orchestrator" / "jp.state.json"
        _make_state(self.state_path, "jp")

    def test_json_mode_emits_json_per_line(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            json_mode=True,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=1,
            _before_first_tick=lambda: _append_event(
                self.state_path, _evt(st.EVENT_PHASE_COMPLETED, phase="x")
            ),
        )
        lines = [line for line in sink.getvalue().splitlines() if line.startswith("{")]
        self.assertGreater(len(lines), 0)
        for line in lines:
            parsed = json.loads(line)
            self.assertIn("slug", parsed)
            self.assertIn("ts", parsed)
            self.assertIn("event", parsed)

    def test_json_mode_verbose_filter_applies(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            json_mode=True,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=1,
            _before_first_tick=lambda: _append_event(
                self.state_path, _evt(st.EVENT_LEASE_EXPIRED, phase="x")
            ),
        )
        lines = [line for line in sink.getvalue().splitlines() if line.startswith("{")]
        for line in lines:
            parsed = json.loads(line)
            self.assertNotEqual(parsed["event"].get("type"), st.EVENT_LEASE_EXPIRED)


class StreamLoopMissingFileTest(CluTestCase):
    def test_missing_state_file_dropped_silently(self) -> None:
        missing = self.tmp_path / "ghost.state.json"
        sink = io.StringIO()
        stream_loop(
            [missing],
            json_mode=False,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=1,
        )
        self.assertEqual(sink.getvalue(), "")

    def test_state_file_deleted_mid_watch(self) -> None:
        project = self.tmp_path / "project"
        state_path = project / "plans" / ".orchestrator" / "gone.state.json"
        _make_state(state_path, "gone")

        def delete_it():
            state_path.unlink()

        sink = io.StringIO()
        stream_loop(
            [state_path],
            json_mode=False,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=1,
            _before_first_tick=delete_it,
        )
        # No exception; path removed from cursors; only snapshot before deletion
        self.assertNotIn("ghost", sink.getvalue())


class StreamLoopMultiPlanTest(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        orch = self.project / "plans" / ".orchestrator"
        self.path_a = orch / "alpha.state.json"
        self.path_b = orch / "beta.state.json"
        _make_state(self.path_a, "alpha")
        _make_state(self.path_b, "beta")

    def test_two_plans_interleaved(self) -> None:
        sink = io.StringIO()

        def inject():
            _append_event(self.path_a, _evt(st.EVENT_PHASE_STARTED, phase="p1", attempts=1))
            _append_event(self.path_b, _evt(st.EVENT_PLAN_COMPLETED))

        stream_loop(
            [self.path_a, self.path_b],
            json_mode=False,
            verbose=False,
            sink=sink,
            poll_interval=0,
            max_ticks=1,
            _before_first_tick=inject,
        )
        out = sink.getvalue()
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        self.assertIn("started", out)
        self.assertIn("PLAN DONE", out)


class StreamLoopSigintTest(CluTestCase):
    def test_sigint_returns_ok(self) -> None:
        project = self.tmp_path / "project"
        state_path = project / "plans" / ".orchestrator" / "sig.state.json"
        _make_state(state_path, "sig")

        sink = io.StringIO()

        with mock.patch("end_of_line.watch.time") as mock_time:
            mock_time.sleep.side_effect = KeyboardInterrupt
            rc = stream_loop(
                [state_path],
                json_mode=False,
                verbose=False,
                sink=sink,
                poll_interval=1.0,
            )

        self.assertEqual(rc, 0)
        self.assertIn("\n", sink.getvalue())
