"""Tests for phase skill-wire: /clu-plan arms Monitor; /clu-monitor mentions watch.

File-content assertions against the bundled SKILL.md sources. Cheap and durable —
they catch content drift without behavioral overhead.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.resources import files
from pathlib import Path
from unittest import mock

from end_of_line.cli import ExitCode, main


class CluPlanSkillMonitorTest(unittest.TestCase):
    def test_clu_plan_skill_mentions_monitor_watch(self):
        content = files("end_of_line").joinpath("skills/clu-plan/SKILL.md").read_text()
        self.assertIn(
            "clu watch",
            content,
            "clu-plan SKILL.md must mention 'clu watch'",
        )
        self.assertIn(
            "Monitor(",
            content,
            "clu-plan SKILL.md must show a Monitor( invocation",
        )


class CluMonitorSkillWatchSiblingTest(unittest.TestCase):
    def test_clu_monitor_skill_mentions_watch_sibling(self):
        content = files("end_of_line").joinpath("skills/clu-monitor/SKILL.md").read_text()
        self.assertIn(
            "clu watch",
            content,
            "clu-monitor SKILL.md must mention 'clu watch'",
        )
        # Must distinguish the live (watch) vs AFK (inbox) channels.
        has_live_note = "live" in content.lower() or "at-desk" in content.lower()
        self.assertTrue(
            has_live_note,
            "clu-monitor SKILL.md must mention live/at-desk channel distinction",
        )
        has_inbox_note = "inbox" in content.lower() or "afk" in content.lower()
        self.assertTrue(
            has_inbox_note,
            "clu-monitor SKILL.md must mention inbox/AFK channel alongside watch",
        )


class InstallSkillDryRunCluPlanPathTest(unittest.TestCase):
    """Regression guard: install-skill --dry-run must name the clu-plan target path."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_install_skill_dry_run_shows_clu_plan_update_path(self):
        expected = self.home / ".claude" / "skills" / "clu-plan" / "SKILL.md"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-skill", "--only", "clu-plan", "--dry-run"])
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertIn(
            str(expected),
            out.getvalue(),
            "dry-run output must name the clu-plan SKILL.md target path",
        )
        # Must not actually create the file.
        self.assertFalse(expected.exists())


if __name__ == "__main__":
    unittest.main()
