"""Tests for the clu-plan SKILL.md --task-list auto-arm and parse rules."""

import pathlib
import unittest

_SKILL_PATH = (
    pathlib.Path(__file__).parent.parent / "end_of_line" / "skills" / "clu-plan" / "SKILL.md"
)


class TestCluPlanSkillWire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = _SKILL_PATH.read_text()

    def test_clu_plan_skill_arm_uses_task_list_flag(self):
        self.assertIn(
            "clu watch --project . --plan <slug> --task-list",
            self.content,
        )

    def test_clu_plan_skill_has_task_protocol_reaction_section(self):
        self.assertIn("Reacting to task-list protocol", self.content)

    def test_clu_plan_skill_mentions_task_create_handler(self):
        self.assertIn("TASK_CREATE", self.content)
        self.assertIn("TaskCreate", self.content)

    def test_clu_plan_skill_mentions_task_update_handler(self):
        self.assertIn("TASK_UPDATE", self.content)
        self.assertIn("TaskUpdate", self.content)
