"""Tests for dispatch failure visibility (fix 7)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import main
from end_of_line.config import ProjectConfig, DispatchSpec
from end_of_line.dispatch import dispatch_for_tick
from end_of_line.supervisor import TickResult
from tests import isolate_registry


PLAN = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `t-a.md` | thing | 1h |
"""


class DispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "t.md").write_text(PLAN)
        main(["init", "--project", str(self.project), "--plan", "t"])
        self.state_path = self.project / "plans" / ".orchestrator" / "t.state.json"
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cfg(self, cmd: str) -> ProjectConfig:
        return ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command=cmd),
        )

    def _result(self) -> TickResult:
        return TickResult(action="dispatch", detail="", phase_id="a", token=self.token)

    def test_missing_command_releases_claim(self) -> None:
        cfg = self._cfg("")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertFalse(ok)
        data = json.loads(self.state_path.read_text())
        self.assertIsNone(data["current_claim"])
        types = [e["type"] for e in data["events"]]
        self.assertIn("dispatch_failed", types)

    def test_fast_fail_releases_claim(self) -> None:
        # Plain non-zero exit that doesn't match a systemic signature
        # (those route through the pause branch — see test_systemic_failure).
        cfg = self._cfg("exit 42")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertFalse(ok)
        data = json.loads(self.state_path.read_text())
        self.assertIsNone(data["current_claim"])
        events = [e for e in data["events"] if e["type"] == "dispatch_failed"]
        self.assertEqual(len(events), 1)
        self.assertIn("rc=", events[0]["reason"])

    def test_long_running_worker_stamps_pid(self) -> None:
        # Sleep longer than fast-fail window so we treat it as "running"
        cfg = self._cfg("sleep 3")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertTrue(ok)
        data = json.loads(self.state_path.read_text())
        claim = data["current_claim"]
        self.assertIsNotNone(claim)
        self.assertIn("pid", claim)
        self.assertIn("log_path", claim)


if __name__ == "__main__":
    unittest.main()
