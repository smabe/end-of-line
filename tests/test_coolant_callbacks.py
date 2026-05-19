"""Integration tests: each worker-callback handler fires coolant.emit_stop.

The `release_claim_and_emit` wrapper is unit-tested in `test_state.py`; this
file confirms each of the four cli.py callback handlers (`cmd_complete`,
`cmd_block`, `cmd_force_complete`, `cmd_release_claim`) routes through it.
Patches `end_of_line.state.coolant.emit_stop` and asserts the emit fires
exactly once with the expected agent_id / session_id.
"""
from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from end_of_line import coolant, state as st
from end_of_line.cli import ExitCode, main
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


class CoolantCallbacksTestCase(unittest.TestCase):
    """Shared fixture: a real git repo + initialized plan, ready to claim."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        _git("config", "user.email", "t@t", cwd=self.project)
        _git("config", "user.name", "t", cwd=self.project)
        _git("commit", "--allow-empty", "-m", "base", cwd=self.project)
        self.sha = _git("rev-parse", "HEAD", cwd=self.project)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        self.assertEqual(
            main(["init", "--project", str(self.project), "--plan", "test-plan"]), 0,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _claim(self, phase: str = "a") -> str:
        with st.mutate(self.state_path) as data:
            return st.claim_phase(data, phase, lease_minutes=30)

    def _argv(self, cmd: str, *extra: str) -> list[str]:
        return [
            cmd,
            "--project", str(self.project),
            "--plan", "test-plan",
            *extra,
        ]

    def _expect_one_stop_call(self, emit, *, token: str, phase: str = "a") -> None:
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        self.assertEqual(kwargs["session_id"], token)
        self.assertEqual(
            kwargs["agent_id"], coolant.format_agent_id("test-plan", phase),
        )
        self.assertEqual(kwargs["agent_type"], coolant.AGENT_TYPE)

    def test_cmd_complete_emits_stop(self) -> None:
        token = self._claim()
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            rc = main(self._argv(
                "complete", "--phase", "a", "--token", token,
                "--commit", self.sha,
                "--skip-verify", "--skip-simplify",
            ))
        self.assertEqual(rc, ExitCode.OK)
        self._expect_one_stop_call(emit, token=token)

    def test_cmd_block_emits_stop(self) -> None:
        token = self._claim()
        buf = io.StringIO()
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            with redirect_stderr(buf), redirect_stdout(buf):
                rc = main(self._argv(
                    "block", "--phase", "a", "--token", token,
                    "--question", "stuck?", "--option", "A", "--option", "B",
                ))
        self.assertEqual(rc, ExitCode.OK)
        self._expect_one_stop_call(emit, token=token)

    def test_cmd_force_complete_emits_stop(self) -> None:
        token = self._claim()
        buf = io.StringIO()
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            with redirect_stderr(buf), redirect_stdout(buf):
                rc = main(self._argv(
                    "force-complete", "--phase", "a", "--commit", self.sha,
                    "--reason", "zombie",
                ))
        self.assertEqual(rc, ExitCode.OK)
        self._expect_one_stop_call(emit, token=token)

    def test_cmd_release_claim_emits_stop(self) -> None:
        token = self._claim()
        buf = io.StringIO()
        # Fresh-heartbeat live claim needs --force to release.
        with patch("end_of_line.state.coolant.emit_stop") as emit:
            with redirect_stderr(buf), redirect_stdout(buf):
                rc = main(self._argv("release-claim", "--force"))
        self.assertEqual(rc, ExitCode.OK)
        self._expect_one_stop_call(emit, token=token)


if __name__ == "__main__":
    unittest.main()
