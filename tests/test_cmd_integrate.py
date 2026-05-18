"""Tests for `clu integrate` operator command.

Phase 4 of dry-merge-gate: wraps dry_merge.attempt_merge for on-demand
replay. Does NOT mutate plan state or file follow-up plans (the cross-plan
rule owns that).
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import registry, state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import CONFIG_FILENAME, load_project_config
from end_of_line.dry_merge import MergeResult
from tests import CluTestCase, git as _git, make_git_project as _make_git_project


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_branch(repo: Path, branch: str) -> None:
    _git(repo, "checkout", "-b", branch)
    _git(repo, "checkout", "main")


def _register_done_plan(
    project: Path,
    slug: str,
    batch_id: str | None = None,
    branch: str | None = None,
) -> Path:
    """Register plan and create a DONE state file with optional batch+worktree."""
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


class IntegrateBranchesTests(CluTestCase):
    """Tests for --branches mode (no batch resolution, no git needed)."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "proj"
        self.project.mkdir()
        (self.project / "plans" / ".orchestrator").mkdir(parents=True)

    def _argv(self, *extra: str) -> list[str]:
        return ["integrate", "--project", str(self.project), *extra]

    def test_integrate_requires_batch_or_branches(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv())
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("--batch", buf.getvalue() + "")

    def test_integrate_explicit_branches_overrides_batch_resolution(self) -> None:
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--branches", "clu/plan-a,clu/plan-b"))
        self.assertEqual(rc, ExitCode.OK)
        _, kwargs = m.call_args
        branches_arg = m.call_args[0][2]
        self.assertEqual(branches_arg, ["clu/plan-a", "clu/plan-b"])

    def test_integrate_clean_returns_ok(self) -> None:
        with mock.patch("end_of_line.dry_merge.attempt_merge", return_value=_CLEAN):
            rc = main(self._argv("--branches", "a,b"))
        self.assertEqual(rc, ExitCode.OK)

    def test_integrate_dirty_returns_nonzero(self) -> None:
        buf = io.StringIO()
        with mock.patch("end_of_line.dry_merge.attempt_merge", return_value=_DIRTY):
            with redirect_stdout(buf):
                rc = main(self._argv("--branches", "a,b"))
        self.assertEqual(rc, ExitCode.GENERIC)
        output = buf.getvalue()
        self.assertIn("foo.py", output)

    def test_integrate_no_suite_flag_skips_test_command(self) -> None:
        (self.project / CONFIG_FILENAME).write_text(
            '{"dispatch":{"command":"echo hi"},'
            ' "test_command":"python3 -m unittest"}'
        )
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--branches", "a,b", "--no-suite"))
        self.assertEqual(rc, ExitCode.OK)
        test_cmd_passed = m.call_args[0][3]  # positional: project_root, base_ref, branches, test_command
        self.assertIsNone(test_cmd_passed)

    def test_integrate_with_suite_passes_test_command(self) -> None:
        (self.project / CONFIG_FILENAME).write_text(
            '{"dispatch":{"command":"echo hi"},'
            ' "test_command":"python3 -m unittest"}'
        )
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--branches", "a,b"))
        self.assertEqual(rc, ExitCode.OK)
        test_cmd_passed = m.call_args[0][3]
        self.assertEqual(test_cmd_passed, "python3 -m unittest")

    def test_integrate_base_ref_forwarded(self) -> None:
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--branches", "a,b", "--base-ref", "develop"))
        self.assertEqual(rc, ExitCode.OK)
        base_ref_passed = m.call_args[0][1]
        self.assertEqual(base_ref_passed, "develop")

    def test_integrate_stdout_reports_outcome(self) -> None:
        buf = io.StringIO()
        with mock.patch("end_of_line.dry_merge.attempt_merge", return_value=_CLEAN):
            with redirect_stdout(buf):
                main(self._argv("--branches", "a,b"))
        self.assertIn("clean", buf.getvalue())


class IntegrateBatchResolutionTests(CluTestCase):
    """Tests for --batch mode (batch resolution from registry + state files)."""

    def setUp(self) -> None:
        super().setUp()
        self.project = _make_git_project(self.tmp_path)
        _make_branch(self.project, "clu/plan-a")
        _make_branch(self.project, "clu/plan-b")
        _register_done_plan(
            self.project, "plan-a", batch_id="b1", branch="clu/plan-a",
        )
        _register_done_plan(
            self.project, "plan-b", batch_id="b1", branch="clu/plan-b",
        )

    def _argv(self, *extra: str) -> list[str]:
        return ["integrate", "--project", str(self.project), *extra]

    def test_integrate_resolves_batch_to_done_member_branches(self) -> None:
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--batch", "b1"))
        self.assertEqual(rc, ExitCode.OK)
        branches_passed = m.call_args[0][2]
        self.assertIn("clu/plan-a", branches_passed)
        self.assertIn("clu/plan-b", branches_passed)

    def test_integrate_batch_skips_plan_without_worktree(self) -> None:
        # Add a third DONE plan in batch b1 but without a worktree record.
        _register_done_plan(self.project, "plan-c", batch_id="b1", branch=None)
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            rc = main(self._argv("--batch", "b1"))
        self.assertEqual(rc, ExitCode.OK)
        branches_passed = m.call_args[0][2]
        self.assertNotIn("plan-c", str(branches_passed))
        self.assertEqual(len(branches_passed), 2)

    def test_integrate_batch_too_few_plans_returns_generic(self) -> None:
        # Only one DONE plan in batch.
        _register_done_plan(self.project, "lone-plan", batch_id="only1", branch=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--batch", "only1"))
        self.assertEqual(rc, ExitCode.GENERIC)

    def test_integrate_batch_skips_nonexistent_branch(self) -> None:
        # plan-b's branch doesn't exist in git (deleted).
        # Simulate by pointing to a branch that was never created.
        state_path = self.project / "plans" / ".orchestrator" / "plan-b.state.json"
        with st.mutate(state_path) as data:
            data["worktree"]["branch"] = "clu/nonexistent-branch"
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--batch", "b1"))
        # Only 1 live branch remains → GENERIC
        self.assertEqual(rc, ExitCode.GENERIC)


if __name__ == "__main__":
    unittest.main()
