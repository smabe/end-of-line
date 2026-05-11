"""Tests for `clu prior-blocker` — worker-side helper that detects the
resume-after-answer case without reinventing inline Python in every shell."""
from __future__ import annotations

import io
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


class PriorBlockerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        rc = main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _argv(self, phase: str) -> list[str]:
        return [
            "prior-blocker",
            "--project", str(self.project),
            "--plan", "test-plan",
            "--phase", phase,
        ]

    def _mutate(self, mut) -> None:
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            mut(data)
            st.save_atomic(self.state_path, data)

    def _run(self, phase: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(self._argv(phase))
        return rc, out.getvalue(), err.getvalue()

    def test_answered_blocker_prints_answer_and_exits_zero(self) -> None:
        def mut(d: dict) -> None:
            st.add_blocker(d, "a", "Q?", ["yes", "no"], "ctx")
            st.answer_blocker(d, d["blockers"][-1]["id"], "yes")
        self._mutate(mut)

        rc, out, _ = self._run("a")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("yes", out)

    def test_unanswered_blocker_exits_nonzero(self) -> None:
        def mut(d: dict) -> None:
            st.add_blocker(d, "a", "Q?", ["yes"], "ctx")
        self._mutate(mut)

        rc, _, err = self._run("a")
        self.assertNotEqual(rc, 0)
        self.assertTrue(err.strip(), "stderr should explain the miss")

    def test_no_blocker_for_phase_exits_nonzero(self) -> None:
        # Blocker exists for a different phase — should not match phase 'a'.
        def mut(d: dict) -> None:
            st.add_blocker(d, "b", "Q?", ["yes"], "ctx")
            st.answer_blocker(d, d["blockers"][-1]["id"], "yes")
        self._mutate(mut)

        rc, _, err = self._run("a")
        self.assertNotEqual(rc, 0)
        self.assertTrue(err.strip())

    def test_returns_most_recent_answer_when_phase_blocked_twice(self) -> None:
        # A phase that resumed twice has two answered blockers; the worker
        # cares about the latest one (the answer that just unblocked it).
        def mut(d: dict) -> None:
            st.add_blocker(d, "a", "Q1?", ["one"], "")
            st.answer_blocker(d, d["blockers"][-1]["id"], "one")
            st.add_blocker(d, "a", "Q2?", ["two"], "")
            st.answer_blocker(d, d["blockers"][-1]["id"], "two")
        self._mutate(mut)

        rc, out, _ = self._run("a")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("two", out)
        self.assertNotIn("one", out)

    def test_rejects_invalid_phase_slug(self) -> None:
        rc, _, _ = self._run("../etc/passwd")
        self.assertEqual(rc, ExitCode.INVALID_SLUG)


if __name__ == "__main__":
    unittest.main()
