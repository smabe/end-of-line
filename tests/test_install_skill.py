"""Tests for `clu install-skill` — copies bundled skills into
~/.claude/skills/<name>/SKILL.md.

Default installs every skill in `BUNDLED_SKILLS`. `--only <name>`
installs one. `--force` overrides the no-clobber-non-symlink safety.

HOME is redirected per-test so we never write to the real ~/.claude.

Setup builds per-skill `self.targets[name]` paths and
`self.bundled_bytes[name]` bodies driven by `BUNDLED_SKILLS` so adding
a new bundled skill auto-extends coverage without editing each test.
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

from end_of_line.cli import (
    _CLU_NOTE_END,
    _CLU_NOTE_START,
    BUNDLED_SKILLS,
    ExitCode,
    main,
)


class InstallSkillTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.targets: dict[str, Path] = {
            name: self.home / ".claude" / "skills" / name / "SKILL.md"
            for name in BUNDLED_SKILLS
        }
        self.bundled_bytes_by_name: dict[str, bytes] = {
            name: files("end_of_line")
            .joinpath(f"skills/{name}/SKILL.md")
            .read_bytes()
            for name in BUNDLED_SKILLS
        }
        # Backward-compat aliases used by tests targeting specific skills.
        self.target = self.targets["clu-phase"]
        self.plan_target = self.targets["plan"]
        self.brainstorm_target = self.targets["brainstorm"]
        self.monitor_target = self.targets["clu-monitor"]
        self.clu_plan_target = self.targets["clu-plan"]
        self.bundled_bytes = self.bundled_bytes_by_name["clu-phase"]
        self.bundled_plan_bytes = self.bundled_bytes_by_name["plan"]
        self.bundled_brainstorm_bytes = self.bundled_bytes_by_name["brainstorm"]
        self.bundled_monitor_bytes = self.bundled_bytes_by_name["clu-monitor"]
        self.bundled_clu_plan_bytes = self.bundled_bytes_by_name["clu-plan"]

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-skill", *argv])
        return rc, out.getvalue(), err.getvalue()

    def _assert_only_installed(self, only_name: str) -> None:
        """Assert exactly `only_name` is installed; all other bundled skills are absent."""
        for name, target in self.targets.items():
            if name == only_name:
                self.assertTrue(target.exists(), f"{name} should be installed")
            else:
                self.assertFalse(target.exists(), f"{name} should be absent")


class FreshInstallTests(InstallSkillTestBase):
    def test_default_installs_all_bundled_skills(self):
        rc, out, _ = self._run()
        self.assertEqual(rc, int(ExitCode.OK))
        for name, target in self.targets.items():
            self.assertTrue(target.exists(), f"{name} not installed")
            self.assertEqual(
                target.read_bytes(),
                self.bundled_bytes_by_name[name],
                f"{name} bytes differ from bundled",
            )
            self.assertIn(str(target), out, f"{name} target path not in stdout")

    def test_creates_parent_dirs(self):
        for name, target in self.targets.items():
            self.assertFalse(target.parent.exists(), f"{name} parent leaked")
        rc, _, _ = self._run()
        self.assertEqual(rc, int(ExitCode.OK))
        for name, target in self.targets.items():
            self.assertTrue(target.exists(), f"{name} not installed")


class OnlyFlagTests(InstallSkillTestBase):
    def test_only_each_bundled_skill_isolates_install(self):
        """Per-skill: `--only X` installs X and no other bundled skill."""
        for name in BUNDLED_SKILLS:
            with self.subTest(skill=name):
                # Re-isolate HOME per subTest so prior installs don't leak.
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                home = Path(tmp.name)
                with mock.patch.dict(os.environ, {"HOME": str(home)}):
                    targets = {
                        n: home / ".claude" / "skills" / n / "SKILL.md"
                        for n in BUNDLED_SKILLS
                    }
                    out_buf, err_buf = io.StringIO(), io.StringIO()
                    with redirect_stdout(out_buf), redirect_stderr(err_buf):
                        rc = main(["install-skill", "--only", name])
                    self.assertEqual(rc, int(ExitCode.OK))
                    for n, target in targets.items():
                        if n == name:
                            self.assertTrue(target.exists(), f"{name} not installed")
                            self.assertEqual(
                                target.read_bytes(),
                                self.bundled_bytes_by_name[n],
                            )
                            self.assertIn(str(target), out_buf.getvalue())
                        else:
                            self.assertFalse(target.exists(), f"{n} should be absent")

    def test_only_unknown_name_exits_clean(self):
        rc, _, err = self._run("--only", "banana")
        self.assertNotEqual(rc, int(ExitCode.OK))
        for target in self.targets.values():
            self.assertFalse(target.exists())
        # Message must list every valid name so the operator can self-correct.
        for name in BUNDLED_SKILLS:
            self.assertIn(name, err, f"{name} missing from error message")
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
        # clu-phase target is a regular file → refuse. Other targets are
        # fresh → would install, but abort-all means they MUST NOT install.
        others = {n: t for n, t in self.targets.items() if n != "clu-phase"}
        for name, target in others.items():
            self.assertFalse(target.exists(), f"{name} pre-state leaked")
        rc, _, _ = self._run()
        self.assertEqual(rc, int(ExitCode.STATUS_TRANSITION))
        for name, target in others.items():
            self.assertFalse(target.exists(), f"{name} installed despite abort")


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
            self.target.stat().st_ino,
            self.linked.stat().st_ino,
        )


class DirectorySymlinkTests(InstallSkillTestBase):
    """target.parent is a directory symlink (operator's canonical setup:
    ~/.claude/skills/<name> → ~/projects/abe-skills/skills/<name>).
    install-skill must warn on stderr then succeed (follow-into is intentional).
    """

    def setUp(self) -> None:
        super().setUp()
        self.canonical = self.home / "abe-skills" / "skills" / "clu-phase"
        self.canonical.mkdir(parents=True)
        self.target.parent.parent.mkdir(parents=True, exist_ok=True)
        # ~/.claude/skills/clu-phase is a symlink to the canonical dir
        self.target.parent.symlink_to(self.canonical)

    def test_install_skill_warns_on_directory_symlink(self):
        rc, _, err = self._run("--only", "clu-phase")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertIn("warning", err)
        self.assertIn("symlink", err)
        self.assertTrue((self.canonical / "SKILL.md").exists())

    def test_directory_symlink_warning_exit_code_zero(self):
        rc, _, _ = self._run("--only", "clu-phase")
        self.assertEqual(rc, int(ExitCode.OK))


class ClaudeMdNoteTests(InstallSkillTestBase):
    """`--add-claude-md-note` / `--no-claude-md-note` flow.

    Issue #16 — install-skill optionally writes an autonomous-loop-pacing
    section into ~/.claude/CLAUDE.md, fenced by clu-managed markers so the
    write is idempotent.
    """

    NOTE_START = _CLU_NOTE_START
    NOTE_END = _CLU_NOTE_END

    @property
    def claude_md(self) -> Path:
        return self.home / ".claude" / "CLAUDE.md"

    def test_no_flag_no_tty_skips_silently(self):
        # No interactive TTY, no flag → CLAUDE.md must not be created.
        with mock.patch("sys.stdin.isatty", return_value=False):
            rc, _, _ = self._run()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.claude_md.exists())

    def test_no_claude_md_note_flag_skips_silently(self):
        rc, out, _ = self._run("--no-claude-md-note")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.claude_md.exists())
        # No mention of CLAUDE.md in stdout (skills installed, nothing more).
        self.assertNotIn("CLAUDE.md", out)

    def test_add_claude_md_note_creates_fresh_file(self):
        self.assertFalse(self.claude_md.exists())
        rc, out, _ = self._run("--add-claude-md-note")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertTrue(self.claude_md.exists())
        body = self.claude_md.read_text()
        self.assertIn(self.NOTE_START, body)
        self.assertIn(self.NOTE_END, body)
        self.assertIn("ScheduleWakeup", body)
        self.assertIn(str(self.claude_md), out)

    def test_add_claude_md_note_appends_to_existing(self):
        self.claude_md.parent.mkdir(parents=True, exist_ok=True)
        prior = "# My personal CLAUDE.md\n\nSome existing prose.\n"
        self.claude_md.write_text(prior)
        rc, _, _ = self._run("--add-claude-md-note")
        self.assertEqual(rc, int(ExitCode.OK))
        body = self.claude_md.read_text()
        # Prior content is preserved verbatim.
        self.assertIn(prior, body)
        # Section is appended after.
        self.assertTrue(body.endswith(self.NOTE_END + "\n"))

    def test_add_claude_md_note_idempotent_replaces_between_markers(self):
        self.claude_md.parent.mkdir(parents=True, exist_ok=True)
        prior = (
            "# Personal CLAUDE.md\n\n"
            f"{self.NOTE_START}\n"
            "stale outdated content\n"
            f"{self.NOTE_END}\n\n"
            "## More stuff after\n"
        )
        self.claude_md.write_text(prior)
        rc, _, _ = self._run("--add-claude-md-note")
        self.assertEqual(rc, int(ExitCode.OK))
        body = self.claude_md.read_text()
        # Stale content gone, fresh content present.
        self.assertNotIn("stale outdated content", body)
        self.assertIn("ScheduleWakeup", body)
        # Markers still present and exactly once each.
        self.assertEqual(body.count(self.NOTE_START), 1)
        self.assertEqual(body.count(self.NOTE_END), 1)
        # Pre + post content preserved.
        self.assertIn("# Personal CLAUDE.md", body)
        self.assertIn("## More stuff after", body)

    def test_partial_markers_fail_loud(self):
        # Start marker without end (or vice versa) is malformed state — bail
        # rather than guess where to insert.
        self.claude_md.parent.mkdir(parents=True, exist_ok=True)
        self.claude_md.write_text(f"# Personal\n\n{self.NOTE_START}\nno end marker here\n")
        rc, _, err = self._run("--add-claude-md-note")
        self.assertNotEqual(rc, int(ExitCode.OK))
        self.assertIn("malformed", err.lower())

    def test_flags_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self._run("--add-claude-md-note", "--no-claude-md-note")

    def test_interactive_accept(self):
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value="y"),
        ):
            rc, _, _ = self._run()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertTrue(self.claude_md.exists())
        self.assertIn(self.NOTE_START, self.claude_md.read_text())

    def test_interactive_decline(self):
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", return_value=""),
        ):
            rc, _, _ = self._run()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.claude_md.exists())


class ListFlagTests(InstallSkillTestBase):
    """`--list` enumerates bundled skills and their install targets without
    touching the filesystem. Closes #13."""

    def test_list_prints_bundled_skills_with_target_paths(self):
        rc, out, _ = self._run("--list")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertIn("Bundled skills", out)
        for name in BUNDLED_SKILLS:
            target = self.home / ".claude" / "skills" / name / "SKILL.md"
            # Each name must appear on the same line as its target path.
            lines = [ln for ln in out.splitlines() if name in ln]
            self.assertTrue(
                any(str(target) in ln for ln in lines),
                f"expected `{name}` and `{target}` on the same line; got:\n{out}",
            )
        # No filesystem writes.
        self.assertFalse((self.home / ".claude" / "skills").exists())

    def test_list_short_circuits_other_flags(self):
        # --list with --force is a no-op listing — must not crash, must not
        # write, must not consult --force / --only / --dry-run.
        rc, out, _ = self._run("--list", "--force", "--dry-run")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertIn("Bundled skills", out)
        self.assertFalse((self.home / ".claude" / "skills").exists())


class DryRunTests(InstallSkillTestBase):
    def test_dry_run_prints_all_destinations(self):
        rc, out, _ = self._run("--dry-run")
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(self.target.exists())
        self.assertFalse(self.plan_target.exists())
        self.assertFalse(self.brainstorm_target.exists())
        self.assertFalse(self.monitor_target.exists())
        self.assertFalse(self.clu_plan_target.exists())
        self.assertIn(str(self.target), out)
        self.assertIn(str(self.plan_target), out)
        self.assertIn(str(self.brainstorm_target), out)
        self.assertIn(str(self.monitor_target), out)
        self.assertIn(str(self.clu_plan_target), out)
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
