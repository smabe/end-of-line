"""`clu worktree attach --project P --plan S --path PATH` — retrofit a
worktree record onto an already-init'd plan.

Mirror of `cmd_worktree_reattach` minus the "must already have a record"
precondition, plus autodetection of branch + base_ref from the worktree
path. Motivated by the resume-workout-rearchitect incident (2026-05-15)
where three plans had been init'd without `--worktree` but the operator
had built their worktrees by hand — neither `init --worktree` (refuses:
state exists) nor `reattach` (refuses: no worktree record) covered the
retrofit case (#25)."""
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


class WorktreeAttachTestCase(unittest.TestCase):
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

    def _init_plan_no_worktree(self, slug: str) -> Path:
        (self.project / "plans" / f"{slug}.md").write_text(PLAN_BODY)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["init", "--project", str(self.project), "--plan", slug])
        return self.project / "plans" / ".orchestrator" / f"{slug}.state.json"

    def _init_plan_with_worktree(self, slug: str) -> Path:
        (self.project / "plans" / f"{slug}.md").write_text(PLAN_BODY)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main([
                "init", "--project", str(self.project), "--plan", slug,
                "--worktree",
            ])
        return self.project / "plans" / ".orchestrator" / f"{slug}.state.json"

    def _make_worktree(self, name: str, branch: str) -> Path:
        wt_path = self.parent / name
        _git(self.project, "worktree", "add", "-b", branch, str(wt_path))
        return wt_path

    def _attach(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["worktree", "attach", "--project",
                       str(self.project), *extra])
        return rc, out.getvalue(), err.getvalue()

    def test_happy_path_writes_record_with_autodetected_branch_and_sha(self) -> None:
        state_path = self._init_plan_no_worktree("alpha")
        self.assertIsNone(st.get_worktree(st.load(state_path)))

        wt_path = self._make_worktree("myrepo-alpha-wt", "feature/alpha")
        head_sha = subprocess.run(
            ["git", "-C", str(wt_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        rc, stdout, _ = self._attach("--plan", "alpha", "--path", str(wt_path))
        self.assertEqual(rc, 0)
        record = st.get_worktree(st.load(state_path))
        self.assertIsNotNone(record)
        self.assertEqual(record["path"], str(wt_path.resolve()))
        self.assertEqual(record["branch"], "feature/alpha")
        self.assertEqual(record["base_ref"], head_sha)
        self.assertIn("alpha", stdout)
        self.assertIn("feature/alpha", stdout)

    def test_emits_worktree_attached_event(self) -> None:
        state_path = self._init_plan_no_worktree("alpha")
        wt_path = self._make_worktree("myrepo-alpha-wt", "feature/alpha")
        self._attach("--plan", "alpha", "--path", str(wt_path))
        events = st.load(state_path)["events"]
        kinds = [e.get("type") for e in events]
        self.assertIn(st.EVENT_WORKTREE_ATTACHED, kinds)

    def test_refuses_when_state_already_has_worktree(self) -> None:
        # init --worktree → record exists → attach is the wrong tool;
        # operator should use reattach instead.
        self._init_plan_with_worktree("alpha")
        new_wt = self._make_worktree("myrepo-alpha-second-wt", "feature/alpha2")
        rc, _stdout, stderr = self._attach(
            "--plan", "alpha", "--path", str(new_wt),
        )
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("reattach", stderr.lower())

    def test_refuses_nonexistent_path(self) -> None:
        self._init_plan_no_worktree("alpha")
        missing = self.parent / "nope"
        rc, _stdout, _stderr = self._attach(
            "--plan", "alpha", "--path", str(missing),
        )
        self.assertEqual(rc, ExitCode.GENERIC)

    def test_refuses_non_git_path(self) -> None:
        self._init_plan_no_worktree("alpha")
        plain = self.parent / "plain-dir"
        plain.mkdir()
        rc, _stdout, _stderr = self._attach(
            "--plan", "alpha", "--path", str(plain),
        )
        self.assertEqual(rc, ExitCode.GENERIC)

    def test_refuses_unknown_plan(self) -> None:
        rc, _stdout, _stderr = self._attach(
            "--plan", "nonexistent", "--path", str(self.project),
        )
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)

    def test_refuses_detached_head(self) -> None:
        # Worktree checked out at a SHA, not a branch. autodetect would
        # return empty branch — attach must refuse with a clear message.
        self._init_plan_no_worktree("alpha")
        wt_path = self._make_worktree("myrepo-alpha-wt", "feature/alpha")
        # Detach HEAD inside the worktree.
        _git(wt_path, "checkout", "--detach", "HEAD")
        rc, _stdout, stderr = self._attach(
            "--plan", "alpha", "--path", str(wt_path),
        )
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("detached", stderr.lower())

    def test_does_not_change_status(self) -> None:
        state_path = self._init_plan_no_worktree("alpha")
        with st.mutate(state_path) as data:
            data["status"] = st.STATUS_PAUSED
        wt_path = self._make_worktree("myrepo-alpha-wt", "feature/alpha")
        rc, _stdout, _stderr = self._attach(
            "--plan", "alpha", "--path", str(wt_path),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(st.load(state_path)["status"], st.STATUS_PAUSED)


if __name__ == "__main__":
    unittest.main()
