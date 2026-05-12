"""Tests for dispatch failure visibility (fix 7)."""
from __future__ import annotations

import json
import os
import tempfile
import time
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

    def _cfg(self, cmd: str, path: str = "") -> ProjectConfig:
        return ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command=cmd, path=path),
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

    def _capture_env_value(self, var: str, path: str = "") -> str:
        """Run a sentinel worker that writes one env var to a tempfile.

        Returns the captured value (stripped). Uses a polled wait so the test
        doesn't need to know the dispatch fast-fail timing; the sentinel
        command finishes fast but `dispatch_for_tick` may treat it as either
        fast-fail or running depending on scheduling.
        """
        sentinel = self.project / f"{var}.captured"
        cfg = self._cfg(f'sh -c \'printf "%s" "${var}" > {sentinel}\'', path=path)
        dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if sentinel.exists():
                break
            time.sleep(0.05)
        self.assertTrue(sentinel.exists(), f"sentinel for {var} never written")
        return sentinel.read_text()

    def test_dispatch_no_path_omits_env(self) -> None:
        """Empty dispatch.path => worker inherits parent PATH unchanged."""
        captured = self._capture_env_value("PATH", path="")
        self.assertEqual(captured, os.environ["PATH"])

    def test_dispatch_with_path_overrides_env(self) -> None:
        """Non-empty dispatch.path => worker's $PATH is exactly that value.

        This is the Diagnosis falsifiable test from the master plan.
        """
        captured = self._capture_env_value("PATH", path="/usr/bin:/bin")
        self.assertEqual(captured, "/usr/bin:/bin")

    def test_dispatch_with_path_preserves_home(self) -> None:
        """Custom PATH must MERGE with os.environ, not replace it.

        If the implementation did `env={"PATH": ...}` alone, `$HOME` would
        be empty in the child. We assert it survives.
        """
        expected_home = os.environ.get("HOME", "")
        # The test only proves merge-vs-replace when HOME is actually set.
        self.assertTrue(expected_home, "test prerequisite: HOME must be set")
        captured = self._capture_env_value("HOME", path="/usr/bin:/bin")
        self.assertEqual(captured, expected_home)

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
