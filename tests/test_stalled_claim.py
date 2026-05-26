"""Stalled-claim transition detection — supervisor pings once per stalled
claim, separate from the heartbeat-based stalled detection.

The existing tick path auto-releases an expired-lease claim. This phase
adds an iMessage + inbox surface for that transition so the operator
finds out about the dead worker instead of silently re-dispatching the
phase.
"""

from __future__ import annotations

import datetime as _dt
import json
import tempfile
import unittest
from pathlib import Path

from end_of_line import inbox, notify
from end_of_line import state as st
from end_of_line.config import DispatchSpec, ProjectConfig
from end_of_line.supervisor import tick
from tests import isolate_registry

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
| B | `test-plan-b.md` | thing | 1h |
"""


class StalledClaimTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.cfg = ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command="echo"),
        )
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        self.state_path.parent.mkdir(parents=True)
        with st.locked(self.state_path):
            st.save_atomic(self.state_path, st.empty_state("test-plan", "plans"))

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def _claim_and_age(self, lease_minutes_past: int) -> None:
        """First tick dispatches phase 'a'; then backdate the lease."""
        tick(self.state_path, self.cfg)
        with st.mutate(self.state_path) as data:
            past = (st._now_utc() - _dt.timedelta(minutes=lease_minutes_past)).strftime(st._ISO_FMT)
            data["current_claim"]["lease_expires"] = past

    def test_active_claim_within_lease_no_notify(self) -> None:
        tick(self.state_path, self.cfg)  # claim phase a; lease 30 min in future
        result = tick(self.state_path, self.cfg)
        kinds = [k for k, _ in result.side_notifies]
        self.assertNotIn(notify.KIND_STALLED_CLAIM, kinds)

    def test_expired_lease_with_status_running_fires(self) -> None:
        self._claim_and_age(lease_minutes_past=10)
        result = tick(self.state_path, self.cfg)
        kinds = [k for k, _ in result.side_notifies]
        self.assertIn(notify.KIND_STALLED_CLAIM, kinds)
        data = self._read()
        types = [e["type"] for e in data["events"]]
        self.assertIn(st.EVENT_STALLED_CLAIM_NOTIFIED, types)

    def test_expired_lease_does_not_refire_after_release(self) -> None:
        """Auto-release on tick 1 clears the claim; tick 2 has nothing to fire."""
        self._claim_and_age(lease_minutes_past=10)
        first = tick(self.state_path, self.cfg)
        self.assertIn(notify.KIND_STALLED_CLAIM, [k for k, _ in first.side_notifies])
        second = tick(self.state_path, self.cfg)
        self.assertNotIn(notify.KIND_STALLED_CLAIM, [k for k, _ in second.side_notifies])

    def test_expired_lease_with_status_halted_does_not_fire(self) -> None:
        tick(self.state_path, self.cfg)  # claim phase a
        with st.mutate(self.state_path) as data:
            past = (st._now_utc() - _dt.timedelta(minutes=10)).strftime(st._ISO_FMT)
            data["current_claim"]["lease_expires"] = past
            data["status"] = st.STATUS_HALTED
        result = tick(self.state_path, self.cfg)
        kinds = [k for k, _ in result.side_notifies]
        self.assertNotIn(notify.KIND_STALLED_CLAIM, kinds)

    def test_stalled_inbox_event_shape(self) -> None:
        self._claim_and_age(lease_minutes_past=15)
        tick(self.state_path, self.cfg)
        events = inbox.list_for_project(str(self.project))
        stalled = [e for e in events if e["type"] == "stalled_claim"]
        self.assertEqual(len(stalled), 1)
        evt = stalled[0]
        self.assertEqual(evt["plan_slug"], "test-plan")
        self.assertEqual(evt["details"]["phase_id"], "a")
        self.assertIn("stalled_min", evt["details"])
        self.assertIn("claimed_by", evt["details"])

    def test_stalled_body_renders_phase_and_release_hint(self) -> None:
        self._claim_and_age(lease_minutes_past=10)
        result = tick(self.state_path, self.cfg)
        bodies = [body for k, body in result.side_notifies if k == notify.KIND_STALLED_CLAIM]
        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        self.assertIn("test-plan", body)
        self.assertIn("a", body)
        self.assertIn("clu release-claim", body)

    def test_stalled_then_released_then_re_stalled_fires_again(self) -> None:
        """A fresh claim (no stalled_notified) re-fires when its own lease expires."""
        self._claim_and_age(lease_minutes_past=5)
        first = tick(self.state_path, self.cfg)  # fires + auto-release
        self.assertIn(notify.KIND_STALLED_CLAIM, [k for k, _ in first.side_notifies])
        # Second tick dispatches phase a again (status still RUNNING).
        second = tick(self.state_path, self.cfg)
        self.assertEqual(second.action, "dispatch")
        # Age the new claim's lease + re-tick.
        with st.mutate(self.state_path) as data:
            past = (st._now_utc() - _dt.timedelta(minutes=5)).strftime(st._ISO_FMT)
            data["current_claim"]["lease_expires"] = past
        third = tick(self.state_path, self.cfg)
        self.assertIn(notify.KIND_STALLED_CLAIM, [k for k, _ in third.side_notifies])


if __name__ == "__main__":
    unittest.main()
