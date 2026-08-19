"""`skill_sync` — placement, content, and provenance classification.

Table-driven over the five `placement` values, the three `content` values and
the three `provenance` values, under the harness `HOME` (`CluTestCase` points
`Path.home()` at a temp dir). Includes the three cases the old fused doctor
check silently mis-reported: a dangling symlink (read as "not installed"), a
real file under a symlinked parent (read as writable), and a file whose read
raises (read as in sync) — plus the two provenance sources (clu's install
record and the shipped fingerprint manifest) and the git-backed check that the
manifest holds CONTENT hashes rather than git blob ids.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from unittest import mock, skipIf

from end_of_line import db, skill_sync
from end_of_line._xdg_guard import clu_config_dir
from end_of_line.cli import BUNDLED_SKILLS
from tests import CluTestCase

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
REPO_ROOT = Path(__file__).resolve().parents[1]


def bundled_bytes(name: str) -> bytes:
    return files("end_of_line").joinpath(f"skills/{name}/SKILL.md").read_bytes()


class SkillHomeTestCase(CluTestCase):
    """A resolved temp `HOME` with an empty `~/.claude/skills/`.

    Shared by the scan and provenance suites: both need a home whose path
    carries no symlinks of its own, and both install SKILL.md fixtures into it.
    Holds no tests.
    """

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

    def _status(self, name: str) -> skill_sync.SkillStatus:
        found = [s for s in skill_sync.scan([name]) if s.name == name]
        self.assertEqual(len(found), 1, f"scan() returned {len(found)} records for {name}")
        return found[0]


class SkillSyncScanTest(SkillHomeTestCase):
    """One `SkillStatus` per skill, decided from the filesystem alone."""

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


class ProvenanceTest(SkillHomeTestCase):
    """`provenance` — did clu write these bytes, or did somebody else?

    Recognition is a MEMBERSHIP test over two sources, never an equality test
    against one: clu's own install record (the sidecar under
    `clu_config_dir()`) and the shipped manifest of fingerprints of every
    SKILL.md version clu has released. A copy installed by an older clu has no
    record entry and is recognized only by the manifest; a copy installed by a
    newer clu than the manifest knows about is recognized only by the record.
    """

    def test_absent_install_reports_absent_provenance_and_unknown_content(self):
        # Nothing installed: there are no bytes to attribute. `provenance` and
        # `content` are pinned together here so the pairing is deliberate —
        # every no-bytes placement reports ("absent", "unknown").
        s = self._status("clu-phase")
        self.assertEqual(s.provenance, "absent")
        self.assertEqual(s.content, "unknown")

    def test_unreadable_bytes_report_absent_provenance(self):
        # A dangling symlink is "installed" in the loosest sense, but there is
        # nothing to hash, so it cannot be attributed to anyone. Reporting it
        # `foreign` would claim knowledge scan() does not have.
        target = self.skills / "clu-plan" / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(self.elsewhere / "gone" / "SKILL.md")
        s = self._status("clu-plan")
        self.assertEqual(s.placement, "broken")
        self.assertEqual(s.provenance, "absent")

    def test_copy_matching_the_install_record_is_recognized(self):
        # Input: bytes clu wrote earlier and recorded. Nothing in the manifest
        # matches them — the record alone is enough.
        body = b"# a clu-reply from a clu newer than the manifest\n"
        self._install_file("clu-reply", body)
        skill_sync.record_install("clu-reply", skill_sync.digest(body))
        self.assertEqual(self._status("clu-reply").provenance, "recognized")

    def test_copy_matching_an_older_shipped_fingerprint_is_recognized(self):
        # Input: bytes clu shipped in some earlier version, installed before
        # the sidecar existed, so the record is empty. This is the migration
        # case the manifest exists for.
        body = b"# the clu-reply skill as clu shipped it two releases ago\n"
        self._install_file("clu-reply", body)
        self.assertEqual(skill_sync.installed_record(), {})
        with mock.patch.object(
            skill_sync,
            "shipped_fingerprints",
            return_value={"clu-reply": [skill_sync.digest(body)]},
        ):
            self.assertEqual(self._status("clu-reply").provenance, "recognized")

    def test_copy_matching_neither_source_is_foreign(self):
        # Input: bytes nobody shipped — a hand edit.
        self._install_file("clu-reply", b"# my own notes bolted onto clu-reply\n")
        self.assertEqual(self._status("clu-reply").provenance, "foreign")

    def test_one_edited_byte_makes_a_recognized_copy_foreign(self):
        body = b"# clu wrote this\n"
        self._install_file("clu-reply", body)
        skill_sync.record_install("clu-reply", skill_sync.digest(body))
        self.assertEqual(self._status("clu-reply").provenance, "recognized")

        self._install_file("clu-reply", body[:-1] + b"!\n")

        self.assertEqual(self._status("clu-reply").provenance, "foreign")

    def test_a_fingerprint_recorded_for_one_skill_does_not_recognize_another(self):
        # The record and the manifest are both keyed by skill NAME. A global
        # hash-set membership test would recognize a clu-reply copy dropped
        # into clu-phase's directory.
        body = b"# shared bytes\n"
        skill_sync.record_install("clu-reply", skill_sync.digest(body))
        self._install_file("clu-phase", body)
        self.assertEqual(self._status("clu-phase").provenance, "foreign")

    def test_missing_manifest_degrades_to_the_record_alone(self):
        # A wheel built without the manifest in package-data, or a clu older
        # than the manifest. scan() must still classify, not raise.
        recorded = b"# clu wrote this one\n"
        self._install_file("clu-reply", recorded)
        skill_sync.record_install("clu-reply", skill_sync.digest(recorded))
        self._install_file("clu-phase", b"# and this one clu did not\n")

        with mock.patch.object(skill_sync, "MANIFEST_FILENAME", "no-such-manifest.json"):
            self.assertEqual(skill_sync.shipped_fingerprints(), {})
            self.assertEqual(self._status("clu-reply").provenance, "recognized")
            self.assertEqual(self._status("clu-phase").provenance, "foreign")

    def test_current_bundled_copy_is_recognized_with_an_empty_record(self):
        # The shipped manifest carries the current version, so a fresh install
        # from an older clu — or one whose sidecar was deleted — is still ours.
        self._install_file("clu-reply", bundled_bytes("clu-reply"))
        self.assertEqual(skill_sync.installed_record(), {})
        s = self._status("clu-reply")
        self.assertEqual(s.content, "in_sync")
        self.assertEqual(s.provenance, "recognized")

    def test_every_scanned_skill_carries_a_provenance(self):
        self.assertTrue(all(s.provenance for s in skill_sync.scan()))


class InstallRecordTest(CluTestCase):
    """The install receipt, in the host database's `skills` table."""

    def test_record_install_is_readable_back(self):
        skill_sync.record_install("clu-plan", "a" * 64)
        self.assertEqual(skill_sync.installed_record(), {"clu-plan": "a" * 64})

    def test_record_install_accumulates_across_skills_and_overwrites_per_skill(self):
        skill_sync.record_install("clu-plan", "a" * 64)
        skill_sync.record_install("clu-phase", "b" * 64)
        skill_sync.record_install("clu-plan", "c" * 64)
        self.assertEqual(
            skill_sync.installed_record(),
            {"clu-plan": "c" * 64, "clu-phase": "b" * 64},
        )

    def test_installed_record_is_empty_when_nothing_was_recorded(self):
        self.assertFalse(db.host_db_path().exists())
        self.assertEqual(skill_sync.installed_record(), {})

    def test_an_unreadable_store_reads_as_empty_rather_than_raising(self):
        # Recognition degrades to the manifest alone; doctor keeps running.
        path = db.host_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not a database")
        self.assertEqual(skill_sync.installed_record(), {})

    def test_a_newer_schema_is_skipped_not_misread(self):
        # Upstream decision #6: a store a newer clu wrote is skipped, never
        # read optimistically.
        skill_sync.record_install("clu-plan", "a" * 64)
        conn = sqlite3.connect(str(db.host_db_path()))
        conn.execute(f"PRAGMA user_version = {db.HOST_SCHEMA_VERSION + 1}")
        conn.close()
        self.assertEqual(skill_sync.installed_record(), {})

    def test_the_receipt_lives_beside_the_registry_not_inside_a_skill(self):
        # A per-install stamp inside a SKILL.md would make the installed copy
        # differ from the bundled one by construction, so every skill would
        # report drift forever.
        skill_sync.record_install("clu-plan", "a" * 64)
        self.assertEqual(db.host_db_path().parent, clu_config_dir())
        self.assertTrue(db.host_db_path().exists())
        self.assertFalse((Path.home() / ".claude" / "skills").exists())


