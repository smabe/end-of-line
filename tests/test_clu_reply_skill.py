"""Tests for the bundled /clu-reply skill.

Covers membership in BUNDLED_SKILLS, package-data shipping, frontmatter
validity, and install-skill integration.
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

from end_of_line.cli import BUNDLED_SKILLS, ExitCode, main


class CluReplyBundledTests(unittest.TestCase):
    def test_clu_reply_in_bundled_skills(self):
        self.assertIn("clu-reply", BUNDLED_SKILLS)

    def test_clu_reply_skill_file_ships_with_package(self):
        text = (
            files("end_of_line")
            .joinpath("skills/clu-reply/SKILL.md")
            .read_text()
        )
        self.assertTrue(text.strip())

    def test_clu_reply_skill_frontmatter_valid(self):
        text = (
            files("end_of_line")
            .joinpath("skills/clu-reply/SKILL.md")
            .read_text()
        )
        # Frontmatter sits between the first two '---\n' delimiters.
        parts = text.split("---\n", 2)
        self.assertGreaterEqual(
            len(parts), 3, "SKILL.md missing frontmatter delimiters"
        )
        fm: dict[str, str] = {}
        for line in parts[1].splitlines():
            if ":" in line and not line.startswith(" "):
                key, _, value = line.partition(":")
                fm[key.strip()] = value.strip()
        self.assertEqual(fm.get("name"), "clu-reply")
        self.assertIn("description", fm)
        self.assertTrue(fm["description"])


class CluReplyInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-skill", *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_install_skill_installs_clu_reply(self):
        rc, _, _ = self._run("--only", "clu-reply", "--no-claude-md-note")
        self.assertEqual(rc, int(ExitCode.OK))
        target = self.home / ".claude" / "skills" / "clu-reply" / "SKILL.md"
        self.assertTrue(target.exists())
        self.assertTrue(target.read_text().strip())

    def test_install_skill_list_shows_clu_reply(self):
        rc, out, _ = self._run("--list")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertIn("clu-reply", out)


if __name__ == "__main__":
    unittest.main()
