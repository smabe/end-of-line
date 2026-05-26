"""Tests for tool_stuck handling in clu_inbox_surface (worker-watchdog P5).

When the supervisor writes a tool_stuck event into the inbox (#67), the
session-start hook surfaces it like any other event PLUS appends an
investigate-then-recommend instruction block teaching the primary
session what to do — investigate autonomously, propose a kill plan,
await operator approval before any destructive action.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from end_of_line import inbox
from tests import isolate_monitor_marker


def _run_hook(
    *,
    cwd: Path,
    xdg: Path,
    stdin_payload: str = "{}",
    timeout: float = 5.0,
) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(xdg)
    env["PYTHONPATH"] = (
        str(
            Path(__file__).resolve().parent.parent,
        )
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    proc = subprocess.run(
        [sys.executable, "-m", "end_of_line.hooks.clu_inbox_surface"],
        cwd=str(cwd),
        env=env,
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


class ToolStuckHookTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        self.xdg = self.tmp
        self.proj = self.tmp / "proj"
        self.proj.mkdir()

    def _write_tool_stuck_event(self) -> None:
        inbox.write_event(
            type="tool_stuck",
            plan_slug="plan-x",
            project_root=str(self.proj),
            summary=(
                "Worker on plan-x/ai-tools stuck in subprocess for 600s (/usr/bin/xcodebuild test)"
            ),
            details={
                "phase_id": "ai-tools",
                "worker_pid": 78233,
                "descendant_pid": 81681,
                "command": "/usr/bin/xcodebuild test -project HealthDash.xcodeproj",
                "elapsed_seconds": 600,
                "cpu_seconds": 0,
            },
        )

    def test_tool_stuck_event_surfaced_in_context(self) -> None:
        self._write_tool_stuck_event()
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("plan-x", ctx)
        self.assertIn("tool_stuck", ctx)

    def test_tool_stuck_appends_investigate_instruction_block(self) -> None:
        self._write_tool_stuck_event()
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        # The contract has three required elements: investigate, recommend,
        # await operator approval before destructive action.
        lowered = ctx.lower()
        self.assertIn("investigate", lowered)
        self.assertIn("recommend", lowered)
        # Explicit no-auto-intervention guard.
        self.assertTrue(
            "operator-approval" in lowered or "operator approval" in lowered or "do not" in lowered,
            f"missing operator-approval guard in:\n{ctx}",
        )

    def test_no_tool_stuck_event_no_instruction_block(self) -> None:
        # An inbox with only blocker / halted events must NOT include the
        # tool_stuck instruction block.
        inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root=str(self.proj),
            summary="just a halt",
        )
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("investigate autonomously", ctx.lower())

    def test_instruction_block_appears_once_even_with_multiple_stucks(self) -> None:
        # Two tool_stuck events in the inbox — the instruction block should
        # be appended once, not per-event.
        self._write_tool_stuck_event()
        self._write_tool_stuck_event()
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(ctx.lower().count("investigate autonomously"), 1)

    def test_instruction_block_references_kill_recommendation(self) -> None:
        # The block must teach the session to propose a kill plan (not run
        # it) so the operator-approval gate is respected.
        self._write_tool_stuck_event()
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        lowered = ctx.lower()
        self.assertIn("kill", lowered)
        # Must explicitly call out the destructive commands so the session
        # doesn't try to "be helpful" by running them.
        self.assertTrue(
            "release-claim" in lowered or "force-complete" in lowered,
            f"instruction should name the destructive commands to gate:\n{ctx}",
        )


if __name__ == "__main__":
    unittest.main()