class ShippedManifestTest(CluTestCase):
    """`skills_manifest.json` — the fingerprint history that ships in the wheel."""

    def test_manifest_covers_exactly_the_bundled_skills(self):
        self.assertEqual(sorted(skill_sync.shipped_fingerprints()), sorted(BUNDLED_SKILLS))

    def test_every_skill_has_at_least_one_fingerprint(self):
        # A skill with an empty list is a generation bug, not an empty history:
        # every bundled skill was committed at least once. `audit-skill` has
        # the shortest history (one commit) and is the case most likely to look
        # like a bug while being correct.
        for name, hashes in skill_sync.shipped_fingerprints().items():
            self.assertGreater(len(hashes), 0, f"{name} has no shipped fingerprints")

    def test_fingerprints_are_lowercase_sha256_hex_and_deduped(self):
        for name, hashes in skill_sync.shipped_fingerprints().items():
            self.assertEqual(len(hashes), len(set(hashes)), f"{name} has duplicates")
            for h in hashes:
                self.assertRegex(h, r"^[0-9a-f]{64}$", f"{name}: {h!r}")

    def test_the_current_bundled_version_of_every_skill_is_in_the_manifest(self):
        # THE currency guard. When a SKILL.md changes and the manifest is not
        # regenerated, the version just shipped is in no manifest and every
        # user who installs it reads as foreign — silently, forever. This test
        # is what makes the generator get re-run.
        manifest = skill_sync.shipped_fingerprints()
        for name in BUNDLED_SKILLS:
            current = skill_sync.digest(bundled_bytes(name))
            self.assertIn(
                current,
                manifest.get(name, []),
                f"{name}'s current SKILL.md is absent from skills_manifest.json — "
                f"run `python3 scripts/gen_skill_manifest.py` and commit the result",
            )


