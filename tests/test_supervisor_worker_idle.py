"""Tests for the _emit_worker_idle gap-fill in supervisor.py (wedge-watchdogs P2)."""

from __future__ import annotations

import datetime as _dt
import unittest

from end_of_line import inbox
from end_of_line import state as st
from end_of_line.config import ProjectConfig
from end_of_line.supervisor import _emit_worker_idle
from tests import CluTestCase, utcnow_minus


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _ts_minus(minutes: float) -> str:
    return (_now() - _dt.timedelta(minutes=minutes)).isoformat()


def _idle_samples(count: int = 6, span_minutes: float = 12.0, cpu: float = 0.3) -> list[dict]:
    """Generate count low-CPU samples spread over span_minutes, ending ~now."""
    base = _now() - _dt.timedelta(minutes=span_minutes)
    if count == 1:
        return [{"ts": base.isoformat(), "cpu": cpu}]
    step = (span_minutes * 60) / (count - 1)
    return [
        {
            "ts": (base + _dt.timedelta(seconds=i * step)).isoformat(),
            "cpu": cpu,
        }
        for i in range(count)
    ]


def _data_with_idle_claim(*, with_active_tool: bool = False, worker_pid: int = 42000) -> dict:
    data = st.empty_state("plan-y", "/tmp/plan-y")
    claim: dict = {
        "phase_id": "my-phase",
        "claimed_by": "session-xyz",
        "pid": worker_pid,
        "lease_expires": "2099-01-01T00:00:00Z",
        "started_at": utcnow_minus(800),
        "last_heartbeat_at": utcnow_minus(60),
        "attempts": 1,
        "cpu_samples": _idle_samples(),
    }
    if with_active_tool:
        claim["active_tool_started_at"] = utcnow_minus(120)
    data["current_claim"] = claim
    return data


_NO_ANTHROPIC_LSOF = "nothing here"
_ANTHROPIC_LSOF = "42000  claude  TCP ->api.anthropic.com:443 (ESTABLISHED)"


def _tree_snapshot(root_pid: int, child_pid: int | None = None) -> str:
    """A `ps -eo pid,ppid,etime,time,command` snapshot for the idle tree walk.

    Root line plus an optional single child whose ppid is root_pid. Mirrors the
    injection shape the stuck-tool tests use for `walk_worker_tree`.
    """
    lines = [
        "  PID  PPID    ELAPSED        TIME COMMAND",
        f"{root_pid}     1   13:00        0:30.00 claude --print /clu-phase plan-y my-phase",
    ]
    if child_pid is not None:
        lines.append(
            f"{child_pid} {root_pid}   12:00        0:05.00 python3 -m unittest discover"
        )
    return "\n".join(lines) + "\n"


