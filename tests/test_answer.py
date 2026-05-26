"""Tests for `clu answer` — including the CWD-default for --project (#43)."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import isolate_registry

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""


class AnswerCwdDefaultTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name).resolve()
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        rc = main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.assertEqual(rc, 0)
        with st.mutate(self.state_path) as d:
            st.add_blocker(d, "a", "Postgres or sqlite?", ["yes", "no"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["answer", *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_answer_without_project_uses_cwd(self) -> None:
        """Regression guard for #43: omitting --project still works (locator uses registry)."""
        old_cwd = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, str(old_cwd))
        rc, out, _ = self._run("--plan", "test-plan", "0")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("Answered q-1: yes", out)

    def test_answer_with_explicit_project_still_works(self) -> None:
        """Explicit --project accepted (backward compat); locator uses registry."""
        rc, out, _ = self._run(
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "0",
        )
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("Answered q-1: yes", out)


if __name__ == "__main__":
    unittest.main()