@skipIf(not (REPO_ROOT / ".git").exists(), "manifest history needs a git checkout")
class ShippedManifestAgainstGitTest(SkillHomeTestCase):
    """The manifest against real git history — the end-to-end recognition path.

    The failure this guards is silent: hashing the git BLOB ID instead of the
    file CONTENT produces a well-formed manifest in which nothing ever matches,
    so every installed copy reads as foreign and the feature does nothing.
    """

    SKILL = "clu-reply"

    def _committed_bytes(self, rev: str) -> bytes:
        path = f"end_of_line/skills/{self.SKILL}/SKILL.md"
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{rev}:{path}"],
            capture_output=True,
            check=True,
        )
        return out.stdout

    def _oldest_rev(self) -> str:
        path = f"end_of_line/skills/{self.SKILL}/SKILL.md"
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "log", "--format=%H", "--", path],
            capture_output=True,
            text=True,
            check=True,
        )
        revs = out.stdout.split()
        self.assertGreater(len(revs), 1, f"{self.SKILL} needs >1 committed version")
        return revs[-1]

    def test_an_older_committed_version_is_in_the_manifest_by_content_hash(self):
        old = self._committed_bytes(self._oldest_rev())
        self.assertIn(
            skill_sync.digest(old),
            skill_sync.shipped_fingerprints()[self.SKILL],
        )

    def test_the_manifest_holds_no_git_blob_ids(self):
        # `git hash-object` prepends "blob <len>\0" before hashing, so a blob
        # id never equals a hash taken of the same bytes on disk. If the
        # generator recorded blob ids, they would be in here (and nothing
        # else would ever match).
        blob = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "rev-parse",
                f"{self._oldest_rev()}:end_of_line/skills/{self.SKILL}/SKILL.md",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertNotIn(blob, skill_sync.shipped_fingerprints()[self.SKILL])

    def test_an_older_shipped_version_installs_as_recognized_and_an_edit_as_foreign(self):
        old = self._committed_bytes(self._oldest_rev())
        self._install_file(self.SKILL, old)

        recognized = self._status(self.SKILL)

        self.assertEqual(recognized.content, "differs")
        self.assertEqual(recognized.provenance, "recognized")

        self._install_file(self.SKILL, old + b"# and one line of my own\n")

        edited = self._status(self.SKILL)

        self.assertEqual(edited.content, "differs")
        self.assertEqual(edited.provenance, "foreign")


