"""Tests for the auto_archive_rule cross-plan rule.

Phase auto-archive-rule of auto-archive-on-merge: a rule that fires when
a STATUS_DONE plan's worktree branch has been merged into origin/main.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import end_of_line.cli as _cli
from end_of_line import cross_plan_rules, notify, registry, state as st
from end_of_line.cross_plan_rules import (
    ProjectPlan,
    register_rule,
    run_rules,
)
from tests import CluTestCase, git as _git, make_git_project as _make_git_project


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_done_plan_with_worktree(
    project: Path,
    slug: str,
    branch: str = "clu/alpha",
) -> ProjectPlan:
    state_path = project / "plans" / ".orchestrator" / f"{slug}.state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = st.empty_state(slug, "plans")
    data["status"] = st.STATUS_DONE
    data["worktree"] = {
        "branch": branch,
        "path": f"/tmp/nonexistent-wt-{slug}",
        "base_ref": "abc123",
    }
    st.save_atomic(state_path, data)
    return ProjectPlan(slug, dict(data), state_path)


def _make_tracked_plan_file(project: Path, slug: str) -> None:
    plan_file = project / "plans" / f"{slug}.md"
    plan_file.write_text(
        f"# {slug}\n\n## Sessions index\n\n"
        "| Session | Plan file | Scope | Effort |\n"
        "|---|---|---|---|\n"
        f"| run | `{slug}-run.md` | do stuff | 1h |\n"
    )
    _git(project, "add", f"plans/{slug}.md")
    _git(project, "commit", "-m", f"add plan {slug}")


# ---------------------------------------------------------------------------
# base
# ---------------------------------------------------------------------------

class _AutoArchiveRuleBase(CluTestCase):
    """Isolates _RULES and registers only auto_archive_rule per test."""

    def setUp(self) -> None:
        super().setUp()
        self._rules_snapshot = list(cross_plan_rules._RULES)
        cross_plan_rules._RULES.clear()
        from end_of_line.cross_plan_rules import auto_archive_rule  # noqa: PLC0415
        register_rule(auto_archive_rule)

    def tearDown(self) -> None:
        cross_plan_rules._RULES[:] = self._rules_snapshot
        super().tearDown()


# ---------------------------------------------------------------------------
# skipped cases (no real git needed)
# ---------------------------------------------------------------------------

class TestAutoArchiveRuleSkipped(_AutoArchiveRuleBase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "proj"
        self.project.mkdir()
        (self.project / "plans").mkdir()

    def _plan(self, slug: str, status: str, branch: str | None = "clu/alpha") -> ProjectPlan:
        state_path = self.project / "plans" / ".orchestrator" / f"{slug}.state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        data = st.empty_state(slug, "plans")
        data["status"] = status
        if branch is not None:
            data["worktree"] = {
                "branch": branch,
                "path": f"/tmp/nonexistent-wt-{slug}",
                "base_ref": "abc",
            }
        st.save_atomic(state_path, data)
        return ProjectPlan(slug, dict(data), state_path)

    def test_skipped_when_status_not_done(self) -> None:
        p = self._plan("alpha", st.STATUS_RUNNING)
        with mock.patch("end_of_line.state.is_branch_merged_into", return_value=True):
            result = run_rules(self.project, [p])
        self.assertIsNone(result)

    def test_skipped_when_no_worktree_record(self) -> None:
        p = self._plan("alpha", st.STATUS_DONE, branch=None)
        with mock.patch("end_of_line.state.is_branch_merged_into", return_value=True):
            result = run_rules(self.project, [p])
        self.assertIsNone(result)

    def test_skipped_when_branch_not_merged(self) -> None:
        p = self._plan("alpha", st.STATUS_DONE)
        with mock.patch("end_of_line.state.is_branch_merged_into", return_value=False):
            result = run_rules(self.project, [p])
        self.assertIsNone(result)

    def test_disabled_by_auto_archive_false_via_getattr(self) -> None:
        p = self._plan("alpha", st.STATUS_DONE)
        fake_cfg = mock.MagicMock()
        fake_cfg.auto_archive = False
        with (
            mock.patch(
                "end_of_line.cross_plan_rules.load_project_config",
                return_value=fake_cfg,
            ),
            mock.patch("end_of_line.state.is_branch_merged_into", return_value=True),
            mock.patch.object(_cli, "_perform_archive") as mock_archive,
        ):
            result = run_rules(self.project, [p])
        self.assertIsNone(result)
        mock_archive.assert_not_called()


# ---------------------------------------------------------------------------
# fires + idempotent + ordering (real git project)
# ---------------------------------------------------------------------------

class TestAutoArchiveRuleFires(_AutoArchiveRuleBase):
    def setUp(self) -> None:
        super().setUp()
        self.project = _make_git_project(self.tmp_path)

    def test_fires_when_done_and_branch_merged(self) -> None:
        _make_tracked_plan_file(self.project, "alpha")
        p = _make_done_plan_with_worktree(self.project, "alpha", branch="clu/alpha")
        registry.register(self.project, "alpha")

        with (
            mock.patch("end_of_line.state.is_branch_merged_into", return_value=True),
            mock.patch.object(_cli, "_perform_archive", wraps=_cli._perform_archive) as spy,
        ):
            result = run_rules(self.project, [p])

        self.assertIsNotNone(result)
        self.assertEqual(result.rule_name, "auto_archive")
        spy.assert_called_once()
        _, call_kwargs = spy.call_args
        self.assertTrue(call_kwargs.get("unregister"))
        # Plan file moved to shipped/
        self.assertFalse((self.project / "plans" / "alpha.md").exists())
        self.assertTrue((self.project / "plans" / "shipped" / "alpha.md").exists())
        # Registry entry pruned
        entries = registry.entries_for_project(self.project)
        self.assertFalse(any(e.plan_slug == "alpha" for e in entries))
        # Notify includes KIND_PLAN_AUTO_ARCHIVED mentioning slug + branch
        kinds = [k for k, _ in result.notifies]
        self.assertIn(notify.KIND_PLAN_AUTO_ARCHIVED, kinds)
        bodies = [b for k, b in result.notifies if k == notify.KIND_PLAN_AUTO_ARCHIVED]
        self.assertTrue(any("alpha" in b and "clu/alpha" in b for b in bodies))

    def test_idempotent_after_fire(self) -> None:
        _make_tracked_plan_file(self.project, "alpha")
        p = _make_done_plan_with_worktree(self.project, "alpha", branch="clu/alpha")
        registry.register(self.project, "alpha")

        with mock.patch("end_of_line.state.is_branch_merged_into", return_value=True):
            first = run_rules(self.project, [p])
        self.assertIsNotNone(first)

        # Reload state — worktree is now None after archive
        state_path = self.project / "plans" / ".orchestrator" / "alpha.state.json"
        updated = st.load(state_path)
        updated_p = ProjectPlan("alpha", updated, state_path)

        with mock.patch("end_of_line.state.is_branch_merged_into", return_value=True):
            second = run_rules(self.project, [updated_p])
        self.assertIsNone(second)

    def test_first_eligible_wins_in_registry_order(self) -> None:
        _make_tracked_plan_file(self.project, "alpha")
        _make_tracked_plan_file(self.project, "beta")
        p_alpha = _make_done_plan_with_worktree(self.project, "alpha", branch="clu/alpha")
        p_beta = _make_done_plan_with_worktree(self.project, "beta", branch="clu/beta")
        registry.register(self.project, "alpha")
        registry.register(self.project, "beta")

        with mock.patch("end_of_line.state.is_branch_merged_into", return_value=True):
            result = run_rules(self.project, [p_alpha, p_beta])

        self.assertIsNotNone(result)
        # Only alpha (first in list) archived
        self.assertFalse((self.project / "plans" / "alpha.md").exists())
        self.assertTrue((self.project / "plans" / "shipped" / "alpha.md").exists())
        self.assertTrue((self.project / "plans" / "beta.md").exists())
        # alpha registry entry pruned, beta still present
        slugs = {e.plan_slug for e in registry.entries_for_project(self.project)}
        self.assertNotIn("alpha", slugs)
        self.assertIn("beta", slugs)
