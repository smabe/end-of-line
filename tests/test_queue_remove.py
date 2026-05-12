"""Phase `list` tests: `clu queue remove`.

Pending → history transition with outcome=removed; rejection paths for
invalid slugs, slugs not in the pending queue, and slugs whose only
relationship to the project is the registry (i.e. currently running).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from end_of_line import queue, registry
from end_of_line.cli import ExitCode, main
from end_of_line.config import ProjectConfig
from tests import isolate_queue

_PLAN_BODY = "# placeholder plan\n"


def _write_plan(project: Path, slug: str) -> Path:
    plans_dir = project / "plans"
    plans_dir.mkdir(exist_ok=True)
    path = plans_dir / f"{slug}.md"
    path.write_text(_PLAN_BODY)
    return path


def _bootstrap(project: Path, slug: str = "seed-plan") -> None:
    _write_plan(project, slug)
    registry.register(project, slug)


def _add(project: Path, slug: str) -> None:
    main(["queue", "add", slug, "--project", str(project)])


class QueueRemoveTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        isolate_queue(self, self.project)
        self.queue_path = ProjectConfig(project_root=self.project).queue_path()

    def test_remove_success_moves_to_history(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        _add(self.project, "foo")
        rc = main(["queue", "remove", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        data = queue.load(self.queue_path)
        self.assertEqual(data["queue"], [])
        self.assertEqual(len(data["history"]), 1)
        h = data["history"][0]
        self.assertEqual(h["slug"], "foo")
        self.assertEqual(h["outcome"], "removed")
        self.assertIn("ended_at", h)
        self.assertTrue(h["ended_at"].endswith("Z"))

    def test_remove_preserves_other_entries(self) -> None:
        _bootstrap(self.project)
        for slug in ("a", "b", "c"):
            _write_plan(self.project, slug)
            _add(self.project, slug)
        rc = main(["queue", "remove", "b", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        data = queue.load(self.queue_path)
        self.assertEqual([e["slug"] for e in data["queue"]], ["a", "c"])
        self.assertEqual([h["slug"] for h in data["history"]], ["b"])
        self.assertEqual(data["history"][0]["outcome"], "removed")

    def test_remove_rejects_invalid_slug(self) -> None:
        rc = main(["queue", "remove", "Bad Slug!", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.INVALID_SLUG)

    def test_remove_rejects_slug_not_in_pending(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        _add(self.project, "foo")
        rc = main(["queue", "remove", "bar", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)
        # foo stays put.
        data = queue.load(self.queue_path)
        self.assertEqual([e["slug"] for e in data["queue"]], ["foo"])
        self.assertEqual(data["history"], [])

    def test_remove_refuses_on_corrupt_queue(self) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text("{not valid json")
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["queue", "remove", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("queue.json corrupt", err.getvalue())
        self.assertIn("Open Claude in this project to repair", err.getvalue())
        # The corrupt file is NOT modified by the refusal path.
        self.assertEqual(self.queue_path.read_text(), "{not valid json")

    def test_remove_does_not_touch_running_slug(self) -> None:
        # foo is registered (treated as currently running) but never enqueued.
        _bootstrap(self.project, slug="foo")
        rc = main(["queue", "remove", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)


if __name__ == "__main__":
    unittest.main()
