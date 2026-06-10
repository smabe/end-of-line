"""Tests for the ready_to_ship_rule + render_ready_to_ship.

Phase 7 of clu-ship.md: a cross-plan rule that emits
KIND_READY_TO_SHIP into the inbox when DONE plans exist with
unmerged worktree branches and no in-flight `data["ship_pending"]`
stamp. The rule slots between `dry_merge_gate_rule` and
`auto_archive_rule` in the tick chain.

Dedup: stamps `data["ready_to_ship_announced"] = {"branch_sha": ...}`
after firing so subsequent ticks don't re-spam the inbox. Re-fires
if the worker pushes new commits to the branch (branch_sha changes).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from end_of_line import cross_plan_rules, notify, registry
from end_of_line import state as st
from end_of_line.cross_plan_rules import ProjectPlan, register_rule, run_rules
from tests import CluTestCase, must
from tests import git as _git
from tests import make_git_project as _make_git_project

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _commit_branch(project: Path, branch: str, msg: str = "work") -> str:
    """Create branch (if needed) + one commit; return the new HEAD SHA.

    Uses explicit file paths in `git add` so the untracked
    `plans/.orchestrator/` state dir doesn't accidentally get swept
    into the branch's tracked files (and then deleted on checkout main).
    """
    branches = _git(project, "branch", "--list", branch).stdout
    if branches.strip():
        _git(project, "checkout", branch)
    else:
        _git(project, "checkout", "-b", branch)
    fname = f"{branch.replace('/', '-')}-{msg}.txt"
    (project / fname).write_text(msg + "\n")
    _git(project, "add", fname)
    _git(project, "commit", "-m", msg)
    sha = _git(project, "rev-parse", "HEAD").stdout.strip()
    _git(project, "checkout", "main")
    return sha


def _make_done_plan(
    project: Path,
    slug: str,
    branch: str,
    *,
    extra_state: dict | None = None,
) -> ProjectPlan:
    registry.register(project, slug)
    state_path = project / "plans" / ".orchestrator" / f"{slug}.state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = st.empty_state(slug, "plans")
    data["status"] = st.STATUS_DONE
    data["worktree"] = {
        "branch": branch,
        "path": str(project.parent / f"wt-{slug}"),
        "base_ref": "main",
    }
    if extra_state:
        data.update(extra_state)
    st.save_atomic(state_path, data)
    return ProjectPlan(slug, dict(data), state_path)


# ---------------------------------------------------------------------------
# rule tests
# ---------------------------------------------------------------------------


class _ReadyToShipRuleBase(CluTestCase):
    """Real git project + bare origin so is_branch_merged_into has
    a meaningful origin/main; only ready_to_ship_rule is registered."""

    def setUp(self) -> None:
        super().setUp()
        self.project = _make_git_project(self.tmp_path)
        # Bare origin remote
        bare = self.tmp_path / "origin.git"
        import subprocess

        subprocess.run(
            ["git", "init", "-q", "--bare", str(bare)],
            check=True,
            capture_output=True,
        )
        _git(self.project, "remote", "add", "origin", str(bare))
        _git(self.project, "push", "-u", "origin", "main")

        self._rules_snapshot = list(cross_plan_rules._RULES)
        cross_plan_rules._RULES.clear()
        from end_of_line.cross_plan_rules import ready_to_ship_rule  # noqa: PLC0415

        register_rule(ready_to_ship_rule)

    def tearDown(self) -> None:
        cross_plan_rules._RULES[:] = self._rules_snapshot
        super().tearDown()


class ReadyToShipEligibilityTests(_ReadyToShipRuleBase):
    def test_no_done_plans_returns_None(self) -> None:
        result = run_rules(self.project, [])
        self.assertIsNone(result)

    def test_running_plan_skipped(self) -> None:
        _commit_branch(self.project, "clu/alpha")
        p = _make_done_plan(self.project, "alpha", "clu/alpha")
        # Override status to RUNNING.
        with st.mutate(p.state_path) as d:
            d["status"] = st.STATUS_RUNNING
        p = ProjectPlan("alpha", st.load(p.state_path), p.state_path)
        result = run_rules(self.project, [p])
        self.assertIsNone(result)

    def test_no_worktree_skipped(self) -> None:
        p = _make_done_plan(self.project, "alpha", "clu/alpha")
        with st.mutate(p.state_path) as d:
            d.pop("worktree", None)
        p = ProjectPlan("alpha", st.load(p.state_path), p.state_path)
        result = run_rules(self.project, [p])
        self.assertIsNone(result)

    def test_already_merged_branch_skipped(self) -> None:
        # Branch is ancestor of origin/main → auto_archive_rule's
        # territory; ready_to_ship stays out.
        _commit_branch(self.project, "clu/alpha")
        _git(self.project, "merge", "--no-ff", "--no-edit", "clu/alpha")
        _git(self.project, "push", "origin", "main")
        p = _make_done_plan(self.project, "alpha", "clu/alpha")
        result = run_rules(self.project, [p])
        self.assertIsNone(result)

    def test_ship_pending_suppresses(self) -> None:
        _commit_branch(self.project, "clu/alpha")
        p = _make_done_plan(
            self.project,
            "alpha",
            "clu/alpha",
            extra_state={
                "ship_pending": {
                    "mode": "as_pr",
                    "pr_url": "https://github.com/example/repo/pull/1",
                    "ts": "2026-05-23T12:00:00Z",
                }
            },
        )
        result = run_rules(self.project, [p])
        self.assertIsNone(result)


class ReadyToShipNotifyTests(_ReadyToShipRuleBase):
    def test_eligible_plan_emits_notification(self) -> None:
        _commit_branch(self.project, "clu/alpha")
        p = _make_done_plan(self.project, "alpha", "clu/alpha")
        result = must(run_rules(self.project, [p]))
        self.assertEqual(result.rule_name, "ready_to_ship")
        kinds = [k for k, _ in result.notifies]
        self.assertIn(notify.KIND_READY_TO_SHIP, kinds)
        body = next(b for k, b in result.notifies if k == notify.KIND_READY_TO_SHIP)
        self.assertIn("alpha", body)

    def test_dedup_via_branch_sha_stamp(self) -> None:
        # First fire stamps; second tick at same branch_sha doesn't re-fire.
        _commit_branch(self.project, "clu/alpha")
        p = _make_done_plan(self.project, "alpha", "clu/alpha")
        first = run_rules(self.project, [p])
        self.assertIsNotNone(first)
        # Reload plan with the stamp applied by the rule.
        p2 = ProjectPlan("alpha", st.load(p.state_path), p.state_path)
        second = run_rules(self.project, [p2])
        self.assertIsNone(second)

    def test_refires_when_branch_tip_advances(self) -> None:
        # Worker added a commit to the branch after the first fire.
        _commit_branch(self.project, "clu/alpha")
        p = _make_done_plan(self.project, "alpha", "clu/alpha")
        first = run_rules(self.project, [p])
        self.assertIsNotNone(first)
        _commit_branch(self.project, "clu/alpha", "more work")
        p2 = ProjectPlan("alpha", st.load(p.state_path), p.state_path)
        second = run_rules(self.project, [p2])
        self.assertIsNotNone(second)

    def test_mode_aware_body_direct(self) -> None:
        # No .orchestrator.json → default mode is "direct" → body says
        # `clu ship --plan X --direct --yes`.
        _commit_branch(self.project, "clu/alpha")
        p = _make_done_plan(self.project, "alpha", "clu/alpha")
        result = must(run_rules(self.project, [p]))
        body = result.notifies[0][1]
        self.assertIn("--direct", body)
        self.assertIn("clu ship", body)

    def test_mode_aware_body_as_pr(self) -> None:
        # .orchestrator.json with ship_mode: as_pr → body says --as-pr.
        cfg_path = self.project / ".orchestrator.json"
        cfg_path.write_text('{"dispatch":{"ship_mode":"as_pr"}}')
        _commit_branch(self.project, "clu/alpha")
        p = _make_done_plan(self.project, "alpha", "clu/alpha")
        result = must(run_rules(self.project, [p]))
        body = result.notifies[0][1]
        self.assertIn("--as-pr", body)
        self.assertNotIn("--direct", body)

    def test_batch_body_uses_all_done(self) -> None:
        _commit_branch(self.project, "clu/alpha")
        _commit_branch(self.project, "clu/beta")
        p1 = _make_done_plan(self.project, "alpha", "clu/alpha")
        p2 = _make_done_plan(self.project, "beta", "clu/beta")
        result = must(run_rules(self.project, [p1, p2]))
        body = result.notifies[0][1]
        self.assertIn("--all-done", body)
        self.assertIn("alpha", body)
        self.assertIn("beta", body)


# ---------------------------------------------------------------------------
# render tests
# ---------------------------------------------------------------------------


class RenderReadyToShipTests(unittest.TestCase):
    def test_single_plan_direct(self) -> None:
        body = notify.render_ready_to_ship(["alpha"], "direct")
        self.assertIn("alpha", body)
        self.assertIn("clu ship --plan alpha --direct --yes", body)
        self.assertNotIn("PR mode", body)

    def test_single_plan_as_pr(self) -> None:
        body = notify.render_ready_to_ship(["alpha"], "as_pr")
        self.assertIn("PR mode", body)
        self.assertIn("clu ship --plan alpha --as-pr --yes", body)

    def test_batch_direct(self) -> None:
        body = notify.render_ready_to_ship(["alpha", "beta"], "direct")
        self.assertIn("clu ship --all-done --direct --yes", body)
        self.assertIn("alpha", body)
        self.assertIn("beta", body)

    def test_batch_as_pr(self) -> None:
        body = notify.render_ready_to_ship(["alpha", "beta"], "as_pr")
        self.assertIn("clu ship --all-done --as-pr --yes", body)
        self.assertIn("PR mode", body)


if __name__ == "__main__":
    unittest.main()
