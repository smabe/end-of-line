"""Tests for `clu watch --task-list` — flag, mutex validation, and pass-through."""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line.cli import ExitCode, main
from tests import CluTestCase


_PLAN_BODY = """\
# test plan
## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| alpha | `alpha.md` | impl | 1h |
| beta | `beta.md` | tests | 30m |
"""


def _init_plan(project: Path, slug: str) -> None:
    plans = project / "plans"
    plans.mkdir(exist_ok=True)
    (plans / f"{slug}.md").write_text(_PLAN_BODY)
    rc = main(["init", "--project", str(project), "--plan", slug])
    assert rc == 0, f"init failed with rc={rc}"


def _mock_loop():
    return mock.patch("end_of_line.watch.stream_loop", return_value=0)


class TaskListMutexTest(CluTestCase):
    """--task-list mutual exclusion validation."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.project.mkdir()

    def test_task_list_and_json_mutually_exclusive(self) -> None:
        _init_plan(self.project, "foo")
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main([
                "watch", "--task-list", "--json",
                "--project", str(self.project), "--plan", "foo",
            ])
        self.assertEqual(rc, int(ExitCode.GENERIC))
        self.assertIn("mutually exclusive", err.getvalue())

    def test_task_list_with_all_accepted(self) -> None:
        _init_plan(self.project, "foo")
        with _mock_loop():
            rc = main([
                "watch", "--task-list", "--all",
                "--project", str(self.project),
            ])
        self.assertNotEqual(rc, int(ExitCode.GENERIC))


class TaskListPassThroughTest(CluTestCase):
    """--task-list passes through to stream_loop."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.project.mkdir()

    def test_task_list_alone_passes_validation(self) -> None:
        _init_plan(self.project, "myplan")
        with _mock_loop() as m:
            rc = main([
                "watch", "--task-list",
                "--project", str(self.project), "--plan", "myplan",
            ])
        self.assertEqual(rc, 0)
        self.assertTrue(m.call_args.kwargs.get("task_list_mode"))

    def test_task_list_missing_master_skips_silently(self) -> None:
        """State exists but master .md is absent → bootstrap skips; watch succeeds."""
        _init_plan(self.project, "no-master")
        (self.project / "plans" / "no-master.md").unlink()
        out = io.StringIO()
        with _mock_loop(), redirect_stdout(out):
            rc = main([
                "watch", "--task-list",
                "--project", str(self.project), "--plan", "no-master",
            ])
        self.assertEqual(rc, 0)
        self.assertNotIn("TASK_CREATE", out.getvalue())


class TaskListHelpTextTest(unittest.TestCase):
    """Help text mentions the protocol."""

    def test_help_text_mentions_task_list_protocol(self) -> None:
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                main(["watch", "--help"])
        except SystemExit:
            pass
        self.assertIn("task-list", out.getvalue())
