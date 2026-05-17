"""Phase `render` tests: `clu queue list` source attribution for worker entries.

Worker-enqueued entries get an annotation block under their table row:
  `  <pos>: (from <source_plan>/<source_phase>)`
  `     reason: <reason>`   (only if reason is present)

Operator entries render unchanged (v1 shape, no annotation).
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from end_of_line import queue, state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import ProjectConfig
from tests import isolate_queue

_PLAN_BODY = "# placeholder plan\n"


def _write_plan(project: Path, slug: str) -> None:
    plans_dir = project / "plans"
    plans_dir.mkdir(exist_ok=True)
    (plans_dir / f"{slug}.md").write_text(_PLAN_BODY)


def _operator_entry(slug: str) -> dict:
    return {
        "slug": slug,
        "added_at": st.utcnow(),
        "added_by": "operator",
        "position_at_add": "tail",
        "source_plan": None,
        "source_phase": None,
        "source_token_fp": None,
        "reason": None,
    }


def _worker_entry(slug: str, source_plan: str, source_phase: str,
                  reason: str | None = None) -> dict:
    return {
        "slug": slug,
        "added_at": st.utcnow(),
        "added_by": "worker",
        "position_at_add": "tail",
        "source_plan": source_plan,
        "source_phase": source_phase,
        "source_token_fp": "abcd1234",
        "reason": reason,
    }


def _seed_queue(project: Path, entries: list[dict]) -> None:
    cfg = ProjectConfig(project_root=project)
    queue_path = cfg.queue_path()
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue.save_atomic(queue_path, {
        "schema_version": queue.SCHEMA_VERSION,
        "queue": entries,
        "history": [],
    })


class WorkerRenderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        isolate_queue(self, self.project)

    def _run(self) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["queue", "list", "--project", str(self.project)])
        return rc, buf.getvalue()

    def test_list_worker_entry_shows_source(self) -> None:
        _write_plan(self.project, "feature-c")
        _seed_queue(self.project, [
            _worker_entry("feature-c", "feature-b", "c-extract"),
        ])
        rc, out = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("(from feature-b/c-extract)", out)

    def test_list_worker_entry_with_reason_shows_reason(self) -> None:
        _write_plan(self.project, "feature-c")
        _seed_queue(self.project, [
            _worker_entry("feature-c", "feature-b", "c-extract",
                          reason="follow-up test coverage"),
        ])
        rc, out = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("(from feature-b/c-extract)", out)
        self.assertIn("reason: follow-up test coverage", out)

    def test_list_operator_entries_unchanged(self) -> None:
        _write_plan(self.project, "plan-a")
        _seed_queue(self.project, [_operator_entry("plan-a")])
        rc, out = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("plan-a", out)
        self.assertNotIn("(from ", out)

    def test_list_mixed_operator_worker_entries(self) -> None:
        for slug in ("plan-a", "plan-b", "plan-c"):
            _write_plan(self.project, slug)
        _seed_queue(self.project, [
            _operator_entry("plan-a"),
            _worker_entry("plan-b", "feature-b", "phase-x"),
            _worker_entry("plan-c", "feature-b", "phase-y",
                          reason="chained work"),
        ])
        rc, out = self._run()
        self.assertEqual(rc, ExitCode.OK)
        # Three POS rows (1, 2, 3 in table).
        self.assertIn("plan-a", out)
        self.assertIn("plan-b", out)
        self.assertIn("plan-c", out)
        # Two worker annotation blocks.
        self.assertEqual(out.count("(from "), 2)
        self.assertIn("(from feature-b/phase-x)", out)
        self.assertIn("(from feature-b/phase-y)", out)
        self.assertIn("reason: chained work", out)
        # Operator entry has no annotation.
        self.assertNotIn("(from feature-b/plan-a)", out)

    def test_list_with_only_worker_entries_no_regression(self) -> None:
        for slug in ("plan-b", "plan-c"):
            _write_plan(self.project, slug)
        _seed_queue(self.project, [
            _worker_entry("plan-b", "src-plan", "phase-1"),
            _worker_entry("plan-c", "src-plan", "phase-2"),
        ])
        rc, out = self._run()
        self.assertEqual(rc, ExitCode.OK)
        # Header present — no IndexError.
        self.assertIn("POS", out)
        self.assertIn("SLUG", out)
        # Both annotation blocks present.
        self.assertEqual(out.count("(from "), 2)
