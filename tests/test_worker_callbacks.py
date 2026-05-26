"""Token validation + SHA quality gate for worker-side CLI commands."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import main
from tests import isolate_registry

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
"""


class WorkerCallbackTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        # Init real git repo so SHA validation can succeed
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "user.name", "t"], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )
        self.sha = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        # Init clu state + claim phase a
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        main(["init", "--project", str(self.project), "--plan", "test-plan"])
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> int:
        return main(list(args))

    def test_complete_requires_matching_token(self) -> None:
        rc = self._run(
            "complete",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            "session-deadbeefdeadbeef",
            "--commit",
            self.sha,
        )
        self.assertEqual(rc, 4)

    def test_complete_rejects_unknown_sha(self) -> None:
        rc = self._run(
            "complete",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            self.token,
            "--commit",
            "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        )
        self.assertEqual(rc, 3)

    def test_complete_succeeds_with_valid_token_and_sha(self) -> None:
        rc = self._run(
            "complete",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            self.token,
            "--commit",
            self.sha,
            "--skip-verify",
            "--skip-simplify",
        )
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertIn("a", st.completed_phase_ids(data))
        self.assertIsNone(data["current_claim"])

    def test_block_requires_matching_token(self) -> None:
        rc = self._run(
            "block",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            "session-wrong0000000000ff",
            "--question",
            "huh?",
            "--option",
            "A",
        )
        self.assertEqual(rc, 4)
        data = st.load(self.state_path)
        # Claim should still exist; no blocker recorded
        self.assertIsNotNone(data["current_claim"])
        self.assertEqual(len(data["blockers"]), 0)

    def test_block_succeeds_with_token(self) -> None:
        rc = self._run(
            "block",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            self.token,
            "--question",
            "huh?",
            "--option",
            "A",
            "--option",
            "B",
        )
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertEqual(len(data["blockers"]), 1)
        self.assertIsNone(data["current_claim"])

    def test_spawn_requires_matching_token(self) -> None:
        rc = self._run(
            "spawn",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            "session-bogusbogusbogus0",
            "--title",
            "cleanup",
        )
        self.assertEqual(rc, 4)


if __name__ == "__main__":
    unittest.main()
