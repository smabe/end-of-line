"""`clu worktree reattach --plan X --path NEW` — operator-driven recovery
when a worktree dir got moved or rebuilt by hand.

Refuses to point at a non-git path so the alive-check the dispatcher
relies on isn't silently broken by reattach. Does not touch status —
operator runs `clu resume` separately if the plan was paused by
EVENT_WORKTREE_MISSING.
"""
from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import isolate_registry


PLAN_BODY = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `foo-a.md` | thing | 1h |
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, check=True,
    )


class WorktreeReattachTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self._tmp.name)
        self.project = self.parent / "myrepo"
        self.project.mkdir()
        isolate_registry(self, self.parent)
        (self.project / "plans").mkdir()
        _git(self.project, "init", "-q")
        _git(self.project, "config", "user.email", "t@t")
        _git(self.project, "config", "user.name", "t")
        _git(self.project, "commit", "--allow-empty", "-m", "init")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init_plan(self, slug: str) -> Path:
        (self.project / "plans" / f"{slug}.md").write_text(PLAN_BODY)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main([
                "init", "--project", str(self.project), "--plan", slug,
                "--worktree",
            ])
        return self.project / "plans" / ".orchestrator" / f"{slug}.state.json"

    def _reattach(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["worktree", "reattach", "--project",
                       str(self.project), *extra])
        return rc, out.getvalue(), err.getvalue()

    def test_happy_path_rewrites_state_worktree_path(self) -> None:
        state_path = self._init_plan("alpha")
        old_path = st.get_worktree(st.load(state_path))["path"]
        # Build a sibling git worktree to reattach to.
        new_path = self.parent / "myrepo-alpha-relocated"
        _git(self.project, "worktree", "add", "-b", "clu/alpha-2", str(new_path))

        rc, stdout, _ = self._reattach(
            "--plan", "alpha", "--path", str(new_path),
        )
        self.assertEqual(rc, 0)
        record = st.get_worktree(st.load(state_path))
        self.assertEqual(record["path"], str(new_path.resolve()))
        # Branch is untouched by reattach.
        self.assertEqual(record["branch"], "clu/alpha")
        self.assertIn("alpha", stdout)
        self.assertIn(str(new_path.resolve()), stdout)

    def test_refuses_nonexistent_path(self) -> None:
        self._init_plan("alpha")
        missing = self.parent / "this-dir-is-gone"
        rc, _stdout, _stderr = self._reattach(
            "--plan", "alpha", "--path", str(missing),
        )
        self.assertEqual(rc, ExitCode.GENERIC)

    def test_refuses_non_git_path(self) -> None:
        self._init_plan("alpha")
        not_a_repo = self.parent / "plain-dir"
        not_a_repo.mkdir()
        rc, _stdout, _stderr = self._reattach(
            "--plan", "alpha", "--path", str(not_a_repo),
        )
        self.assertEqual(rc, ExitCode.GENERIC)

    def test_refuses_when_plan_has_no_worktree(self) -> None:
        # Init without --worktree → state has no worktree record.
        (self.project / "plans" / "alpha.md").write_text(PLAN_BODY)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["init", "--project", str(self.project), "--plan", "alpha"])
        new_path = self.parent / "wt-attempt"
        _git(self.project, "worktree", "add", str(new_path), "-b", "ad-hoc")
        rc, _stdout, _stderr = self._reattach(
            "--plan", "alpha", "--path", str(new_path),
        )
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)

    def test_refuses_unknown_plan(self) -> None:
        rc, _stdout, _stderr = self._reattach(
            "--plan", "nonexistent", "--path", str(self.project),
        )
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)

    def test_does_not_resume_paused_plan(self) -> None:
        """Reattach is a path edit; status changes are the operator's call."""
        state_path = self._init_plan("alpha")
        with st.mutate(state_path) as data:
            data["status"] = st.STATUS_PAUSED

        new_path = self.parent / "myrepo-alpha-relocated"
        _git(self.project, "worktree", "add", "-b", "clu/alpha-2", str(new_path))
        rc, _stdout, _stderr = self._reattach(
            "--plan", "alpha", "--path", str(new_path),
        )
        self.assertEqual(rc, 0)
        # Status unchanged.
        self.assertEqual(st.load(state_path)["status"], st.STATUS_PAUSED)


if __name__ == "__main__":
    unittest.main()
