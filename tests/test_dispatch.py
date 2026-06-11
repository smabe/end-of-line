"""Tests for dispatch failure visibility (fix 7)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import main
from end_of_line.config import DispatchSpec, ProjectConfig
from end_of_line.dispatch import dispatch_for_tick, dispatch_repair_worker, resolved_model
from end_of_line.supervisor import TickResult
from tests import CluTestCase

PLAN = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `t-a.md` | thing | 1h |
"""


class DispatchTestCase(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        (self.project / "plans").mkdir()
        (self.project / "plans" / "t.md").write_text(PLAN)
        main(["init", "--project", str(self.project), "--plan", "t"])
        self.state_path = self.project / "plans" / ".orchestrator" / "t.state.json"
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def _cfg(self, cmd: str, path: str = "") -> ProjectConfig:
        return ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command=cmd, path=path),
        )

    def _result(self, *, worktree: dict | None = None) -> TickResult:
        return TickResult(
            action="dispatch",
            detail="",
            phase_id="a",
            token=self.token,
            worktree=worktree,
        )

    def test_missing_command_releases_claim(self) -> None:
        cfg = self._cfg("")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertFalse(ok)
        data = json.loads(self.state_path.read_text())
        self.assertIsNone(data["current_claim"])
        types = [e["type"] for e in data["events"]]
        self.assertIn("dispatch_failed", types)

    def test_popen_filenotfounderror_releases_claim_without_raising(self) -> None:
        """Non-worktree Popen FileNotFoundError → dispatch_failed, not crash.

        Pre-clu-worktrees the bare `raise` here propagated up and crashed
        the whole `cmd_tick_all` loop. The funnel-through-_release_with_
        failure path is the same shape as a fast-fail rc!=0.
        """
        from unittest import mock

        cfg = self._cfg("true")
        with mock.patch(
            "end_of_line.dispatch.subprocess.Popen",
            side_effect=FileNotFoundError(2, "no such file"),
        ):
            ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertFalse(ok)
        data = json.loads(self.state_path.read_text())
        self.assertIsNone(data["current_claim"])
        events = [e for e in data["events"] if e["type"] == "dispatch_failed"]
        self.assertEqual(len(events), 1)
        self.assertIn("FileNotFoundError", events[0]["reason"])

    def test_fast_fail_releases_claim(self) -> None:
        # Plain non-zero exit that doesn't match a systemic signature
        # (those route through the pause branch — see test_systemic_failure).
        cfg = self._cfg("exit 42")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertFalse(ok)
        data = json.loads(self.state_path.read_text())
        self.assertIsNone(data["current_claim"])
        events = [e for e in data["events"] if e["type"] == "dispatch_failed"]
        self.assertEqual(len(events), 1)
        self.assertIn("rc=", events[0]["reason"])

    def _capture_via_sentinel(
        self,
        *,
        payload: str,
        sentinel_name: str,
        worktree: dict | None = None,
        path: str = "",
    ) -> str:
        """Spawn a worker that writes a shell payload's output to a sentinel.

        `payload` is a `sh -c` fragment with `{s}` substituted for the absolute
        sentinel path; e.g. `'pwd > {s}'` or `'printf "%s" "$PATH" > {s}'`.
        Polled-wait covers the fast-fail-vs-long-running ambiguity in
        `dispatch_for_tick`: the sentinel write is the observable, not the
        worker's exit timing.
        """
        sentinel = self.project / sentinel_name
        cfg = self._cfg(
            f"sh -c '{payload.format(s=sentinel)}'",
            path=path,
        )
        dispatch_for_tick(
            self._result(worktree=worktree),
            cfg,
            "t",
            self.state_path,
        )
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if sentinel.exists():
                break
            time.sleep(0.05)
        self.assertTrue(
            sentinel.exists(),
            f"sentinel {sentinel_name} never written",
        )
        return sentinel.read_text()

    def _capture_env_value(self, var: str, path: str = "") -> str:
        # printf "%s" writes no trailing newline → no .strip() needed.
        return self._capture_via_sentinel(
            payload=f'printf "%s" "${var}" > {{s}}',
            sentinel_name=f"{var}.captured",
            path=path,
        )

    def test_dispatch_no_path_omits_env(self) -> None:
        """Empty dispatch.path => worker inherits parent PATH unchanged."""
        captured = self._capture_env_value("PATH", path="")
        self.assertEqual(captured, os.environ["PATH"])

    def test_dispatch_with_path_overrides_env(self) -> None:
        """Non-empty dispatch.path => worker's $PATH is exactly that value.

        This is the Diagnosis falsifiable test from the master plan.
        """
        captured = self._capture_env_value("PATH", path="/usr/bin:/bin")
        self.assertEqual(captured, "/usr/bin:/bin")

    def test_dispatch_env_carries_claim_identity(self) -> None:
        """Worker env gets CLU_PLAN/PHASE/TOKEN/PROJECT injected at Popen (#91).

        Hooks inside the worker (the activity hook) inherit the worker
        process env — this is what keeps `tool_stuck` coverage alive for
        headless `--print` workers, where worker-side `export` never
        persists across Bash tool calls.
        """
        captured = self._capture_via_sentinel(
            payload=(
                'printf "%s|%s|%s|%s" "$CLU_PLAN" "$CLU_PHASE"'
                ' "$CLU_TOKEN" "$CLU_PROJECT" > {s}'
            ),
            sentinel_name="clu_env.captured",
        )
        self.assertEqual(captured, f"t|a|{self.token}|{self.project}")

    def test_dispatch_with_path_preserves_home(self) -> None:
        """Custom PATH must MERGE with os.environ, not replace it.

        If the implementation did `env={"PATH": ...}` alone, `$HOME` would
        be empty in the child. We assert it survives.
        """
        expected_home = os.environ.get("HOME", "")
        # The test only proves merge-vs-replace when HOME is actually set.
        self.assertTrue(expected_home, "test prerequisite: HOME must be set")
        captured = self._capture_env_value("HOME", path="/usr/bin:/bin")
        self.assertEqual(captured, expected_home)

    def _capture_cwd(self, *, worktree: dict | None) -> str:
        # `pwd` ends in a newline; strip so callers can compare paths directly.
        return self._capture_via_sentinel(
            payload="pwd > {s}",
            sentinel_name="cwd.captured",
            worktree=worktree,
        ).strip()

    def test_dispatch_cwd_is_project_root_without_worktree(self) -> None:
        cwd = self._capture_cwd(worktree=None)
        self.assertEqual(Path(cwd).resolve(), self.project.resolve())

    def test_dispatch_cwd_is_worktree_path_when_set(self) -> None:
        # Phase 4 added a `worktree_alive` gate (stat + `git rev-parse
        # --git-dir`), so the test fixture has to be a real git dir.
        # `git init` is sufficient — rev-parse doesn't care whether it's
        # a primary repo or a worktree.
        wt = Path(tempfile.mkdtemp(prefix="wt-sibling-"))
        subprocess.run(
            ["git", "-C", str(wt), "init", "-q"],
            check=True,
        )
        try:
            cwd = self._capture_cwd(
                worktree={
                    "path": str(wt),
                    "branch": "clu/t",
                    "base_ref": "0" * 40,
                }
            )
            self.assertEqual(Path(cwd).resolve(), wt.resolve())
        finally:
            shutil.rmtree(wt)

    def test_worktree_missing_path_pauses_plan(self) -> None:
        """Worktree dir gone at dispatch → status=PAUSED, event recorded.

        The previous claim is released without burning a phase attempt so
        `clu resume` after the operator fixes the dir picks up cleanly.
        """
        missing = self.project.parent / "this-was-removed"
        # Intentionally don't mkdir; the path doesn't exist on disk.
        cfg = self._cfg("echo should-not-spawn")
        result = self._result(
            worktree={
                "path": str(missing),
                "branch": "clu/t",
                "base_ref": "0" * 40,
            }
        )
        ok = dispatch_for_tick(result, cfg, "t", self.state_path)
        self.assertFalse(ok)
        data = json.loads(self.state_path.read_text())
        self.assertIsNone(data["current_claim"])
        self.assertEqual(data["status"], "paused")
        evts = [e for e in data["events"] if e["type"] == "worktree_missing"]
        self.assertEqual(len(evts), 1)
        self.assertEqual(evts[0]["worktree_path"], str(missing))

    def test_worktree_exists_but_not_git_pauses_plan(self) -> None:
        """Path exists but `git -C path rev-parse --git-dir` fails → pause.

        Catches the `git worktree prune` failure mode where the dir
        remains but git has detached its admin metadata.
        """
        wt = Path(tempfile.mkdtemp(prefix="wt-not-git-"))
        try:
            cfg = self._cfg("echo should-not-spawn")
            result = self._result(
                worktree={
                    "path": str(wt),
                    "branch": "clu/t",
                    "base_ref": "0" * 40,
                }
            )
            ok = dispatch_for_tick(result, cfg, "t", self.state_path)
            self.assertFalse(ok)
            data = json.loads(self.state_path.read_text())
            self.assertEqual(data["status"], "paused")
            self.assertIn(
                "worktree_missing",
                {e["type"] for e in data["events"]},
            )
        finally:
            wt.rmdir()

    def test_main_repo_dispatch_unaffected_by_worktree_check(self) -> None:
        """Plans without a worktree never hit the alive-check codepath."""
        cfg = self._cfg("sleep 3")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertTrue(ok)
        data = json.loads(self.state_path.read_text())
        # No worktree_missing event should appear.
        self.assertNotIn(
            "worktree_missing",
            {e["type"] for e in data["events"]},
        )

    def test_long_running_worker_stamps_pid(self) -> None:
        # Sleep longer than fast-fail window so we treat it as "running"
        cfg = self._cfg("sleep 3")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertTrue(ok)
        data = json.loads(self.state_path.read_text())
        claim = data["current_claim"]
        self.assertIsNotNone(claim)
        self.assertIn("pid", claim)
        self.assertIn("log_path", claim)
        # The worker is spawned start_new_session=True, so it leads its own
        # process group (pgid == pid). pgid is recorded so reapers can killpg
        # the whole group (worker + heartbeat loop) — #75.
        self.assertIn("pgid", claim)
        self.assertEqual(claim["pgid"], claim["pid"])

    def test_session_id_placeholder_substituted_and_stamped(self) -> None:
        # When the command opts in to {session_id}, dispatch generates one uuid,
        # substitutes it into the command, AND stamps the same value on the
        # claim — so `clu top` can find the transcript deterministically.
        sentinel = self.project / "sid.captured"
        cfg = self._cfg(f"sh -c 'printf \"%s\" {{session_id}} > {sentinel}'")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertTrue(ok)
        claim = json.loads(self.state_path.read_text())["current_claim"]
        sid = claim["session_id"]
        uuid.UUID(sid)  # raises if not a valid uuid
        deadline = time.time() + 5.0
        while time.time() < deadline and not sentinel.exists():
            time.sleep(0.05)
        self.assertTrue(sentinel.exists(), "session_id sentinel never written")
        self.assertEqual(sentinel.read_text(), sid)

    def test_no_session_id_placeholder_leaves_claim_unstamped(self) -> None:
        # Commands that don't opt in must not get a session_id — Claude Code
        # generates its own, so a stamp would be a lie (top falls back to
        # cwd-matching instead).
        cfg = self._cfg("sleep 3")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertTrue(ok)
        claim = json.loads(self.state_path.read_text())["current_claim"]
        self.assertIsNone(claim.get("session_id"))

    def test_escaped_session_id_braces_do_not_stamp(self) -> None:
        # `{{session_id}}` is a literal, not a format field — must NOT generate
        # or stamp a uuid (it would never reach the worker, and a phantom stamp
        # suppresses clu top's cwd fallback).
        cfg = self._cfg("sh -c 'echo {{session_id}} && sleep 3'")
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertTrue(ok)
        claim = json.loads(self.state_path.read_text())["current_claim"]
        self.assertIsNone(claim.get("session_id"))

    def test_healthy_spawn_emits_coolant_start(self) -> None:
        """A worker that survives the fast-fail window emits agent-start."""
        from unittest import mock

        cfg = self._cfg("sleep 3")
        with mock.patch("end_of_line.dispatch.coolant.emit_start") as emit:
            ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertTrue(ok)
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        self.assertEqual(kwargs["session_id"], self.token)
        self.assertEqual(kwargs["agent_id"], "clu-t-a")
        self.assertEqual(kwargs["agent_type"], "clu-worker")

    def test_fast_fail_does_not_emit_coolant_start(self) -> None:
        """Workers that exit non-zero within the fast-fail window never had
        their CPU footprint matter to coolant — don't push a phantom +1 onto
        the counter that the failure-release path can't roll back."""
        from unittest import mock

        cfg = self._cfg("exit 42")
        with mock.patch("end_of_line.dispatch.coolant.emit_start") as emit:
            ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertFalse(ok)
        emit.assert_not_called()

    def test_popen_failure_does_not_emit_coolant_start(self) -> None:
        from unittest import mock

        cfg = self._cfg("true")
        with mock.patch(
            "end_of_line.dispatch.subprocess.Popen",
            side_effect=FileNotFoundError(2, "no such file"),
        ):
            with mock.patch("end_of_line.dispatch.coolant.emit_start") as emit:
                ok = dispatch_for_tick(
                    self._result(),
                    cfg,
                    "t",
                    self.state_path,
                )
        self.assertFalse(ok)
        emit.assert_not_called()

    def test_phase_dispatch_wraps_command_through_pty_shim(self) -> None:
        """Phase workers spawn as `python <shim_path> -- <cmd>`.

        The outer Popen drops `shell=True` (list argv) and passes the rendered
        operator command STRING as one final element — the shim runs it through
        `sh -c` itself, so template/quoting semantics are preserved and the
        plan-slug cmdline marker still appears in the shim's argv. The shim is
        invoked by absolute file path (not `-m`) so it works from the worker's
        worktree cwd, where the package isn't importable.
        """
        from unittest import mock

        from end_of_line.dispatch import _PTY_SHIM_PATH

        cfg = self._cfg("claude --print '/clu-phase {plan_slug}'")
        fake = mock.MagicMock()
        fake.pid = 4321
        # Survive the fast-fail window -> treated as the healthy running case.
        fake.wait.side_effect = subprocess.TimeoutExpired(cmd="shim", timeout=0.5)
        # Patching subprocess.Popen is module-global; coolant.emit_start shells
        # out via subprocess.run, so stub it to keep this focused on the argv.
        with mock.patch(
            "end_of_line.dispatch.subprocess.Popen", return_value=fake
        ) as popen, mock.patch("end_of_line.dispatch.coolant.emit_start"):
            ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertTrue(ok)
        args, kwargs = popen.call_args
        argv = args[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1], _PTY_SHIM_PATH)
        self.assertTrue(argv[1].endswith("_pty_spawn_shim.py"))
        self.assertEqual(argv[2], "--")
        # The rendered command (slug shlex-quoted by render_command) is a
        # SINGLE argv element, not split.
        self.assertEqual(argv[3], "claude --print '/clu-phase t'")
        self.assertEqual(len(argv), 4)
        self.assertNotEqual(kwargs.get("shell"), True)

    def test_repair_dispatch_not_wrapped_through_shim(self) -> None:
        """Repair workers stay on the direct `shell=True` Popen path.

        Regression pin: repair is synchronous + short-lived, carries no
        claim/pid, and is not wedge-prone — it must NOT route through the shim.
        """
        from unittest import mock

        cmd = "repair-cmd {corrupt_path}"
        cfg = ProjectConfig(
            project_root=self.project,
            dispatch=DispatchSpec(kind="shell", command="ignored", repair_command=cmd),
        )
        fake = mock.MagicMock()
        fake.wait.return_value = 0
        with mock.patch(
            "end_of_line.dispatch.subprocess.Popen", return_value=fake
        ) as popen:
            dispatch_repair_worker(
                cfg,
                self.project / "corrupt.json",
                self.project / "backup.json",
                "diag",
                self.project / "repair.log",
            )
        args, kwargs = popen.call_args
        self.assertEqual(kwargs.get("shell"), True)
        self.assertIsInstance(args[0], str)
        self.assertIn("repair-cmd", args[0])
        self.assertNotIn("_pty_spawn_shim", args[0])


