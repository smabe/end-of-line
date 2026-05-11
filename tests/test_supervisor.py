"""Integration-ish tests for the supervisor tick logic."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from end_of_line import state as st
from end_of_line.config import ProjectConfig, DispatchSpec
from end_of_line.supervisor import tick


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
| B | `test-plan-b.md` | thing | 1h |
"""


class SupervisorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
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

    def tearDown(self) -> None:
        self._tmp.cleanup()

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

    def test_skips_phase_with_open_blocker(self) -> None:
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            st.add_blocker(data, "a", "Q?", ["X", "Y"], "ctx")
            st.save_atomic(self.state_path, data)
        result = tick(self.state_path, self.cfg)
        # Should not dispatch a; should dispatch b instead
        self.assertEqual(result.action, "dispatch")
        self.assertEqual(result.phase_id, "b")

    def test_max_attempts_halts_plan(self) -> None:
        # Simulate two prior failed attempts via phase_started events
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            data["config"]["max_attempts_per_phase"] = 2
            st.append_event(data, "phase_started", phase="a", claimed_by="x")
            st.append_event(data, "lease_expired", phase="a")
            st.append_event(data, "phase_started", phase="a", claimed_by="y")
            st.append_event(data, "lease_expired", phase="a")
            st.save_atomic(self.state_path, data)
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "halt")
        self.assertEqual(self._read()["status"], "halted")


if __name__ == "__main__":
    unittest.main()