class ManifestHealthTest(SkillHomeTestCase):
    """Absent and unreadable are different conditions and must not collapse.

    A missing manifest is the documented degradation. A corrupt one is a
    defect that fails in the worst direction: every copy clu wrote reads as
    `foreign`, so doctor calls the operator's untouched file a local edit and
    repair declines to touch it. Silent unless something says so.
    """

    def _manifest(self) -> Path:
        return Path(str(files("end_of_line").joinpath(skill_sync.MANIFEST_FILENAME)))

    def _swap_manifest(self, text: str) -> None:
        path = self._manifest()
        original = path.read_bytes()
        self.addCleanup(path.write_bytes, original)
        path.write_text(text)

    def test_healthy_manifest_reports_no_problem(self):
        fingerprints, problem = skill_sync.load_manifest()

        self.assertIsNone(problem)
        self.assertTrue(fingerprints)

    def test_corrupt_manifest_reports_a_reason_and_no_fingerprints(self):
        self._swap_manifest("{ not json at all")

        fingerprints, problem = skill_sync.load_manifest()

        self.assertEqual(fingerprints, {})
        self.assertIsNotNone(problem)
        self.assertIn("JSON", str(problem))

    def test_non_object_manifest_reports_a_reason(self):
        self._swap_manifest("[]")

        _, problem = skill_sync.load_manifest()

        self.assertIsNotNone(problem)
        self.assertIn("object", str(problem))

    def test_one_bad_entry_is_named_and_the_rest_survive(self):
        self._swap_manifest(json.dumps({"clu-phase": ["a" * 64], "clu-plan": "not-a-list"}))

        fingerprints, problem = skill_sync.load_manifest()

        self.assertIn("clu-phase", fingerprints)
        self.assertNotIn("clu-plan", fingerprints)
        self.assertIsNotNone(problem)
        self.assertIn("clu-plan", str(problem))

    def test_corrupt_manifest_turns_a_clu_written_copy_foreign(self):
        # The consequence the reason string exists to explain: without the
        # manifest, an untouched copy clu installed reads as somebody's edit.
        self._install_file("clu-phase", skill_sync.bundled_bytes("clu-phase"))
        self.assertEqual(skill_sync.scan(["clu-phase"])[0].provenance, "recognized")

        self._swap_manifest("{ not json at all")

        self.assertEqual(skill_sync.scan(["clu-phase"])[0].provenance, "foreign")