class ResolvedModelTestCase(unittest.TestCase):
    """Unit tests for `dispatch.resolved_model` — pure parser, no I/O."""

    def test_pinned_with_space(self) -> None:
        cmd = "claude --print --model claude-opus-4-7 '/clu-phase {plan_slug}'"
        self.assertEqual(resolved_model(cmd), "claude-opus-4-7")

    def test_pinned_with_equals(self) -> None:
        cmd = "claude --print --model=claude-opus-4-7 '/clu-phase'"
        self.assertEqual(resolved_model(cmd), "claude-opus-4-7")

    def test_pinned_with_quoted_value(self) -> None:
        cmd = 'claude --print --model "claude-opus-4-7" /clu-phase'
        self.assertEqual(resolved_model(cmd), "claude-opus-4-7")

    def test_no_model_flag(self) -> None:
        cmd = "claude --print '/clu-phase {plan_slug}'"
        self.assertIsNone(resolved_model(cmd))

    def test_dangling_model_flag(self) -> None:
        # `--model` at end of args with no value → treat as absent rather
        # than reach off the end of the token list.
        self.assertIsNone(resolved_model("claude --print --model"))

    def test_unbalanced_quotes(self) -> None:
        # shlex.split raises on unterminated quote — treat as absent
        # rather than crash the CLI on init/queue-add.
        self.assertIsNone(resolved_model("claude --print 'oops"))


