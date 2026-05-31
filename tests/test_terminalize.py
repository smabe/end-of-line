"""Unit tests for state.terminalize + state.reap_claim (#75 phase 2).

TDD: written before the implementation. `terminalize` flips a non-terminal
plan to a terminal status (compare-and-set, no-op if already terminal) and
emits an audit event. `reap_claim` best-effort kills the active claim's worker
process GROUP via the phase-1 `reap_orphan_pgroup`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import unittest

from end_of_line import state as st


def _spawn_marked_group(marker: str) -> subprocess.Popen:
    """A leader in its own session (pgid == pid) whose cmdline carries
    `marker`, plus a same-group child — mirrors worker + heartbeat."""
    code = "import subprocess, time; subprocess.Popen(['sleep', '30']); time.sleep(30)"
    leader = subprocess.Popen(
        [sys.executable, "-c", code, marker],
        start_new_session=True,
    )
    time.sleep(0.6)
    return leader


def _group_alive(pgid: int) -> bool:
    return subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True).returncode == 0


class TestTerminalize(unittest.TestCase):
    def _state(self, status: str) -> dict:
        return {"plan_slug": "p", "status": status, "events": []}

    def test_running_flips_to_halted_and_emits(self):
        data = self._state(st.STATUS_RUNNING)
        changed = st.terminalize(data, reason="unregister")
        self.assertTrue(changed)
        self.assertEqual(data["status"], st.STATUS_HALTED)
        evts = [e for e in data["events"] if e["type"] == st.EVENT_PLAN_ABANDONED]
        self.assertEqual(len(evts), 1)
        self.assertEqual(evts[0]["reason"], "unregister")

    def test_noop_on_already_terminal(self):
        for terminal in (st.STATUS_DONE, st.STATUS_HALTED, st.STATUS_PAUSED):
            data = self._state(terminal)
            changed = st.terminalize(data)
            self.assertFalse(changed, f"{terminal} should be a no-op")
            self.assertEqual(data["status"], terminal)
            self.assertEqual(data["events"], [], "no event on a no-op terminalize")

    def test_custom_status_and_event(self):
        data = self._state(st.STATUS_RUNNING)
        changed = st.terminalize(data, status=st.STATUS_DONE, event=st.EVENT_PLAN_COMPLETED)
        self.assertTrue(changed)
        self.assertEqual(data["status"], st.STATUS_DONE)
        self.assertEqual(data["events"][-1]["type"], st.EVENT_PLAN_COMPLETED)

    def test_event_constant(self):
        self.assertEqual(st.EVENT_PLAN_ABANDONED, "plan_abandoned")


class TestReapClaim(unittest.TestCase):
    def test_no_claim_returns_none(self):
        self.assertIsNone(st.reap_claim({"plan_slug": "p", "current_claim": None}))

    def test_claim_without_pgid_or_pid_returns_none(self):
        data = {"plan_slug": "p", "current_claim": {"phase_id": "a", "claimed_by": "t"}}
        self.assertIsNone(st.reap_claim(data))

    def test_claim_with_pgid_but_no_phase_id_refuses(self):
        # No phase_id → no cmdline marker → reaping would have no PID-reuse
        # guard. reap_claim must refuse rather than killpg a possibly-reused
        # group; a live process at that pgid must survive.
        leader = _spawn_marked_group("/clu-phase test-plan a")
        try:
            data = {"plan_slug": "test-plan", "current_claim": {"pgid": leader.pid}}
            self.assertIsNone(st.reap_claim(data))
            time.sleep(0.3)
            self.assertTrue(_group_alive(leader.pid), "must not reap without a marker")
        finally:
            try:
                os.killpg(leader.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            leader.wait()

    def test_reaps_worker_group_with_marker(self):
        leader = _spawn_marked_group("/clu-phase test-plan a")
        try:
            data = {
                "plan_slug": "test-plan",
                "current_claim": {"phase_id": "a", "pgid": leader.pid, "claimed_by": "tok"},
            }
            _waiter = threading.Thread(target=leader.wait, daemon=True)
            _waiter.start()
            result = st.reap_claim(data)
            _waiter.join(timeout=10)
            self.assertIsNotNone(result)
            self.assertIsNotNone(result.signaled)
            time.sleep(0.6)
            self.assertFalse(_group_alive(leader.pid), "worker group should be reaped")
        finally:
            try:
                os.killpg(leader.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            leader.wait()

    def test_pid_fallback_when_pgid_absent(self):
        # Old state files (pre-#75) have pid but no pgid; reap_claim falls back
        # to pid, which == pgid because the worker is a session leader.
        leader = _spawn_marked_group("/clu-phase test-plan a")
        try:
            data = {
                "plan_slug": "test-plan",
                "current_claim": {"phase_id": "a", "pid": leader.pid, "claimed_by": "tok"},
            }
            _waiter = threading.Thread(target=leader.wait, daemon=True)
            _waiter.start()
            result = st.reap_claim(data)
            _waiter.join(timeout=10)
            self.assertIsNotNone(result.signaled)
        finally:
            try:
                os.killpg(leader.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            leader.wait()


if __name__ == "__main__":
    unittest.main()
