"""`clu doctor` skill-drift guard (#75 phase 4).

A stale installed `~/.claude/skills/<name>/SKILL.md` is what shipped the pre-#72
heartbeat loop at the incident, and clu had no way to surface it. doctor now
SHA-256-compares each installed skill against the bundled copy and warns on
drift. HOME is redirected per-test so we never read the real ~/.claude.
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from importlib.resources import files
from unittest import mock

from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase, write_config


class SkillDriftHealthTest(GitProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        write_config(self.project)  # doctor refuses without .orchestrator.json
        self.home = self.tmp_path / "home"
        (self.home / ".claude" / "skills").mkdir(parents=True)

    def _install(self, name: str, content: bytes) -> None:
        d = self.home / ".claude" / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_bytes(content)

    def _bundled(self, name: str) -> bytes:
        return files("end_of_line").joinpath(f"skills/{name}/SKILL.md").read_bytes()

    def _doctor(self) -> str:
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), redirect_stdout(buf):
            rc = main(["doctor", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        return buf.getvalue()

    def test_drift_flagged(self):
        self._install("clu-phase", b"# a stale, behind-the-bundle copy\n")
        out = self._doctor()
        self.assertIn("differ from the bundle", out)
        self.assertIn("clu-phase", out)

    def test_in_sync_is_quiet(self):
        self._install("clu-phase", self._bundled("clu-phase"))
        out = self._doctor()
        self.assertNotIn("differ from the bundle", out)

    def test_not_installed_is_quiet(self):
        # Nothing installed under the redirected HOME → no drift section.
        out = self._doctor()
        self.assertNotIn("differ from the bundle", out)

    def test_only_drifted_skill_named(self):
        self._install("clu-phase", self._bundled("clu-phase"))  # in sync
        self._install("clu-plan", b"# stale clu-plan\n")  # drifted
        out = self._doctor()
        self.assertIn("clu-plan", out)
        # clu-phase is in sync, so it must not appear in the drift list.
        drift_section = out[out.index("differ from the bundle"):]
        self.assertNotIn("clu-phase", drift_section)
