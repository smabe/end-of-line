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

    def _result(self, *, worktree: dict | None = None) -> TickResult:
        return TickResult(
            action="dispatch", detail="", phase_id="a",
            token=self.token, worktree=worktree,
        )

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

    def _capture_via_sentinel(
        self,
        *,
        payload: str,
        sentinel_name: str,
        worktree: dict | None = None,
        path: str = "",
    ) -> str:
        """Spawn a worker that writes a shell payload's output to a sentinel.

        `payload` is a `sh -c` fragment with `{s}` substituted for the absolute
        sentinel path; e.g. `'pwd > {s}'` or `'printf "%s" "$PATH" > {s}'`.
        Polled-wait covers the fast-fail-vs-long-running ambiguity in
        `dispatch_for_tick`: the sentinel write is the observable, not the
        worker's exit timing.
        """
        sentinel = self.project / sentinel_name
        cfg = self._cfg(
            f'sh -c \'{payload.format(s=sentinel)}\'', path=path,
        )
        dispatch_for_tick(
            self._result(worktree=worktree), cfg, "t", self.state_path,
        )
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if sentinel.exists():
                break
            time.sleep(0.05)
        self.assertTrue(
            sentinel.exists(), f"sentinel {sentinel_name} never written",
        )
        return sentinel.read_text()

    def _capture_env_value(self, var: str, path: str = "") -> str:
        # printf "%s" writes no trailing newline → no .strip() needed.
        return self._capture_via_sentinel(
            payload=f'printf "%s" "${var}" > {{s}}',
            sentinel_name=f"{var}.captured",
            path=path,
        )

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

    def _capture_cwd(self, *, worktree: dict | None) -> str:
        # `pwd` ends in a newline; strip so callers can compare paths directly.
        return self._capture_via_sentinel(
            payload="pwd > {s}",
            sentinel_name="cwd.captured",
            worktree=worktree,
        ).strip()

    def test_dispatch_cwd_is_project_root_without_worktree(self) -> None:
        cwd = self._capture_cwd(worktree=None)
        self.assertEqual(Path(cwd).resolve(), self.project.resolve())

    def test_dispatch_cwd_is_worktree_path_when_set(self) -> None:
        # Real directory on disk so the spawned shell can chdir into it.
        # In production this is a `git worktree`, but `dispatch_for_tick`
        # only Popens with `cwd=path` — it doesn't validate the .git layout.
        # mkdtemp (not a fixed name) so parallel runs of this case can't
        # collide on the sibling dir.
        wt = Path(tempfile.mkdtemp(prefix="wt-sibling-"))
        try:
            cwd = self._capture_cwd(worktree={
                "path": str(wt),
                "branch": "clu/t",
                "base_ref": "0" * 40,
            })
            self.assertEqual(Path(cwd).resolve(), wt.resolve())
        finally:
            wt.rmdir()

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
