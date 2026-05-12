"""Phase `footer` tests: bare `clu` queue-footer hint.

Bare `clu` renders the fleet view (PLAN/STATUS/PHASE table) and now
appends a one-line footer summarizing pending queue work across all
registered projects. The footer is hidden when no project has a
non-empty queue, and surfaces unreadable queue files inline as a
prompt for the operator to investigate.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from end_of_line import queue, registry, state as st
from end_of_line.cli import main
from end_of_line.config import ProjectConfig
from tests import isolate_registry

_PLAN_BODY = "# placeholder plan\n"


class FleetFooterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        isolate_registry(self, self.tmp)

    def _seed_project(self, name: str, *, seed_slug: str = "seed-plan") -> Path:
        project = self.tmp / name
        project.mkdir()
        (project / "plans").mkdir()
        (project / "plans" / f"{seed_slug}.md").write_text(_PLAN_BODY)
        registry.register(project, seed_slug)
        return project

    def _write_queue(
        self, project: Path, slugs: list[str], history: list[dict] | None = None,
    ) -> Path:
        queue_path = ProjectConfig(project_root=project).queue_path()
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue.save_atomic(queue_path, {
            "schema_version": queue.SCHEMA_VERSION,
            "queue": [
                {
                    "slug": s, "added_at": st.utcnow(),
                    "added_by": "operator", "position_at_add": "tail",
                }
                for s in slugs
            ],
            "history": history or [],
        })
        return queue_path

    def _run(self, argv: list[str]) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        self.assertEqual(rc, 0, msg=f"non-zero rc; stdout was: {buf.getvalue()!r}")
        return buf.getvalue()

    def test_fleet_view_no_footer_when_no_queue_files(self) -> None:
        self._seed_project("alpha")
        out = self._run([])
        self.assertIn("seed-plan", out)
        self.assertNotIn("(queue:", out)

    def test_fleet_view_no_footer_when_all_queues_empty(self) -> None:
        project = self._seed_project("alpha")
        # Empty queue file present but no pending entries.
        self._write_queue(project, slugs=[])
        out = self._run([])
        self.assertIn("seed-plan", out)
        self.assertNotIn("(queue:", out)

    def test_fleet_view_footer_for_single_project_with_queue(self) -> None:
        project = self._seed_project("alpha")
        self._write_queue(project, slugs=["a", "b", "c"])
        out = self._run([])
        self.assertIn("(queue: 3 pending in ", out)
        self.assertIn(str(project), out)
        self.assertIn("clu queue list", out)

    def test_fleet_view_footer_for_multiple_projects_with_queues(self) -> None:
        a = self._seed_project("alpha")
        b = self._seed_project("beta")
        self._write_queue(a, slugs=["a1", "a2"])
        self._write_queue(b, slugs=["b1"])
        out = self._run([])
        self.assertIn("(queue: 3 pending across 2 projects", out)
        self.assertIn("--project <P>", out)

    def test_fleet_view_footer_skips_empty_projects_in_multi_count(self) -> None:
        # Two registered projects, only one has pending entries → single-project
        # rendering even though two projects are in the registry.
        a = self._seed_project("alpha")
        b = self._seed_project("beta")
        self._write_queue(a, slugs=["a1", "a2"])
        self._write_queue(b, slugs=[])
        out = self._run([])
        self.assertIn("(queue: 2 pending in ", out)
        self.assertIn(str(a), out)
        # Multi-project rendering must NOT fire.
        self.assertNotIn("across", out)

    def test_fleet_view_footer_dedups_multiple_plans_per_project(self) -> None:
        # registry.entries() yields one row per (project, plan); the footer
        # iterates distinct project_roots, not registry rows.
        project = self._seed_project("alpha")
        (project / "plans" / "second-plan.md").write_text(_PLAN_BODY)
        registry.register(project, "second-plan")
        self._write_queue(project, slugs=["x"])
        out = self._run([])
        self.assertIn("(queue: 1 pending in ", out)
        self.assertNotIn("across", out)

    def test_fleet_view_footer_skips_unreadable_queue(self) -> None:
        project = self._seed_project("alpha")
        queue_path = ProjectConfig(project_root=project).queue_path()
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text("{not valid json")
        out = self._run([])
        self.assertIn("unreadable", out)
        # Footer still renders even when a queue is unparseable.
        self.assertIn("(", out)

    def test_fleet_view_footer_unreadable_does_not_break_counts(self) -> None:
        # One readable + one corrupt → readable count surfaces; corrupt is
        # mentioned separately.
        a = self._seed_project("alpha")
        b = self._seed_project("beta")
        self._write_queue(a, slugs=["a1", "a2"])
        corrupt_path = ProjectConfig(project_root=b).queue_path()
        corrupt_path.parent.mkdir(parents=True, exist_ok=True)
        corrupt_path.write_text("garbage")
        out = self._run([])
        self.assertIn("2 pending", out)
        self.assertIn("unreadable", out)


if __name__ == "__main__":
    unittest.main()
