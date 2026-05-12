"""Tests for `clu install-skill` — copies the bundled worker skill into
~/.claude/skills/clu-phase/SKILL.md.

HOME is redirected per-test so we never write to the real ~/.claude.
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


class InstallSkillTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.target = self.home / ".claude" / "skills" / "clu-phase" / "SKILL.md"
        self.bundled_bytes = (
            files("end_of_line").joinpath("skill/SKILL.md").read_bytes()
        )

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-skill", *argv])
        return rc, out.getvalue(), err.getvalue()


class FreshInstallTests(InstallSkillTestBase):
    def test_creates_target_with_bundled_contents(self):
        rc, out, _ = self._run()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertTrue(self.target.exists())
        self.assertEqual(self.target.read_bytes(), self.bundled_bytes)
        self.assertIn(str(self.target), out)

    def test_creates_parent_dirs(self):
        # Parent dirs ~/.claude/skills/clu-phase/ don't exist yet.
        self.assertFalse(self.target.parent.exists())
        rc, _, _ = self._run()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertTrue(self.target.exists())


class ExistingTargetTests(InstallSkillTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"old contents\n")

    def test_refuses_without_force(self):
        rc, _, err = self._run()
        self.assertEqual(rc, int(ExitCode.STATUS_TRANSITION))
        self.assertEqual(self.target.read_bytes(), b"old contents\n")
        self.assertIn("--force", err)

    def test_overwrites_with_force(self):
        rc, _, _ = self._run("--force")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertEqual(self.target.read_bytes(), self.bundled_bytes)


class SymlinkTargetTests(InstallSkillTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.target.parent.mkdir(parents=True)
        self.linked = self.home / "abe-skills" / "clu-phase" / "SKILL.md"
        self.linked.parent.mkdir(parents=True)
        self.linked.write_bytes(b"upstream skill body\n")
        self.target.symlink_to(self.linked)

    def test_refuses_symlink_without_force(self):
        rc, _, err = self._run()
        self.assertEqual(rc, int(ExitCode.STATUS_TRANSITION))
        self.assertTrue(self.target.is_symlink())
        self.assertIn("symlink", err.lower())
        self.assertIn("--force", err)

    def test_force_unlinks_symlink_and_writes_real_file(self):
        rc, _, _ = self._run("--force")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(self.target.read_bytes(), self.bundled_bytes)
        # Symlink target untouched.
        self.assertEqual(self.linked.read_bytes(), b"upstream skill body\n")


class DryRunTests(InstallSkillTestBase):
    def test_dry_run_fresh_makes_no_changes(self):
        rc, out, _ = self._run("--dry-run")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.target.exists())
        self.assertIn("would", out.lower())
        self.assertIn(str(self.target), out)

    def test_dry_run_force_describes_overwrite(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"old\n")
        rc, out, _ = self._run("--dry-run", "--force")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertEqual(self.target.read_bytes(), b"old\n")
        self.assertIn("overwrite", out.lower())


if __name__ == "__main__":
    unittest.main()
