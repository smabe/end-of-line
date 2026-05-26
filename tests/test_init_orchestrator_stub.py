"""Tests for cmd_init surfacing quality.verify_required in .orchestrator.json (#61 part 2).

When .orchestrator.json exists without a `quality` block, cmd_init
augments it with `{"verify_required": true}` so the knob is
discoverable. When the block already exists (any contents), cmd_init
leaves it untouched — the operator has already expressed intent.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from end_of_line.cli import main
from end_of_line.config import CONFIG_FILENAME
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| phase-a | `test-plan-phase-a.md` | thing | 1h |
"""


class InitQualityStubTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.cfg_path = self.project / CONFIG_FILENAME

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init(self) -> int:
        return main(["init", "--project", str(self.project), "--plan", "test-plan"])

    def _read_cfg(self) -> dict:
        return json.loads(self.cfg_path.read_text())

    # ---- augmentation -----------------------------------------------------

    def test_init_adds_quality_block_when_missing(self) -> None:
        self.cfg_path.write_text(json.dumps({"dispatch": {"command": "echo hi"}}))
        self.assertEqual(self._init(), 0)
        cfg = self._read_cfg()
        self.assertIn("quality", cfg)
        self.assertIs(cfg["quality"]["verify_required"], True)

    def test_init_preserves_existing_dispatch_and_other_keys(self) -> None:
        original = {
            "plan_dir": "plans",
            "dispatch": {"command": "echo hi", "kind": "shell"},
            "test_command": "pytest",
        }
        self.cfg_path.write_text(json.dumps(original))
        self.assertEqual(self._init(), 0)
        cfg = self._read_cfg()
        self.assertEqual(cfg["plan_dir"], "plans")
        self.assertEqual(cfg["dispatch"]["command"], "echo hi")
        self.assertEqual(cfg["test_command"], "pytest")
        self.assertIn("quality", cfg)

    # ---- idempotency: existing quality block left alone --------------------

    def test_init_leaves_existing_quality_block_untouched(self) -> None:
        original = {
            "dispatch": {"command": "echo hi"},
            "quality": {
                "verify_command": "make test",
                "verify_required": False,
            },
        }
        self.cfg_path.write_text(json.dumps(original))
        self.assertEqual(self._init(), 0)
        cfg = self._read_cfg()
        self.assertEqual(cfg["quality"]["verify_command"], "make test")
        self.assertIs(cfg["quality"]["verify_required"], False)

    def test_init_leaves_empty_quality_block_untouched(self) -> None:
        # Empty {} signals "operator has expressed intent to leave it empty"
        # — don't second-guess.
        original = {"dispatch": {"command": "echo hi"}, "quality": {}}
        self.cfg_path.write_text(json.dumps(original))
        self.assertEqual(self._init(), 0)
        cfg = self._read_cfg()
        self.assertEqual(cfg["quality"], {})

    def test_init_is_idempotent_across_repeated_inits(self) -> None:
        # Second plan on same project shouldn't churn the config.
        self.cfg_path.write_text(json.dumps({"dispatch": {"command": "echo hi"}}))
        self.assertEqual(self._init(), 0)
        first = self.cfg_path.read_text()
        # Add a second plan; init again
        (self.project / "plans" / "test-plan-2.md").write_text(PLAN_BODY)
        main(["init", "--project", str(self.project), "--plan", "test-plan-2"])
        second = self.cfg_path.read_text()
        self.assertEqual(first, second)

    # ---- missing config: don't create from nothing ------------------------

    def test_init_does_not_create_orchestrator_json_when_missing(self) -> None:
        # cmd_init should respect operator intent — if there's no
        # .orchestrator.json, clu shouldn't conjure one. (The notify-prompt
        # path may create one on its own; the stub helper alone doesn't.)
        self.assertFalse(self.cfg_path.exists())
        # init still succeeds — state.json gets written, registry updated.
        self.assertEqual(self._init(), 0)
        # No config created by the stub helper.
        self.assertFalse(self.cfg_path.exists())


if __name__ == "__main__":
    unittest.main()