class SkillRepairTest(SkillHomeTestCase):
    """`repair()` — the write path, gated on `recognized` AND `writable`.

    The fixture discipline here is the whole point. Arbitrary stale bytes
    classify `foreign`, which is the copy repair must LEAVE ALONE — so a
    "stale but repairable" fixture has to be RECORDED as clu's own write, not
    merely written. A repair test built by writing bytes alone asserts the
    opposite of what it means to, and passes green while proving nothing.
    """

    def _install_recognized(self, name: str, content: bytes) -> Path:
        """Install `content` and record it as clu's own — the repairable shape.

        Asserts the provenance BEFORE handing the fixture back, so a fixture
        that silently drifted to `foreign` fails here rather than passing a
        repair assertion by exercising the leave-alone path.
        """
        target = self._install_file(name, content)
        skill_sync.record_install(name, skill_sync.digest(content))
        self.assertEqual(
            self._status(name).provenance,
            "recognized",
            f"{name} fixture is not repairable — repair would leave it alone",
        )
        return target

    # --- the repairable case ---------------------------------------------

    def test_recognized_stale_copy_is_brought_current(self):
        target = self._install_recognized("clu-reply", b"# stale, but clu wrote it\n")

        before = self._status("clu-reply")
        self.assertEqual(before.provenance, "recognized")
        self.assertEqual(before.content, "differs")
        self.assertTrue(before.writable)

        result = skill_sync.repair()

        self.assertEqual(result.updated, ["clu-reply"])
        self.assertEqual(result.refused, [])
        self.assertEqual(target.read_bytes(), bundled_bytes("clu-reply"))

    def test_the_repaired_copy_is_recognized_next_time(self):
        # Without the record, the bytes repair just wrote would read as
        # `foreign` on the next scan the moment the bundle moves ahead of the
        # manifest — clu calling its own write somebody's edit.
        self._install_recognized("clu-reply", b"# stale, but clu wrote it\n")

        skill_sync.repair()

        self.assertEqual(
            skill_sync.installed_record()["clu-reply"],
            skill_sync.digest(bundled_bytes("clu-reply")),
        )
        after = self._status("clu-reply")
        self.assertEqual(after.content, "in_sync")
        self.assertEqual(after.provenance, "recognized")

    def test_repair_is_idempotent(self):
        self._install_recognized("clu-reply", b"# stale, but clu wrote it\n")
        self.assertEqual(skill_sync.repair().updated, ["clu-reply"])

        again = skill_sync.repair()

        self.assertEqual(again.updated, [])
        self.assertEqual(again.refused, [])

    def test_a_vendored_skill_is_not_excluded_from_the_write_by_name(self):
        # VENDORED_SKILLS is an ownership signal for REPORTING, never a write
        # guard: `plan` and `brainstorm` are protected by being symlinked, and
        # a recognized regular-file copy of either is clu's own to refresh.
        self._install_recognized("plan", b"# a clu-shipped /plan, since superseded\n")

        self.assertEqual(skill_sync.repair().updated, ["plan"])

    def test_names_limits_what_repair_touches(self):
        self._install_recognized("clu-reply", b"# stale clu-reply\n")
        untouched = self._install_recognized("clu-phase", b"# stale clu-phase\n")

        result = skill_sync.repair(["clu-reply"])

        self.assertEqual(result.updated, ["clu-reply"])
        self.assertEqual(untouched.read_bytes(), b"# stale clu-phase\n")

    # --- the leave-alone cases -------------------------------------------

    def test_foreign_copy_is_untouched_and_reported(self):
        body = b"# my own notes bolted onto clu-reply\n"
        target = self._install_file("clu-reply", body)
        self.assertEqual(self._status("clu-reply").provenance, "foreign")

        result = skill_sync.repair()

        self.assertEqual(result.updated, [])
        self.assertEqual(result.refused, [("clu-reply", "foreign")])
        self.assertEqual(target.read_bytes(), body)

    def test_symlinked_parent_directory_is_refused_and_the_real_file_untouched(self):
        # The real-machine shape: the skill DIRECTORY is the symlink, so the
        # leaf is a regular file and `placement` reads "file". A guard keyed
        # on placement never fires here; only `writable` does.
        real_dir = self.elsewhere / "clu-reply-dir"
        real_dir.mkdir()
        real = real_dir / "SKILL.md"
        body = b"# stale bytes clu itself wrote, in the operator's checkout\n"
        real.write_bytes(body)
        (self.skills / "clu-reply").symlink_to(real_dir, target_is_directory=True)
        skill_sync.record_install("clu-reply", skill_sync.digest(body))

        before = self._status("clu-reply")
        self.assertEqual(before.placement, "file")
        self.assertNotEqual(before.placement, "link")
        self.assertFalse(before.writable)
        self.assertEqual(before.provenance, "recognized")
        self.assertEqual(before.content, "differs")

        result = skill_sync.repair()

        self.assertEqual(result.updated, [])
        self.assertEqual(result.refused, [("clu-reply", "symlink")])
        self.assertEqual(real.read_bytes(), body)
        # No temp file either: `mkstemp(dir=...)` inside the symlinked parent
        # would already have written into the directory clu is refusing.
        self.assertEqual(sorted(p.name for p in real_dir.iterdir()), ["SKILL.md"])

    def test_symlinked_skill_md_is_refused_and_its_destination_untouched(self):
        real = self.elsewhere / "clu-reply-SKILL.md"
        body = b"# stale bytes clu wrote, reached through a link\n"
        real.write_bytes(body)
        target = self.skills / "clu-reply" / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(real)
        skill_sync.record_install("clu-reply", skill_sync.digest(body))
        self.assertEqual(self._status("clu-reply").placement, "link")

        result = skill_sync.repair()

        self.assertEqual(result.refused, [("clu-reply", "symlink")])
        self.assertEqual(real.read_bytes(), body)
        self.assertTrue(target.is_symlink())

    def test_dangling_symlink_is_refused(self):
        target = self.skills / "clu-reply" / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(self.elsewhere / "gone" / "SKILL.md")

        result = skill_sync.repair()

        self.assertEqual(result.updated, [])
        self.assertEqual(result.refused, [("clu-reply", "symlink")])
        self.assertTrue(target.is_symlink())
        self.assertFalse(target.exists())

    @skipIf(IS_ROOT, "root reads mode-000 files")
    def test_unreadable_install_is_refused(self):
        target = self._install_file("clu-reply", b"# unreadable\n")
        target.chmod(0o000)
        self.addCleanup(target.chmod, 0o644)

        result = skill_sync.repair()

        self.assertEqual(result.updated, [])
        self.assertEqual(result.refused, [("clu-reply", "unreadable")])

    def test_a_skill_that_is_not_installed_is_neither_written_nor_reported(self):
        # Repair refreshes what is installed; it is not an installer.
        result = skill_sync.repair()

        self.assertEqual(result.updated, [])
        self.assertEqual(result.refused, [])
        self.assertFalse((self.home / ".claude" / "skills" / "clu-reply").exists())

    def test_an_in_sync_copy_is_silent(self):
        self._install_file("clu-reply", bundled_bytes("clu-reply"))

        result = skill_sync.repair()

        self.assertEqual(result.updated, [])
        self.assertEqual(result.refused, [])

    def test_an_unwritable_but_in_sync_copy_is_not_reported(self):
        # Nothing is being skipped: the bytes already match. Reporting it
        # would put a line on every run, which is the noise the one-line
        # summary exists to avoid.
        real_dir = self.elsewhere / "clu-reply-dir"
        real_dir.mkdir()
        (real_dir / "SKILL.md").write_bytes(bundled_bytes("clu-reply"))
        (self.skills / "clu-reply").symlink_to(real_dir, target_is_directory=True)
        self.assertFalse(self._status("clu-reply").writable)

        result = skill_sync.repair()

        self.assertEqual(result.refused, [])

    # --- write mechanics --------------------------------------------------

    def test_a_write_that_fails_before_replace_leaves_the_original_intact(self):
        body = b"# stale, but clu wrote it\n"
        target = self._install_recognized("clu-reply", body)

        with mock.patch.object(skill_sync.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                skill_sync.repair(["clu-reply"])

        self.assertEqual(target.read_bytes(), body)
        # And the temp file is cleaned up rather than left beside the skill.
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["SKILL.md"])

    def test_a_failed_write_still_records_the_skills_already_written(self):
        # The sidecar is batched, so a raise partway through must not lose the
        # record of what already landed — an unrecorded write reads as
        # `foreign` next time and repair then refuses it forever.
        self._install_recognized("clu-monitor", b"# stale clu-monitor\n")
        self._install_recognized("clu-reply", b"# stale clu-reply\n")
        real_replace = skill_sync.os.replace
        calls: list[int] = []

        def fail_on_the_second(src, dst):
            calls.append(1)
            if len(calls) > 1:
                raise OSError("boom")
            return real_replace(src, dst)

        with mock.patch.object(skill_sync.os, "replace", side_effect=fail_on_the_second):
            with self.assertRaises(OSError):
                skill_sync.repair()

        self.assertIn("clu-monitor", skill_sync.installed_record())

    def test_the_receipt_is_written_in_one_transaction_for_a_multi_skill_repair(self):
        # `record_install` takes the write lock per call. That is fine for the
        # rare explicit install and not fine on a path that now runs at every
        # `clu init` and `clu queue add`.
        self._install_recognized("clu-monitor", b"# stale clu-monitor\n")
        self._install_recognized("clu-reply", b"# stale clu-reply\n")
        real_write_txn = skill_sync.db.write_txn
        opened: list[float | None] = []

        def spy(conn, **kw):
            opened.append(kw.get("timeout_s"))
            return real_write_txn(conn, **kw)

        with mock.patch.object(skill_sync.db, "write_txn", side_effect=spy):
            result = skill_sync.repair()

        self.assertEqual(result.updated, ["clu-monitor", "clu-reply"])
        self.assertEqual(len(opened), 1)
        self.assertEqual(
            skill_sync.installed_record()["clu-monitor"],
            skill_sync.digest(skill_sync.bundled_bytes("clu-monitor")),
        )

    def test_a_target_that_becomes_unwritable_between_scan_and_write_is_refused(self):
        # `writable` is computed once at scan time and the filesystem can
        # change before the write. The re-check must precede `mkstemp`, not
        # only `os.replace`: mkstemp with `dir=` inside a symlinked parent has
        # already written into the directory clu was refusing to touch.
        real_dir = self.elsewhere / "clu-reply-dir"
        real_dir.mkdir()
        body = b"# stale bytes clu wrote\n"
        (real_dir / "SKILL.md").write_bytes(body)
        (self.skills / "clu-reply").symlink_to(real_dir, target_is_directory=True)
        skill_sync.record_install("clu-reply", skill_sync.digest(body))
        stale = dataclasses.replace(self._status("clu-reply"), writable=True)

        with mock.patch.object(skill_sync, "scan", return_value=[stale]):
            result = skill_sync.repair()

        self.assertEqual(result.updated, [])
        self.assertEqual(result.refused, [("clu-reply", "symlink")])
        self.assertEqual((real_dir / "SKILL.md").read_bytes(), body)
        self.assertEqual(sorted(p.name for p in real_dir.iterdir()), ["SKILL.md"])

    def test_repair_result_is_frozen(self):
        result = skill_sync.repair()
        with self.assertRaises(AttributeError):
            setattr(result, "updated", ["clu-reply"])

    def test_the_unresolved_harness_home_would_make_every_skill_unrepairable(self):
        # The evidence behind `SkillHomeTestCase`'s resolve, stated as a test.
        # On macOS $TMPDIR sits under /var — itself a symlink to /private/var —
        # so the raw harness home carries a symlink on its path to the root and
        # every target under it reads writable=False. A repair suite that
        # skipped the resolve would assert over a repair that never ran.
        unresolved = self.tmp_path / "home"
        if unresolved == self.home:
            self.skipTest("$TMPDIR carries no symlink on this platform")
        self._install_recognized("clu-reply", b"# stale, but clu wrote it\n")

        with mock.patch.dict(os.environ, {"HOME": str(unresolved)}):
            self.assertFalse(self._status("clu-reply").writable)
            self.assertEqual(skill_sync.repair().updated, [])

        self.assertTrue(self._status("clu-reply").writable)
        self.assertEqual(skill_sync.repair().updated, ["clu-reply"])


