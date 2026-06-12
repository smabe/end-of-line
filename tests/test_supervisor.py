"""Integration-ish tests for the supervisor tick logic."""

from __future__ import annotations

import datetime as _dt
import json
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.config import DispatchSpec, NotifySpec, ProjectConfig
from end_of_line.supervisor import tick
from tests import CluTestCase, must
from tests.test_quota import SESSION_LINE as QUOTA_LINE

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

    # --- quota-death classification (#94 phase classify) ---

    def _claim_with_log(
        self,
        log_body: str,
        lease_expires: str = "2099-01-01T00:00:00Z",
    ) -> str:
        """Claim phase 'a', stamp pid + log_path, seed the worker log."""
        tick(self.state_path, self.cfg)
        log_dir = self.state_path.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            token = data["current_claim"]["claimed_by"]
            log_path = log_dir / f"a.{token}.log"
            data["current_claim"]["pid"] = 99999
            data["current_claim"]["log_path"] = str(log_path)
            data["current_claim"]["lease_expires"] = lease_expires
            st.save_atomic(self.state_path, data)
        log_path.write_text(log_body)
        return token

    def _tick_worker_dead(self):
        reap = st.ReapResult(signaled=None, escalated_kill=False, cmdline_mismatch=False)
        with (
            mock.patch("end_of_line.state.claim_worker_alive", return_value=False),
            mock.patch("end_of_line.state.reap_orphan_pgroup", return_value=reap),
        ):
            return tick(self.state_path, self.cfg)

    def _event(self, data: dict, event_type: str) -> dict:
        return next(e for e in data["events"] if e["type"] == event_type)

    def test_dead_pid_quota_log_classifies_and_forgives(self) -> None:
        token = self._claim_with_log(QUOTA_LINE + "\n")
        result = self._tick_worker_dead()
        self.assertEqual(result.action, "worker_dead")
        data = self._read()
        death = self._event(data, st.EVENT_QUOTA_DEATH)
        self.assertEqual(death["phase"], "a")
        self.assertEqual(death["token"], token)
        self.assertEqual(death["signature"], "session_limit")
        self.assertIn("session limit", death["line"])
        paused = self._event(data, st.EVENT_QUOTA_PAUSED)
        self.assertIsNotNone(paused["paused_until"])
        # Forgiveness: the dispatch that died on quota burns no attempt.
        self.assertEqual(st.attempts_for_phase(data, "a"), 0)
        # The misleading worker-dead body is suppressed; KIND_QUOTA_*
        # notifications land in phase notify-docs.
        self.assertIsNone(result.notify_body)
        qdata = json.loads((self.state_path.parent / "quota.json").read_text())
        self.assertEqual(qdata["signature"], "session_limit")
        self.assertIsNotNone(qdata["paused_until"])

    def test_dead_pid_quota_stuck_reset_writes_null_pause(self) -> None:
        self._claim_with_log("You've hit your weekly limit · resets Mon 12:00am\n")
        self._tick_worker_dead()
        qdata = json.loads((self.state_path.parent / "quota.json").read_text())
        self.assertIsNone(qdata["paused_until"])
        paused = self._event(self._read(), st.EVENT_QUOTA_PAUSED)
        self.assertIsNone(paused["paused_until"])

    def test_dead_pid_non_quota_log_burns_attempt_and_notifies(self) -> None:
        # Regression: a non-quota death behaves exactly as today.
        self._claim_with_log("Traceback ...\nValueError: bad\n")
        result = self._tick_worker_dead()
        self.assertEqual(result.action, "worker_dead")
        data = self._read()
        types = [e["type"] for e in data["events"]]
        self.assertNotIn(st.EVENT_QUOTA_DEATH, types)
        self.assertEqual(st.attempts_for_phase(data, "a"), 1)
        self.assertIn("99999", must(result.notify_body))
        self.assertFalse((self.state_path.parent / "quota.json").exists())

    def test_lease_expired_quota_log_classifies_and_forgives(self) -> None:
        token = self._claim_with_log(QUOTA_LINE + "\n", lease_expires="2020-01-01T00:00:00Z")
        with mock.patch("end_of_line.state.reap_orphan_pgroup") as mock_reap:
            mock_reap.return_value = st.ReapResult(
                signaled="SIGTERM", escalated_kill=False, cmdline_mismatch=False
            )
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")
        data = self._read()
        death = self._event(data, st.EVENT_QUOTA_DEATH)
        self.assertEqual(death["token"], token)
        self.assertEqual(st.attempts_for_phase(data, "a"), 0)
        self.assertTrue((self.state_path.parent / "quota.json").exists())

    def test_lease_expired_non_quota_log_unchanged(self) -> None:
        self._claim_with_log("benign\n", lease_expires="2020-01-01T00:00:00Z")
        with mock.patch("end_of_line.state.reap_orphan_pgroup") as mock_reap:
            mock_reap.return_value = st.ReapResult(
                signaled=None, escalated_kill=False, cmdline_mismatch=False
            )
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")
        data = self._read()
        types = [e["type"] for e in data["events"]]
        self.assertNotIn(st.EVENT_QUOTA_DEATH, types)
        self.assertEqual(st.attempts_for_phase(data, "a"), 1)

    def test_three_quota_deaths_burn_zero_attempts(self) -> None:
        # Acceptance (#94): 3 consecutive quota deaths never reach the
        # max-attempts halt — the 4th tick still dispatches phase a. Each
        # death writes a project pause (phase gate), which blocks redispatch
        # until the reset; clearing quota.json between deaths models the
        # reset elapsing + the canary redispatching only to die on quota
        # again. Forgiveness holds across all three.
        quota_file = self.state_path.parent / "quota.json"
        for _ in range(3):
            self._claim_with_log(QUOTA_LINE + "\n")
            self._tick_worker_dead()
            quota_file.unlink(missing_ok=True)
        data = self._read()
        self.assertEqual(st.attempts_for_phase(data, "a"), 0)
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "dispatch")
        self.assertEqual(result.phase_id, "a")

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


