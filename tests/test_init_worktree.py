"""`clu init --worktree` end-to-end: parser flags, git worktree creation,
state save with rollback, refusal preconditions, SHA echo.

Each test sets up a real git repo via `git init` (mirroring the convention
in `test_worker_callbacks.py`) so the worktree-add subprocess has a real
target to fork from.
"""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import isolate_registry

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `foo-a.md` | thing | 1h |
"""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


class InitWorktreeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self._tmp.name)
        # The project lives under `parent/` so default worktree path
        # `<parent>/<basename>-<slug>` is comparable to siblings.
        self.project = self.parent / "myrepo"
        self.project.mkdir()
        isolate_registry(self, self.parent)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "foo.md").write_text(PLAN_BODY)
        _git(self.project, "init", "-q")
        _git(self.project, "config", "user.email", "t@t")
        _git(self.project, "config", "user.name", "t")
        _git(self.project, "commit", "--allow-empty", "-m", "init")
        self.head_sha = _git(self.project, "rev-parse", "HEAD").stdout.strip()
        self.state_path = self.project / "plans" / ".orchestrator" / "foo.state.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "init",
                    "--project",
                    str(self.project),
                    "--plan",
                    "foo",
                    *extra,
                ]
            )
        return rc, out.getvalue(), err.getvalue()

    # --- happy paths ---------------------------------------------------

    def test_worktree_default_path_and_branch(self) -> None:
        rc, _stdout, stderr = self._init("--worktree")
        self.assertEqual(rc, 0)
        # `load_project_config` resolves symlinks (e.g. macOS /tmp →
        # /private/var/folders/...), so the worktree default falls out of
        # the resolved project_root.
        default_path = self.project.resolve().parent / "myrepo-foo"
        self.assertTrue(default_path.exists())
        data = st.load(self.state_path)
        self.assertEqual(
            st.get_worktree(data),
            {
                "path": str(default_path),
                "branch": "clu/foo",
                "base_ref": self.head_sha,
            },
        )
        # Provenance echo — symbolic ref + resolved SHA both shown.
        self.assertIn("HEAD", stderr)
        self.assertIn(self.head_sha, stderr)
        self.assertIn("clu/foo", stderr)

    def test_worktree_custom_path_with_tilde(self) -> None:
        # Custom path via tilde — should expand against $HOME, which we
        # redirect into our tmp dir so cleanup is automatic.
        fake_home = self.parent / "home"
        fake_home.mkdir()
        custom = "~/custom-wt"
        expanded = fake_home / "custom-wt"
        with mock.patch.dict("os.environ", {"HOME": str(fake_home)}):
            rc, _stdout, _stderr = self._init("--worktree", custom)
        self.assertEqual(rc, 0)
        self.assertTrue(expanded.exists())
        data = st.load(self.state_path)
        self.assertEqual(st.get_worktree(data)["path"], str(expanded))

    def test_worktree_custom_branch_and_base_ref(self) -> None:
        # Add a second commit on a feature branch so --base-ref points
        # somewhere distinct from HEAD.
        _git(self.project, "checkout", "-q", "-b", "feature")
        _git(self.project, "commit", "--allow-empty", "-m", "feature work")
        feature_sha = _git(
            self.project,
            "rev-parse",
            "HEAD",
        ).stdout.strip()
        _git(self.project, "checkout", "-q", "-")
        rc, _stdout, stderr = self._init(
            "--worktree",
            "--branch",
            "myname/foo",
            "--base-ref",
            "feature",
        )
        self.assertEqual(rc, 0)
        record = st.get_worktree(st.load(self.state_path))
        self.assertEqual(record["branch"], "myname/foo")
        self.assertEqual(record["base_ref"], feature_sha)
        self.assertIn("feature", stderr)
        self.assertIn(feature_sha, stderr)

    def test_no_worktree_flag_omits_field(self) -> None:
        rc, _stdout, _stderr = self._init()
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertIsNone(st.get_worktree(data))

    # --- refusal paths -------------------------------------------------

    def test_refuses_existing_branch(self) -> None:
        _git(self.project, "branch", "clu/foo")
        rc, _stdout, _stderr = self._init("--worktree")
        self.assertEqual(rc, ExitCode.WORKTREE_SETUP_FAILED)
        # State must not have been written.
        self.assertFalse(self.state_path.exists())

    def test_refuses_existing_path(self) -> None:
        (self.parent / "myrepo-foo").mkdir()
        rc, _stdout, _stderr = self._init("--worktree")
        self.assertEqual(rc, ExitCode.WORKTREE_SETUP_FAILED)
        self.assertFalse(self.state_path.exists())
        # Branch must NOT have been created either.
        branch_rc = _git(
            self.project,
            "rev-parse",
            "--verify",
            "refs/heads/clu/foo",
            check=False,
        ).returncode
        self.assertNotEqual(branch_rc, 0)

    def test_refuses_bad_base_ref(self) -> None:
        rc, _stdout, _stderr = self._init(
            "--worktree",
            "--base-ref",
            "no-such-branch",
        )
        self.assertEqual(rc, ExitCode.WORKTREE_SETUP_FAILED)
        self.assertFalse((self.parent / "myrepo-foo").exists())

    def test_refuses_non_git_repo(self) -> None:
        # Wipe the .git dir to make the project a non-repo.
        subprocess.run(["rm", "-rf", str(self.project / ".git")], check=True)
        rc, _stdout, _stderr = self._init("--worktree")
        self.assertEqual(rc, ExitCode.WORKTREE_SETUP_FAILED)

    # --- rollback ------------------------------------------------------

    def test_rollback_on_state_save_failure(self) -> None:
        # Force `save_atomic` to raise after the worktree is materialized;
        # init must tear the worktree + branch back down.
        with mock.patch(
            "end_of_line.cli.st.save_atomic",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                self._init("--worktree")
        # Worktree dir gone.
        self.assertFalse((self.parent / "myrepo-foo").exists())
        # Branch gone.
        branch_rc = _git(
            self.project,
            "rev-parse",
            "--verify",
            "refs/heads/clu/foo",
            check=False,
        ).returncode
        self.assertNotEqual(branch_rc, 0)


if __name__ == "__main__":
    unittest.main()
