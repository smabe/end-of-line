"""Tests for state.is_branch_merged_into.

All tests use real git repos in temporary directories.
"""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from end_of_line.state import is_branch_merged_into
from tests import git as _git


def _make_repo() -> Path:
    d = Path(tempfile.mkdtemp(prefix="clu-test-merged-"))
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@test.invalid")
    _git(d, "config", "user.name", "Test User")
    (d / "README").write_text("init\n")
    _git(d, "add", "README")
    _git(d, "commit", "-m", "init")
    return d


class TestIsBranchMergedInto(unittest.TestCase):

    def setUp(self) -> None:
        self._dirs: list[str] = []

    def tearDown(self) -> None:
        import shutil
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _repo(self) -> Path:
        d = _make_repo()
        self._dirs.append(str(d))
        return d

    def test_returns_true_when_branch_is_ancestor(self) -> None:
        repo = self._repo()
        # create feature branch with one commit
        _git(repo, "checkout", "-b", "feature")
        (repo / "feat.txt").write_text("feature\n")
        _git(repo, "add", "feat.txt")
        _git(repo, "commit", "-m", "feat")
        # merge feature into main
        _git(repo, "checkout", "main")
        _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature")
        # feature HEAD is now an ancestor of main
        self.assertTrue(is_branch_merged_into(repo, "feature", "main"))

    def test_returns_false_when_branch_ahead_of_base(self) -> None:
        repo = self._repo()
        _git(repo, "checkout", "-b", "feature")
        (repo / "feat.txt").write_text("unmerged\n")
        _git(repo, "add", "feat.txt")
        _git(repo, "commit", "-m", "unmerged feat")
        # feature has commits past main but hasn't been merged
        self.assertFalse(is_branch_merged_into(repo, "feature", "main"))

    def test_returns_false_when_branch_missing(self) -> None:
        repo = self._repo()
        result = is_branch_merged_into(repo, "nonexistent-branch", "main")
        self.assertFalse(result)

    def test_returns_false_when_base_ref_missing(self) -> None:
        repo = self._repo()
        result = is_branch_merged_into(repo, "main", "refs/remotes/origin/nonexistent")
        self.assertFalse(result)

    def test_default_base_ref_is_origin_main(self) -> None:
        sig = inspect.signature(is_branch_merged_into)
        self.assertEqual(sig.parameters["base_ref"].default, "origin/main")