class EmitWorkerIdleTestCase(CluTestCase):
    def _cfg(self) -> ProjectConfig:
        return ProjectConfig(project_root=self.tmp_path)

    def test_fires_after_threshold_window(self) -> None:
        data = _data_with_idle_claim()
        cfg = self._cfg()
        side_notifies: list = []
        _emit_worker_idle(data, cfg, side_notifies, lsof_output=_NO_ANTHROPIC_LSOF)
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(len(events), 1)
        self.assertIn("worker_idle", events[0]["type"])
        self.assertEqual(len(side_notifies), 1)
        kind, body = side_notifies[0]
        self.assertIn("idle", kind)
        self.assertIn("my-phase", body)

    def test_fires_writes_inbox_event(self) -> None:
        data = _data_with_idle_claim()
        cfg = self._cfg()
        _emit_worker_idle(data, cfg, [], lsof_output=_NO_ANTHROPIC_LSOF)
        events = inbox.read_unprocessed()
        worker_idle = [e for e in events if e["type"] == "worker_idle"]
        self.assertEqual(len(worker_idle), 1)
        self.assertEqual(worker_idle[0]["details"]["phase_id"], "my-phase")

    def test_idempotent_within_same_claim(self) -> None:
        data = _data_with_idle_claim()
        cfg = self._cfg()
        _emit_worker_idle(data, cfg, [], lsof_output=_NO_ANTHROPIC_LSOF)
        _emit_worker_idle(data, cfg, [], lsof_output=_NO_ANTHROPIC_LSOF)
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(len(events), 1)

    def test_suppressed_when_anthropic_socket_open(self) -> None:
        data = _data_with_idle_claim()
        cfg = self._cfg()
        side_notifies: list = []
        _emit_worker_idle(data, cfg, side_notifies, lsof_output=_ANTHROPIC_LSOF)
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(events, [])
        self.assertEqual(side_notifies, [])

    def test_suppressed_when_active_tool_present(self) -> None:
        data = _data_with_idle_claim(with_active_tool=True)
        cfg = self._cfg()
        side_notifies: list = []
        _emit_worker_idle(data, cfg, side_notifies, lsof_output=_NO_ANTHROPIC_LSOF)
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(events, [])
        self.assertEqual(side_notifies, [])

    def test_suppressed_when_high_cpu(self) -> None:
        data = _data_with_idle_claim()
        # Replace samples with one high-CPU sample in the window
        data["current_claim"]["cpu_samples"] = _idle_samples(6, 12.0, cpu=30.0)
        cfg = self._cfg()
        side_notifies: list = []
        _emit_worker_idle(data, cfg, side_notifies, lsof_output=_NO_ANTHROPIC_LSOF)
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(events, [])

    def test_suppressed_when_too_few_samples(self) -> None:
        data = _data_with_idle_claim()
        data["current_claim"]["cpu_samples"] = _idle_samples(3, 8.0, cpu=0.0)
        cfg = self._cfg()
        side_notifies: list = []
        _emit_worker_idle(data, cfg, side_notifies, lsof_output=_NO_ANTHROPIC_LSOF)
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(events, [])

    def test_notified_cleared_on_release_allows_re_fire(self) -> None:
        data = _data_with_idle_claim()
        cfg = self._cfg()
        _emit_worker_idle(data, cfg, [], lsof_output=_NO_ANTHROPIC_LSOF)
        self.assertTrue(data["current_claim"].get("worker_idle_notified"))

        # Release claim and re-claim the same phase fresh
        data["current_claim"] = None
        data["current_claim"] = {
            "phase_id": "my-phase",
            "claimed_by": "session-xyz2",
            "pid": 42001,
            "lease_expires": "2099-01-01T00:00:00Z",
            "started_at": utcnow_minus(800),
            "last_heartbeat_at": utcnow_minus(60),
            "attempts": 2,
            "cpu_samples": _idle_samples(),
        }
        side_notifies: list = []
        _emit_worker_idle(data, cfg, side_notifies, lsof_output=_NO_ANTHROPIC_LSOF)
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        # Two fires, one per claim
        self.assertEqual(len(events), 2)

    def test_no_emit_when_no_claim(self) -> None:
        data = st.empty_state("plan-y", "/tmp/plan-y")
        cfg = self._cfg()
        _emit_worker_idle(data, cfg, [], lsof_output=_NO_ANTHROPIC_LSOF)
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(events, [])

    def test_no_emit_when_no_pid(self) -> None:
        data = _data_with_idle_claim(worker_pid=0)
        data["current_claim"].pop("pid", None)
        cfg = self._cfg()
        _emit_worker_idle(data, cfg, [], lsof_output=_NO_ANTHROPIC_LSOF)
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(events, [])

    def test_busy_descendant_suppresses_idle(self) -> None:
        # Root pid reads ~idle but a child (test run) is burning CPU. Summing the
        # tree trips the >1% gate and suppresses the idle event; sampling
        # claim.pid alone (the pre-fix behavior) would have false-fired.
        data = _data_with_idle_claim()
        cfg = self._cfg()
        side_notifies: list = []
        _emit_worker_idle(
            data,
            cfg,
            side_notifies,
            tree_ps_output=_tree_snapshot(42000, 42001),
            ps_output="0.1\n30.0\n",
            lsof_output=_NO_ANTHROPIC_LSOF,
        )
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(events, [])
        self.assertEqual(side_notifies, [])

    def test_idle_tree_emits(self) -> None:
        # Root and child both idle → tree sum stays ≤1% → window proceeds.
        data = _data_with_idle_claim()
        cfg = self._cfg()
        side_notifies: list = []
        _emit_worker_idle(
            data,
            cfg,
            side_notifies,
            tree_ps_output=_tree_snapshot(42000, 42001),
            ps_output="0.2\n0.3\n",
            lsof_output=_NO_ANTHROPIC_LSOF,
        )
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(len(events), 1)

    def test_descendant_disappears_between_walk_and_ps(self) -> None:
        # walk_worker_tree found a child, but it exited before `ps -p` ran, so
        # ps returns only the root's line. No crash; sample from what survived.
        data = _data_with_idle_claim()
        cfg = self._cfg()
        side_notifies: list = []
        _emit_worker_idle(
            data,
            cfg,
            side_notifies,
            tree_ps_output=_tree_snapshot(42000, 42001),
            ps_output="0.2\n",
            lsof_output=_NO_ANTHROPIC_LSOF,
        )
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(len(events), 1)

    def test_no_descendants_matches_single_pid(self) -> None:
        # Today's common case: worker has no children. Behavior is identical to
        # the single-pid sampling it replaces.
        data = _data_with_idle_claim()
        cfg = self._cfg()
        side_notifies: list = []
        _emit_worker_idle(
            data,
            cfg,
            side_notifies,
            tree_ps_output=_tree_snapshot(42000),
            ps_output="0.3\n",
            lsof_output=_NO_ANTHROPIC_LSOF,
        )
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(len(events), 1)

    def test_emits_when_lsof_output_none_no_anthropic(self) -> None:
        # lsof_output=None means "no seam injected"; function will try real lsof.
        # Since we can't control the real lsof in tests, use the seam with empty output.
        data = _data_with_idle_claim()
        cfg = self._cfg()
        _emit_worker_idle(data, cfg, [], lsof_output="")
        events = [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