class RecordLockContentionTest(SkillHomeTestCase):
    """The receipt's wait is bounded, and both call sites survive the timeout.

    `repair()` runs on `clu init` and `clu queue add`, so an unbounded wait
    turns a stuck writer into a hung operator command with no output. `DbBusy`
    is a RuntimeError, not an OSError, so a guard on OSError alone would
    convert that hang into a crash rather than into a reported degradation.
    """

    @contextmanager
    def _write_lock_held(self):
        """Hold the host DB's write lock from a second connection."""
        db.host_db_path().parent.mkdir(parents=True, exist_ok=True)
        holder = db.connect(db.host_db_path())
        db.ensure_host_schema(holder)
        holder.execute("BEGIN IMMEDIATE")
        try:
            yield
        finally:
            holder.execute("ROLLBACK")
            holder.close()

    def test_db_busy_is_not_an_oserror(self):
        # Pins the fact both call sites' except clauses depend on.
        self.assertFalse(issubclass(db.DbBusy, OSError))

    def test_record_installs_gives_up_rather_than_hanging(self):
        started = time.monotonic()

        with self._write_lock_held():
            with self.assertRaises(db.DbBusy):
                with mock.patch.object(skill_sync, "RECORD_LOCK_TIMEOUT_S", 0.3):
                    skill_sync.record_install("clu-phase", "a" * 64)

        self.assertLess(time.monotonic() - started, 10.0)

    def test_repair_surfaces_the_timeout_rather_than_blocking(self):
        # A recognized-but-stale fixture, inline: installed AND recorded, so
        # it is the repairable case rather than the leave-alone one.
        body = b"# an older copy clu itself wrote\n"
        self._install_file("clu-phase", body)
        skill_sync.record_install("clu-phase", skill_sync.digest(body))
        self.assertEqual(skill_sync.scan(["clu-phase"])[0].provenance, "recognized")

        with self._write_lock_held():
            with mock.patch.object(skill_sync, "RECORD_LOCK_TIMEOUT_S", 0.3):
                with self.assertRaises(db.DbBusy):
                    skill_sync.repair(["clu-phase"])

        # The write itself still landed — repair records on the way out, and
        # the bytes now equal the bundle, which the manifest recognizes.
        self.assertEqual(
            skill_sync.installed_path("clu-phase").read_bytes(),
            skill_sync.bundled_bytes("clu-phase"),
        )
        self.assertEqual(skill_sync.scan(["clu-phase"])[0].provenance, "recognized")

