"""Tests for the `clu_inbox_surface` UserPromptSubmit hook script.

The hook script is invoked by Claude Code at the start of every user
prompt. It reads `~/.config/clu/inbox/`, filters events to the current
project_root, emits `hookSpecificOutput` JSON on stdout, and marks each
surfaced event processed.

Tests invoke the script as a subprocess to exercise the full headless
boundary — stdin payload + stdout JSON shape + filesystem side effects.
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
from unittest import mock

from end_of_line import inbox

from tests import isolate_monitor_marker


def _run_hook(
    *, cwd: Path, xdg: Path, stdin_payload: str = "{}",
    timeout: float = 5.0,
) -> tuple[int, str, str, float]:
    """Run the hook as a subprocess from `cwd` with `XDG_CONFIG_HOME=xdg`."""
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(xdg)
    env["PYTHONPATH"] = str(
        Path(__file__).resolve().parent.parent,
    ) + os.pathsep + env.get("PYTHONPATH", "")
    start = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "end_of_line.hooks.clu_inbox_surface"],
        cwd=str(cwd), env=env, input=stdin_payload,
        capture_output=True, text=True, timeout=timeout,
    )
    elapsed = time.time() - start
    return proc.returncode, proc.stdout, proc.stderr, elapsed


class HookTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        self.xdg = self.tmp
        self.proj = self.tmp / "proj-a"
        self.proj.mkdir()


class HookSurfacingTests(HookTestBase):
    def test_hook_empty_inbox_exits_zero_no_stdout(self) -> None:
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_hook_surfaces_events_for_current_project(self) -> None:
        inbox.write_event(
            type="halted", plan_slug="foo", project_root=str(self.proj),
            summary="proj-a event 1",
        )
        inbox.write_event(
            type="blocked", plan_slug="foo", project_root=str(self.proj),
            summary="proj-a event 2",
        )
        other = self.tmp / "proj-b"
        other.mkdir()
        inbox.write_event(
            type="halted", plan_slug="bar", project_root=str(other),
            summary="proj-b event",
        )

        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"],
            "UserPromptSubmit",
        )
        self.assertIn("proj-a event 1", ctx)
        self.assertIn("proj-a event 2", ctx)
        self.assertNotIn("proj-b event", ctx)

    def test_hook_marks_surfaced_events_processed(self) -> None:
        inbox.write_event(
            type="halted", plan_slug="foo", project_root=str(self.proj),
            summary="event 1",
        )
        inbox.write_event(
            type="blocked", plan_slug="foo", project_root=str(self.proj),
            summary="event 2",
        )
        rc, _, _, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0)
        # Subsequent run sees an empty unprocessed inbox.
        rc2, out2, _, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc2, 0)
        self.assertEqual(out2, "")

    def test_hook_caps_at_20_events_with_footer(self) -> None:
        for i in range(25):
            inbox.write_event(
                type="halted", plan_slug="foo",
                project_root=str(self.proj), summary=f"event {i:02d}",
            )
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        # The 5 oldest must be summarized as a footer, not listed inline.
        self.assertNotIn("event 00", ctx)
        self.assertNotIn("event 04", ctx)
        self.assertIn("event 24", ctx)
        self.assertIn("5 older events", ctx)

    def test_hook_truncates_additional_context_at_10k_chars(self) -> None:
        huge = "X" * 2000
        for i in range(20):
            inbox.write_event(
                type="halted", plan_slug="foo",
                project_root=str(self.proj),
                summary=f"{i:02d} {huge}",
            )
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), 10_000)
        self.assertIn("truncated", ctx.lower())

    def test_hook_falls_back_to_cwd_when_no_git(self) -> None:
        non_repo = self.tmp / "no-repo"
        non_repo.mkdir()
        inbox.write_event(
            type="halted", plan_slug="foo",
            project_root=str(non_repo), summary="cwd fallback",
        )
        rc, out, err, _ = _run_hook(cwd=non_repo, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("cwd fallback", ctx)

    def test_hook_exits_zero_on_corrupt_event_file(self) -> None:
        inbox.write_event(
            type="halted", plan_slug="foo",
            project_root=str(self.proj), summary="ok",
        )
        # Corrupt a sibling file.
        root = inbox.inbox_root()
        (root / "bogus.json").write_text("{{{ not valid")
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        # Valid event still surfaces.
        payload = json.loads(out)
        self.assertIn("ok", payload["hookSpecificOutput"]["additionalContext"])

    def test_hook_under_500ms_with_50_events(self) -> None:
        for i in range(50):
            inbox.write_event(
                type="halted", plan_slug="foo",
                project_root=str(self.proj), summary=f"event {i}",
            )
        # CI buffer: 2s. Local typical: <500ms. The mandate is "comfortably
        # under the latency budget"; if this trips on slow CI, raise the
        # ceiling, don't delete the test.
        _, _, _, elapsed = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
