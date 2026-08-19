"""`clu doctor` skill-drift guard (#75 phase 4).

A stale installed `~/.claude/skills/<name>/SKILL.md` is what shipped the pre-#72
heartbeat loop at the incident, and clu had no way to surface it. doctor reports
drift from `skill_sync.scan()` and formats it; the classification itself is
covered in tests/test_skill_sync.py. HOME is redirected by the harness
(`CluTestCase.setUp`) so we never read the real ~/.claude.

The second suite here covers the write path's CALL SITES — `clu init` and
`clu queue add` repairing what they can, staying quiet when there is nothing
to say, and a worker-mode `queue add` doing neither.
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from importlib.resources import files
from pathlib import Path
from unittest import mock, skipIf

from end_of_line import skill_sync
from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase, must, write_config

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

    def _install_as_clu(self, name: str, content: bytes) -> None:
        """Install `content` AND record it as clu's own write.

        A stale copy is only "drift clu can re-sync" when clu wrote it —
        anything else is somebody's edit and gets the foreign line instead. So
        a drift-section test has to state which of the two it is seeding.
        """
        self._install(name, content)
        skill_sync.record_install(name, skill_sync.digest(content))

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
        # A copy clu itself wrote, since left behind by the bundle.
        self._install_as_clu("clu-phase", b"# a stale, behind-the-bundle copy\n")
        out = self._doctor()
        self.assertIn("differ from the bundle", out)
        self.assertIn("clu-phase", out)

    def test_foreign_copy_is_reported_as_a_local_edit_not_as_drift(self):
        # Same difference from the bundle, different cause: these bytes match
        # no version clu ever shipped, so the `--force` re-sync instruction
        # would be an instruction to destroy the operator's edit.
        self._install("clu-phase", b"# my own edits on top of clu-phase\n")

        out = self._doctor()

        self.assertNotIn("differ from the bundle", out)
        self.assertIn("clu didn't write", out)
        self.assertIn("edited locally", out)
        self.assertIn("clu-phase", out)

    def test_recognized_and_foreign_get_different_lines(self):
        # The failure this guards: two different states producing one
        # indistinguishable message.
        self._install_as_clu("clu-phase", b"# stale but clu's own\n")
        self._install("clu-plan", b"# hand-edited by the operator\n")

        out = self._doctor()

        stale_section = out[out.index("differ from the bundle") : out.index("clu didn't write")]
        edited_section = out[out.index("clu didn't write") :]
        self.assertIn("clu-phase", stale_section)
        self.assertNotIn("clu-plan", stale_section)
        self.assertIn("clu-plan", edited_section)
        self.assertNotIn("clu-phase", edited_section)

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
        self._install_as_clu("clu-plan", b"# stale clu-plan\n")  # drifted
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
        # Nor in the foreign section: clu is not canonical for `plan`, so a
        # copy it never wrote is the expected steady state, not a finding.
        self.assertNotIn("clu didn't write", out)

    def test_vendored_differs_native_in_sync_is_quiet(self):
        # A differing vendored skill alongside an in-sync native skill produces
        # no drift section at all — the vendored difference is suppressed and the
        # native skill matches.
        self._install("plan", b"# operator's own /plan\n")  # vendored, differs
        self._install("clu-phase", self._bundled("clu-phase"))  # native, in sync
        out = self._doctor()
        self.assertNotIn("differ from the bundle", out)
        self.assertNotIn("clu didn't write", out)

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


class SkillRepairCallSiteTest(GitProjectTestCase):
    """`clu init` and `clu queue add` bring clu's own skills current.

    The two moments a person is present. Never at dispatch, never on the
    supervisor tick, and never from a worker — a repair firing there rewrites
    a skill under a running worker.
    """

    def setUp(self) -> None:
        super().setUp()
        # Resolved for the same reason as the class above: on macOS the raw
        # harness home carries a symlink on its path, which makes every skill
        # unwritable and every repair assertion vacuous.
        self.home = (self.tmp_path / "home").resolve()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.skills = self.home / ".claude" / "skills"
        self.skills.mkdir(parents=True, exist_ok=True)

    # --- fixtures ---------------------------------------------------------

    def _bundled(self, name: str) -> bytes:
        return files("end_of_line").joinpath(f"skills/{name}/SKILL.md").read_bytes()

    def _install(self, name: str, content: bytes) -> Path:
        target = self.skills / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def _install_recognized(self, name: str, content: bytes) -> Path:
        """A stale copy clu itself wrote — the only shape repair may replace.

        Recorded, not merely written: arbitrary stale bytes match no shipped
        fingerprint and classify `foreign`, which is the copy repair must
        leave alone. Asserted here so a drifting fixture fails at the fixture
        rather than passing a repair assertion from the leave-alone path.
        """
        target = self._install(name, content)
        skill_sync.record_install(name, skill_sync.digest(content))
        self.assertEqual(skill_sync.scan([name])[0].provenance, "recognized")
        return target

    def _plan(self, slug: str) -> None:
        (self.project / "plans" / f"{slug}.md").write_text("# placeholder\n")

    def _run(self, argv: list[str]) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        self.assertEqual(rc, ExitCode.OK, buf.getvalue())
        return buf.getvalue()

    def _init(self, slug: str) -> str:
        self._plan(slug)
        return self._run(["init", "--project", str(self.project), "--plan", slug])

    def _queue_add(self, slug: str) -> str:
        self._plan(slug)
        with mock.patch("end_of_line.cli._spawn_post_action_tick"):
            return self._run(["queue", "add", slug, "--project", str(self.project)])

    # --- init -------------------------------------------------------------

    def test_init_brings_a_stale_recognized_skill_current(self):
        target = self._install_recognized("clu-reply", b"# stale, but clu wrote it\n")

        out = self._init("second-plan")

        self.assertEqual(target.read_bytes(), self._bundled("clu-reply"))
        self.assertIn("skills: updated clu-reply", out)

    def test_a_second_init_is_silent_and_changes_nothing(self):
        # Idempotence from a KNOWN-STALE start: the first run must be seen to
        # act, or "both runs printed nothing" would satisfy the same words
        # while proving the feature never ran at all. A second `init` needs a
        # second plan slug — re-initializing one that exists refuses before
        # it reaches repair.
        target = self._install_recognized("clu-reply", b"# stale, but clu wrote it\n")
        self.assertIn("skills: updated clu-reply", self._init("second-plan"))
        after_first = target.read_bytes()

        out = self._init("third-plan")

        self.assertNotIn("skills:", out)
        self.assertEqual(target.read_bytes(), after_first)

    def test_init_is_silent_when_nothing_is_installed(self):
        out = self._init("second-plan")

        self.assertNotIn("skills:", out)

    # --- queue add --------------------------------------------------------

    def test_queue_add_brings_a_stale_recognized_skill_current(self):
        target = self._install_recognized("clu-reply", b"# stale, but clu wrote it\n")

        out = self._queue_add("next-plan")

        self.assertEqual(target.read_bytes(), self._bundled("clu-reply"))
        self.assertIn("skills: updated clu-reply", out)

    def test_queue_add_leaves_a_foreign_copy_alone_and_names_it(self):
        body = b"# my own edits on top of audit-skill\n"
        target = self._install("audit-skill", body)

        out = self._queue_add("next-plan")

        self.assertEqual(target.read_bytes(), body)
        self.assertIn("left alone: audit-skill (edited locally)", out)

    def test_updated_and_left_alone_share_one_line(self):
        self._install_recognized("clu-reply", b"# stale, but clu wrote it\n")
        self._install("audit-skill", b"# my own edits\n")

        out = self._queue_add("next-plan")

        line = must(
            next((ln for ln in out.splitlines() if ln.startswith("skills:")), None)
        )
        self.assertEqual(
            line,
            "skills: updated clu-reply · left alone: audit-skill (edited locally)",
        )

    def test_queue_add_repairs_before_the_detached_tick(self):
        # Ordering, not inspection: the detached tick can dispatch a worker
        # immediately, and a worker that starts before the repair reads the
        # stale skill. Everything looks correct afterwards, which is what
        # makes this worth pinning.
        self._install_recognized("clu-reply", b"# stale, but clu wrote it\n")
        order: list[str] = []
        self._plan("next-plan")

        with (
            mock.patch.object(
                skill_sync,
                "repair",
                side_effect=lambda *a, **k: (
                    order.append("repair"),
                    skill_sync.RepairResult(updated=[], refused=[]),
                )[1],
            ),
            mock.patch(
                "end_of_line.cli._spawn_post_action_tick",
                side_effect=lambda cfg: order.append("tick"),
            ),
        ):
            self._run(["queue", "add", "next-plan", "--project", str(self.project)])

        self.assertEqual(order, ["repair", "tick"])

    def test_worker_mode_queue_add_writes_nothing(self):
        # A worker calling `queue add` mid-plan must never rewrite the
        # operator's skills — the phase it is running may be the one that
        # edited them.
        body = b"# stale, but clu wrote it\n"
        target = self._install_recognized("clu-reply", body)
        token = self._claim()
        self._plan("chained-plan")

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(
                [
                    "queue",
                    "add",
                    "chained-plan",
                    "--project",
                    str(self.project),
                    "--token",
                    token,
                    "--plan",
                    "test-plan",
                    "--phase",
                    "a",
                ]
            )

        self.assertEqual(rc, ExitCode.OK, buf.getvalue())
        self.assertEqual(target.read_bytes(), body)
        self.assertNotIn("skills:", buf.getvalue())

    # --- ownership --------------------------------------------------------

    def test_a_symlinked_vendored_skill_is_not_reported(self):
        # `plan` and `brainstorm` are the two clu bundles without being
        # canonical for them, and the operator's own copy is normally a
        # symlink into their own checkout. Naming it on every `queue add`
        # would put a permanent line on a command that is supposed to be
        # quiet unless something happened.
        real = self.tmp_path / "operators-own-plan.md"
        real.write_bytes(b"# the operator's own richer /plan\n")
        (self.skills / "plan").mkdir(parents=True)
        (self.skills / "plan" / "SKILL.md").symlink_to(real)

        out = self._queue_add("next-plan")

        self.assertNotIn("skills:", out)
        self.assertEqual(real.read_bytes(), b"# the operator's own richer /plan\n")

    def test_a_symlinked_native_skill_is_named_as_refused(self):
        real = self.tmp_path / "elsewhere-clu-reply.md"
        real.write_bytes(b"# stale bytes clu wrote, reached through a link\n")
        skill_sync.record_install("clu-reply", skill_sync.digest(real.read_bytes()))
        (self.skills / "clu-reply").mkdir(parents=True)
        (self.skills / "clu-reply" / "SKILL.md").symlink_to(real)

        out = self._queue_add("next-plan")

        self.assertIn("left alone: clu-reply (symlink on its path)", out)
        self.assertEqual(real.read_bytes(), b"# stale bytes clu wrote, reached through a link\n")
