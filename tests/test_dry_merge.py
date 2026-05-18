"""Tests for end_of_line.dry_merge.attempt_merge.

All tests use real git repos in temporary directories.
The cmd_answer regression test reproduces the canonical 2026-05-18
incident: textual-merge succeeds but suite fails on a renamed function.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from end_of_line.dry_merge import MergeResult, attempt_merge
from tests import git as _git


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_tmp_repo() -> Path:
    """Create a minimal git repo in a temp dir.

    Returns the Path.  Caller owns cleanup (wrap in TemporaryDirectory or
    add to self.addCleanup).
    """
    d = Path(tempfile.mkdtemp(prefix="clu-test-repo-"))
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@test.invalid")
    _git(d, "config", "user.name", "Test User")
    # initial commit so branches have a common base
    (d / "README").write_text("init\n")
    _git(d, "add", "README")
    _git(d, "commit", "-m", "init")
    return d


def _branch(repo: Path, name: str) -> None:
    _git(repo, "checkout", "-b", name)


def _checkout(repo: Path, name: str) -> None:
    _git(repo, "checkout", name)


def _commit_file(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", message)


def _no_scratch_worktrees(repo: Path) -> bool:
    """Return True if no clu-dry-merge-* worktrees are listed."""
    result = _git(repo, "worktree", "list")
    return "clu-dry-merge-" not in result.stdout


# ---------------------------------------------------------------------------
# test cases
# ---------------------------------------------------------------------------

class _DryMergeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = _make_tmp_repo()
        self.addCleanup(shutil.rmtree, str(self.repo), True)


class AttemptMergeCleanTest(_DryMergeTestCase):
    def test_attempt_merge_clean(self) -> None:
        """Two branches with additive changes to separate files: clean merge."""
        repo = self.repo

        _branch(repo, "branch-a")
        _commit_file(repo, "file_a.txt", "content A\n", "add file_a")
        _checkout(repo, "main")

        _branch(repo, "branch-b")
        _commit_file(repo, "file_b.txt", "content B\n", "add file_b")
        _checkout(repo, "main")

        base_sha = _git(repo, "rev-parse", "main").stdout.strip()

        result = attempt_merge(repo, base_sha, ["branch-a", "branch-b"])

        self.assertEqual(result.outcome, "clean")
        self.assertEqual(result.conflict_files, [])
        self.assertEqual(result.merged_branches, ["branch-a", "branch-b"])
        self.assertEqual(result.base_sha, base_sha)
        self.assertTrue(_no_scratch_worktrees(repo))


class AttemptMergeTextualConflictTest(_DryMergeTestCase):
    def test_attempt_merge_textual_conflict(self) -> None:
        """Two branches edit the same line: textual conflict."""
        repo = self.repo
        _commit_file(repo, "shared.txt", "original line\n", "add shared")

        _branch(repo, "branch-a")
        _commit_file(repo, "shared.txt", "branch-a edit\n", "branch a edit")
        _checkout(repo, "main")

        _branch(repo, "branch-b")
        _commit_file(repo, "shared.txt", "branch-b edit\n", "branch b edit")
        _checkout(repo, "main")

        base_sha = _git(repo, "rev-parse", "main").stdout.strip()

        result = attempt_merge(repo, base_sha, ["branch-a", "branch-b"])

        self.assertEqual(result.outcome, "textual_conflict")
        self.assertIn("shared.txt", result.conflict_files)
        self.assertTrue(_no_scratch_worktrees(repo))


class AttemptMergeCmdAnswerRegressionTest(_DryMergeTestCase):
    """Reproducer for the 2026-05-18 cmd_answer incident.

    Branch A renames a function (old callers break); branch B adds a new
    test that calls the OLD name.  No textual overlap → auto-merge succeeds;
    suite fails at runtime.
    """

    def test_attempt_merge_suite_failed_cmd_answer_regression(self) -> None:
        repo = self.repo

        # Set up shared baseline: src/util.py with old function signature
        (repo / "src").mkdir()
        _commit_file(
            repo, "src/util.py",
            "def foo(blocker_id, idx):\n    return f'{blocker_id}-{idx}'\n",
            "add util.foo",
        )
        _commit_file(repo, "src/__init__.py", "", "src init")
        (repo / "tests").mkdir()
        _commit_file(repo, "tests/__init__.py", "", "tests init")
        _commit_file(
            repo, "tests/test_baseline.py",
            (
                "import unittest\n"
                "from src.util import foo\n\n"
                "class BaselineTest(unittest.TestCase):\n"
                "    def test_baseline(self): assert foo('x', 0)\n"
            ),
            "add baseline test",
        )

        base_sha = _git(repo, "rev-parse", "main").stdout.strip()

        # Branch A: rename function signature (update existing test to match)
        _branch(repo, "branch-a")
        _commit_file(
            repo, "src/util.py",
            "def foo(answer, *, plan=None):\n    return f'{answer}-{plan}'\n",
            "branch-a: rename foo signature",
        )
        _commit_file(
            repo, "tests/test_baseline.py",
            (
                "import unittest\n"
                "from src.util import foo\n\n"
                "class BaselineTest(unittest.TestCase):\n"
                "    def test_baseline(self): assert foo('x', plan='p')\n"
            ),
            "branch-a: update existing test",
        )
        _checkout(repo, "main")

        # Branch B: add a NEW test file calling OLD signature (no overlap with A)
        _branch(repo, "branch-b")
        _commit_file(
            repo, "tests/test_b.py",
            (
                "import unittest\n"
                "from src.util import foo\n\n"
                "class BranchBTest(unittest.TestCase):\n"
                "    def test_b(self): assert foo('b-1', 0)\n"
            ),
            "branch-b: add test_b calling old foo signature",
        )
        _checkout(repo, "main")

        result = attempt_merge(
            repo, base_sha, ["branch-a", "branch-b"],
            "python3 -m unittest discover -s tests -t .",
        )

        self.assertEqual(result.outcome, "suite_failed")
        self.assertNotEqual(result.test_exit_code, 0)
        # stderr_tail should contain evidence of a TypeError or unexpected args
        combined = result.stderr_tail.lower()
        self.assertTrue(
            "typeerror" in combined or "error" in combined,
            f"expected error in stderr_tail, got: {result.stderr_tail!r}",
        )
        self.assertTrue(_no_scratch_worktrees(repo))


class AttemptMergeScratchWorktreeCleanupTest(_DryMergeTestCase):
    """Scratch worktree must be removed even when a mid-sequence merge fails."""

    def test_attempt_merge_scratch_worktree_always_cleaned_up(self) -> None:
        repo = self.repo
        _commit_file(repo, "shared.txt", "original\n", "add shared")

        base_sha = _git(repo, "rev-parse", "main").stdout.strip()

        _branch(repo, "branch-a")
        _commit_file(repo, "file_a.txt", "only in a\n", "a")
        _checkout(repo, "main")

        _branch(repo, "branch-b")
        _commit_file(repo, "shared.txt", "b edit\n", "b conflicts")
        _checkout(repo, "main")

        # branch-c conflicts with branch-b so the second merge aborts mid-sequence
        _branch(repo, "branch-c")
        _commit_file(repo, "shared.txt", "c edit\n", "c conflicts")
        _checkout(repo, "main")

        result = attempt_merge(repo, base_sha, ["branch-a", "branch-b", "branch-c"])

        self.assertIn(result.outcome, ("textual_conflict", "suite_failed", "clean"))
        self.assertTrue(_no_scratch_worktrees(repo))


class AttemptMergeTimeoutTest(_DryMergeTestCase):
    """test_command that sleeps longer than timeout → suite_failed, no exception escape."""

    def test_attempt_merge_test_command_timeout(self) -> None:
        repo = self.repo

        _branch(repo, "branch-a")
        _commit_file(repo, "file_a.txt", "a\n", "a")
        _checkout(repo, "main")

        base_sha = _git(repo, "rev-parse", "main").stdout.strip()

        result = attempt_merge(
            repo, base_sha, ["branch-a"],
            "sleep 5",
            timeout=1,
        )

        self.assertEqual(result.outcome, "suite_failed")
        self.assertEqual(result.test_exit_code, -1)
        self.assertIn("timeout", result.stderr_tail.lower())
        self.assertTrue(_no_scratch_worktrees(repo))
