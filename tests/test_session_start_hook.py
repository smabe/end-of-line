"""Tests for the SessionStart hook script (#70 cold-start arming) and
its CLI install/uninstall path."""

from __future__ import annotations

import io
import json
import os
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
from tests import must, write_state

# ---- hook script unit tests ------------------------------------------------


class SessionStartHookScriptTest(unittest.TestCase):
    """The hook script itself — invoked by Claude Code on session start.

    Hermetic: a tmp XDG_CONFIG_HOME isolates the registry, so tests never
    read (or depend on) the developer's real ~/.config/clu state. Emission
    is gated on a live (non-terminal) registered plan, so tests that expect
    output must register one first.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._base = Path(self._tmp.name)
        self._project = self._base / "some-project"
        (self._project / "plans" / ".orchestrator").mkdir(parents=True)
        self._xdg = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self._base)})
        self._xdg.start()
        self.addCleanup(self._xdg.stop)

    def _register_plan(self, slug: str, status: str = st.STATUS_RUNNING) -> None:
        registry.register(self._project, slug)
        state_path = self._project / "plans" / ".orchestrator" / f"{slug}.state.json"
        data = st.empty_state(slug, "plans")
        data["status"] = status
        write_state(state_path, data)

    def _run(self) -> tuple[int, str]:
        with (
            mock.patch.object(sys, "stdin", io.StringIO("")),
            mock.patch.object(sys, "stdout", io.StringIO()) as out,
        ):
            rc = clu_session_start.main()
        return rc, out.getvalue()

    def test_main_emits_hook_specific_output(self) -> None:
        self._register_plan("live-plan")
        rc, raw = self._run()
        self.assertEqual(rc, 0)
        payload = json.loads(raw)
        self.assertIn("hookSpecificOutput", payload)
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"],
            "SessionStart",
        )
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("clu operator dashboard", ctx)
        self.assertIn("clu watch --all --operator", ctx)
        self.assertIn("persistent=True", ctx)

    def test_empty_registry_emits_nothing(self) -> None:
        rc, raw = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(raw, "")

    def test_terminal_only_registry_emits_nothing(self) -> None:
        # Registry rows persist after plans finish (until an archive sweep);
        # a registry holding only terminal plans must not cost the session
        # the dashboard tokens.
        self._register_plan("finished", status=st.STATUS_DONE)
        self._register_plan("halted", status=st.STATUS_HALTED)
        rc, raw = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(raw, "")

    def test_registry_read_failure_falls_back_to_emitting(self) -> None:
        with mock.patch.object(registry, "entries", side_effect=OSError("boom")):
            rc, raw = self._run()
        self.assertEqual(rc, 0)
        ctx = json.loads(raw)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("clu operator dashboard", ctx)

    def test_additional_context_under_10k_chars(self) -> None:
        # Claude Code documents a 10K cap on additionalContext; the
        # instruction must stay well under it.
        self.assertLess(len(clu_session_start.INSTRUCTION), 9500)

    def test_main_returns_zero_on_stdin_failure(self) -> None:
        bad_stdin = mock.MagicMock()
        bad_stdin.read.side_effect = OSError("pipe closed")
        with (
            mock.patch.object(sys, "stdin", bad_stdin),
            mock.patch.object(sys, "stdout", io.StringIO()),
        ):
            # Hook must never propagate errors — Claude Code would surface
            # them as session-start failures.
            rc = clu_session_start.main()
        self.assertEqual(rc, 0)

    def test_main_pops_clu_test_mode_env(self) -> None:
        # Inherited CLU_TEST_MODE must not false-trip the XDG guard inside
        # the hook process if the hook ever calls into clu state code.
        with (
            mock.patch.dict(os.environ, {"CLU_TEST_MODE": "1"}),
            mock.patch.object(sys, "stdin", io.StringIO("")),
            mock.patch.object(sys, "stdout", io.StringIO()),
        ):
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
            {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")},
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
    def test_install_no_flag_adds_session_start_and_not_the_inbox(self) -> None:
        # The dashboard surface is the default install now; the inbox
        # surface is retired and only `--inbox` wires it back up.
        rc, _, err = self._install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        hooks = self._hooks_block()
        self.assertIn("SessionStart", hooks)
        self.assertEqual(hooks.get("UserPromptSubmit", []), [])

    def test_install_with_flag_adds_session_start_entry(self) -> None:
        rc, _, err = self._install("--session-start")
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        hooks = self._hooks_block()
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
        m = must(monitor.load_marker())
        self.assertIn("session_start_hook_path", m)
        self.assertIn("clu_session_start.py", m["session_start_hook_path"])

    def test_install_no_flag_sets_the_session_start_marker_field(self) -> None:
        # `/clu-monitor` reports the install DATE back to the operator, and
        # settings.json cannot supply it — so the default install has to
        # record one even with the inbox surface retired.
        rc, _, _ = self._install()
        self.assertEqual(rc, int(ExitCode.OK))
        m = must(monitor.load_marker())
        self.assertIn("session_start_hook_path", m)
        self.assertIn("settings_json_path", m)

    def test_install_session_start_after_plain_install_adds_only_session_start(self) -> None:
        # Operator opts the retired inbox surface back in, then later
        # re-runs install. The UPS entry stays put; SessionStart is
        # already there from the first run and must not duplicate.
        self._install("--inbox")
        ups_before = self._hooks_block().get("UserPromptSubmit", [])
        self._install("--session-start")
        hooks_after = self._hooks_block()
        self.assertEqual(
            len(hooks_after["UserPromptSubmit"]), len(ups_before), "UPS entry should not duplicate"
        )
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
        self.settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {"hooks": [{"type": "command", "command": "/usr/bin/my-other-hook"}]}
                        ]
                    }
                }
            )
        )
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
        self._xdg_patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self._base)})
        self._xdg_patch.start()
        self.addCleanup(self._xdg_patch.stop)

    def _register_plan(self, slug: str, status: str = st.STATUS_RUNNING) -> None:
        """Register a plan in the CWD project and write a proper state file."""
        registry.register(self._project, slug)
        state_path = self._project / "plans" / ".orchestrator" / f"{slug}.state.json"
        data = st.empty_state(slug, "plans")
        data["status"] = status
        write_state(state_path, data)

    def _run_hook(self) -> tuple[int, str]:
        """Run the hook with os.getcwd() patched to the test project dir."""
        out = io.StringIO()
        with (
            mock.patch.object(os, "getcwd", return_value=str(self._project.resolve())),
            mock.patch.object(sys, "stdin", io.StringIO("")),
            mock.patch.object(sys, "stdout", out),
        ):
            rc = clu_session_start.main()
        payload = json.loads(out.getvalue())
        return rc, payload["hookSpecificOutput"]["additionalContext"]

    # ------------------------------------------------------------------

    def test_no_active_plans_omits_per_plan_block(self) -> None:
        # A live plan elsewhere on the host keeps the dashboard block, but
        # with nothing running in THIS cwd there must be no per-plan block.
        other = self._base / "elsewhere"
        other.mkdir()
        (other / "plans" / ".orchestrator").mkdir(parents=True)
        registry.register(other, "elsewhere-plan")
        state_path = other / "plans" / ".orchestrator" / "elsewhere-plan.state.json"
        write_state(state_path, st.empty_state("elsewhere-plan", "plans"))
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
        # paused/halted/done are all TERMINAL_STATUSES: nothing to watch,
        # so the hook now emits nothing at all (not even the dashboard).
        self._register_plan("paused-plan", status=st.STATUS_PAUSED)
        self._register_plan("halted-plan", status=st.STATUS_HALTED)
        self._register_plan("done-plan", status=st.STATUS_DONE)
        out = io.StringIO()
        with (
            mock.patch.object(os, "getcwd", return_value=str(self._project.resolve())),
            mock.patch.object(sys, "stdin", io.StringIO("")),
            mock.patch.object(sys, "stdout", out),
        ):
            rc = clu_session_start.main()
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "")

    def test_other_project_plans_excluded(self) -> None:
        other = self._base / "other-project"
        other.mkdir()
        (other / "plans" / ".orchestrator").mkdir(parents=True)
        registry.register(other, "other-slug")
        state_path = other / "plans" / ".orchestrator" / "other-slug.state.json"
        write_state(state_path, st.empty_state("other-slug", "plans"))
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertNotIn("--task-list", ctx)

    def test_corrupt_state_tolerated(self) -> None:
        registry.register(self._project, "corrupt-plan")
        state_path = self._project / "plans" / ".orchestrator" / "corrupt-plan.state.json"
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


# ---- open-blocker surfacing at session start ------------------------------


class SessionStartBlockerSurfaceTest(unittest.TestCase):
    """Hook renders this project's OPEN blockers (question + numbered options +
    a `--blocker` routing instruction) so the operator can answer in-session.

    The affordance the retired inbox surface used to provide, rebuilt as a
    state read at session start (no consume-once machinery).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._base = Path(self._tmp.name)
        self._project = self._base / "project"
        self._project.mkdir()
        (self._project / "plans" / ".orchestrator").mkdir(parents=True)
        self._xdg_patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self._base)})
        self._xdg_patch.start()
        self.addCleanup(self._xdg_patch.stop)

    def _blocker(
        self,
        bid: str,
        question: str,
        options: list[str],
        *,
        phase: str = "impl",
        answer: str | None = None,
    ) -> dict:
        return {
            "id": bid,
            "phase_id": phase,
            "type": "decision",
            "question": question,
            "options": list(options),
            "context": "",
            "asked_at": "2026-08-21T00:00:00Z",
            "answer": answer,
            "answered_at": None if answer is None else "2026-08-21T01:00:00Z",
        }

    def _write_plan(
        self,
        slug: str,
        *,
        status: str = st.STATUS_RUNNING,
        blockers: list[dict] | None = None,
        project: Path | None = None,
    ) -> None:
        project = project or self._project
        registry.register(project, slug)
        state_path = project / "plans" / ".orchestrator" / f"{slug}.state.json"
        data = st.empty_state(slug, "plans")
        data["status"] = status
        data["blockers"] = list(blockers or [])
        write_state(state_path, data)

    def _run_hook(self) -> tuple[int, str]:
        out = io.StringIO()
        with (
            mock.patch.object(os, "getcwd", return_value=str(self._project.resolve())),
            mock.patch.object(sys, "stdin", io.StringIO("")),
            mock.patch.object(sys, "stdout", out),
        ):
            rc = clu_session_start.main()
        raw = out.getvalue()
        ctx = json.loads(raw)["hookSpecificOutput"]["additionalContext"] if raw else ""
        return rc, ctx

    def test_open_blocker_is_surfaced_with_options(self) -> None:
        self._write_plan(
            "with-blocker",
            blockers=[self._blocker("q-1", "Bcrypt or argon2 for hashing?", ["bcrypt", "argon2"])],
        )
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("Bcrypt or argon2 for hashing?", ctx)
        self.assertIn("bcrypt", ctx)
        self.assertIn("argon2", ctx)
        self.assertIn("q-1", ctx)

    def test_paused_plan_blocker_is_still_surfaced(self) -> None:
        # The SLA case: an over-24h blocker PAUSES its plan (a terminal
        # status). The blocker that has waited longest is exactly the one that
        # most needs surfacing, so it must NOT sit behind the liveness gate.
        self._write_plan(
            "sla-paused",
            status=st.STATUS_PAUSED,
            blockers=[self._blocker("q-1", "Which migration order is safe?", ["a-then-b", "b-then-a"])],
        )
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("Which migration order is safe?", ctx)
        self.assertIn("a-then-b", ctx)

    def test_blockers_from_other_projects_are_not_surfaced(self) -> None:
        other = self._base / "other-project"
        other.mkdir()
        (other / "plans" / ".orchestrator").mkdir(parents=True)
        self._write_plan(
            "foreign",
            project=other,
            blockers=[self._blocker("q-1", "FOREIGN-QUESTION-marker", ["x", "y"])],
        )
        # Something in THIS cwd must emit, or output is empty by design.
        self._write_plan("local-live")
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertNotIn("FOREIGN-QUESTION-marker", ctx)

    def test_answered_blocker_is_not_surfaced(self) -> None:
        self._write_plan(
            "answered",
            blockers=[
                self._blocker("q-1", "ANSWERED-QUESTION-marker", ["x", "y"], answer="x"),
            ],
        )
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertNotIn("ANSWERED-QUESTION-marker", ctx)
        self.assertNotIn("Open blockers", ctx)

    def test_instruction_names_the_blocker_flag(self) -> None:
        self._write_plan(
            "flag",
            blockers=[self._blocker("q-1", "pick one", ["x", "y"])],
        )
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("--blocker", ctx)
        self.assertIn("clu answer", ctx)

    def test_many_blockers_are_capped_and_output_stays_under_9500(self) -> None:
        long_q = "Should we " + ("reticulate the splines " * 40)  # ~900 chars
        blockers = [self._blocker(f"q-{i}", f"{long_q} #{i}", ["yes", "no"]) for i in range(15)]
        self._write_plan("many", blockers=blockers)
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(ctx), 9500)
        # 15 open, MAX_BLOCKERS=10 shown → 5 not shown, and the section says so.
        self.assertIn("5 more open blocker", ctx)

    def test_no_blockers_emits_no_blocker_section(self) -> None:
        self._write_plan("clean")  # running, no blockers
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertIn("clu operator dashboard", ctx)  # dashboard still emits
        self.assertNotIn("Open blockers", ctx)

    def test_single_blocker_with_huge_options_stays_under_9500_and_keeps_instruction(self) -> None:
        # One blocker whose OPTIONS (not just question) are pathologically
        # long must not blow the ceiling — and the reply instruction, which is
        # appended AFTER the entries, must survive so the operator can still
        # answer. A host-side 10k truncation cutting the `--blocker` line is
        # the exact failure this guards against.
        huge = [("choice-" + "z" * 5000) for _ in range(6)]
        self._write_plan("huge", blockers=[self._blocker("q-1", "pick one", huge)])
        rc, ctx = self._run_hook()
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(ctx), 9500)
        self.assertIn("--blocker", ctx)
        self.assertIn("clu answer", ctx)


if __name__ == "__main__":
    unittest.main()
