"""Tests for `clu tick-all` — registry-walking cron entry point.

Replaces the old `examples/clu-tick-all.sh` parser. The shell version
piped `clu list` through awk and fired one `clu tick` per plan;
tick-all does the same in Python so per-plan exceptions can be caught
+ logged without aborting the loop.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import main
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""


class TickAllTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        isolate_registry(self, self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_project(self, name: str, plan: str = "test-plan") -> Path:
        project = self.tmp / name
        (project / "plans").mkdir(parents=True)
        (project / "plans" / f"{plan}.md").write_text(PLAN_BODY)
        rc = main(["init", "--project", str(project), "--plan", plan])
        self.assertEqual(rc, 0)
        return project

    def _state_path(self, project: Path, plan: str = "test-plan") -> Path:
        return project / "plans" / ".orchestrator" / f"{plan}.state.json"

    def _events(self, project: Path, plan: str = "test-plan") -> list[dict]:
        return st.load(self._state_path(project, plan))["events"]

    def _run(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["tick-all"])
        return rc, out.getvalue(), err.getvalue()

    def test_empty_registry_is_noop(self) -> None:
        rc, _, err = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")

    def test_ticks_each_registered_plan(self) -> None:
        p1 = self._make_project("alpha")
        p2 = self._make_project("beta")
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        # Each plan should have been ticked → phase_started event.
        for project in (p1, p2):
            evts = self._events(project)
            self.assertTrue(
                any(e["type"] == st.EVENT_PHASE_STARTED for e in evts),
                f"{project} has no phase_started event after tick-all",
            )
        # Stdout should mention each plan so the LaunchAgent log is
        # readable.
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_one_bad_plan_does_not_abort_others(self) -> None:
        p1 = self._make_project("alpha")
        p2 = self._make_project("beta")
        # Corrupt alpha's state file — tick() will fail when it tries
        # to load it. beta must still tick normally and overall exit 0.
        self._state_path(p1).write_text("{ not valid json")

        rc, _, err = self._run()
        self.assertEqual(rc, 0)
        evts_beta = self._events(p2)
        self.assertTrue(
            any(e["type"] == st.EVENT_PHASE_STARTED for e in evts_beta),
            "beta should still tick when alpha is broken",
        )
        self.assertIn("alpha", err)


if __name__ == "__main__":
    unittest.main()
