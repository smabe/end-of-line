"""`clu unregister --all-archived` — batch prune ghost registry entries.

Closes #12. The flag walks `registry.entries()`, identifies entries whose
master plan file no longer exists, and removes them under one registry
mutate window. `--dry-run` prints without mutating. Entries with
malformed `.orchestrator.json` are surfaced as "skipped" and NOT
auto-removed (operator decides).
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import registry
from end_of_line.cli import ExitCode, main
from tests import isolate_registry


def _register_with_plan(project: Path, slug: str, write_master: bool = True) -> None:
    """Register `slug` against `project` and optionally write its master file."""
    (project / "plans").mkdir(parents=True, exist_ok=True)
    if write_master:
        (project / "plans" / f"{slug}.md").write_text("# placeholder\n")
    registry.register(project, slug)


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class UnregisterAllArchivedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        isolate_registry(self, self.tmp)

    def _slugs(self) -> set[str]:
        return {e.plan_slug for e in registry.entries()}

    def test_all_archived_removes_entries_with_missing_master_files(self) -> None:
        project = self.tmp / "proj"
        project.mkdir()
        _register_with_plan(project, "kept-plan", write_master=True)
        _register_with_plan(project, "ghost-plan", write_master=False)

        rc, out, _ = _run("unregister", "--all-archived")

        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._slugs(), {"kept-plan"})
        self.assertIn("ghost-plan", out)
        self.assertNotIn("kept-plan", out)

    def test_all_archived_keeps_entries_with_present_master_files(self) -> None:
        project = self.tmp / "proj"
        project.mkdir()
        _register_with_plan(project, "alpha")
        _register_with_plan(project, "beta")

        rc, out, _ = _run("unregister", "--all-archived")

        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._slugs(), {"alpha", "beta"})
        self.assertIn("nothing to unregister", out.lower())

    def test_all_archived_dry_run_does_not_mutate(self) -> None:
        project = self.tmp / "proj"
        project.mkdir()
        _register_with_plan(project, "kept-plan", write_master=True)
        _register_with_plan(project, "ghost-plan", write_master=False)

        rc, out, _ = _run("unregister", "--all-archived", "--dry-run")

        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._slugs(), {"kept-plan", "ghost-plan"})
        self.assertIn("Would unregister", out)
        self.assertIn("ghost-plan", out)

    def test_all_archived_handles_malformed_orchestrator_json(self) -> None:
        project = self.tmp / "proj"
        project.mkdir()
        _register_with_plan(project, "needs-config", write_master=False)
        (project / ".orchestrator.json").write_text("{ not json")

        rc, out, _ = _run("unregister", "--all-archived")

        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._slugs(), {"needs-config"})
        self.assertIn("skipped", out.lower())
        self.assertIn("needs-config", out)

    def test_all_archived_handles_missing_project_dir(self) -> None:
        project = self.tmp / "gone-project"
        project.mkdir()
        _register_with_plan(project, "doomed", write_master=False)
        # Wipe the project dir after registration — registry entry lingers.
        for child in project.iterdir():
            if child.is_dir():
                for sub in child.iterdir():
                    sub.unlink()
                child.rmdir()
            else:
                child.unlink()
        project.rmdir()

        rc, out, _ = _run("unregister", "--all-archived")

        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._slugs(), set())
        self.assertIn("doomed", out)

    def test_all_archived_with_plan_arg_rejected(self) -> None:
        project = self.tmp / "proj"
        project.mkdir()
        _register_with_plan(project, "anything")

        rc, _, err = _run(
            "unregister", "--all-archived",
            "--project", str(project), "--plan", "anything",
        )

        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("--all-archived", err)
        self.assertIn("--plan", err)
        self.assertEqual(self._slugs(), {"anything"})

    def test_unregister_per_plan_still_works(self) -> None:
        project = self.tmp / "proj"
        project.mkdir()
        _register_with_plan(project, "keep-me")
        _register_with_plan(project, "drop-me")

        rc, out, _ = _run(
            "unregister", "--project", str(project), "--plan", "drop-me",
        )

        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._slugs(), {"keep-me"})
        self.assertIn("Unregistered", out)

    def test_unregister_per_plan_requires_project_and_plan(self) -> None:
        rc, _, err = _run("unregister")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertTrue("--project" in err or "--plan" in err)

    def test_all_archived_empty_registry_is_ok(self) -> None:
        rc, out, _ = _run("unregister", "--all-archived")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("nothing to unregister", out.lower())


if __name__ == "__main__":
    unittest.main()
