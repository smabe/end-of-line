"""`clu doctor` skill-drift guard (#75 phase 4).

A stale installed `~/.claude/skills/<name>/SKILL.md` is what shipped the pre-#72
heartbeat loop at the incident, and clu had no way to surface it. doctor reports
drift from `skill_sync.scan()` and formats it; the classification itself is
covered in tests/test_skill_sync.py. HOME is redirected by the harness
(`CluTestCase.setUp`) so we never read the real ~/.claude.
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from importlib.resources import files
from unittest import mock, skipIf

from end_of_line import skill_sync
from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase, write_config

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


class SkillDriftHealthTest(GitProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        write_config(self.project)  # doctor refuses without .orchestrator.json
        # The harness home is `tmp_path / "home"`; on macOS $TMPDIR sits under
        # /var, itself a symlink to /private/var, so an UNRESOLVED temp home
        # already has a symlink on its path and every install under it reads as
        # unwritable. Resolve it so these tests model a real home
        # (/Users/<name>) and the unwritable line fires only on a symlink a
        # fixture actually made.
        self.home = (self.tmp_path / "home").resolve()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.home / ".claude" / "skills").mkdir(parents=True)

    def _install(self, name: str, content: bytes) -> None:
        d = self.home / ".claude" / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_bytes(content)

    def _bundled(self, name: str) -> bytes:
        return files("end_of_line").joinpath(f"skills/{name}/SKILL.md").read_bytes()

    def _doctor(self) -> str:
        # No HOME patch here — setUp owns it for the whole class.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["doctor", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        return buf.getvalue()

    def test_unwritable_and_differing_skill_gets_one_coherent_instruction(self):
        # Regression: a differing skill clu cannot write once appeared in BOTH
        # sections — "re-sync with --force" beside "clu won't write through
        # it". The operator was told to run a command that cannot work.
        d = self.home / ".claude" / "skills" / "clu-plan"
        d.mkdir(parents=True, exist_ok=True)
        mine = self.home / "mine.md"
        mine.write_text("my own copy, differs from the bundle\n")
        (d / "SKILL.md").symlink_to(mine)

        out = self._doctor()

        self.assertNotIn("differ from the bundle", out)
        self.assertIn("clu-plan", out)
        self.assertIn("and differs from the bundle", out)
        self.assertIn("won't write through it", out)

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
        # Nothing installed under the redirected HOME → every skill scans as
        # `absent`, and absent is not drift, so doctor stays silent.
        self.assertEqual({s.placement for s in skill_sync.scan()}, {"absent"})

        out = self._doctor()
        self.assertNotIn("differ from the bundle", out)
        self.assertNotIn("can't compare or re-sync", out)

    def test_only_drifted_skill_named(self):
        self._install("clu-phase", self._bundled("clu-phase"))  # in sync
        self._install("clu-plan", b"# stale clu-plan\n")  # drifted
        out = self._doctor()
        self.assertIn("clu-plan", out)
        # clu-phase is in sync, so it must not appear in the drift list.
        drift_section = out[out.index("differ from the bundle"):]
        self.assertNotIn("clu-phase", drift_section)

    def test_vendored_skill_not_flagged(self):
        # `plan` is VENDORED — clu bundles it but isn't canonical for it, so an
        # installed copy that differs from clu's bundle is the expected steady
        # state, not drift. It must not appear in the drift warning.
        self._install("plan", b"# the operator's own richer /plan\n")
        out = self._doctor()
        self.assertNotIn("differ from the bundle", out)

    def test_vendored_differs_native_in_sync_is_quiet(self):
        # A differing vendored skill alongside an in-sync native skill produces
        # no drift section at all — the vendored difference is suppressed and the
        # native skill matches.
        self._install("plan", b"# operator's own /plan\n")  # vendored, differs
        self._install("clu-phase", self._bundled("clu-phase"))  # native, in sync
        out = self._doctor()
        self.assertNotIn("differ from the bundle", out)

    def test_vendored_skills_subset_of_bundled(self):
        # Guards a typo in VENDORED_SKILLS that would silently never match a
        # bundled skill (and so never suppress anything).
        from end_of_line.cli import BUNDLED_SKILLS, VENDORED_SKILLS

        self.assertTrue(VENDORED_SKILLS <= set(BUNDLED_SKILLS))

    def test_dangling_symlink_is_reported(self):
        # The old check called `exists()` first, which follows the link, so a
        # dangling install read as "not installed" and warned about nothing.
        d = self.home / ".claude" / "skills" / "clu-phase"
        d.mkdir(parents=True)
        (d / "SKILL.md").symlink_to(self.tmp_path / "gone" / "SKILL.md")

        out = self._doctor()

        self.assertIn("can't compare or re-sync", out)
        self.assertIn("dangling symlink", out)
        self.assertIn("clu-phase", out)

    @skipIf(IS_ROOT, "root reads mode-000 files")
    def test_unreadable_install_is_reported(self):
        # The old check swallowed the OSError with `continue` — an install it
        # could not read was reported as in sync.
        self._install("clu-phase", b"# unreadable\n")
        target = self.home / ".claude" / "skills" / "clu-phase" / "SKILL.md"
        target.chmod(0o000)
        self.addCleanup(target.chmod, 0o644)

        out = self._doctor()

        self.assertIn("can't compare or re-sync", out)
        self.assertIn("can't be read", out)
        self.assertIn("clu-phase", out)

    def test_symlinked_install_is_reported(self):
        real = self.tmp_path / "elsewhere-clu-phase.md"
        real.write_bytes(self._bundled("clu-phase"))
        d = self.home / ".claude" / "skills" / "clu-phase"
        d.mkdir(parents=True)
        (d / "SKILL.md").symlink_to(real)

        out = self._doctor()

        self.assertIn("can't compare or re-sync", out)
        self.assertIn("clu won't write through it", out)
        # In sync byte-for-byte, so it is NOT drift — only unwritable.
        self.assertNotIn("differ from the bundle", out)

    def test_symlinked_vendored_skill_is_quiet(self):
        # `plan` is the skill the operator most often symlinks into their own
        # checkout. clu isn't canonical for it, so neither the symlink nor a
        # content difference is worth a warning.
        real = self.tmp_path / "operators-own-plan.md"
        real.write_bytes(b"# the operator's own richer /plan\n")
        d = self.home / ".claude" / "skills" / "plan"
        d.mkdir(parents=True)
        (d / "SKILL.md").symlink_to(real)

        out = self._doctor()

        self.assertNotIn("can't compare or re-sync", out)
        self.assertNotIn("differ from the bundle", out)

    def test_symlinked_parent_directory_is_reported(self):
        # The shape the operator's own machine actually has for `plan`: the
        # skill DIRECTORY is the symlink and SKILL.md inside it is a regular
        # file, so a leaf-only symlink test sees nothing. Reported because
        # clu must not write through it.
        real_dir = self.tmp_path / "elsewhere-clu-phase"
        real_dir.mkdir()
        (real_dir / "SKILL.md").write_bytes(self._bundled("clu-phase"))
        (self.home / ".claude" / "skills" / "clu-phase").symlink_to(
            real_dir, target_is_directory=True
        )

        out = self._doctor()

        self.assertIn("can't compare or re-sync", out)
        self.assertIn("a symlink on its path", out)
        self.assertIn("clu-phase", out)
