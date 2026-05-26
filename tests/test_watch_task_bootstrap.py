"""Tests for watch.bootstrap_task_list — TASK_CREATE emission on startup."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from end_of_line.config import ProjectConfig
from end_of_line.watch import bootstrap_task_list


def _make_cfg_loader(project_root: Path):
    def loader(state_path: Path) -> ProjectConfig:
        return ProjectConfig(project_root=project_root)

    return loader


class BootstrapEmissionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _state_path(self, slug: str) -> Path:
        p = self.tmp / "plans" / ".orchestrator" / f"{slug}.state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
        return p

    def _plan_path(self, slug: str) -> Path:
        return self.tmp / "plans" / f"{slug}.md"

    def _write_master(self, slug: str, phases: list) -> None:
        rows = "\n".join(f"| {ph} | `{slug}-{ph}.md` | scope {ph} | 1h |" for ph in phases)
        content = (
            f"# {slug}\n\n"
            "## Sessions index\n\n"
            "| Session | Plan file | Scope | Effort |\n"
            "|---|---|---|---|\n"
            f"{rows}\n"
        )
        self._plan_path(slug).parent.mkdir(parents=True, exist_ok=True)
        self._plan_path(slug).write_text(content)

    def test_bootstrap_emits_parent_then_phases_in_order(self):
        slug = "my-plan"
        state = self._state_path(slug)
        self._write_master(slug, ["a", "b", "c"])
        sink = io.StringIO()
        bootstrap_task_list([state], _make_cfg_loader(self.tmp), sink)
        lines = sink.getvalue().splitlines()
        self.assertEqual(
            lines,
            [
                "TASK_CREATE task=my-plan status=pending",
                "TASK_CREATE task=my-plan/a parent=my-plan status=pending",
                "TASK_CREATE task=my-plan/b parent=my-plan status=pending",
                "TASK_CREATE task=my-plan/c parent=my-plan status=pending",
            ],
        )

    def test_bootstrap_parent_line_has_no_parent_field(self):
        slug = "my-plan"
        state = self._state_path(slug)
        self._write_master(slug, ["a"])
        sink = io.StringIO()
        bootstrap_task_list([state], _make_cfg_loader(self.tmp), sink)
        lines = sink.getvalue().splitlines()
        parent_line = lines[0]
        self.assertEqual(parent_line, "TASK_CREATE task=my-plan status=pending")
        self.assertNotIn("parent=", parent_line)

    def test_bootstrap_missing_master_file_skips_silently(self):
        slug = "ghost-plan"
        state = self._state_path(slug)
        sink = io.StringIO()
        bootstrap_task_list([state], _make_cfg_loader(self.tmp), sink)
        self.assertEqual(sink.getvalue(), "")

    def test_bootstrap_single_phase_master_emits_parent_only(self):
        slug = "solo-plan"
        state = self._state_path(slug)
        plan_path = self._plan_path(slug)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("# solo-plan\n\nSingle-phase plan with no sessions index.\n")
        sink = io.StringIO()
        bootstrap_task_list([state], _make_cfg_loader(self.tmp), sink)
        lines = sink.getvalue().splitlines()
        self.assertEqual(lines, ["TASK_CREATE task=solo-plan status=pending"])

    def test_bootstrap_multiple_plans_each_get_their_own_tree(self):
        slug_a = "plan-alpha"
        slug_b = "plan-beta"
        state_a = self._state_path(slug_a)
        state_b = self._state_path(slug_b)
        self._write_master(slug_a, ["x", "y"])
        self._write_master(slug_b, ["p", "q"])
        sink = io.StringIO()
        bootstrap_task_list([state_a, state_b], _make_cfg_loader(self.tmp), sink)
        lines = sink.getvalue().splitlines()
        self.assertEqual(
            lines,
            [
                "TASK_CREATE task=plan-alpha status=pending",
                "TASK_CREATE task=plan-alpha/x parent=plan-alpha status=pending",
                "TASK_CREATE task=plan-alpha/y parent=plan-alpha status=pending",
                "TASK_CREATE task=plan-beta status=pending",
                "TASK_CREATE task=plan-beta/p parent=plan-beta status=pending",
                "TASK_CREATE task=plan-beta/q parent=plan-beta status=pending",
            ],
        )

    def test_bootstrap_state_path_pointing_at_missing_state_skips(self):
        nonexistent = self.tmp / "plans" / ".orchestrator" / "ghost.state.json"
        sink = io.StringIO()
        bootstrap_task_list([nonexistent], _make_cfg_loader(self.tmp), sink)
        self.assertEqual(sink.getvalue(), "")

    def _state_path_with_claim(
        self, slug: str, phase_id: str, *, plan_status: str = "running"
    ) -> Path:
        p = self.tmp / "plans" / ".orchestrator" / f"{slug}.state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "plan_slug": slug,
                    "status": plan_status,
                    "current_claim": {"phase_id": phase_id},
                }
            )
        )
        return p

    def test_bootstrap_emits_task_update_when_phase_active(self):
        slug = "my-plan"
        state = self._state_path_with_claim(slug, "p1")
        self._write_master(slug, ["p1", "p2"])
        sink = io.StringIO()
        bootstrap_task_list([state], _make_cfg_loader(self.tmp), sink)
        lines = sink.getvalue().splitlines()
        updates = [line for line in lines if line.startswith("TASK_UPDATE")]
        self.assertEqual(len(updates), 2)
        self.assertIn(
            'TASK_UPDATE task=my-plan status=in_progress msg="bootstrap: plan running"',
            updates,
        )
        self.assertIn(
            'TASK_UPDATE task=my-plan/p1 parent=my-plan status=in_progress msg="bootstrap: already active"',
            updates,
        )

    def test_bootstrap_no_task_update_when_no_claim(self):
        slug = "my-plan"
        state = self._state_path(slug)  # writes "{}" — current_claim absent
        self._write_master(slug, ["p1"])
        sink = io.StringIO()
        bootstrap_task_list([state], _make_cfg_loader(self.tmp), sink)
        updates = [line for line in sink.getvalue().splitlines() if line.startswith("TASK_UPDATE")]
        self.assertEqual(updates, [])

    def test_bootstrap_skips_task_update_when_status_not_running(self):
        # current_claim present but plan is blocked/paused — out of scope per #62
        slug = "my-plan"
        state = self._state_path_with_claim(slug, "p1", plan_status="blocked")
        self._write_master(slug, ["p1"])
        sink = io.StringIO()
        bootstrap_task_list([state], _make_cfg_loader(self.tmp), sink)
        updates = [line for line in sink.getvalue().splitlines() if line.startswith("TASK_UPDATE")]
        self.assertEqual(updates, [])