class RepairWorkerEnvTestCase(CluTestCase):
    """Repair workers carry no claim — no CLU_* identity injection (#91)."""

    def test_repair_worker_env_has_no_claim_identity(self) -> None:
        from unittest import mock

        sentinel = self.tmp_path / "repair_env.captured"
        # Doubled braces survive the repair-template .format() as literals,
        # so the worker shell sees ${CLU_TOKEN-unset}.
        cmd = 'sh -c \'printf "%s" "${{CLU_TOKEN-unset}}" > ' + str(sentinel) + "'"
        cfg = ProjectConfig(
            project_root=self.tmp_path,
            dispatch=DispatchSpec(kind="shell", command="ignored", repair_command=cmd),
        )
        # Deterministic regardless of how THIS test process was launched:
        # a clu-dispatched worker running the suite has CLU_TOKEN set, and
        # env=None inheritance would leak it into the child.
        with mock.patch.dict(os.environ):
            for key in ("CLU_PLAN", "CLU_PHASE", "CLU_TOKEN", "CLU_PROJECT"):
                os.environ.pop(key, None)
            rc = dispatch_repair_worker(
                cfg,
                self.tmp_path / "corrupt.json",
                self.tmp_path / "backup.json",
                "diag",
                self.tmp_path / "repair.log",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(sentinel.read_text(), "unset")


if __name__ == "__main__":
    unittest.main()
