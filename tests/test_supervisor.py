"""Integration-ish tests for the supervisor tick logic."""
from __future__ import annotations

import datetime as _dt
import json
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.config import ProjectConfig, DispatchSpec, NotifySpec
from end_of_line.supervisor import tick

from tests import CluTestCase


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
        self.assertIsNotNone(result.notify_body)
        self.assertIn("a", result.notify_body)  # phase id appears
        self.assertIn("2", result.notify_body)  # attempt count appears

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
        with mock.patch("end_of_line.state.reap_orphan_pid") as mock_reap:
            mock_reap.return_value = st.ReapResult(
                signaled="SIGTERM", escalated_kill=False, cmdline_mismatch=False
            )
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")
        mock_reap.assert_called_once_with(
            99999, cmdline_match="/clu-phase test-plan a"
        )
        event_types = [e["type"] for e in self._read()["events"]]
        expired_idx = next(i for i, t in enumerate(event_types) if t == st.EVENT_LEASE_EXPIRED)
        reaped_idx = next(i for i, t in enumerate(event_types) if t == st.EVENT_PHASE_ORPHAN_REAPED)
        self.assertLess(expired_idx, reaped_idx)

    def test_lease_expired_no_pid_skips_reap(self) -> None:
        self._claim_and_expire(pid=None)
        with mock.patch("end_of_line.state.reap_orphan_pid") as mock_reap:
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")
        mock_reap.assert_not_called()
        events = self._read()["events"]
        orphan_events = [e for e in events if e["type"] == st.EVENT_PHASE_ORPHAN_REAPED]
        self.assertEqual(orphan_events, [])

    def test_orphan_reaped_event_carries_signal(self) -> None:
        self._claim_and_expire(pid=88888)
        with mock.patch("end_of_line.state.reap_orphan_pid") as mock_reap:
            mock_reap.return_value = st.ReapResult(
                signaled="SIGTERM", escalated_kill=False, cmdline_mismatch=False
            )
            tick(self.state_path, self.cfg)
        events = self._read()["events"]
        reaped = next(e for e in events if e["type"] == st.EVENT_PHASE_ORPHAN_REAPED)
        self.assertEqual(reaped["signaled"], "SIGTERM")
        self.assertFalse(reaped["cmdline_mismatch"])
        self.assertEqual(reaped["pid"], 88888)


if __name__ == "__main__":
    unittest.main()
