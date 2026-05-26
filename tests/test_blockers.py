"""Tests for `clu blockers list|show` — read-only blocker inspection."""

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


class BlockersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        rc = main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["blockers", *argv])
        return rc, out.getvalue(), err.getvalue()

    def _plan_args(self) -> list[str]:
        return ["--project", str(self.project), "--plan", "test-plan"]

    def _mutate(self, mut) -> None:
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            mut(data)
            st.save_atomic(self.state_path, data)

    def test_blockers_list_empty(self) -> None:
        rc, out, _ = self._run("list", *self._plan_args())
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("no open blockers", out)
        self.assertIn("test-plan", out)

    def test_blockers_list_open_only(self) -> None:
        def mut(d: dict) -> None:
            st.add_blocker(d, "phase-a", "Which approach?", ["A", "B"], "context")
            st.add_blocker(d, "phase-b", "Old question?", ["yes"], "")
            st.answer_blocker(d, d["blockers"][-1]["id"], "yes")

        self._mutate(mut)
        rc, out, _ = self._run("list", *self._plan_args())
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("q-1", out)
        self.assertIn("phase-a", out)
        self.assertIn("Which approach?", out)
        self.assertIn("A", out)
        self.assertNotIn("Old question?", out)
        self.assertNotIn("phase-b", out)

    def test_blockers_show_happy_path(self) -> None:
        def mut(d: dict) -> None:
            st.add_blocker(d, "phase-a", "Which approach?", ["A", "B"], "important context")

        self._mutate(mut)
        rc, out, _ = self._run("show", "q-1", *self._plan_args())
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("q-1", out)
        self.assertIn("phase-a", out)
        self.assertIn("Which approach?", out)
        self.assertIn("important context", out)
        self.assertIn("A", out)
        self.assertIn("B", out)

    def test_blockers_show_includes_related_events(self) -> None:
        # add_blocker emits EVENT_PHASE_BLOCKED with blocker_id="q-1";
        # show should surface it in an Events section.
        def mut(d: dict) -> None:
            st.add_blocker(d, "phase-a", "Q?", ["yes"], "ctx")

        self._mutate(mut)
        rc, out, _ = self._run("show", "q-1", *self._plan_args())
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("phase_blocked", out)

    def test_blockers_show_not_found(self) -> None:
        rc, _, err = self._run("show", "q-99", *self._plan_args())
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)
        self.assertIn("q-99", err)
        self.assertIn("no blocker", err)

    def test_blockers_list_defaults_project_to_cwd(self) -> None:
        old_cwd = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, str(old_cwd))
        rc, out, err = self._run("list", "--plan", "test-plan")
        self.assertNotIn("AttributeError", err)
        self.assertIn(rc, (int(ExitCode.OK), int(ExitCode.UNKNOWN_TASK)))

    def test_blockers_show_defaults_project_to_cwd(self) -> None:
        old_cwd = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, str(old_cwd))
        rc, out, err = self._run("show", "--plan", "test-plan", "q-99")
        self.assertNotIn("AttributeError", err)
        self.assertIn(rc, (int(ExitCode.OK), int(ExitCode.UNKNOWN_TASK)))


if __name__ == "__main__":
    unittest.main()
