"""`clu worktree gc` — list candidates, optionally remove + drop branches.

Each test sets up a real git repo + one or more `clu init --worktree`
plans so `git worktree remove` has a real target. The scope filter is
status-at-list-time (then re-checked at action-time).
"""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import main
from tests import isolate_registry

PLAN_BODY = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `foo-a.md` | thing | 1h |
"""


def _git(repo: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=kw.pop("check", True),
        **kw,
    )


class WorktreeGcTestCase(unittest.TestCase):
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
            main(
                [
                    "init",
                    "--project",
                    str(self.project),
                    "--plan",
                    slug,
                    "--worktree",
                ]
            )
        return self._state_path(slug)

    def _state_path(self, slug: str) -> Path:
        return self.project / "plans" / ".orchestrator" / f"{slug}.state.json"

    def _set_status(self, slug: str, status: str) -> None:
        with st.mutate(self._state_path(slug)) as data:
            data["status"] = status

    def _gc(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["worktree", "gc", "--project", str(self.project), *extra])
        return rc, out.getvalue(), err.getvalue()

    # --- list / dry-run -----------------------------------------------

    def test_list_excludes_running_plans(self) -> None:
        # Default after init is RUNNING — should not show up.
        self._init_plan("alpha")
        rc, stdout, _ = self._gc()
        self.assertEqual(rc, 0)
        self.assertIn("no worktree-bearing", stdout)

    def test_list_includes_done_and_halted(self) -> None:
        self._init_plan("alpha")
        self._init_plan("beta")
        self._set_status("alpha", st.STATUS_DONE)
        self._set_status("beta", st.STATUS_HALTED)
        rc, stdout, _ = self._gc()
        self.assertEqual(rc, 0)
        self.assertIn("alpha", stdout)
        self.assertIn("beta", stdout)
        self.assertIn("dry run", stdout)
        # No actual removal — both worktree dirs still on disk.
        self.assertTrue((self.parent / "myrepo-alpha").exists())
        self.assertTrue((self.parent / "myrepo-beta").exists())

    def test_list_excludes_plans_without_worktree(self) -> None:
        # Init without --worktree.
        (self.project / "plans" / "alpha.md").write_text(PLAN_BODY)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["init", "--project", str(self.project), "--plan", "alpha"])
        self._set_status("alpha", st.STATUS_DONE)
        rc, stdout, _ = self._gc()
        self.assertEqual(rc, 0)
        self.assertIn("no worktree-bearing", stdout)

    # --- archived plans ------------------------------------------------

    def test_archived_skipped_by_default(self) -> None:
        self._init_plan("alpha")
        self._set_status("alpha", st.STATUS_DONE)
        # Archive the master plan file.
        (self.project / "plans" / "alpha.md").unlink()
        rc, stdout, _ = self._gc()
        self.assertEqual(rc, 0)
        self.assertIn("no worktree-bearing", stdout)

    def test_archived_included_with_flag(self) -> None:
        self._init_plan("alpha")
        self._set_status("alpha", st.STATUS_DONE)
        (self.project / "plans" / "alpha.md").unlink()
        rc, stdout, _ = self._gc("--include-archived")
        self.assertEqual(rc, 0)
        self.assertIn("alpha", stdout)
        self.assertIn("(archived)", stdout)

    # --- --confirm action -----------------------------------------------

    def test_confirm_removes_worktree(self) -> None:
        self._init_plan("alpha")
        self._set_status("alpha", st.STATUS_DONE)
        wt_path = self.project.resolve().parent / "myrepo-alpha"
        self.assertTrue(wt_path.exists())
        rc, stdout, _ = self._gc("--confirm")
        self.assertEqual(rc, 0)
        self.assertFalse(wt_path.exists())
        # Branch still present (no --delete-branch).
        branch_rc = _git(
            self.project,
            "rev-parse",
            "--verify",
            "refs/heads/clu/alpha",
            check=False,
        ).returncode
        self.assertEqual(branch_rc, 0)
        self.assertIn("Removed 1/1", stdout)

    def test_delete_branch_drops_branch(self) -> None:
        self._init_plan("alpha")
        self._set_status("alpha", st.STATUS_DONE)
        rc, _stdout, _ = self._gc("--confirm", "--delete-branch")
        self.assertEqual(rc, 0)
        branch_rc = _git(
            self.project,
            "rev-parse",
            "--verify",
            "refs/heads/clu/alpha",
            check=False,
        ).returncode
        self.assertNotEqual(branch_rc, 0)

    def test_status_changed_since_list_skips(self) -> None:
        """Plan reverted to RUNNING between list-time and action-time → skip.

        Hard to simulate cleanly — the first `st.load` (list scan) must
        see DONE so alpha lands in the candidate list, but the second
        load (action re-check) must see RUNNING so gc bails. Counter-
        based mock flips status only on the re-check load.
        """
        self._init_plan("alpha")
        self._set_status("alpha", st.STATUS_DONE)
        from unittest import mock

        original_load = st.load
        # Count only loads against the alpha state file — `st.load` is also
        # used by `registry` for registry.json, which we don't want to
        # interfere with. First alpha-load = list scan (keep DONE).
        # Subsequent alpha-loads = action re-check (flip to RUNNING).
        alpha_calls = [0]

        def fake_load(path, **kw):
            data = original_load(path, **kw)
            if "alpha.state.json" in str(path):
                alpha_calls[0] += 1
                if alpha_calls[0] > 1:
                    data["status"] = st.STATUS_RUNNING
            return data

        with mock.patch.object(st, "load", side_effect=fake_load):
            rc, stdout, _ = self._gc("--confirm")
        self.assertEqual(rc, 0)
        self.assertIn("status changed", stdout)
        # Worktree must still exist — gc bailed.
        self.assertTrue((self.parent / "myrepo-alpha").exists())

    def test_nonexistent_worktree_path_logs_failure(self) -> None:
        """gc on a plan whose worktree dir is gone exits cleanly + names the slug.

        Whether git's `worktree remove --force` succeeds against a vanished
        dir depends on the local git version (newer git tolerates it). The
        assertion is intentionally loose: gc must exit 0 (no crash) and
        mention the slug in EITHER channel — that's the operator-facing
        contract; the exact git behaviour is git's call.
        """
        import shutil

        self._init_plan("alpha")
        self._set_status("alpha", st.STATUS_DONE)
        wt_path = self.project.resolve().parent / "myrepo-alpha"
        shutil.rmtree(wt_path)
        rc, stdout, stderr = self._gc("--confirm")
        self.assertEqual(rc, 0)
        self.assertIn("alpha", stdout + stderr)


if __name__ == "__main__":
    unittest.main()
