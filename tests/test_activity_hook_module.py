"""Thin activity hook entry point — `python3 -m end_of_line.activity_hook`.

Designed for hot-path invocation from Claude Code's PreToolUse / PostToolUse
hooks. Imports only `end_of_line.state` (and stdlib) so it skips the full
orchestrator import cost of `clu activity`. Behavior must match `cli.cmd_activity`
end-to-end — both routes delegate to `state.stamp_activity_marker`.
"""
from __future__ import annotations

import subprocess
import sys
import unittest

from end_of_line import state as st
from end_of_line.cli import main as cli_main
from tests import CluTestCase


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""


class ActivityHookModuleTestCase(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        cli_main(["init", "--project", str(self.project), "--plan", "test-plan"])
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def _argv(self, *extra: str) -> list[str]:
        return [
            "--project", str(self.project),
            "--plan", "test-plan",
            "--phase", "a",
            "--token", self.token,
            *extra,
        ]

    def test_start_bash_stamps_active_marker(self) -> None:
        from end_of_line import activity_hook
        rc = activity_hook.main(self._argv("--start-bash"))
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertIn("active_tool_started_at", data["current_claim"])

    def test_end_bash_clears_active_marker(self) -> None:
        from end_of_line import activity_hook
        activity_hook.main(self._argv("--start-bash"))
        rc = activity_hook.main(self._argv("--end-bash"))
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertNotIn("active_tool_started_at", data["current_claim"])

    def test_wrong_token_rejected(self) -> None:
        from end_of_line import activity_hook
        rc = activity_hook.main([
            "--project", str(self.project), "--plan", "test-plan",
            "--phase", "a", "--token", "session-wrong00000000",
            "--start-bash",
        ])
        self.assertNotEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertNotIn("active_tool_started_at", data["current_claim"])

    def test_no_action_flag_rejected(self) -> None:
        from end_of_line import activity_hook
        rc = activity_hook.main([
            "--project", str(self.project), "--plan", "test-plan",
            "--phase", "a", "--token", self.token,
        ])
        self.assertNotEqual(rc, 0)


class ActivityHookImportIsolationTestCase(unittest.TestCase):
    """Regression guard — the thin entry point must not transitively import
    the heavy orchestrator surface (cli, dispatch, fleet, monitor, etc.).
    A future contributor adding `from .cli import _die` would silently
    re-inflate the cold-start cost."""

    HEAVY_MODULES = (
        "end_of_line.cli",
        "end_of_line.dispatch",
        "end_of_line.fleet",
        "end_of_line.monitor",
        "end_of_line.queue",
        "end_of_line.supervisor",
        "end_of_line.watch",
        "end_of_line.notify",
    )

    def test_import_does_not_drag_in_orchestrator(self) -> None:
        # Run in a fresh subprocess so this test's own imports don't pollute.
        code = (
            "import sys, json; "
            "import end_of_line.activity_hook; "
            "print(json.dumps(sorted(m for m in sys.modules if m.startswith('end_of_line'))))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        )
        import json as _json
        loaded = _json.loads(result.stdout)
        for heavy in self.HEAVY_MODULES:
            self.assertNotIn(
                heavy, loaded,
                f"thin activity_hook accidentally imported {heavy} — loaded: {loaded}",
            )


if __name__ == "__main__":
    unittest.main()
