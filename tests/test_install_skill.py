"""Tests for `clu install-skill` — copies bundled skills into
~/.claude/skills/<name>/SKILL.md.

clu ships three skills: `clu-phase` (worker contract), `plan` (authorship),
and `brainstorm` (parallel-persona pre-planning). Default installs all three.
`--only <name>` installs one. `--force` overrides the no-clobber-non-symlink
safety.

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
        self.plan_target = self.home / ".claude" / "skills" / "plan" / "SKILL.md"
        self.brainstorm_target = (
            self.home / ".claude" / "skills" / "brainstorm" / "SKILL.md"
        )
        self.bundled_bytes = (
            files("end_of_line").joinpath("skills/clu-phase/SKILL.md").read_bytes()
        )
        self.bundled_plan_bytes = (
            files("end_of_line").joinpath("skills/plan/SKILL.md").read_bytes()
        )
        self.bundled_brainstorm_bytes = (
            files("end_of_line").joinpath("skills/brainstorm/SKILL.md").read_bytes()
        )

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-skill", *argv])
        return rc, out.getvalue(), err.getvalue()


class FreshInstallTests(InstallSkillTestBase):
    def test_default_installs_all_three_skills(self):
        rc, out, _ = self._run()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertTrue(self.target.exists())
        self.assertTrue(self.plan_target.exists())
        self.assertTrue(self.brainstorm_target.exists())
        self.assertEqual(self.target.read_bytes(), self.bundled_bytes)
        self.assertEqual(self.plan_target.read_bytes(), self.bundled_plan_bytes)
        self.assertEqual(
            self.brainstorm_target.read_bytes(), self.bundled_brainstorm_bytes,
        )
        self.assertIn(str(self.target), out)
        self.assertIn(str(self.plan_target), out)
        self.assertIn(str(self.brainstorm_target), out)

    def test_creates_parent_dirs(self):
        self.assertFalse(self.target.parent.exists())
        self.assertFalse(self.plan_target.parent.exists())
        self.assertFalse(self.brainstorm_target.parent.exists())
        rc, _, _ = self._run()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertTrue(self.target.exists())
        self.assertTrue(self.plan_target.exists())
        self.assertTrue(self.brainstorm_target.exists())


class OnlyFlagTests(InstallSkillTestBase):
    def test_only_clu_phase(self):
        rc, out, _ = self._run("--only", "clu-phase")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertTrue(self.target.exists())
        self.assertFalse(self.plan_target.exists())
        self.assertFalse(self.brainstorm_target.exists())
        self.assertIn(str(self.target), out)

    def test_only_plan(self):
        rc, out, _ = self._run("--only", "plan")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.target.exists())
        self.assertTrue(self.plan_target.exists())
        self.assertFalse(self.brainstorm_target.exists())
        self.assertIn(str(self.plan_target), out)

    def test_only_brainstorm(self):
        rc, out, _ = self._run("--only", "brainstorm")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.target.exists())
        self.assertFalse(self.plan_target.exists())
        self.assertTrue(self.brainstorm_target.exists())
        self.assertEqual(
            self.brainstorm_target.read_bytes(), self.bundled_brainstorm_bytes,
        )
        self.assertIn(str(self.brainstorm_target), out)

    def test_only_unknown_name_exits_clean(self):
        rc, _, err = self._run("--only", "banana")
        self.assertNotEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.target.exists())
        self.assertFalse(self.plan_target.exists())
        self.assertFalse(self.brainstorm_target.exists())
        # Message must list the valid names so the operator can self-correct.
        self.assertIn("clu-phase", err)
        self.assertIn("plan", err)
        self.assertIn("brainstorm", err)
        self.assertIn("banana", err)


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

    def test_refusal_is_atomic_other_skills_not_installed(self):
        # clu-phase target is a regular file → refuse. plan + brainstorm
        # targets are fresh → would install, but abort-all means they MUST
        # NOT install.
        self.assertFalse(self.plan_target.exists())
        self.assertFalse(self.brainstorm_target.exists())
        rc, _, _ = self._run()
        self.assertEqual(rc, int(ExitCode.STATUS_TRANSITION))
        self.assertFalse(self.plan_target.exists())
        self.assertFalse(self.brainstorm_target.exists())


class SymlinkTargetTests(InstallSkillTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.target.parent.mkdir(parents=True)
        self.linked = self.home / "abe-skills" / "clu-phase" / "SKILL.md"
        self.linked.parent.mkdir(parents=True)
        self.linked.write_bytes(b"upstream skill body\n")
        self.target.symlink_to(self.linked)

    def test_overwrites_symlink_without_force(self):
        # Symlinks are fair game — clu owns the ones it wrote, and a symlink
        # at the target is harmless to replace (the symlink destination is
        # left untouched). No --force needed.
        rc, _, _ = self._run("--only", "clu-phase")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(self.target.read_bytes(), self.bundled_bytes)
        # Symlink destination preserved.
        self.assertEqual(self.linked.read_bytes(), b"upstream skill body\n")

    def test_force_unlinks_symlink_and_writes_real_file(self):
        rc, _, _ = self._run("--force", "--only", "clu-phase")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(self.target.read_bytes(), self.bundled_bytes)
        self.assertEqual(self.linked.read_bytes(), b"upstream skill body\n")


class HardlinkTargetTests(InstallSkillTestBase):
    """Some operators ingest skills via hardlinks (e.g. `cp -al`) rather
    than symlinks. The naive "open target for write" path would modify the
    shared inode, hitting the upstream copy. Force-install must break the
    hardlink instead.
    """
    def setUp(self) -> None:
        super().setUp()
        self.target.parent.mkdir(parents=True)
        self.linked = self.home / "abe-skills" / "clu-phase" / "SKILL.md"
        self.linked.parent.mkdir(parents=True)
        self.linked.write_bytes(b"upstream skill body\n")
        os.link(self.linked, self.target)

    def test_force_breaks_hardlink_upstream_untouched(self):
        rc, _, _ = self._run("--force", "--only", "clu-phase")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertEqual(self.target.read_bytes(), self.bundled_bytes)
        self.assertEqual(self.linked.read_bytes(), b"upstream skill body\n")
        self.assertNotEqual(
            self.target.stat().st_ino, self.linked.stat().st_ino,
        )


class DryRunTests(InstallSkillTestBase):
    def test_dry_run_prints_all_destinations(self):
        rc, out, _ = self._run("--dry-run")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.target.exists())
        self.assertFalse(self.plan_target.exists())
        self.assertFalse(self.brainstorm_target.exists())
        self.assertIn(str(self.target), out)
        self.assertIn(str(self.plan_target), out)
        self.assertIn(str(self.brainstorm_target), out)
        self.assertIn("would", out.lower())

    def test_dry_run_force_describes_overwrite(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"old\n")
        rc, out, _ = self._run("--dry-run", "--force")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertEqual(self.target.read_bytes(), b"old\n")
        self.assertIn("overwrite", out.lower())


if __name__ == "__main__":
    unittest.main()
