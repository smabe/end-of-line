"""Tests for end_of_line.cross_plan_rules: runner, registry, and loader."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from end_of_line import cross_plan_rules, registry
from end_of_line import state as st
from end_of_line.config import ProjectConfig, load_project_config
from end_of_line.cross_plan_rules import (
    ProjectPlan,
    RuleResult,
    load_plans_for_project,
    register_rule,
    run_rules,
)
from tests import CluTestCase


def _make_state_file(state_path: Path, slug: str, plan_dir: str = "plans") -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = st.empty_state(slug, plan_dir)
    st.save_atomic(state_path, data)


class CrossPlanRulesTestCase(CluTestCase):
    """Base that isolates the global _RULES registry between tests."""

    def setUp(self) -> None:
        super().setUp()
        self._rules_snapshot = list(cross_plan_rules._RULES)
        cross_plan_rules._RULES.clear()

    def tearDown(self) -> None:
        cross_plan_rules._RULES[:] = self._rules_snapshot
        super().tearDown()


# ---------------------------------------------------------------------------
# Runner tests
# ---------------------------------------------------------------------------


class TestRunRulesEmptyRegistry(CrossPlanRulesTestCase):
    def test_run_rules_empty_registry_returns_none(self) -> None:
        result = run_rules(Path("/nonexistent"), [])
        self.assertIsNone(result)


class TestRunRulesFirstMatchWins(CrossPlanRulesTestCase):
    def test_run_rules_first_match_wins(self) -> None:
        calls: list[str] = []

        def rule1(project_root: Path, plans: list) -> RuleResult | None:
            calls.append("rule1")
            return RuleResult(events_per_plan={}, rule_name="rule1")

        def rule2(project_root: Path, plans: list) -> RuleResult | None:
            calls.append("rule2")
            return RuleResult(events_per_plan={}, rule_name="rule2")

        register_rule(rule1)
        register_rule(rule2)

        result = run_rules(Path("/x"), [])

        self.assertEqual(calls, ["rule1"])
        self.assertIsNotNone(result)
        self.assertEqual(result.rule_name, "rule1")  # type: ignore[union-attr]


class TestRunRulesSkipsSilentRules(CrossPlanRulesTestCase):
    def test_run_rules_skips_silent_rules(self) -> None:
        calls: list[str] = []

        def silent_rule(project_root: Path, plans: list) -> RuleResult | None:
            calls.append("silent")
            return None

        def emitting_rule(project_root: Path, plans: list) -> RuleResult | None:
            calls.append("emitting")
            return RuleResult(events_per_plan={}, rule_name="emitting")

        register_rule(silent_rule)
        register_rule(emitting_rule)

        result = run_rules(Path("/x"), [])

        self.assertEqual(calls, ["silent", "emitting"])
        self.assertIsNotNone(result)
        self.assertEqual(result.rule_name, "emitting")  # type: ignore[union-attr]


class TestRunRulesStableOrder(CrossPlanRulesTestCase):
    def test_run_rules_stable_iteration_order(self) -> None:
        order: list[int] = []

        def make_rule(n: int):
            def rule(project_root: Path, plans: list) -> RuleResult | None:
                order.append(n)
                return None

            return rule

        for i in range(5):
            register_rule(make_rule(i))

        run_rules(Path("/x"), [])

        self.assertEqual(order, [0, 1, 2, 3, 4])


class TestRegisterRule(CrossPlanRulesTestCase):
    def test_register_rule_appends_to_registry(self) -> None:
        self.assertEqual(len(cross_plan_rules._RULES), 0)

        def my_rule(project_root: Path, plans: list) -> RuleResult | None:
            return None

        register_rule(my_rule)
        self.assertEqual(len(cross_plan_rules._RULES), 1)
        self.assertIs(cross_plan_rules._RULES[0], my_rule)


class TestRunnerDoesNotTakeStateLocks(CrossPlanRulesTestCase):
    def test_runner_does_not_take_state_locks(self) -> None:
        """A rule can read state files during its execution (no lock held by runner)."""
        tmp = self.tmp_path / "proj"
        tmp.mkdir()
        state_dir = tmp / "plans" / ".orchestrator"
        state_path = state_dir / "test-plan.state.json"
        _make_state_file(state_path, "test-plan")

        read_inside_rule: list[bool] = []

        def probe_rule(project_root: Path, plans: list) -> RuleResult | None:
            # If the runner held the state lock here, this would deadlock.
            data = st.load(state_path)
            read_inside_rule.append("status" in data)
            return RuleResult(events_per_plan={}, rule_name="probe")

        register_rule(probe_rule)
        run_rules(tmp, [])

        self.assertTrue(read_inside_rule[0], "rule could read state file without deadlock")


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------


class TestLoadPlansForProject(CrossPlanRulesTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "myrepo"
        self.project.mkdir()
        (self.project / "plans").mkdir()
        self.cfg = load_project_config(self.project)

    def _state_path(self, slug: str) -> Path:
        return self.project / "plans" / ".orchestrator" / f"{slug}.state.json"

    def test_load_plans_for_project_no_plans_returns_empty(self) -> None:
        result = load_plans_for_project(self.project, self.cfg)
        self.assertEqual(result, [])

    def test_load_plans_for_project_one_plan_loads_state(self) -> None:
        registry.register(self.project, "my-plan")
        _make_state_file(self._state_path("my-plan"), "my-plan")

        result = load_plans_for_project(self.project, self.cfg)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].slug, "my-plan")
        self.assertIsInstance(result[0].state, dict)
        self.assertEqual(result[0].state_path.resolve(), self._state_path("my-plan").resolve())

    def test_load_plans_for_project_skips_missing_state(self) -> None:
        registry.register(self.project, "ghost-plan")
        # Deliberately do not create the state file.

        result = load_plans_for_project(self.project, self.cfg)

        self.assertEqual(result, [])

    def test_load_plans_for_project_skips_schema_mismatch(self) -> None:
        registry.register(self.project, "old-plan")
        state_path = self._state_path("old-plan")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        # Write a state file with the wrong schema_version.
        state_path.write_text(json.dumps({"schema_version": 999, "events": []}))

        result = load_plans_for_project(self.project, self.cfg)

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
