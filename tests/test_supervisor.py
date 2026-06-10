"""Integration-ish tests for the supervisor tick logic."""

from __future__ import annotations

import datetime as _dt
import json
import unittest
from unittest import mock

from end_of_line import state as st
from end_of_line.config import DispatchSpec, NotifySpec, ProjectConfig
from end_of_line.supervisor import tick
from tests import CluTestCase, must

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
| B | `test-plan-b.md` | thing | 1h |
"""


class SupervisorTestCase(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.cfg = ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command="echo {phase_id}"),
        )
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        self.state_path.parent.mkdir(parents=True)
        data = st.empty_state("test-plan", "plans")
        with st.locked(self.state_path):
            st.save_atomic(self.state_path, data)

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def test_idle_when_no_state(self) -> None:
        self.state_path.unlink()
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "idle")

    def test_dispatches_first_phase_when_clean(self) -> None:
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "dispatch")
        self.assertEqual(result.phase_id, "a")
        data = self._read()
        self.assertEqual(data["current_claim"]["phase_id"], "a")

    def test_idle_when_claim_active(self) -> None:
        tick(self.state_path, self.cfg)  # claims a
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "idle")

    def test_releases_expired_lease(self) -> None:
        tick(self.state_path, self.cfg)  # claims a
        data = self._read()
        data["current_claim"]["lease_expires"] = "2020-01-01T00:00:00Z"
        with st.locked(self.state_path):
            st.save_atomic(self.state_path, data)
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")
        self.assertIsNone(self._read()["current_claim"])

    def test_dispatches_b_after_a_completes(self) -> None:
        tick(self.state_path, self.cfg)  # claims a
        # Simulate worker completing a
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            st.append_event(data, "phase_completed", phase="a")
            data["current_claim"] = None
            st.save_atomic(self.state_path, data)
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "dispatch")
        self.assertEqual(result.phase_id, "b")

    def test_marks_plan_done_when_all_phases_complete(self) -> None:
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            st.append_event(data, "phase_completed", phase="a")
            st.append_event(data, "phase_completed", phase="b")
            st.save_atomic(self.state_path, data)
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "plan_done")
        self.assertEqual(self._read()["status"], "done")

    def test_open_blocker_pins_lane_idle(self) -> None:
        # When phase A files a blocker the claim releases but A stays
        # pending-not-complete. The supervisor MUST NOT dispatch the
        # successor phase B — plan-file ordering encodes an implicit
        # dependency that successors should not race past the blocked
        # phase. Lane-pin = "any open blocker on this plan halts
        # dispatch until consumed" (#28).
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            st.add_blocker(data, "a", "Q?", ["X", "Y"], "ctx")
            st.save_atomic(self.state_path, data)
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "idle")
        self.assertIn("blocker", result.detail.lower())

    def test_lane_unpins_after_answer_consumed(self) -> None:
        # Open blocker pins; once operator answers AND the consume tick
        # runs (priority 4), the lane reopens and dispatch resumes.
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            bid = st.add_blocker(data, "a", "Q?", ["X", "Y"], "ctx")
            st.save_atomic(self.state_path, data)
        self.assertEqual(tick(self.state_path, self.cfg).action, "idle")
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            st.answer_blocker(data, bid, "X")
            st.save_atomic(self.state_path, data)
        # First post-answer tick consumes the blocker (priority 4).
        self.assertEqual(tick(self.state_path, self.cfg).action, "blocker_resumed")
        # Next tick dispatches phase A — successor B is still gated by
        # plan-file order, which is the right behavior.
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "dispatch")
        self.assertEqual(result.phase_id, "a")

    def _age_blocker_past_sla(self) -> str:
        """Add a blocker on phase 'a' and backdate it past the 24h SLA."""
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            blocker_id = st.add_blocker(data, "a", "Q?", ["X", "Y"], "ctx")
            stale = (st._now_utc() - _dt.timedelta(hours=25)).strftime(st._ISO_FMT)
            for b in data["blockers"]:
                if b["id"] == blocker_id:
                    b["asked_at"] = stale
            st.save_atomic(self.state_path, data)
        return blocker_id

    def test_sla_during_loud_hours_escalates(self) -> None:
        self._age_blocker_past_sla()
        self.cfg.notify = NotifySpec(quiet_hours=("22:00", "08:00"))
        # Force "loud" wall-clock (noon local) regardless of when tests run.
        loud = _dt.datetime(2026, 5, 11, 12, 0, 0)
        with mock.patch("end_of_line.supervisor._local_now", return_value=loud):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "escalate")
        self.assertEqual(self._read()["status"], st.STATUS_PAUSED)

    def test_sla_during_quiet_hours_defers(self) -> None:
        self._age_blocker_past_sla()
        self.cfg.notify = NotifySpec(quiet_hours=("22:00", "08:00"))
        # 3am local — squarely inside the default 22:00–08:00 quiet window.
        quiet = _dt.datetime(2026, 5, 11, 3, 0, 0)
        with mock.patch("end_of_line.supervisor._local_now", return_value=quiet):
            result = tick(self.state_path, self.cfg)
        self.assertNotEqual(result.action, "escalate")
        data = self._read()
        self.assertNotEqual(data["status"], st.STATUS_PAUSED)
        # SLA event must not be emitted while we're still quiet — otherwise we'd
        # only escalate once and silently miss the user's wake-up window.
        types = [e["type"] for e in data["events"]]
        self.assertNotIn(st.EVENT_BLOCKER_SLA_EXCEEDED, types)

    def test_sla_resumes_when_quiet_ends(self) -> None:
        self._age_blocker_past_sla()
        self.cfg.notify = NotifySpec(quiet_hours=("22:00", "08:00"))
        # First tick at 3am — deferred.
        with mock.patch(
            "end_of_line.supervisor._local_now",
            return_value=_dt.datetime(2026, 5, 11, 3, 0, 0),
        ):
            tick(self.state_path, self.cfg)
        # Next tick at 9am — quiet window over, escalation fires.
        with mock.patch(
            "end_of_line.supervisor._local_now",
            return_value=_dt.datetime(2026, 5, 11, 9, 0, 0),
        ):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "escalate")

    def _seed_max_attempts(self) -> None:
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            data["config"]["max_attempts_per_phase"] = 2
            st.append_event(data, "phase_started", phase="a", claimed_by="x")
            st.append_event(data, "lease_expired", phase="a")
            st.append_event(data, "phase_started", phase="a", claimed_by="y")
            st.append_event(data, "lease_expired", phase="a")
            st.save_atomic(self.state_path, data)

    def test_max_attempts_halts_plan(self) -> None:
        self._seed_max_attempts()
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "halt")
        self.assertEqual(self._read()["status"], "halted")

    def test_halt_first_time_sets_notify_body(self) -> None:
        self._seed_max_attempts()
        result = tick(self.state_path, self.cfg)
        body = must(result.notify_body)
        self.assertIn("a", body)  # phase id appears
        self.assertIn("2", body)  # attempt count appears

    def test_halt_does_not_renotify_on_subsequent_ticks(self) -> None:
        self._seed_max_attempts()
        first = tick(self.state_path, self.cfg)
        self.assertIsNotNone(first.notify_body)
        second = tick(self.state_path, self.cfg)
        # Status is now HALTED → TERMINAL_STATUSES short-circuit means the
        # second tick goes "idle" rather than reaching the halt branch
        # again. That's what protects the user from a 5-min ping loop.
        self.assertEqual(second.action, "idle")
        self.assertIsNone(second.notify_body)

    # --- orphan-reap on lease expiry ---

    def _claim_and_expire(self, pid: int | None = None) -> None:
        tick(self.state_path, self.cfg)  # claims "a"
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            data["current_claim"]["lease_expires"] = "2020-01-01T00:00:00Z"
            if pid is not None:
                data["current_claim"]["pid"] = pid
            else:
                data["current_claim"].pop("pid", None)
            st.save_atomic(self.state_path, data)

    def test_lease_expired_reaps_orphan_pid(self) -> None:
        self._claim_and_expire(pid=99999)
        with mock.patch("end_of_line.state.reap_orphan_pgroup") as mock_reap:
            mock_reap.return_value = st.ReapResult(
                signaled="SIGTERM", escalated_kill=False, cmdline_mismatch=False
            )
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")
        mock_reap.assert_called_once_with(99999, cmdline_match="test-plan")
        event_types = [e["type"] for e in self._read()["events"]]
        expired_idx = next(i for i, t in enumerate(event_types) if t == st.EVENT_LEASE_EXPIRED)
        reaped_idx = next(i for i, t in enumerate(event_types) if t == st.EVENT_PHASE_ORPHAN_REAPED)
        self.assertLess(expired_idx, reaped_idx)

    def test_lease_expired_no_pid_skips_reap(self) -> None:
        self._claim_and_expire(pid=None)
        with mock.patch("end_of_line.state.reap_orphan_pgroup") as mock_reap:
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")
        mock_reap.assert_not_called()
        events = self._read()["events"]
        orphan_events = [e for e in events if e["type"] == st.EVENT_PHASE_ORPHAN_REAPED]
        self.assertEqual(orphan_events, [])

    def test_orphan_reaped_event_carries_signal(self) -> None:
        self._claim_and_expire(pid=88888)
        with mock.patch("end_of_line.state.reap_orphan_pgroup") as mock_reap:
            mock_reap.return_value = st.ReapResult(
                signaled="SIGTERM", escalated_kill=False, cmdline_mismatch=False
            )
            tick(self.state_path, self.cfg)
        events = self._read()["events"]
        reaped = next(e for e in events if e["type"] == st.EVENT_PHASE_ORPHAN_REAPED)
        self.assertEqual(reaped["signaled"], "SIGTERM")
        self.assertFalse(reaped["cmdline_mismatch"])
        self.assertEqual(reaped["pid"], 88888)

    # --- dead-PID detection (priority 2, before stalled-heartbeat) ---

    def _claim_with_pid(
        self,
        pid: int | None = None,
        lease_expires: str | None = None,
    ) -> None:
        """Claim phase 'a' and stamp PID; optionally pin lease_expires."""
        tick(self.state_path, self.cfg)
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            if pid is not None:
                data["current_claim"]["pid"] = pid
            else:
                data["current_claim"].pop("pid", None)
            if lease_expires is not None:
                data["current_claim"]["lease_expires"] = lease_expires
            st.save_atomic(self.state_path, data)

    def test_dead_pid_fires_releases_claim_and_emits_event(self) -> None:
        # Lease far in the future so lease-expiry (priority 1) doesn't preempt.
        self._claim_with_pid(pid=99999, lease_expires="2099-01-01T00:00:00Z")
        original_claim = dict(self._read()["current_claim"])
        original_token = original_claim["claimed_by"]
        reap_return = st.ReapResult(
            signaled=None,
            escalated_kill=False,
            cmdline_mismatch=False,
        )
        with (
            mock.patch(
                "end_of_line.state.claim_worker_alive",
                return_value=False,
            ) as mock_alive,
            mock.patch(
                "end_of_line.state.reap_orphan_pgroup",
                return_value=reap_return,
            ) as mock_reap,
            mock.patch(
                "end_of_line.supervisor.coolant.emit_stop",
            ) as emit,
        ):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "worker_dead")
        self.assertIsNone(self._read()["current_claim"])
        event_types = [e["type"] for e in self._read()["events"]]
        self.assertIn(st.EVENT_PHASE_WORKER_DEAD, event_types)
        # PID-reuse defense: helper + reap must both receive cmdline_match.
        # If a future refactor drops the kwarg, the PID-reuse protection
        # silently disappears — pin both sites in tests.
        mock_alive.assert_called_once()
        self.assertEqual(
            mock_alive.call_args.kwargs["cmdline_match"],
            "test-plan",
        )
        mock_reap.assert_called_once_with(
            original_claim["pid"],
            cmdline_match="test-plan",
        )
        emit.assert_called_once()
        self.assertEqual(emit.call_args.kwargs["session_id"], original_token)
        # Operator-notification wiring: notify_body must be set so
        # ACTION_NOTIFY_KIND["worker_dead"] fires an iMessage in cmd_tick.
        self.assertIn("99999", must(result.notify_body))

    def test_live_pid_no_op_falls_through(self) -> None:
        import os as _os

        self._claim_with_pid(
            pid=_os.getpid(),
            lease_expires="2099-01-01T00:00:00Z",
        )
        with mock.patch(
            "end_of_line.state.claim_worker_alive",
            return_value=True,
        ):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "idle")
        self.assertIsNotNone(self._read()["current_claim"])
        event_types = [e["type"] for e in self._read()["events"]]
        self.assertNotIn(st.EVENT_PHASE_WORKER_DEAD, event_types)

    def test_pid_none_skips_dead_pid_check(self) -> None:
        # Popen→_stamp_pid race window: claim active, pid not yet stamped.
        # The rule must not fire (and must not call the helper either).
        self._claim_with_pid(pid=None, lease_expires="2099-01-01T00:00:00Z")
        with mock.patch(
            "end_of_line.state.claim_worker_alive",
        ) as mock_alive:
            result = tick(self.state_path, self.cfg)
        mock_alive.assert_not_called()
        self.assertEqual(result.action, "idle")
        self.assertIsNotNone(self._read()["current_claim"])

    def test_live_plan_style_worker_not_falsely_reaped(self) -> None:
        # Regression (#75): the incident host's dispatch template is
        # `/plan {plan_slug} ...`, which lacks the `/clu-phase` substring. With
        # the old `/clu-phase <plan> <phase>` marker, claim_worker_alive saw a
        # mismatch and falsely reported a LIVE worker dead — releasing + reaping
        # a healthy worker. The slug marker IS present in this cmdline, so the
        # worker is correctly seen alive and the dead-PID rule does not fire.
        import subprocess
        import sys
        import time

        worker = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", "/plan", "test-plan", "resume"],
        )
        time.sleep(0.3)
        try:
            self._claim_with_pid(pid=worker.pid, lease_expires="2099-01-01T00:00:00Z")
            result = tick(self.state_path, self.cfg)
            self.assertNotEqual(
                result.action,
                "worker_dead",
                "a live /plan-style worker must not be declared dead",
            )
            self.assertIsNotNone(
                self._read()["current_claim"], "the live claim must survive"
            )
        finally:
            worker.terminate()
            worker.wait()

    def test_dead_pid_reap_exception_does_not_block_release(self) -> None:
        # Ordering invariant: durable state (event + release) must complete
        # even if best-effort reap raises (e.g. `ps` timeout, OSError). If
        # reap blocked release, we'd loop forever — every tick would re-detect
        # the same dead PID and crash before releasing.
        self._claim_with_pid(pid=99999, lease_expires="2099-01-01T00:00:00Z")
        with (
            mock.patch(
                "end_of_line.state.claim_worker_alive",
                return_value=False,
            ),
            mock.patch(
                "end_of_line.state.reap_orphan_pgroup",
                side_effect=OSError("simulated ps failure"),
            ),
        ):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "worker_dead")
        self.assertIsNone(self._read()["current_claim"])
        event_types = [e["type"] for e in self._read()["events"]]
        self.assertIn(st.EVENT_PHASE_WORKER_DEAD, event_types)

    def test_pid_reuse_cmdline_mismatch_treated_as_dead(self) -> None:
        # claim_worker_alive returns False when PID exists but cmdline mismatch
        # — the rule fires the same as for a dead PID. The reap call records
        # the mismatch flag for the audit trail.
        import os as _os

        self._claim_with_pid(
            pid=_os.getpid(),
            lease_expires="2099-01-01T00:00:00Z",
        )
        reap_return = st.ReapResult(
            signaled=None,
            escalated_kill=False,
            cmdline_mismatch=True,
        )
        with (
            mock.patch(
                "end_of_line.state.claim_worker_alive",
                return_value=False,
            ),
            mock.patch(
                "end_of_line.state.reap_orphan_pgroup",
                return_value=reap_return,
            ),
        ):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "worker_dead")
        self.assertIsNone(self._read()["current_claim"])

    def test_lease_expired_emits_coolant_stop(self) -> None:
        """The lease-expiry branch decrements coolant's counter — the
        worker's CPU footprint dropped when the process died (or hung)
        even though no callback fired.

        Snapshot of phase_id + claimed_by must happen BEFORE the
        `release_if_expired` call wipes `current_claim`. Verified by
        asserting the emit's session_id matches the claim's
        original `claimed_by`.
        """
        self._claim_and_expire(pid=77777)
        data = self._read()
        original_token = data["current_claim"]["claimed_by"]
        reap_return = st.ReapResult(
            signaled="SIGTERM",
            escalated_kill=False,
            cmdline_mismatch=False,
        )
        with (
            mock.patch(
                "end_of_line.state.reap_orphan_pgroup",
                return_value=reap_return,
            ),
            mock.patch("end_of_line.supervisor.coolant.emit_stop") as emit,
        ):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        self.assertEqual(kwargs["session_id"], original_token)
        self.assertEqual(kwargs["agent_id"], "clu-test-plan-a")
        self.assertEqual(kwargs["agent_type"], "clu-worker")


if __name__ == "__main__":
    unittest.main()
