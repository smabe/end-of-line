"""Tests for the previous-attempt context block emitted on attempt > 1 (#60).

Pure-function tests mock `subprocess.run` so we don't need a real git
worktree to validate the markdown shape. One integration test wires it
through `dispatch_for_tick` to assert the sidecar file lands where the
worker skill expects it.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.cli import main
from end_of_line.config import ProjectConfig, DispatchSpec
from end_of_line.dispatch import (
    _delete_stale_attempt_context,
    _last_termination_reason,
    _prev_attempt_context,
    _write_prev_attempt_context,
    dispatch_for_tick,
)
from end_of_line.supervisor import TickResult
from tests import CluTestCase


PLAN = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `t-a.md` | thing | 1h |
"""


def _mock_git_run(status: str, diff: str, log: str, *, rc: int = 0):
    """Build a side-effect for mock.patch on subprocess.run that returns
    different results based on which git subcommand is invoked."""
    def _side(args, **kwargs):
        # args is the ["git", "-C", path, <subcmd>, ...] list
        sub = args[3] if len(args) > 3 else ""
        if sub == "status":
            out = status
        elif sub == "diff":
            out = diff
        elif sub == "log":
            out = log
        else:
            out = ""
        cp = mock.MagicMock()
        cp.stdout = out
        cp.returncode = rc
        return cp
    return _side


class PrevAttemptContextTestCase(unittest.TestCase):
    """Pure-function tests for the markdown builder."""

    def test_includes_attempt_number_and_termination_reason(self) -> None:
        with mock.patch(
            "end_of_line.dispatch.subprocess.run",
            side_effect=_mock_git_run("", "", ""),
        ):
            md = _prev_attempt_context(
                worktree_path="/tmp/wt", base_ref="abc1234",
                phase_id="schema", attempt=2,
                termination_reason="lease expired (worker didn't callback in time)",
            )
        self.assertIn("attempt 2", md)
        self.assertIn("lease expired", md)

    def test_clean_worktree_explicit_clean_message(self) -> None:
        with mock.patch(
            "end_of_line.dispatch.subprocess.run",
            side_effect=_mock_git_run("", "", ""),
        ):
            md = _prev_attempt_context(
                worktree_path="/tmp/wt", base_ref="abc1234",
                phase_id="schema", attempt=2, termination_reason=None,
            )
        self.assertIn("Worktree is clean", md)
        self.assertIn("No commits landed by prior attempts", md)

    def test_dirty_worktree_includes_status_and_diff_blocks(self) -> None:
        status = " M foo.py\n?? new_file.py\n"
        diff = " foo.py | 12 +++++--\n 1 file changed\n"
        with mock.patch(
            "end_of_line.dispatch.subprocess.run",
            side_effect=_mock_git_run(status, diff, ""),
        ):
            md = _prev_attempt_context(
                worktree_path="/tmp/wt", base_ref="abc1234",
                phase_id="schema", attempt=2, termination_reason=None,
            )
        self.assertIn("foo.py", md)
        self.assertIn("Uncommitted changes", md)
        self.assertIn("Diff stat", md)
        self.assertIn("1 file changed", md)

    def test_includes_commits_landed_when_log_non_empty(self) -> None:
        log = "abc1234 partial work\ndef5678 more\n"
        with mock.patch(
            "end_of_line.dispatch.subprocess.run",
            side_effect=_mock_git_run("", "", log),
        ):
            md = _prev_attempt_context(
                worktree_path="/tmp/wt", base_ref="zzz0000",
                phase_id="schema", attempt=3, termination_reason=None,
            )
        self.assertIn("Commits landed by prior attempts", md)
        self.assertIn("partial work", md)
        self.assertIn("zzz0000", md)  # reset hint references base ref

    def test_git_failure_graceful_degradation(self) -> None:
        def _side(args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args, timeout=5)
        with mock.patch(
            "end_of_line.dispatch.subprocess.run", side_effect=_side,
        ):
            md = _prev_attempt_context(
                worktree_path="/tmp/wt", base_ref="abc1234",
                phase_id="schema", attempt=2, termination_reason=None,
            )
        self.assertIn("git status unavailable", md)
        self.assertIn("commit log unavailable", md)
        # Should not raise.

    def test_includes_reset_hint(self) -> None:
        with mock.patch(
            "end_of_line.dispatch.subprocess.run",
            side_effect=_mock_git_run(" M foo.py\n", "", ""),
        ):
            md = _prev_attempt_context(
                worktree_path="/tmp/wt", base_ref="abc1234",
                phase_id="schema", attempt=2, termination_reason=None,
            )
        self.assertIn("reset --hard abc1234", md)


class WriteContextFileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self._tmp.name) / "logs"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_creates_log_dir_and_file(self) -> None:
        path = _write_prev_attempt_context(
            self.log_dir, "myplan", "schema", "hello\n",
        )
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(), "hello\n")
        self.assertEqual(path.name, "attempt-context.myplan.schema.md")

    def test_write_overwrites_existing(self) -> None:
        self.log_dir.mkdir()
        existing = self.log_dir / "attempt-context.myplan.schema.md"
        existing.write_text("stale content")
        path = _write_prev_attempt_context(
            self.log_dir, "myplan", "schema", "fresh\n",
        )
        self.assertEqual(path.read_text(), "fresh\n")

    def test_delete_stale_when_file_exists(self) -> None:
        self.log_dir.mkdir()
        (self.log_dir / "attempt-context.myplan.schema.md").write_text("x")
        _delete_stale_attempt_context(self.log_dir, "myplan", "schema")
        self.assertFalse(
            (self.log_dir / "attempt-context.myplan.schema.md").exists()
        )

    def test_delete_stale_when_file_absent_is_noop(self) -> None:
        # Should not raise.
        _delete_stale_attempt_context(self.log_dir, "myplan", "schema")


class LastTerminationReasonTestCase(unittest.TestCase):
    def test_returns_none_when_no_events(self) -> None:
        self.assertIsNone(_last_termination_reason({"events": []}, "schema"))

    def test_returns_none_when_no_matching_phase(self) -> None:
        data = {"events": [
            {"type": "lease_expired", "phase": "other"},
        ]}
        self.assertIsNone(_last_termination_reason(data, "schema"))

    def test_returns_reason_for_lease_expired(self) -> None:
        data = {"events": [
            {"type": "phase_started", "phase": "schema"},
            {"type": "lease_expired", "phase": "schema"},
        ]}
        reason = _last_termination_reason(data, "schema")
        self.assertIsNotNone(reason)
        self.assertIn("lease expired", reason)

    def test_returns_most_recent_when_multiple(self) -> None:
        data = {"events": [
            {"type": "lease_expired", "phase": "schema"},
            {"type": "claim_force_released", "phase": "schema"},
        ]}
        reason = _last_termination_reason(data, "schema")
        self.assertIn("force-released", reason)


class DispatchIntegrationTestCase(CluTestCase):
    """Integration: dispatch_for_tick wires the context file correctly."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        (self.project / "plans").mkdir()
        (self.project / "plans" / "t.md").write_text(PLAN)
        main(["init", "--project", str(self.project), "--plan", "t"])
        self.state_path = self.project / "plans" / ".orchestrator" / "t.state.json"
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)
        self.log_dir = self.state_path.parent / "logs"
        self.ctx_path = self.log_dir / "attempt-context.t.a.md"

    def _cfg(self, cmd: str = "true") -> ProjectConfig:
        return ProjectConfig(
            project_root=self.project, plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command=cmd, path=""),
        )

    def _result(self, *, worktree: dict | None = None) -> TickResult:
        return TickResult(
            action="dispatch", detail="", phase_id="a",
            token=self.token, worktree=worktree,
        )

    def _bump_attempts_to(self, n: int) -> None:
        with st.mutate(self.state_path) as data:
            data["current_claim"]["attempts"] = n

    def test_attempt_1_with_worktree_does_not_write_context(self) -> None:
        # Pre-existing stale file should be deleted on attempt 1.
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.ctx_path.write_text("stale")
        wt = {"path": str(self.project), "base_ref": "abc1234"}
        with mock.patch(
            "end_of_line.dispatch.subprocess.run",
            side_effect=_mock_git_run("", "", ""),
        ):
            dispatch_for_tick(self._result(worktree=wt), self._cfg(), "t", self.state_path)
        self.assertFalse(self.ctx_path.exists())

    def test_attempt_2_with_worktree_writes_context(self) -> None:
        self._bump_attempts_to(2)
        wt = {"path": str(self.project), "base_ref": "abc1234"}
        with mock.patch(
            "end_of_line.dispatch.subprocess.run",
            side_effect=_mock_git_run(" M foo.py\n", "", ""),
        ):
            dispatch_for_tick(self._result(worktree=wt), self._cfg(), "t", self.state_path)
        self.assertTrue(self.ctx_path.exists())
        content = self.ctx_path.read_text()
        self.assertIn("attempt 2", content)
        self.assertIn("foo.py", content)

    def test_no_worktree_skips_context_machinery(self) -> None:
        # Non-worktree plans don't use the context block.
        self._bump_attempts_to(2)
        with mock.patch(
            "end_of_line.dispatch.subprocess.run",
            side_effect=_mock_git_run(" M foo.py\n", "", ""),
        ):
            dispatch_for_tick(self._result(worktree=None), self._cfg(), "t", self.state_path)
        self.assertFalse(self.ctx_path.exists())


if __name__ == "__main__":
    unittest.main()