def _one_phase_plan(slug: str) -> str:
    # Phase id = plan_file stem minus the master-stem prefix → "go".
    return f"""\
# {slug}

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| Go | `{slug}-go.md` | thing | 1h |
"""


class QuotaGateSupervisorTests(CluTestCase):
    """The dispatch gate + canary auto-resume, end-to-end through tick().

    Two single-phase plans share one orchestrator dir (and thus one
    quota.json). The gate must idle every plan while paused, let exactly
    ONE dispatch as the canary past reset, and resume the fleet (clearing
    the file + emitting EVENT_QUOTA_RESUMED) once the canary survives.
    """

    NOW = _dt.datetime(2026, 6, 12, 6, 0, tzinfo=_dt.UTC)

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        (self.project / "plans").mkdir()
        self.orch = self.project / "plans" / ".orchestrator"
        self.orch.mkdir(parents=True)
        self.cfg = ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command="echo {phase_id}"),
        )
        self.paths: dict[str, Path] = {}
        for slug in ("plan-a", "plan-b"):
            (self.project / "plans" / f"{slug}.md").write_text(_one_phase_plan(slug))
            sp = self.orch / f"{slug}.state.json"
            with st.locked(sp):
                st.save_atomic(sp, st.empty_state(slug, "plans"))
            self.paths[slug] = sp
        self.quota_path = self.orch / "quota.json"

    def _write_pause(self, **over: object) -> None:
        base = {
            "schema_version": 1,
            "paused_until": "2026-06-12T05:52:00Z",
            "signature": "session_limit",
            "line": QUOTA_LINE,
            "canary_plan": None,
            "canary_deadline": None,
            "created_at": "2026-06-12T03:00:00Z",
        }
        base.update(over)
        self.quota_path.write_text(json.dumps(base))

    def _read(self, slug: str) -> dict:
        return json.loads(self.paths[slug].read_text())

    def _read_quota(self) -> dict:
        return json.loads(self.quota_path.read_text())

    def test_active_pause_idles_every_plan(self) -> None:
        self._write_pause(paused_until="2026-06-12T07:00:00Z")  # future reset
        with mock.patch("end_of_line.state._now_utc", return_value=self.NOW):
            a = tick(self.paths["plan-a"], self.cfg)
            b = tick(self.paths["plan-b"], self.cfg)
        self.assertEqual(a.action, "idle")
        self.assertEqual(b.action, "idle")
        self.assertIn("quota_paused", a.detail)
        self.assertIsNone(self._read("plan-a")["current_claim"])
        self.assertIsNone(self._read("plan-b")["current_claim"])

    def test_past_reset_dispatches_exactly_one_canary(self) -> None:
        self._write_pause(paused_until="2026-06-12T05:52:00Z")  # past reset
        with mock.patch("end_of_line.state._now_utc", return_value=self.NOW):
            a = tick(self.paths["plan-a"], self.cfg)  # first → canary
            b = tick(self.paths["plan-b"], self.cfg)  # gated by canary window
        self.assertEqual(a.action, "dispatch")
        self.assertEqual(b.action, "idle")
        # quota.json now names plan-a the canary with a +180s deadline.
        q = self._read_quota()
        self.assertEqual(q["canary_plan"], "plan-a")
        self.assertEqual(
            st.parse_iso(q["canary_deadline"]),
            self.NOW + _dt.timedelta(seconds=180),
        )
        # Exactly one plan holds a fresh claim during the canary window.
        claims = [
            self._read(s)["current_claim"] is not None for s in ("plan-a", "plan-b")
        ]
        self.assertEqual(claims.count(True), 1)
        self.assertIsNotNone(self._read("plan-a")["current_claim"])

    def test_past_deadline_resumes_and_clears_file(self) -> None:
        self._write_pause(
            paused_until="2026-06-12T05:52:00Z",
            canary_plan="plan-a",
            canary_deadline="2026-06-12T05:55:00Z",  # already elapsed by NOW
        )
        with mock.patch("end_of_line.state._now_utc", return_value=self.NOW):
            b = tick(self.paths["plan-b"], self.cfg)
        self.assertEqual(b.action, "dispatch")
        self.assertFalse(self.quota_path.exists())  # cleared on resume
        events = [e["type"] for e in self._read("plan-b")["events"]]
        self.assertIn(st.EVENT_QUOTA_RESUMED, events)

    def test_stuck_pause_idles(self) -> None:
        self._write_pause(paused_until=None)  # unparseable reset → stuck
        with mock.patch("end_of_line.state._now_utc", return_value=self.NOW):
            a = tick(self.paths["plan-a"], self.cfg)
        self.assertEqual(a.action, "idle")
        self.assertIn("quota_stuck", a.detail)
        self.assertIsNone(self._read("plan-a")["current_claim"])
        self.assertTrue(self.quota_path.exists())  # only the operator clears it


if __name__ == "__main__":
    unittest.main()
