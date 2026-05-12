"""Phase `add` tests: `clu queue add` CLI subcommand.

All six exit paths plus --front, history-only re-add, running-slug re-add,
entry shape, and symlink path-resolution. Uses `isolate_queue` (registry +
per-project tmp paths) so the host registry is never touched.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from end_of_line import queue, registry
from end_of_line.cli import ExitCode, main
from end_of_line.config import ProjectConfig
from tests import isolate_queue

_PLAN_BODY = "# placeholder plan\n"


def _bootstrap(project: Path, slug: str = "seed-plan") -> None:
    """Register `slug` against `project` and write its plan file.

    `clu queue add` requires the project to be in the host registry (at
    least one row) before it accepts an enqueue. Tests that exercise the
    happy path pre-seed that row.
    """
    (project / "plans").mkdir(exist_ok=True)
    (project / "plans" / f"{slug}.md").write_text(_PLAN_BODY)
    registry.register(project, slug)


def _write_plan(project: Path, slug: str) -> Path:
    plans_dir = project / "plans"
    plans_dir.mkdir(exist_ok=True)
    path = plans_dir / f"{slug}.md"
    path.write_text(_PLAN_BODY)
    return path


class QueueAddTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        isolate_queue(self, self.project)
        self.queue_path = ProjectConfig(project_root=self.project).queue_path()

    # --- happy paths ---

    def test_add_success_appends_to_tail(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "new-plan")
        rc = main(["queue", "add", "new-plan", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        data = queue.load(self.queue_path)
        slugs = [e["slug"] for e in data["queue"]]
        self.assertEqual(slugs, ["new-plan"])

    def test_add_front_inserts_at_position_0(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "a")
        _write_plan(self.project, "b")
        _write_plan(self.project, "c")
        main(["queue", "add", "a", "--project", str(self.project)])
        main(["queue", "add", "b", "--project", str(self.project)])
        rc = main(["queue", "add", "c", "--front", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = [e["slug"] for e in queue.load(self.queue_path)["queue"]]
        self.assertEqual(slugs, ["c", "a", "b"])

    def test_add_appends_when_queue_nonempty(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "a")
        _write_plan(self.project, "b")
        main(["queue", "add", "a", "--project", str(self.project)])
        rc = main(["queue", "add", "b", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = [e["slug"] for e in queue.load(self.queue_path)["queue"]]
        self.assertEqual(slugs, ["a", "b"])

    # --- rejection paths (the four documented exit codes besides OK) ---

    def test_add_rejects_invalid_slug(self) -> None:
        _bootstrap(self.project)
        rc = main(["queue", "add", "Bad Slug!", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.INVALID_SLUG)
        self.assertFalse(self.queue_path.exists())

    def test_add_rejects_unknown_project(self) -> None:
        # No bootstrap: registry is empty for this project.
        _write_plan(self.project, "foo")  # plan file exists; bootstrap check fires first.
        rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertFalse(self.queue_path.exists())

    def test_add_rejects_missing_plan_file(self) -> None:
        _bootstrap(self.project)
        rc = main(["queue", "add", "nonexistent", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)
        self.assertFalse(self.queue_path.exists())

    def test_add_rejects_duplicate_pending(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        main(["queue", "add", "foo", "--project", str(self.project)])
        rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        slugs = [e["slug"] for e in queue.load(self.queue_path)["queue"]]
        self.assertEqual(slugs, ["foo"])

    # --- re-add edge cases ---

    def test_add_allows_re_add_of_history_only_slug(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        # Seed history entry (without a pending row).
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        with queue.mutate(self.queue_path) as data:
            data["history"].append({"slug": "foo", "outcome": "removed"})
        rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        data = queue.load(self.queue_path)
        self.assertEqual([e["slug"] for e in data["queue"]], ["foo"])
        self.assertEqual(data["history"], [{"slug": "foo", "outcome": "removed"}])

    def test_add_idempotency_on_currently_running_slug(self) -> None:
        # Slug is registered (treated as currently running in production) but
        # not in the pending queue — re-enqueue is allowed.
        _bootstrap(self.project, slug="foo")
        rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = [e["slug"] for e in queue.load(self.queue_path)["queue"]]
        self.assertEqual(slugs, ["foo"])

    # --- shape + path resolution ---

    def test_add_entry_shape(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "shape")
        main(["queue", "add", "shape", "--project", str(self.project)])
        entry = queue.load(self.queue_path)["queue"][0]
        self.assertEqual(entry["slug"], "shape")
        self.assertEqual(entry["added_by"], "operator")
        self.assertEqual(entry["position_at_add"], "tail")
        self.assertIn("added_at", entry)
        # ISO-Z timestamp shape.
        self.assertTrue(entry["added_at"].endswith("Z"))

    def test_add_front_records_position_at_add(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "head")
        main(["queue", "add", "head", "--front", "--project", str(self.project)])
        entry = queue.load(self.queue_path)["queue"][0]
        self.assertEqual(entry["position_at_add"], "front")

    def test_add_refuses_on_corrupt_queue(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text("{not valid json")
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("queue.json corrupt", err.getvalue())
        self.assertIn("Open Claude in this project to repair", err.getvalue())
        # File is NOT touched by the refusal path.
        self.assertEqual(self.queue_path.read_text(), "{not valid json")

    def test_add_uses_resolved_path_for_bootstrap(self) -> None:
        # Symlinked project root: `clu queue add` should accept the symlink as
        # --project and still find the registry row written under the real
        # path. registry.register canonicalises via Path.resolve(); the
        # bootstrap check must mirror that.
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        link = Path(self._tmp.name).parent / f"link-{self.project.name}"
        os.symlink(self.project, link)
        self.addCleanup(link.unlink)
        rc = main(["queue", "add", "foo", "--project", str(link)])
        self.assertEqual(rc, ExitCode.OK)


if __name__ == "__main__":
    unittest.main()
