"""Tests for --batch flag schema additions to `clu queue add` and related data paths.

Phase `schema` of the `dry-merge-gate` plan (#50). Seven tests verify the
additive `batch_id` field: operator stamping, null default, slug validation,
worker rejection, queue-pop propagation to plan state, absorbed-path history
preservation, and empty_state baseline.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import queue, registry
from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import ProjectConfig, load_project_config
from end_of_line.cross_plan_rules import ProjectPlan, queue_advancement_rule
from tests import CluTestCase, isolate_queue

_PLAN_BODY = "# placeholder plan\n"


def _bootstrap(project: Path, slug: str = "seed-plan") -> None:
    (project / "plans").mkdir(exist_ok=True)
    (project / "plans" / f"{slug}.md").write_text(_PLAN_BODY)
    registry.register(project, slug)


def _write_plan(project: Path, slug: str) -> Path:
    plans_dir = project / "plans"
    plans_dir.mkdir(exist_ok=True)
    path = plans_dir / f"{slug}.md"
    path.write_text(_PLAN_BODY)
    return path


class QueueBatchSchemaTestCase(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "repo"
        self.project.mkdir()
        isolate_queue(self, self.project)
        self.queue_path = ProjectConfig(project_root=self.project).queue_path()

    def test_queue_add_stamps_batch_id_uniformly(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "plan-a")
        _write_plan(self.project, "plan-b")
        _write_plan(self.project, "plan-c")
        rc = main(
            [
                "queue",
                "add",
                "plan-a",
                "plan-b",
                "plan-c",
                "--batch",
                "my-batch",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.OK)
        entries = queue.load(self.queue_path)["queue"]
        self.assertEqual(len(entries), 3)
        for entry in entries:
            self.assertEqual(entry["batch_id"], "my-batch")

    def test_queue_add_without_batch_id_is_null(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "plan-a")
        rc = main(["queue", "add", "plan-a", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        entry = queue.load(self.queue_path)["queue"][0]
        self.assertIsNone(entry.get("batch_id"))

    def test_queue_add_invalid_batch_slug_rejects(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "plan-a")
        rc = main(
            [
                "queue",
                "add",
                "plan-a",
                "--batch",
                "BAD_SLUG",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.INVALID_SLUG)

    def test_worker_queue_add_rejects_batch_flag(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "child-plan")

        rc = main(
            [
                "queue",
                "add",
                "child-plan",
                "--token",
                "some-token",
                "--plan",
                "parent-plan",
                "--phase",
                "some-phase",
                "--batch",
                "my-batch",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.GENERIC)
        # Queue must be unchanged — no entries added before the rejection.
        if self.queue_path.exists():
            self.assertEqual(queue.load(self.queue_path)["queue"], [])

    def test_queue_pop_propagates_batch_id_to_plan_state(self) -> None:
        _bootstrap(self.project, "seed")
        _write_plan(self.project, "batched-plan")

        with queue.mutate(self.queue_path) as data:
            data["queue"].append(
                {
                    "slug": "batched-plan",
                    "added_at": st.utcnow(),
                    "added_by": "operator",
                    "position_at_add": "tail",
                    "source_plan": None,
                    "source_phase": None,
                    "source_token_fp": None,
                    "reason": None,
                    "batch_id": "my-batch",
                }
            )

        cfg = load_project_config(self.project)
        state_path = cfg.state_path("batched-plan")

        with mock.patch("end_of_line.cli._tick_one_plan"):
            result = queue_advancement_rule(self.project, [])

        self.assertIsNotNone(result)
        self.assertTrue(state_path.exists())
        state = st.load(state_path)
        self.assertEqual(state.get("batch_id"), "my-batch")

    def test_history_entry_preserves_batch_id_on_absorbed(self) -> None:
        _bootstrap(self.project, "absorbed-plan")
        _write_plan(self.project, "absorbed-plan")

        cfg = load_project_config(self.project)
        state_path = cfg.state_path("absorbed-plan")
        fresh = st.empty_state("absorbed-plan", "plans")
        fresh["status"] = st.STATUS_DONE
        st.save_atomic(state_path, fresh)

        with queue.mutate(self.queue_path) as data:
            data["queue"].append(
                {
                    "slug": "absorbed-plan",
                    "added_at": st.utcnow(),
                    "added_by": "operator",
                    "position_at_add": "tail",
                    "source_plan": None,
                    "source_phase": None,
                    "source_token_fp": None,
                    "reason": None,
                    "batch_id": "absorbed-batch",
                }
            )

        plans = [
            ProjectPlan(
                slug="absorbed-plan",
                state=fresh,
                state_path=state_path,
            )
        ]
        result = queue_advancement_rule(self.project, plans)
        self.assertIsNotNone(result)
        data = queue.load(self.queue_path)
        self.assertEqual(len(data["history"]), 1)
        self.assertEqual(data["history"][0]["batch_id"], "absorbed-batch")

    def test_empty_state_has_null_batch_id(self) -> None:
        state = st.empty_state("my-plan", "plans")
        self.assertIn("batch_id", state)
        self.assertIsNone(state["batch_id"])


if __name__ == "__main__":
    unittest.main()
