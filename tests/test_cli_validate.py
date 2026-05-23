"""Tests for `clu validate` — the mode-agnostic dry-validate verb.

Extracted from the original `clu integrate` (which is now a
deprecation alias). `cmd_validate` is the shared validate path used
by both `clu ship --direct --check` and `clu ship --as-pr --check`
in clu-ship.md.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import registry, state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import CONFIG_FILENAME
from end_of_line.dry_merge import MergeResult
from tests import CluTestCase, git as _git, make_git_project as _make_git_project


def _make_branch(repo: Path, branch: str) -> None:
    _git(repo, "checkout", "-b", branch)
    _git(repo, "checkout", "main")


def _register_done_plan(
    project: Path,
    slug: str,
    batch_id: str | None = None,
    branch: str | None = None,
) -> Path:
    registry.register(project, slug)
    state_path = project / "plans" / ".orchestrator" / f"{slug}.state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = st.empty_state(slug, "plans")
    data["status"] = st.STATUS_DONE
    data["batch_id"] = batch_id
    if branch is not None:
        data["worktree"] = {
            "branch": branch,
            "path": str(project.parent / f"wt-{slug}"),
            "base_ref": "main",
        }
    st.save_atomic(state_path, data)
    return state_path


_CLEAN = MergeResult(outcome="clean", merged_branches=["a", "b"], base_sha="abc123")
_DIRTY = MergeResult(
    outcome="textual_conflict",
    conflict_files=["foo.py", "bar.py"],
    merged_branches=["a", "b"],
    base_sha="abc123",
)


class ValidateBranchesTests(CluTestCase):
    """`clu validate --branches a,b` — no git needed, dry_merge mocked."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "proj"
        self.project.mkdir()
        (self.project / "plans" / ".orchestrator").mkdir(parents=True)

    def _argv(self, *extra: str) -> list[str]:
        return ["validate", "--project", str(self.project), *extra]

    def test_requires_batch_or_branches(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv())
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("--batch", buf.getvalue())

    def test_explicit_branches_overrides_batch_resolution(self) -> None:
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--branches", "clu/plan-a,clu/plan-b"))
        self.assertEqual(rc, ExitCode.OK)
        branches_arg = m.call_args[0][2]
        self.assertEqual(branches_arg, ["clu/plan-a", "clu/plan-b"])

    def test_clean_returns_ok(self) -> None:
        with mock.patch("end_of_line.dry_merge.attempt_merge", return_value=_CLEAN):
            rc = main(self._argv("--branches", "a,b"))
        self.assertEqual(rc, ExitCode.OK)

    def test_dirty_returns_nonzero(self) -> None:
        buf = io.StringIO()
        with mock.patch("end_of_line.dry_merge.attempt_merge", return_value=_DIRTY):
            with redirect_stdout(buf):
                rc = main(self._argv("--branches", "a,b"))
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("foo.py", buf.getvalue())

    def test_no_suite_flag_skips_test_command(self) -> None:
        (self.project / CONFIG_FILENAME).write_text(
            '{"dispatch":{"command":"echo hi"},'
            ' "test_command":"python3 -m unittest"}'
        )
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--branches", "a,b", "--no-suite"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNone(m.call_args[0][3])

    def test_with_suite_passes_test_command(self) -> None:
        (self.project / CONFIG_FILENAME).write_text(
            '{"dispatch":{"command":"echo hi"},'
            ' "test_command":"python3 -m unittest"}'
        )
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--branches", "a,b"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(m.call_args[0][3], "python3 -m unittest")

    def test_base_ref_forwarded(self) -> None:
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--branches", "a,b", "--base-ref", "develop"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(m.call_args[0][1], "develop")

    def test_stdout_reports_outcome(self) -> None:
        buf = io.StringIO()
        with mock.patch("end_of_line.dry_merge.attempt_merge", return_value=_CLEAN):
            with redirect_stdout(buf):
                main(self._argv("--branches", "a,b"))
        self.assertIn("clean", buf.getvalue())

    def test_stderr_carries_no_deprecation_warning(self) -> None:
        # `clu validate` is the canonical verb — no deprecation noise.
        err = io.StringIO()
        with mock.patch("end_of_line.dry_merge.attempt_merge", return_value=_CLEAN):
            with redirect_stderr(err):
                main(self._argv("--branches", "a,b"))
        self.assertNotIn("deprecat", err.getvalue().lower())


class ValidateBatchResolutionTests(CluTestCase):
    """`clu validate --batch B` — resolves DONE plans' branches from registry."""

    def setUp(self) -> None:
        super().setUp()
        self.project = _make_git_project(self.tmp_path)
        _make_branch(self.project, "clu/plan-a")
        _make_branch(self.project, "clu/plan-b")
        _register_done_plan(self.project, "plan-a", batch_id="b1", branch="clu/plan-a")
        _register_done_plan(self.project, "plan-b", batch_id="b1", branch="clu/plan-b")

    def _argv(self, *extra: str) -> list[str]:
        return ["validate", "--project", str(self.project), *extra]

    def test_resolves_batch_to_done_member_branches(self) -> None:
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--batch", "b1"))
        self.assertEqual(rc, ExitCode.OK)
        branches_passed = m.call_args[0][2]
        self.assertIn("clu/plan-a", branches_passed)
        self.assertIn("clu/plan-b", branches_passed)

    def test_batch_skips_plan_without_worktree(self) -> None:
        _register_done_plan(self.project, "plan-c", batch_id="b1", branch=None)
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--batch", "b1"))
        self.assertEqual(rc, ExitCode.OK)
        branches_passed = m.call_args[0][2]
        self.assertNotIn("plan-c", str(branches_passed))
        self.assertEqual(len(branches_passed), 2)

    def test_batch_too_few_plans_returns_generic(self) -> None:
        _register_done_plan(self.project, "lone-plan", batch_id="only1", branch=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--batch", "only1"))
        self.assertEqual(rc, ExitCode.GENERIC)

    def test_batch_skips_nonexistent_branch(self) -> None:
        state_path = self.project / "plans" / ".orchestrator" / "plan-b.state.json"
        with st.mutate(state_path) as data:
            data["worktree"]["branch"] = "clu/nonexistent-branch"
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--batch", "b1"))
        self.assertEqual(rc, ExitCode.GENERIC)


if __name__ == "__main__":
    unittest.main()
