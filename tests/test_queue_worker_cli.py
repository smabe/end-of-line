"""Phase `cli` tests: worker-mode argparse flags + mutual-exclusion gate.

Covers the four new flags (--token, --plan, --phase, --reason) on
`clu queue add` and the runtime checks that guard worker-only combos.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

from end_of_line import queue, registry
from end_of_line.cli import ExitCode, main
from end_of_line.config import ProjectConfig
from tests import isolate_queue

_PLAN_BODY = "# placeholder plan\n"


def _bootstrap(project: Path, slug: str = "seed-plan") -> None:
    (project / "plans").mkdir(exist_ok=True)
    (project / "plans" / f"{slug}.md").write_text(_PLAN_BODY)
    registry.register(project, slug)


def _write_plan(project: Path, slug: str) -> Path:
    plans_dir = project / "plans"
    plans_dir.mkdir(exist_ok=True)
    path = plans_dir / f"{slug}.md"
    path.write_text(_PLAN_BODY)
    return path


def _stderr(args: list[str]) -> tuple[int, str]:
    """Run main(args), capture stderr, return (exit_code, stderr_text)."""
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        rc = main(args)
    finally:
        sys.stderr = old
    return rc, buf.getvalue()


class WorkerModeGateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        isolate_queue(self, self.project)
        _bootstrap(self.project)

    def test_token_alone_rejected_missing_plan_phase(self) -> None:
        """--token without --plan and --phase must exit GENERIC."""
        _write_plan(self.project, "foo")
        rc, err = _stderr(
            [
                "queue",
                "add",
                "foo",
                "--token",
                "T",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("--plan", err)
        self.assertIn("--phase", err)

    def test_token_with_front_rejected(self) -> None:
        """--front is operator-only; combine with --token must exit GENERIC."""
        _write_plan(self.project, "foo")
        rc, err = _stderr(
            [
                "queue",
                "add",
                "foo",
                "--token",
                "T",
                "--plan",
                "X",
                "--phase",
                "Y",
                "--front",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("--front", err)

    def test_token_with_multi_slug_rejected(self) -> None:
        """Worker mode accepts exactly one slug; two must exit GENERIC."""
        _write_plan(self.project, "foo")
        _write_plan(self.project, "bar")
        rc, err = _stderr(
            [
                "queue",
                "add",
                "foo",
                "bar",
                "--token",
                "T",
                "--plan",
                "X",
                "--phase",
                "Y",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("single slug", err)

    def test_token_combo_passes_parse_layer(self) -> None:
        """Valid worker-mode args reach dispatch (fails on missing source state)."""
        _write_plan(self.project, "foo")
        rc, err = _stderr(
            [
                "queue",
                "add",
                "foo",
                "--token",
                "T",
                "--plan",
                "source-plan",
                "--phase",
                "phase-a",
                "--project",
                str(self.project),
            ]
        )
        # No state.json for source-plan → UNKNOWN_TASK, proving we're past argparse.
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)

    def test_operator_mode_unchanged(self) -> None:
        """Operator add without --token still returns OK (regression guard)."""
        _write_plan(self.project, "new-plan")
        rc = main(
            [
                "queue",
                "add",
                "new-plan",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.OK)

    def test_reason_accepted_in_operator_mode(self) -> None:
        """--reason sets the reason field on the queue entry."""
        _write_plan(self.project, "my-plan")
        rc = main(
            [
                "queue",
                "add",
                "my-plan",
                "--reason",
                "follow-up",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.OK)
        cfg = ProjectConfig(project_root=self.project)
        data = queue.load(cfg.queue_path())
        entry = data["queue"][0]
        self.assertEqual(entry["reason"], "follow-up")

    def test_plan_phase_without_token_rejected(self) -> None:
        """--plan/--phase without --token (worker fields in operator context) must exit GENERIC."""
        _write_plan(self.project, "foo")
        rc, err = _stderr(
            [
                "queue",
                "add",
                "foo",
                "--plan",
                "X",
                "--phase",
                "Y",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("--token", err)
