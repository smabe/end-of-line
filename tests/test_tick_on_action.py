"""Tests for `_spawn_post_action_tick` — push dispatch on state-changing
worker callbacks and operator actions, replacing the cron-tick wait.

Each state-mutating command (`complete`, `block`, `task-done`,
`force-complete`, `queue add`) fires a detached `clu tick --project P`
as its last act before exiting. The supervisor sees the new state
immediately and dispatches the next phase without waiting for cron.

Coverage:
- Each of 5 spawn sites invokes the helper with the project's cfg.
- The helper itself invokes `subprocess.Popen` with the right argv
  and detachment kwargs.
- `cfg.tick_on_action = False` suppresses the spawn (operator
  escape hatch).

Tests mock the helper at callback sites (not `subprocess.Popen`) because
patching `Popen` breaks the `subprocess.run` calls in `_verify_commit_shas`
on the way through cmd_complete — same module-level `subprocess.Popen`
is the implementation of `subprocess.run`.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.cli import _spawn_post_action_tick, main
from end_of_line.config import ProjectConfig
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
| B | `test-plan-b.md` | thing | 1h |
"""

OTHER_PLAN_BODY = """\
# Other plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `other-plan-a.md` | thing | 1h |
"""


class _TickOnActionBase(unittest.TestCase):
    """Project + plan + claim setup so callback tests have a valid token."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self._write_config({})
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.email", "t@t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.name", "t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "--allow-empty", "-m", "init"],
            check=True, capture_output=True,
        )
        self.sha = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        rc = main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.assertEqual(rc, 0)
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_config(self, extra: dict) -> None:
        body = {"plan_dir": "plans"}
        body.update(extra)
        (self.project / ".orchestrator.json").write_text(json.dumps(body))


class SpawnInvocationTests(_TickOnActionBase):
    """Each callback invokes `_spawn_post_action_tick(cfg)` after writing
    state. We mock the helper at the call site so the test doesn't fire
    a real detached subprocess."""

    def _assert_helper_called_for_project(self, helper_mock) -> None:
        self.assertTrue(
            helper_mock.called,
            "_spawn_post_action_tick was not called",
        )
        call = helper_mock.call_args
        cfg_arg = call.args[0]
        self.assertEqual(
            cfg_arg.project_root.resolve(), self.project.resolve(),
        )

    def test_complete_invokes_spawn_helper(self) -> None:
        with mock.patch("end_of_line.cli._spawn_post_action_tick") as helper:
            rc = main([
                "complete", "--project", str(self.project), "--plan", "test-plan",
                "--phase", "a", "--token", self.token, "--commit", self.sha,
                "--skip-verify", "--skip-simplify",
            ])
        self.assertEqual(rc, 0)
        self._assert_helper_called_for_project(helper)

    def test_block_invokes_spawn_helper(self) -> None:
        with mock.patch("end_of_line.cli._spawn_post_action_tick") as helper, \
                mock.patch("end_of_line.cli.notify.notify"):
            rc = main([
                "block", "--project", str(self.project), "--plan", "test-plan",
                "--phase", "a", "--token", self.token,
                "--question", "should we?",
                "--type", st.BLOCKER_INPUT,
            ])
        self.assertEqual(rc, 0)
        self._assert_helper_called_for_project(helper)

    def test_force_complete_invokes_spawn_helper(self) -> None:
        with mock.patch("end_of_line.cli._spawn_post_action_tick") as helper:
            rc = main([
                "force-complete", "--project", str(self.project),
                "--plan", "test-plan", "--phase", "a",
                "--commit", self.sha, "--reason", "test",
            ])
        self.assertEqual(rc, 0)
        self._assert_helper_called_for_project(helper)

    def test_queue_add_invokes_spawn_helper(self) -> None:
        (self.project / "plans" / "other-plan.md").write_text(OTHER_PLAN_BODY)
        with mock.patch("end_of_line.cli._spawn_post_action_tick") as helper:
            rc = main([
                "queue", "add", "--project", str(self.project), "other-plan",
            ])
        self.assertEqual(rc, 0)
        self._assert_helper_called_for_project(helper)


class TaskDoneSpawnTests(_TickOnActionBase):
    """`task-done` (spawned-subtask completion callback) also invokes
    the helper. Lives in its own class because it needs a `spawn` to
    happen in setUp before task-done has something to complete."""

    def setUp(self) -> None:
        super().setUp()
        rc = main([
            "spawn", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "a", "--token", self.token,
            "--title", "side task",
        ])
        self.assertEqual(rc, 0)

    def test_task_done_invokes_spawn_helper(self) -> None:
        with mock.patch("end_of_line.cli._spawn_post_action_tick") as helper:
            rc = main([
                "task-done", "--project", str(self.project),
                "--plan", "test-plan", "task-1",
                "--token", self.token,
            ])
        self.assertEqual(rc, 0)
        self.assertTrue(helper.called)
        cfg_arg = helper.call_args.args[0]
        self.assertEqual(
            cfg_arg.project_root.resolve(), self.project.resolve(),
        )


class SpawnHelperTests(unittest.TestCase):
    """The helper itself: argv shape, detachment kwargs, opt-out
    short-circuit, swallowed OSError."""

    def test_helper_invokes_popen_with_project_scoped_argv(self) -> None:
        cfg = ProjectConfig(project_root=Path("/tmp/example-proj"))
        with mock.patch("end_of_line.cli.subprocess.Popen") as popen:
            _spawn_post_action_tick(cfg)
        self.assertEqual(len(popen.call_args_list), 1)
        argv = popen.call_args.args[0]
        self.assertIn("tick", argv)
        self.assertIn("--project", argv)
        self.assertIn(str(Path("/tmp/example-proj").resolve()), argv)
        self.assertNotIn("--plan", argv)

    def test_helper_uses_detach_kwargs(self) -> None:
        cfg = ProjectConfig(project_root=Path("/tmp/example-proj"))
        with mock.patch("end_of_line.cli.subprocess.Popen") as popen:
            _spawn_post_action_tick(cfg)
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)

    def test_helper_noop_when_tick_on_action_false(self) -> None:
        cfg = ProjectConfig(
            project_root=Path("/tmp/example-proj"), tick_on_action=False,
        )
        with mock.patch("end_of_line.cli.subprocess.Popen") as popen:
            _spawn_post_action_tick(cfg)
        self.assertFalse(popen.called)

    def test_helper_swallows_oserror(self) -> None:
        """Spawn failure must not break the caller — state is on disk;
        cron will catch up. We assert no exception propagates."""
        cfg = ProjectConfig(project_root=Path("/tmp/example-proj"))
        with mock.patch(
            "end_of_line.cli.subprocess.Popen",
            side_effect=OSError("simulated fork failure"),
        ):
            _spawn_post_action_tick(cfg)  # must not raise


class SpawnSuppressionEndToEndTests(_TickOnActionBase):
    """End-to-end: with `tick_on_action: false` in the project config,
    cmd_complete still succeeds but the helper short-circuits the spawn.

    We don't mock the helper here — we mock Popen and confirm no
    tick spawn appears. (Other subprocess.run calls inside cmd_complete
    don't go through Popen-as-attribute when patched this way for
    cmd_queue_add, which has no git verification path.)"""

    def setUp(self) -> None:
        super().setUp()
        self._write_config({"tick_on_action": False})

    def test_queue_add_skips_popen_when_disabled(self) -> None:
        (self.project / "plans" / "other-plan.md").write_text(OTHER_PLAN_BODY)
        with mock.patch("end_of_line.cli.subprocess.Popen") as popen:
            rc = main([
                "queue", "add", "--project", str(self.project), "other-plan",
            ])
        self.assertEqual(rc, 0)
        tick_calls = [
            c for c in popen.call_args_list
            if len(c.args) >= 1 and isinstance(c.args[0], list)
            and "tick" in c.args[0]
        ]
        self.assertEqual(len(tick_calls), 0)


if __name__ == "__main__":
    unittest.main()
