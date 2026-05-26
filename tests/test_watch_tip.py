"""Tests for clu-watch tip emission on `clu init` and `clu queue add`."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import monitor, registry
from end_of_line.cli import main
from tests import isolate_monitor_marker

_PLAN_BODY = "# placeholder\n"
_WATCH_TIP_PLAN = "clu watch --project . --plan"
_WATCH_TIP_ALL = "clu watch --project . --all"


def _make_project(tmp: Path) -> Path:
    project = tmp
    project.mkdir(parents=True, exist_ok=True)
    project = project.resolve()
    (project / "plans").mkdir(exist_ok=True)
    return project


def _write_plan(project: Path, slug: str) -> None:
    (project / "plans" / f"{slug}.md").write_text(_PLAN_BODY)


class WatchTipTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp_path)
        self.project = _make_project(self.tmp_path / "proj")
        _write_plan(self.project, "foo")
        # Mark monitor as scheduled so the monitor tip stays silent in most
        # tests — we only want to exercise the watch tip behaviour.
        monitor.record_hook_installed("/abs/hook.py", "/home/x/.claude/settings.json")
        # Register the project so queue-add commands can find it.
        registry.register(self.project, "foo")

    def _run(self, *argv: str, tty: bool = False) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            with mock.patch.object(sys.stdout, "isatty", return_value=tty):
                rc = main(list(argv))
        return rc, buf.getvalue()

    # ------------------------------------------------------------------
    # clu init
    # ------------------------------------------------------------------

    def test_clu_init_prints_watch_tip(self) -> None:
        rc, out = self._run(
            "init",
            "--project",
            str(self.project),
            "--plan",
            "foo",
            "--no-claude-md",
        )
        self.assertEqual(rc, 0)
        self.assertIn("clu watch --project . --plan foo", out)

    def test_clu_init_quiet_suppresses_watch_tip(self) -> None:
        rc, out = self._run(
            "init",
            "--project",
            str(self.project),
            "--plan",
            "foo",
            "--no-claude-md",
            "--quiet",
        )
        self.assertEqual(rc, 0)
        self.assertNotIn(_WATCH_TIP_PLAN, out)

    # ------------------------------------------------------------------
    # clu queue add
    # ------------------------------------------------------------------

    def test_clu_queue_add_prints_watch_tip_all(self) -> None:
        _write_plan(self.project, "bar")
        rc, out = self._run("queue", "add", "bar", "--project", str(self.project))
        self.assertEqual(rc, 0)
        self.assertIn(_WATCH_TIP_ALL, out)

    def test_clu_queue_add_quiet_suppresses_watch_tip(self) -> None:
        _write_plan(self.project, "bar")
        rc, out = self._run(
            "queue",
            "add",
            "bar",
            "--project",
            str(self.project),
            "--quiet",
        )
        self.assertEqual(rc, 0)
        self.assertNotIn(_WATCH_TIP_ALL, out)

    # ------------------------------------------------------------------
    # Regression: monitor tip still fires (unchanged behaviour)
    # ------------------------------------------------------------------

    def test_existing_monitor_tip_still_prints(self) -> None:
        """Both watch + monitor tips coexist; monitor tip behaviour unchanged."""
        with mock.patch("end_of_line.monitor.is_scheduled", return_value=False):
            rc, out = self._run(
                "init",
                "--project",
                str(self.project),
                "--plan",
                "foo",
                "--no-claude-md",
                tty=True,
            )
        self.assertEqual(rc, 0)
        self.assertIn("/clu-monitor", out)
        self.assertIn("clu watch --project . --plan foo", out)


if __name__ == "__main__":
    unittest.main()
