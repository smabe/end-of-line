"""`clu migrate-archive` — one-shot migration helper from the flat
`plans/shipped/<file>.md` layout (pre-#65 auto-archive) to the
nested `plans/archive/<slug>/<file>.md` layout (#65 canonical).

Grouping rule: a file's slug is the longest stem `M` in `plans/shipped/`
such that the file equals `M.md` or starts with `M-`. Each group is
one `git mv` into `plans/archive/<slug>/`.
"""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line.cli import ExitCode, main
from tests import isolate_registry


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _write_tracked(repo: Path, rel: str, body: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    _git(repo, "add", rel)
    _git(repo, "commit", "-m", f"add {rel}")


class MigrateArchiveBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self._tmp.name)
        self.project = self.parent / "myrepo"
        self.project.mkdir()
        isolate_registry(self, self.parent)
        (self.project / "plans").mkdir()
        _git(self.project, "init", "-q")
        _git(self.project, "config", "user.email", "t@t")
        _git(self.project, "config", "user.name", "t")
        _git(self.project, "commit", "--allow-empty", "-m", "init")
        _git(self.project, "branch", "-M", "main")
        # `clu migrate-archive` loads project config, so seed it.
        (self.project / ".orchestrator.json").write_text("{}\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["migrate-archive", "--project", str(self.project), *extra])
        return rc, out.getvalue(), err.getvalue()


class TestMigrateArchive(MigrateArchiveBase):
    def test_noop_when_no_shipped_dir(self) -> None:
        rc, stdout, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("nothing to migrate", stdout.lower())

    def test_noop_when_empty_shipped_dir(self) -> None:
        (self.project / "plans" / "shipped").mkdir()
        rc, stdout, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("removed empty plans/shipped/", stdout)
        self.assertFalse((self.project / "plans" / "shipped").exists())

    def test_dry_run_keeps_empty_shipped_dir(self) -> None:
        (self.project / "plans" / "shipped").mkdir()
        rc, stdout, _ = self._run("--dry-run")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("dry-run", stdout)
        self.assertTrue((self.project / "plans" / "shipped").exists())

    def test_groups_master_and_sub_plans_under_slug(self) -> None:
        _write_tracked(self.project, "plans/shipped/alpha.md")
        _write_tracked(self.project, "plans/shipped/alpha-schema.md")
        _write_tracked(self.project, "plans/shipped/alpha-engine.md")
        rc, stdout, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        for name in ("alpha.md", "alpha-schema.md", "alpha-engine.md"):
            self.assertTrue(
                (self.project / "plans" / "archive" / "alpha" / name).exists(),
                f"expected plans/archive/alpha/{name}",
            )
        self.assertFalse((self.project / "plans" / "shipped").exists())
        self.assertIn("migrated 3", stdout.lower())

    def test_separates_unrelated_masters(self) -> None:
        # Two independent slugs (no shared prefix) → two archive subdirs.
        _write_tracked(self.project, "plans/shipped/alpha.md")
        _write_tracked(self.project, "plans/shipped/beta.md")
        _write_tracked(self.project, "plans/shipped/beta-phase-1.md")
        rc, _, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertTrue((self.project / "plans" / "archive" / "alpha" / "alpha.md").exists())
        self.assertTrue((self.project / "plans" / "archive" / "beta" / "beta.md").exists())
        self.assertTrue((self.project / "plans" / "archive" / "beta" / "beta-phase-1.md").exists())

    def test_overlapping_prefix_picks_longest_master(self) -> None:
        # `task-list-nesting.md` and `task-list-blocked.md` are independent
        # masters (no `task-list.md` exists in the set). Each becomes its
        # own slug subdir; neither absorbs the other.
        _write_tracked(self.project, "plans/shipped/task-list-nesting.md")
        _write_tracked(self.project, "plans/shipped/task-list-blocked.md")
        rc, _, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertTrue(
            (
                self.project / "plans" / "archive" / "task-list-nesting" / "task-list-nesting.md"
            ).exists()
        )
        self.assertTrue(
            (
                self.project / "plans" / "archive" / "task-list-blocked" / "task-list-blocked.md"
            ).exists()
        )

    def test_dry_run_previews_without_mutating(self) -> None:
        _write_tracked(self.project, "plans/shipped/alpha.md")
        _write_tracked(self.project, "plans/shipped/alpha-schema.md")
        rc, stdout, _ = self._run("--dry-run")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("would migrate", stdout.lower())
        self.assertIn("plans/archive/alpha", stdout)
        # No move actually happened.
        self.assertTrue((self.project / "plans" / "shipped" / "alpha.md").exists())
        self.assertFalse((self.project / "plans" / "archive").exists())

    def test_commits_moves_in_one_commit(self) -> None:
        _write_tracked(self.project, "plans/shipped/alpha.md")
        _write_tracked(self.project, "plans/shipped/alpha-schema.md")
        head_before = _git(self.project, "rev-parse", "HEAD").stdout.strip()
        rc, _, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        head_after = _git(self.project, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(head_before, head_after, "expected a new commit")
        msg = _git(self.project, "log", "-1", "--format=%s").stdout.strip()
        self.assertIn("migrate-archive", msg)
        # No staged or unstaged-tracked changes left (untracked test
        # scaffolding like the seeded `.orchestrator.json` is fine).
        status = _git(self.project, "status", "--porcelain").stdout
        dirty = [line for line in status.splitlines() if not line.startswith("??")]
        self.assertEqual(dirty, [], f"unexpected dirty state: {status!r}")


if __name__ == "__main__":
    unittest.main()
