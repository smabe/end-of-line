"""Tests for `clu tick` CLI wiring — default is now dispatch.

Pre-flip, `clu tick` defaulted to no-dispatch; an opt-in `--dispatch`
flag spawned the worker. That produced a phantom-claim footgun for
manual ticks (a 30-min lease block on cron). The flip inverts the
default: `clu tick` dispatches; `--dry-tick` is the explicit opt-out.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from end_of_line.cli import ExitCode, main
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
"""


class TickDefaultDispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        (self.project / ".orchestrator.json").write_text(
            json.dumps(
                {
                    "plan_dir": "plans",
                    "dispatch": {"kind": "shell", "command": "true"},
                }
            )
        )
        rc = main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _argv(self, *extra: str) -> list[str]:
        return ["tick", "--project", str(self.project), "--plan", "test-plan", *extra]

    def test_tick_default_dispatches(self) -> None:
        with (
            mock.patch("end_of_line.dispatch.dispatch_for_tick") as mocked,
            redirect_stdout(StringIO()),
        ):
            rc = main(self._argv())
        self.assertTrue(mocked.called)
        self.assertEqual(rc, ExitCode.OK)

    def test_tick_dry_tick_skips_dispatch(self) -> None:
        with (
            mock.patch("end_of_line.dispatch.dispatch_for_tick") as mocked,
            redirect_stdout(StringIO()),
        ):
            rc = main(self._argv("--dry-tick"))
        self.assertFalse(mocked.called)
        self.assertEqual(rc, ExitCode.OK)

    def test_tick_old_dispatch_flag_rejected(self) -> None:
        # --dispatch is gone — argparse rejects with SystemExit(2).
        with self.assertRaises(SystemExit):
            main(self._argv("--dispatch"))


PLAN_BODY_B = """\
# Test plan B

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-b-a.md` | thing | 1h |
"""


class TickProjectScopedTestCase(unittest.TestCase):
    """`clu tick --project P` (without --plan) ticks every plan in P and
    runs the cross-plan rule chain. Mirrors the post-loop logic that
    `cmd_tick_all` runs host-wide but scoped to one project."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir(parents=True)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "plan-a.md").write_text(PLAN_BODY)
        (self.project / "plans" / "plan-b.md").write_text(PLAN_BODY_B)
        (self.project / ".orchestrator.json").write_text(
            json.dumps(
                {
                    "plan_dir": "plans",
                    "dispatch": {"kind": "shell", "command": "true"},
                }
            )
        )
        rc = main(["init", "--project", str(self.project), "--plan", "plan-a"])
        self.assertEqual(rc, 0)
        rc = main(["init", "--project", str(self.project), "--plan", "plan-b"])
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_project_tick_ticks_every_registered_plan(self) -> None:
        with mock.patch("end_of_line.cli._tick_one_plan") as mocked, redirect_stdout(StringIO()):
            rc = main(["tick", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = sorted(call.args[0] for call in mocked.call_args_list)
        self.assertEqual(slugs, ["plan-a", "plan-b"])

    def test_project_tick_runs_cross_plan_chain(self) -> None:
        with (
            mock.patch("end_of_line.cross_plan_rules.run_rules") as mocked,
            mock.patch("end_of_line.cli._tick_one_plan"),
            redirect_stdout(StringIO()),
        ):
            rc = main(["tick", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertTrue(mocked.called)

    def test_project_tick_skips_plans_in_other_projects(self) -> None:
        other = Path(self._tmp.name) / "other"
        other.mkdir(parents=True)
        (other / "plans").mkdir()
        (other / "plans" / "plan-c.md").write_text(PLAN_BODY)
        (other / ".orchestrator.json").write_text(
            json.dumps(
                {
                    "plan_dir": "plans",
                    "dispatch": {"kind": "shell", "command": "true"},
                }
            )
        )
        rc = main(["init", "--project", str(other), "--plan", "plan-c"])
        self.assertEqual(rc, 0)
        with mock.patch("end_of_line.cli._tick_one_plan") as mocked, redirect_stdout(StringIO()):
            rc = main(["tick", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = sorted(call.args[0] for call in mocked.call_args_list)
        self.assertEqual(slugs, ["plan-a", "plan-b"])

    def test_plan_scoped_still_works(self) -> None:
        with mock.patch("end_of_line.cli._tick_one_plan") as mocked, redirect_stdout(StringIO()):
            rc = main(
                [
                    "tick",
                    "--project",
                    str(self.project),
                    "--plan",
                    "plan-a",
                ]
            )
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(len(mocked.call_args_list), 1)
        self.assertEqual(mocked.call_args_list[0].args[0], "plan-a")


if __name__ == "__main__":
    unittest.main()
