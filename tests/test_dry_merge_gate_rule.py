"""Tests for the dry_merge_gate_rule cross-plan rule.

Phase 3 of dry-merge-gate: registers a rule that fires when ≥2 sibling
plans in the same batch_id are STATUS_DONE with live worktree records.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import cross_plan_rules, notify, state as st
from end_of_line.config import load_project_config
from end_of_line.cross_plan_rules import (
    ProjectPlan,
    load_plans_for_project,
    register_rule,
    run_rules,
)
from end_of_line.dry_merge import MergeResult
from tests import CluTestCase, git as _git, make_git_project as _make_git_project


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_branch(repo: Path, branch: str, filename: str, content: str) -> str:
    """Create branch with one file commit; return the branch HEAD SHA."""
    _git(repo, "checkout", "-b", branch)
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", f"add {filename}")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _git(repo, "checkout", "main")
    return sha


def _make_done_plan(
    project: Path,
    slug: str,
    batch_id: str | None = None,
    branch: str | None = None,
) -> ProjectPlan:
    """Create a DONE plan state file under project/plans/.orchestrator/."""
    state_path = project / "plans" / ".orchestrator" / f"{slug}.state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = st.empty_state(slug, "plans")
    data["status"] = st.STATUS_DONE
    data["batch_id"] = batch_id
    if branch is not None:
        data["worktree"] = {
            "branch": branch,
            "path": f"/tmp/wt-{slug}",
            "base_ref": "main",
        }
    st.save_atomic(state_path, data)
    return ProjectPlan(slug, dict(data), state_path)


# ---------------------------------------------------------------------------
# base
# ---------------------------------------------------------------------------

class _GateRuleBase(CluTestCase):
    """Isolates _RULES and registers only dry_merge_gate_rule per test."""

    def setUp(self) -> None:
        super().setUp()
        self._rules_snapshot = list(cross_plan_rules._RULES)
        cross_plan_rules._RULES.clear()
        from end_of_line.cross_plan_rules import dry_merge_gate_rule  # noqa: PLC0415
        register_rule(dry_merge_gate_rule)

    def tearDown(self) -> None:
        cross_plan_rules._RULES[:] = self._rules_snapshot
        super().tearDown()


# ---------------------------------------------------------------------------
# skipped cases (no git needed)
# ---------------------------------------------------------------------------

class TestGateSkipped(_GateRuleBase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "proj"
        self.project.mkdir()
        (self.project / "plans").mkdir()

    def test_gate_skipped_when_single_done_sibling(self) -> None:
        p = _make_done_plan(self.project, "alpha", batch_id="b1", branch="clu/alpha")
        result = run_rules(self.project, [p])
        self.assertIsNone(result)

    def test_gate_skipped_when_no_batch_id(self) -> None:
        pa = _make_done_plan(self.project, "alpha", batch_id=None, branch="clu/alpha")
        pb = _make_done_plan(self.project, "beta", batch_id=None, branch="clu/beta")
        result = run_rules(self.project, [pa, pb])
        self.assertIsNone(result)

    def test_gate_skipped_when_one_plan_archived(self) -> None:
        pa = _make_done_plan(self.project, "alpha", batch_id="b1", branch="clu/alpha")
        pb = _make_done_plan(self.project, "beta", batch_id="b1")  # no worktree → archived
        result = run_rules(self.project, [pa, pb])
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# integration cases (real git repo)
# ---------------------------------------------------------------------------

class TestGateIntegration(_GateRuleBase):
    def setUp(self) -> None:
        super().setUp()
        self.project = _make_git_project(self.tmp_path)
        _make_branch(self.project, "clu/plan-a", "file_a.txt", "hello from a\n")
        _make_branch(self.project, "clu/plan-b", "file_b.txt", "hello from b\n")

    def test_gate_clean_stamps_gate_result_on_each_plan(self) -> None:
        pa = _make_done_plan(self.project, "plan-a", batch_id="b1", branch="clu/plan-a")
        pb = _make_done_plan(self.project, "plan-b", batch_id="b1", branch="clu/plan-b")

        result = run_rules(self.project.resolve(), [pa, pb])

        self.assertIsNotNone(result)
        self.assertEqual(result.rule_name, "dry_merge_gate")  # type: ignore[union-attr]

        kinds = [k for k, _ in result.notifies]  # type: ignore[union-attr]
        self.assertIn(notify.KIND_GATE_CLEAN, kinds)

        data_a = st.load(pa.state_path)
        data_b = st.load(pb.state_path)
        self.assertEqual(data_a["gate_result"]["outcome"], "clean")
        self.assertEqual(data_b["gate_result"]["outcome"], "clean")
        self.assertEqual(data_a["gate_result"]["sha_key"], data_b["gate_result"]["sha_key"])

        # No follow-up plan files written
        master_files = list((self.project / "plans").glob("merge-resolve-*.md"))
        self.assertEqual(master_files, [])

    def test_gate_dirty_writes_followup_plan_pair_not_queued(self) -> None:
        pa = _make_done_plan(self.project, "plan-a", batch_id="b1", branch="clu/plan-a")
        pb = _make_done_plan(self.project, "plan-b", batch_id="b1", branch="clu/plan-b")

        dirty = MergeResult(
            outcome="textual_conflict",
            conflict_files=["foo.py"],
            merged_branches=["clu/plan-a", "clu/plan-b"],
            base_sha="abc123",
        )
        with mock.patch("end_of_line.dry_merge.attempt_merge", return_value=dirty):
            result = run_rules(self.project.resolve(), [pa, pb])

        self.assertIsNotNone(result)

        plan_files = list((self.project / "plans").glob("merge-resolve-*.md"))
        self.assertEqual(len(plan_files), 2)  # master + sub-plan

        data_a = st.load(pa.state_path)
        data_b = st.load(pb.state_path)
        self.assertEqual(data_a["gate_result"]["outcome"], "textual_conflict")
        self.assertIn("follow_up_plan", data_a["gate_result"])
        self.assertEqual(data_a["gate_result"]["follow_up_plan"], data_b["gate_result"]["follow_up_plan"])

        kinds = [k for k, _ in result.notifies]  # type: ignore[union-attr]
        self.assertIn(notify.KIND_GATE_DIRTY, kinds)

        queue_path = self.project / "plans" / ".orchestrator" / "queue.json"
        self.assertFalse(queue_path.exists())

    def test_gate_idempotent_on_same_shas(self) -> None:
        pa = _make_done_plan(self.project, "plan-a", batch_id="b1", branch="clu/plan-a")
        pb = _make_done_plan(self.project, "plan-b", batch_id="b1", branch="clu/plan-b")

        result1 = run_rules(self.project.resolve(), [pa, pb])
        self.assertIsNotNone(result1)

        # Reload plans from disk — gate_result is now stamped
        data_a = st.load(pa.state_path)
        data_b = st.load(pb.state_path)
        plans_v2 = [
            ProjectPlan("plan-a", data_a, pa.state_path),
            ProjectPlan("plan-b", data_b, pb.state_path),
        ]

        result2 = run_rules(self.project.resolve(), plans_v2)
        self.assertIsNone(result2)

    def test_gate_re_runs_after_new_sibling_done(self) -> None:
        pa = _make_done_plan(self.project, "plan-a", batch_id="b1", branch="clu/plan-a")
        pb = _make_done_plan(self.project, "plan-b", batch_id="b1", branch="clu/plan-b")

        result1 = run_rules(self.project.resolve(), [pa, pb])
        self.assertIsNotNone(result1)
        sha_key_1 = next(
            iter(result1.field_updates_per_plan.values())  # type: ignore[union-attr]
        )["gate_result"]["sha_key"]

        # Third plan joins the batch
        _make_branch(self.project, "clu/plan-c", "file_c.txt", "hello from c\n")
        pc = _make_done_plan(self.project, "plan-c", batch_id="b1", branch="clu/plan-c")

        # Reload a and b so their gate_result is in memory
        data_a = st.load(pa.state_path)
        data_b = st.load(pb.state_path)
        pa_v2 = ProjectPlan("plan-a", data_a, pa.state_path)
        pb_v2 = ProjectPlan("plan-b", data_b, pb.state_path)

        result2 = run_rules(self.project.resolve(), [pa_v2, pb_v2, pc])
        self.assertIsNotNone(result2)
        sha_key_2 = next(
            iter(result2.field_updates_per_plan.values())  # type: ignore[union-attr]
        )["gate_result"]["sha_key"]

        self.assertNotEqual(sha_key_1, sha_key_2)


if __name__ == "__main__":
    unittest.main()
