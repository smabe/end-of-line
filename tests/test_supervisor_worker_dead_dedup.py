"""Supervisor honors the heartbeat daemon's worker-death dedup marker.

When the daemon has already reported a worker death (the marker is stamped on
the claim), the supervisor's own worker-dead branch still releases the claim,
emits its own EVENT_PHASE_WORKER_DEAD, and reaps — but it does NOT fire a
second operator notification. Without the marker check the operator gets two
pings for one death.
"""

from __future__ import annotations

from unittest import mock

from end_of_line import state as st
from end_of_line.supervisor import tick
from tests.test_supervisor import SupervisorTestCase


class WorkerDeadDedupTestCase(SupervisorTestCase):
    def _claim_dead(self, *, mark: bool) -> None:
        """Dispatch phase 'a', stamp a dead-looking PID, optionally pre-mark."""
        tick(self.state_path, self.cfg)  # dispatch phase a
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            claim = data["current_claim"]
            claim["pid"] = 99999
            # Lease far out so lease-expiry (priority 1) doesn't preempt.
            claim["lease_expires"] = "2099-01-01T00:00:00Z"
            if mark:
                st.mark_worker_death_reported(claim, st._now_utc())
            st.save_atomic(self.state_path, data)

    def _tick_dead(self):
        with (
            mock.patch(
                "end_of_line.state.claim_worker_alive",
                return_value=False,
            ),
            mock.patch(
                "end_of_line.state.reap_orphan_pgroup",
                return_value=st.ReapResult(
                    signaled=None, escalated_kill=False, cmdline_mismatch=False
                ),
            ),
            mock.patch("end_of_line.supervisor.coolant.emit_stop"),
        ):
            return tick(self.state_path, self.cfg)

    def test_marker_suppresses_second_notification(self) -> None:
        self._claim_dead(mark=True)
        result = self._tick_dead()
        self.assertEqual(result.action, "worker_dead")
        # The daemon already pinged the operator — no duplicate notify body.
        self.assertIsNone(result.notify_body)
        # ...but the durable state transition still happens.
        self.assertIsNone(self._read()["current_claim"])
        types = [e["type"] for e in self._read()["events"]]
        self.assertIn(st.EVENT_PHASE_WORKER_DEAD, types)

    def test_no_marker_still_notifies(self) -> None:
        self._claim_dead(mark=False)
        result = self._tick_dead()
        self.assertEqual(result.action, "worker_dead")
        self.assertIsNotNone(result.notify_body)

    def _daemon_report_and_release(self) -> None:
        """Simulate the death-recovery daemon: report + release the claim, as
        cmd_notify_worker_dead now does. Leaves no current_claim behind."""
        tick(self.state_path, self.cfg)  # dispatch phase a
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            claim = data["current_claim"]
            token = claim["claimed_by"]
            st.mark_worker_death_reported(claim, st._now_utc())
            st.append_event(
                data,
                st.EVENT_PHASE_WORKER_DEAD_REPORTED,
                phase="a",
                pid=claim.get("pid"),
            )
            st.release_claim_and_emit(
                data,
                expected_token=token,
                expected_phase="a",
                coolant_enabled=False,
            )
            st.save_atomic(self.state_path, data)

    def test_tick_after_daemon_release_takes_no_worker_dead_action(self) -> None:
        # The daemon already released — a tick five seconds later finds no
        # claim, so it must NOT re-run the worker-dead branch (no second
        # EVENT_PHASE_WORKER_DEAD, no duplicate operator ping).
        self._daemon_report_and_release()
        with mock.patch(
            "end_of_line.state.claim_worker_alive", return_value=False
        ):
            result = self._tick_dead()
        self.assertNotEqual(result.action, "worker_dead")
        self.assertIsNone(result.notify_body)
        types = [e["type"] for e in self._read()["events"]]
        self.assertNotIn(st.EVENT_PHASE_WORKER_DEAD, types)

    def test_tick_after_daemon_release_redispatches_phase(self) -> None:
        # Recovery, not just tidy-up: with the claim released the very next tick
        # dispatches the phase again (attempt budget permitting).
        self._daemon_report_and_release()
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "dispatch")
        self.assertEqual(result.phase_id, "a")
