"""Tests for fix 6: spawned-task completion path + spawn cap."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import main
from tests import isolate_registry

PLAN = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `t-a.md` | thing | 1h |
"""


class SpawnedTaskTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "t.md").write_text(PLAN)
        main(["init", "--project", str(self.project), "--plan", "t"])
        self.state_path = self.project / "plans" / ".orchestrator" / "t.state.json"
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _spawn(self, title: str) -> int:
        return main(
            [
                "spawn",
                "--project",
                str(self.project),
                "--plan",
                "t",
                "--phase",
                "a",
                "--token",
                self.token,
                "--source",
                "simplify",
                "--title",
                title,
            ]
        )

    def test_spawn_then_task_done_with_token(self) -> None:
        self.assertEqual(self._spawn("dedupe foo"), 0)
        rc = main(
            [
                "task-done",
                "--project",
                str(self.project),
                "--plan",
                "t",
                "--token",
                self.token,
                "task-1",
            ]
        )
        self.assertEqual(rc, 0)
        data = json.loads(self.state_path.read_text())
        self.assertEqual(data["spawned_tasks"][0]["status"], "done")

    def test_task_done_force_bypasses_token(self) -> None:
        self.assertEqual(self._spawn("manual cleanup"), 0)
        # Release claim so there's no live worker
        with st.mutate(self.state_path) as data:
            st.release_claim(data, expected_token=self.token, expected_phase="a")
        rc = main(
            [
                "task-done",
                "--project",
                str(self.project),
                "--plan",
                "t",
                "--force",
                "task-1",
            ]
        )
        self.assertEqual(rc, 0)

    def test_task_done_without_token_or_force_fails(self) -> None:
        self.assertEqual(self._spawn("noop"), 0)
        rc = main(
            [
                "task-done",
                "--project",
                str(self.project),
                "--plan",
                "t",
                "task-1",  # no token, no force
            ]
        )
        self.assertEqual(rc, 4)

    def test_task_done_unknown_id(self) -> None:
        rc = main(
            [
                "task-done",
                "--project",
                str(self.project),
                "--plan",
                "t",
                "--force",
                "task-999",
            ]
        )
        self.assertEqual(rc, 6)

    def test_spawn_cap_enforced(self) -> None:
        # Default cap is 10
        for i in range(10):
            self.assertEqual(self._spawn(f"task {i}"), 0)
        # 11th should fail with rc=5
        rc = self._spawn("over")
        self.assertEqual(rc, 5)


if __name__ == "__main__":
    unittest.main()
