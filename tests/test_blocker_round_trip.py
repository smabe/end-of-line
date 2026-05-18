"""End-to-end round-trip test: worker blocks → operator answers → supervisor re-dispatches."""
from __future__ import annotations

import subprocess
import unittest

from end_of_line import notify, state as st
from end_of_line.cli import main
from end_of_line.config import DispatchSpec, ProjectConfig
from end_of_line.supervisor import tick
from tests import CluTestCase, isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| foundation | `test-plan-foundation.md` | setup | 1h |
"""


class BlockerRoundTripTestCase(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.email", "t@t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.name", "t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "--allow-empty", "-m", "init"],
            check=True, capture_output=True,
        )
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.cfg = ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command="echo {phase_id}"),
        )
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "foundation", lease_minutes=30)
        notify.set_global_suppress(True)

    def tearDown(self) -> None:
        notify.set_global_suppress(False)
        super().tearDown()

    def test_blocker_round_trip_re_dispatches_with_answer(self) -> None:
        # Worker blocks — claim released, blocker recorded.
        rc = main([
            "block", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "foundation", "--token", self.token,
            "--question", "go?", "--option", "A", "--option", "B",
        ])
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertEqual(len(data["blockers"]), 1)
        self.assertIsNone(data["current_claim"])
        blocker_id = data["blockers"][0]["id"]

        # Operator answers via index "0" → resolves to option text "A".
        # Post-plan-locator: cmd_answer takes just the answer index;
        # state_locator resolves which blocker via --plan.
        rc = main([
            "answer", "--plan", "test-plan", "0",
        ])
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertEqual(data["blockers"][0]["answer"], "A")
        self.assertFalse(data["blockers"][0].get("consumed", False))

        # Tick 1: rule 4 (answered-blocker resume) fires.
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "blocker_resumed")
        data = st.load(self.state_path)
        self.assertTrue(data["blockers"][0].get("consumed"))
        self.assertEqual(data["status"], st.STATUS_RUNNING)
        self.assertIsNone(data["current_claim"])

        # Tick 2: dispatch fires; new claim on foundation.
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "dispatch")
        self.assertEqual(result.phase_id, "foundation")
        data = st.load(self.state_path)
        self.assertIsNotNone(data["current_claim"])
        self.assertEqual(data["current_claim"]["phase_id"], "foundation")

        # Answered blocker persists with consumed=True so the new worker can read it.
        b = data["blockers"][0]
        self.assertEqual(b["answer"], "A")
        self.assertTrue(b.get("consumed"))


if __name__ == "__main__":
    unittest.main()
