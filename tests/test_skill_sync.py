"""`skill_sync.scan()` — placement + content classification, symlink-aware.

Table-driven over the five `placement` values and the three `content` values,
under the harness `HOME` (`CluTestCase` points `Path.home()` at a temp dir).
Includes the three cases the old fused doctor check silently mis-reported: a
dangling symlink (read as "not installed"), a real file under a symlinked
parent (read as writable), and a file whose read raises (read as in sync).
"""

from __future__ import annotations

import os
import shutil
from importlib.resources import files
from pathlib import Path
from unittest import mock, skipIf

from end_of_line import skill_sync
from end_of_line.cli import BUNDLED_SKILLS
from tests import CluTestCase

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def bundled_bytes(name: str) -> bytes:
    return files("end_of_line").joinpath(f"skills/{name}/SKILL.md").read_bytes()


class SkillSyncScanTest(CluTestCase):
    """One `SkillStatus` per skill, decided from the filesystem alone."""

    def setUp(self) -> None:
        super().setUp()
        # The harness home is `tmp_path / "home"`, and on macOS $TMPDIR sits
        # under /var — itself a symlink to /private/var. So an UNRESOLVED temp
        # home already has a symlink on its path to the root, and the
        # parent walk reports every target under it as unwritable. Resolving
        # it models a real home (/Users/<name>), where the only symlinks are
        # the ones a fixture creates. p4's repair tests will need the same.
        self.home = (self.tmp_path / "home").resolve()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.skills = self.home / ".claude" / "skills"
        self.skills.mkdir(parents=True, exist_ok=True)
        # Somewhere outside ~/.claude for symlink targets to point at.
        self.elsewhere = self.home.parent / "elsewhere"
        self.elsewhere.mkdir()

    # --- fixtures ---------------------------------------------------------

    def _install_file(self, name: str, content: bytes) -> Path:
        """A plain regular file at ~/.claude/skills/<name>/SKILL.md."""
        target = self.skills / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def _install_link(self, name: str, content: bytes) -> Path:
        """SKILL.md is a symlink to a real file outside ~/.claude."""
        real = self.elsewhere / f"{name}-SKILL.md"
        real.write_bytes(content)
        target = self.skills / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(real)
        return target

    def _install_dangling(self, name: str) -> Path:
        """SKILL.md is a symlink whose destination does not exist."""
        target = self.skills / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(self.elsewhere / "gone" / "SKILL.md")
        return target

    def _install_under_symlinked_parent(self, name: str, content: bytes) -> Path:
        """A real file whose PARENT directory is the symlink."""
        real_dir = self.elsewhere / f"{name}-dir"
        real_dir.mkdir()
        (real_dir / "SKILL.md").write_bytes(content)
        (self.skills / name).symlink_to(real_dir, target_is_directory=True)
        return self.skills / name / "SKILL.md"

    def _status(self, name: str) -> skill_sync.SkillStatus:
        found = [s for s in skill_sync.scan([name]) if s.name == name]
        self.assertEqual(len(found), 1, f"scan() returned {len(found)} records for {name}")
        return found[0]

    # --- placement: all five ---------------------------------------------

    def test_placement_absent_when_nothing_installed(self):
        # Input: no ~/.claude/skills/<name>/ at all.
        s = self._status("clu-phase")
        self.assertEqual(s.placement, "absent")
        self.assertEqual(s.content, "unknown")
        self.assertTrue(s.writable)

    def test_placement_file_for_a_regular_file(self):
        # Input: a plain regular file.
        self._install_file("clu-phase", bundled_bytes("clu-phase"))
        s = self._status("clu-phase")
        self.assertEqual(s.placement, "file")
        self.assertTrue(s.writable)

    def test_placement_link_for_a_symlinked_skill_md(self):
        # Input: SKILL.md itself is a symlink to a real file.
        self._install_link("clu-plan", bundled_bytes("clu-plan"))
        s = self._status("clu-plan")
        self.assertEqual(s.placement, "link")
        self.assertEqual(s.content, "in_sync")
        self.assertFalse(s.writable)

    def test_placement_broken_for_a_dangling_symlink(self):
        # Input: a symlink pointing at a path that does not exist.
        # The old check called `exists()` first, which follows the link and
        # reported this as "not installed" — i.e. as no drift at all.
        self._install_dangling("clu-plan")
        s = self._status("clu-plan")
        self.assertEqual(s.placement, "broken")
        self.assertEqual(s.content, "unknown")
        self.assertFalse(s.writable)

    @skipIf(IS_ROOT, "root reads mode-000 files")
    def test_placement_unreadable_when_the_read_raises(self):
        # Input: a regular file with mode 000. The old check swallowed the
        # OSError with `continue`, which reported it as in sync.
        target = self._install_file("clu-monitor", b"# whatever\n")
        target.chmod(0o000)
        self.addCleanup(target.chmod, 0o644)
        s = self._status("clu-monitor")
        self.assertEqual(s.placement, "unreadable")
        self.assertEqual(s.content, "unknown")

    # --- content: all three ----------------------------------------------

    def test_content_in_sync_for_bundled_bytes(self):
        # Input: byte-identical to the bundled copy.
        self._install_file("clu-reply", bundled_bytes("clu-reply"))
        self.assertEqual(self._status("clu-reply").content, "in_sync")

    def test_content_differs_for_a_stale_copy(self):
        # Input: any other bytes.
        self._install_file("clu-reply", b"# a stale, behind-the-bundle copy\n")
        self.assertEqual(self._status("clu-reply").content, "differs")

    def test_content_unknown_when_there_is_nothing_to_compare(self):
        # Input: the three placements with no readable bytes.
        self._install_dangling("clu-plan")
        self.assertEqual(self._status("clu-plan").content, "unknown")
        self.assertEqual(self._status("audit-skill").content, "unknown")  # absent

    # --- writable: the filesystem-keyed safety check ----------------------

    def test_symlinked_parent_directory_is_not_writable(self):
        # The leaf is a real file, so a leaf-only `is_symlink()` check reports
        # it writable — and a write through it lands in the operator's other
        # checkout. The parent walk is what catches it.
        self._install_under_symlinked_parent("clu-plan", bundled_bytes("clu-plan"))
        s = self._status("clu-plan")
        self.assertEqual(s.placement, "file")
        self.assertEqual(s.content, "in_sync")
        self.assertFalse(s.writable)

    def test_symlink_above_the_skills_dir_is_not_writable(self):
        # A walk that stops at ~/.claude/skills/ misses a symlinked home. The
        # parent walk goes to the filesystem root, so this is caught too.
        link_home = self.home.parent / "home-link"
        link_home.symlink_to(self.home, target_is_directory=True)
        self._install_file("clu-phase", bundled_bytes("clu-phase"))
        with mock.patch.dict(os.environ, {"HOME": str(link_home)}):
            s = self._status("clu-phase")
        self.assertEqual(s.placement, "file")
        self.assertTrue(s.writable is False)

    def test_absent_target_under_a_clean_path_is_writable(self):
        self.assertTrue(self._status("clu-phase").writable)

    # --- surface ----------------------------------------------------------

    def test_scan_covers_every_bundled_skill_by_default(self):
        names = [s.name for s in skill_sync.scan()]
        self.assertEqual(names, list(BUNDLED_SKILLS))

    def test_scan_target_is_the_installed_skill_md_path(self):
        s = self._status("clu-phase")
        self.assertEqual(s.target, self.home / ".claude" / "skills" / "clu-phase" / "SKILL.md")

    def test_scan_writes_nothing(self):
        # Purity: a scan against a home with no ~/.claude must not create one.
        shutil.rmtree(self.skills.parent)
        skill_sync.scan()
        self.assertFalse((self.home / ".claude").exists())

    def test_status_is_frozen(self):
        s = self._status("clu-phase")
        with self.assertRaises(AttributeError):
            setattr(s, "placement", "file")

    def test_every_bundled_skill_ships_exactly_one_skill_md(self):
        # The packaged unit is one SKILL.md per skill (pyproject.toml
        # package-data ships `skills/*/SKILL.md` and nothing else). scan()
        # compares that one file instead of walking the directory, so a second
        # file appearing in a bundled skill dir must fail loudly here rather
        # than silently going un-synced.
        root = Path(str(files("end_of_line").joinpath("skills")))
        for name in BUNDLED_SKILLS:
            entries = sorted(p.name for p in (root / name).iterdir())
            self.assertEqual(
                entries,
                ["SKILL.md"],
                f"bundled skill {name} holds files beyond SKILL.md — only "
                f"SKILL.md is packaged, so the rest would never install",
            )


class ScanNameValidationTest(CluTestCase):
    """An unbundled name must fail the same way whatever is on disk.

    Before this guard, `bundled_bytes` was reached only after the installed
    read succeeded, so a caller's typo raised FileNotFoundError when a file
    happened to exist at that path and returned a clean `absent` record when
    it did not — the same mistake taking two different shapes.
    """

    def test_unbundled_name_raises_when_nothing_is_installed(self):
        with self.assertRaises(ValueError) as ctx:
            skill_sync.scan(["not-a-clu-skill"])

        self.assertIn("not-a-clu-skill", str(ctx.exception))

    def test_unbundled_name_raises_the_same_way_when_a_file_is_installed(self):
        home = Path.home()
        target = home / ".claude" / "skills" / "not-a-clu-skill" / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"something is here\n")

        with self.assertRaises(ValueError) as ctx:
            skill_sync.scan(["not-a-clu-skill"])

        self.assertIn("not-a-clu-skill", str(ctx.exception))

    def test_bundled_names_still_scan(self):
        self.assertEqual(len(skill_sync.scan(["clu-phase"])), 1)
