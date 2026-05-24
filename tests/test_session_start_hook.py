"""Tests for the SessionStart hook script (#70 cold-start arming) and
its CLI install/uninstall path."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import monitor, registry
from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from end_of_line.hooks import clu_session_start


# ---- hook script unit tests ------------------------------------------------


class SessionStartHookScriptTest(unittest.TestCase):
    """The hook script itself — invoked by Claude Code on session start."""

    def test_main_emits_hook_specific_output(self) -> None:
        with mock.patch.object(sys, "stdin", io.StringIO("")), \
             mock.patch.object(sys, "stdout", io.StringIO()) as out:
            rc = clu_session_start.main()
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertIn("hookSpecificOutput", payload)
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"], "SessionStart",
        )
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("clu operator dashboard", ctx)
        self.assertIn("clu watch --all --operator", ctx)
        self.assertIn("persistent=True", ctx)

    def test_additional_context_under_10k_chars(self) -> None:
        # Claude Code documents a 10K cap on additionalContext; the
        # instruction must stay well under it.
        self.assertLess(len(clu_session_start.INSTRUCTION), 9500)

    def test_main_returns_zero_on_stdin_failure(self) -> None:
        bad_stdin = mock.MagicMock()
        bad_stdin.read.side_effect = IOError("pipe closed")
        with mock.patch.object(sys, "stdin", bad_stdin), \
             mock.patch.object(sys, "stdout", io.StringIO()):
            # Hook must never propagate errors — Claude Code would surface
            # them as session-start failures.
            rc = clu_session_start.main()
        self.assertEqual(rc, 0)

    def test_main_pops_clu_test_mode_env(self) -> None:
        # Inherited CLU_TEST_MODE must not false-trip the XDG guard inside
        # the hook process if the hook ever calls into clu state code.
        with mock.patch.dict(os.environ, {"CLU_TEST_MODE": "1"}), \
             mock.patch.object(sys, "stdin", io.StringIO("")), \
             mock.patch.object(sys, "stdout", io.StringIO()):
            clu_session_start.main()
        # Side effect: CLU_TEST_MODE should be popped from the hook's env.
        self.assertNotIn("CLU_TEST_MODE", os.environ)


# ---- CLI install/uninstall integration -------------------------------------


class SessionStartInstallTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.patcher_env = mock.patch.dict(
            os.environ,
            {"HOME": str(self.home),
             "XDG_CONFIG_HOME": str(self.home / ".config")},
        )
        self.patcher_env.start()
        self.addCleanup(self.patcher_env.stop)
        self.settings = self.home / ".claude" / "settings.json"

    def _install(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-hook", *extra])
        return rc, out.getvalue(), err.getvalue()

    def _uninstall(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["uninstall-hook"])
        return rc, out.getvalue(), err.getvalue()

    def _hooks_block(self) -> dict:
        data = json.loads(self.settings.read_text())
        return data.get("hooks", {})


class InstallSessionStartFlagTests(SessionStartInstallTestBase):
    def test_install_no_flag_does_not_add_session_start(self) -> None:
        rc, _, err = self._install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        hooks = self._hooks_block()
        self.assertIn("UserPromptSubmit", hooks)
        self.assertNotIn("SessionStart", hooks)

    def test_install_with_flag_adds_session_start_entry(self) -> None:
        rc, _, err = self._install("--session-start")
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        hooks = self._hooks_block()
        self.assertIn("UserPromptSubmit", hooks)
        self.assertIn("SessionStart", hooks)
        ss = hooks["SessionStart"]
        self.assertEqual(len(ss), 1)
        # Verify the entry references clu_session_start.py
        entry = ss[0]
        cmd = entry.get("command") or entry.get("hooks", [{}])[0].get("command", "")
        self.assertIn("clu_session_start.py", cmd)

    def test_install_with_flag_idempotent(self) -> None:
        rc1, _, _ = self._install("--session-start")
        rc2, _, _ = self._install("--session-start")
        self.assertEqual(rc1, int(ExitCode.OK))
        self.assertEqual(rc2, int(ExitCode.OK))
        ss = self._hooks_block().get("SessionStart", [])
        self.assertEqual(len(ss), 1, "should not duplicate the entry")

    def test_install_with_flag_records_marker_field(self) -> None:
        rc, _, _ = self._install("--session-start")
        self.assertEqual(rc, int(ExitCode.OK))
        m = monitor.load_marker()
        self.assertIsNotNone(m)
        self.assertIn("session_start_hook_path", m)
        self.assertIn("clu_session_start.py", m["session_start_hook_path"])

    def test_install_no_flag_does_not_set_marker_field(self) -> None:
        rc, _, _ = self._install()
        self.assertEqual(rc, int(ExitCode.OK))
        m = monitor.load_marker()
        self.assertIsNotNone(m)
        self.assertNotIn("session_start_hook_path", m)

    def test_install_session_start_after_plain_install_adds_only_session_start(self) -> None:
        # Operator runs `install-hook` first, then later runs
        # `install-hook --session-start`. Plain UPS entry stays put;
        # SessionStart gets added on top.
        self._install()
        ups_before = self._hooks_block().get("UserPromptSubmit", [])
        self._install("--session-start")
        hooks_after = self._hooks_block()
        self.assertEqual(len(hooks_after["UserPromptSubmit"]),
                         len(ups_before), "UPS entry should not duplicate")
        self.assertIn("SessionStart", hooks_after)

    def test_uninstall_removes_session_start_entry(self) -> None:
        self._install("--session-start")
        rc, _, _ = self._uninstall()
        self.assertEqual(rc, int(ExitCode.OK))
        hooks = self._hooks_block()
        # Both UPS and SessionStart entries should be gone (matched by path).
        self.assertNotIn(
            "clu_session_start.py",
            json.dumps(hooks.get("SessionStart", [])),
        )

    def test_install_preserves_unrelated_session_start_entry(self) -> None:
        # Operator already has a SessionStart hook (their own work).
        # Install --session-start must not clobber it.
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command",
                                "command": "/usr/bin/my-other-hook"}]}
                ]
            }
        }))
        rc, _, _ = self._install("--session-start")
        self.assertEqual(rc, int(ExitCode.OK))
        ss = self._hooks_block().get("SessionStart", [])
        self.assertEqual(len(ss), 2, "operator's entry must survive")


# ---- per-plan Monitor arming based on active plans in CWD ----------------


class SessionStartActivePlansTest(unittest.TestCase):
    """Hook emits per-plan Monitor arming + TaskCreate/TaskUpdate protocol
    when active (STATUS_RUNNING) plans are detected in the current CWD's
    registry entries."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._base = Path(self._tmp.name)
        self._project = self._base / "project"
        self._project.mkdir()
        (self._project / "plans" / ".orchestrator").mkdir(parents=True)
        self._xdg_patch = mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self._base)}
        )
        self._xdg_patch.start()
        self.addCleanup(self._xdg_patch.stop)

    def _register_plan(self, slug: str, status: str = st.STATUS_RUNNING) -> None:
        """Register a plan in the CWD project and write a proper state file."""
        registry.register(self._project, slug)
        state_path = (
            self._project / "plans" / ".orchestrator" / f"{slug}.state.json"
        )
        data = st.empty_state(slug, "plans")
        data["status"] = status
        st.save_atomic(state_path, data)

    def _run_hook(self) -> tuple[int, str]:
        """Run the hook with os.getcwd() patched to the test project dir."""
        out = io.StringIO()
        with mock.patch.object(os, "getcwd", return_value=str(self._project.resolve())), \
             mock.patch.object(sys, "stdin", io.StringIO("")), \
             mock.patch.object(sys, "stdout", out):
            rc = clu_session_start.main()
        payload = json.loads(out.getvalue())
        return rc, payload["hookSpecificOutput"]["additionalContext"]

    # ------------------------------------------------------------------

    def test_no_active_plans_omits_per_plan_block(self) -> None:
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("clu watch --all --operator", ctx)
        self.assertNotIn("--task-list", ctx)
        self.assertNotIn("TASK_CREATE", ctx)

    def test_one_running_plan_emits_arming_block(self) -> None:
        self._register_plan("my-plan")
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("Monitor(", ctx)
        self.assertIn("--plan my-plan --task-list", ctx)

    def test_multiple_running_plans_arm_each(self) -> None:
        self._register_plan("plan-one")
        self._register_plan("plan-two")
        self._register_plan("plan-three")
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        # Each plan emits one Monitor(...) block with --project . --plan <slug>
        self.assertEqual(ctx.count("--project . --plan "), 3)
        # Protocol block is emitted exactly once (not once per plan)
        self.assertEqual(ctx.count("clu task-list protocol"), 1)

    def test_non_running_plans_excluded(self) -> None:
        self._register_plan("paused-plan", status=st.STATUS_PAUSED)
        self._register_plan("halted-plan", status=st.STATUS_HALTED)
        self._register_plan("done-plan", status=st.STATUS_DONE)
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertNotIn("--task-list", ctx)
        self.assertNotIn("TASK_CREATE", ctx)

    def test_other_project_plans_excluded(self) -> None:
        other = self._base / "other-project"
        other.mkdir()
        (other / "plans" / ".orchestrator").mkdir(parents=True)
        registry.register(other, "other-slug")
        state_path = other / "plans" / ".orchestrator" / "other-slug.state.json"
        st.save_atomic(state_path, st.empty_state("other-slug", "plans"))
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertNotIn("--task-list", ctx)

    def test_corrupt_state_tolerated(self) -> None:
        registry.register(self._project, "corrupt-plan")
        state_path = (
            self._project / "plans" / ".orchestrator" / "corrupt-plan.state.json"
        )
        state_path.write_text("{not valid json")
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("clu watch --all --operator", ctx)
        self.assertNotIn("--task-list", ctx)

    def test_protocol_block_present_when_plans_active(self) -> None:
        self._register_plan("active-plan")
        _, ctx = self._run_hook()
        self.assertIn("TASK_CREATE", ctx)
        self.assertIn("TASK_UPDATE", ctx)
        self.assertIn("└ ", ctx)
        self.assertIn("Do NOT re-set subject", ctx)

    def test_runtime_output_under_10k_with_max_plans(self) -> None:
        for i in range(10):
            self._register_plan(f"plan-{i:02d}")
        _, ctx = self._run_hook()
        self.assertLess(len(ctx), 9500)


if __name__ == "__main__":
    unittest.main()
