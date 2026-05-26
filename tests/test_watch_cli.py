"""Tests for `clu watch` CLI subcommand — arg resolution and dispatch."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line.cli import ExitCode, main
from tests import CluTestCase

_PLAN_BODY = """\
# placeholder
## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `a.md` | thing | 1h |
"""


def _init_plan(project: Path, slug: str) -> None:
    plans = project / "plans"
    plans.mkdir(exist_ok=True)
    (plans / f"{slug}.md").write_text(_PLAN_BODY)
    rc = main(["init", "--project", str(project), "--plan", slug])
    assert rc == 0, f"init failed with rc={rc}"


def _mock_loop():
    return mock.patch("end_of_line.watch.stream_loop", return_value=0)


class WatchArgparseTest(CluTestCase):
    """Parser shape and early error paths."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.project.mkdir()

    def test_argparse_plan_and_all_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            main(["watch", "--plan", "x", "--all", "--project", str(self.project)])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_argparse_plan_requires_project(self) -> None:
        # --plan without --project: CWD project has no such plan → UNKNOWN_TASK
        out, err = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(out),
            redirect_stderr(err),
            mock.patch.object(Path, "cwd", return_value=self.project),
        ):
            rc = main(["watch", "--plan", "x"])
        self.assertEqual(rc, int(ExitCode.UNKNOWN_TASK))
        self.assertIn("x", err.getvalue())

    def test_unknown_plan_exits_unknown_task(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["watch", "--plan", "nonexistent", "--project", str(self.project)])
        self.assertEqual(rc, int(ExitCode.UNKNOWN_TASK))
        self.assertIn("nonexistent", err.getvalue())


class WatchResolutionTest(CluTestCase):
    """State-path resolution — verified via mocked stream_loop."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.project.mkdir()

    def test_cwd_default_resolution(self) -> None:
        _init_plan(self.project, "alpha")
        _init_plan(self.project, "beta")
        with _mock_loop() as m, mock.patch.object(Path, "cwd", return_value=self.project):
            rc = main(["watch"])
        self.assertEqual(rc, 0)
        state_paths = m.call_args.args[0]
        slugs = {p.stem.removesuffix(".state") for p in state_paths}
        self.assertEqual(slugs, {"alpha", "beta"})

    def test_all_mode_enumerates_registry(self) -> None:
        proj2 = self.tmp_path / "project2"
        proj2.mkdir()
        _init_plan(self.project, "plan-a")
        _init_plan(self.project, "plan-b")
        _init_plan(proj2, "plan-c")
        with _mock_loop() as m:
            rc = main(["watch", "--all"])
        self.assertEqual(rc, 0)
        state_paths = m.call_args.args[0]
        self.assertEqual(len(state_paths), 3)

    def test_json_flag_propagates(self) -> None:
        _init_plan(self.project, "my-plan")
        with _mock_loop() as m:
            rc = main(["watch", "--plan", "my-plan", "--project", str(self.project), "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(m.call_args.kwargs["json_mode"])

    def test_verbose_flag_propagates(self) -> None:
        _init_plan(self.project, "my-plan")
        with _mock_loop() as m:
            rc = main(["watch", "--plan", "my-plan", "--project", str(self.project), "--verbose"])
        self.assertEqual(rc, 0)
        self.assertTrue(m.call_args.kwargs["verbose"])

    def test_interval_flag_parsed(self) -> None:
        _init_plan(self.project, "my-plan")
        with _mock_loop() as m:
            rc = main(
                ["watch", "--plan", "my-plan", "--project", str(self.project), "--interval", "0.5"]
            )
        self.assertEqual(rc, 0)
        self.assertAlmostEqual(m.call_args.kwargs["poll_interval"], 0.5)
