"""Tests for the per-project verify-required opt-out (#61 part 1).

When `quality.verify_required: false` is set in `.orchestrator.json`,
`cmd_complete` no longer refuses without a verify stamp. The simplify
gate is unaffected (still refuses on large diffs without a simplify
stamp). An audit event records the policy bypass per-phase.
"""
from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import CONFIG_FILENAME
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| phase-a | `test-plan-phase-a.md` | thing | 1h |
"""


def _write_config(project: Path, *, quality: dict | None = None) -> None:
    cfg: dict = {"dispatch": {"command": "echo hi"}}
    if quality:
        cfg["quality"] = quality
    (project / CONFIG_FILENAME).write_text(json.dumps(cfg))


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


class VerifyOptOutTestCase(unittest.TestCase):
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
        self.base_sha = _git("rev-parse", "HEAD", cwd=self.project)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init(self, *, quality: dict | None = None) -> None:
        _write_config(self.project, quality=quality)
        self.assertEqual(
            main(["init", "--project", str(self.project), "--plan", "test-plan"]), 0,
        )

    def _claim(self, phase: str = "phase-a") -> str:
        with st.mutate(self.state_path) as data:
            token = st.claim_phase(data, phase, lease_minutes=30)
            data["current_claim"]["head_sha_at_claim"] = self.base_sha
            return token

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def _events_of_type(self, type_: str) -> list[dict]:
        return [e for e in self._read()["events"] if e["type"] == type_]

    # ---- default behavior (verify_required: true) preserved -------------------

    def test_default_policy_refuses_without_verify_stamp(self) -> None:
        # Regression: existing #55 behavior must be intact.
        self._init()
        token = self._claim()
        argv = [
            "complete", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "phase-a", "--token", token,
        ]
        with redirect_stderr(io.StringIO()) as buf:
            rc = main(argv)
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("verify gate", buf.getvalue())

    # ---- opt-out policy bypasses verify gate ----------------------------------

    def test_opt_out_policy_skips_verify_refusal(self) -> None:
        self._init(quality={"verify_required": False})
        token = self._claim()
        argv = [
            "complete", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "phase-a", "--token", token,
        ]
        rc = main(argv)
        self.assertEqual(rc, 0)

    def test_opt_out_emits_verify_policy_skipped_event(self) -> None:
        self._init(quality={"verify_required": False})
        token = self._claim()
        argv = [
            "complete", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "phase-a", "--token", token,
        ]
        self.assertEqual(main(argv), 0)
        events = self._events_of_type("verify_policy_skipped")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["phase"], "phase-a")

    def test_opt_out_does_not_emit_operator_skip_verify_event(self) -> None:
        # Policy bypass is its own signal; the operator flag event is for
        # explicit --skip-verify and should NOT fire just because the
        # policy is opt-out.
        self._init(quality={"verify_required": False})
        token = self._claim()
        argv = [
            "complete", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "phase-a", "--token", token,
        ]
        self.assertEqual(main(argv), 0)
        self.assertEqual(self._events_of_type("operator_skip_verify"), [])

    # ---- simplify gate unaffected by verify opt-out ---------------------------

    def test_opt_out_does_not_bypass_simplify_gate(self) -> None:
        # Big diff (>30 lines) without simplify stamp → still refused.
        self._init(quality={"verify_required": False})
        token = self._claim()
        # Generate a 50-line diff to trip the simplify threshold.
        (self.project / "x.txt").write_text("\n".join(f"l{i}" for i in range(50)))
        _git("add", "x.txt", cwd=self.project)
        _git("commit", "-m", "big", cwd=self.project)
        argv = [
            "complete", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "phase-a", "--token", token,
        ]
        with redirect_stderr(io.StringIO()) as buf:
            rc = main(argv)
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("simplify gate", buf.getvalue())

    # ---- --skip-verify flag still works regardless of policy ------------------

    def test_operator_skip_verify_flag_under_default_policy(self) -> None:
        self._init()  # default verify_required=True
        token = self._claim()
        argv = [
            "complete", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "phase-a", "--token", token, "--skip-verify",
        ]
        self.assertEqual(main(argv), 0)
        self.assertEqual(len(self._events_of_type("operator_skip_verify")), 1)

    def test_operator_skip_verify_flag_under_opt_out_emits_both(self) -> None:
        # Belt-and-suspenders: operator flag + policy opt-out both apply.
        # Both signals should record (orthogonal).
        self._init(quality={"verify_required": False})
        token = self._claim()
        argv = [
            "complete", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "phase-a", "--token", token, "--skip-verify",
        ]
        self.assertEqual(main(argv), 0)
        self.assertEqual(len(self._events_of_type("operator_skip_verify")), 1)
        self.assertEqual(len(self._events_of_type("verify_policy_skipped")), 1)

    # ---- config parsing ------------------------------------------------------

    def test_verify_required_defaults_to_true(self) -> None:
        from end_of_line.config import load_project_config
        _write_config(self.project)
        cfg = load_project_config(self.project)
        self.assertTrue(cfg.quality.verify_required)

    def test_verify_required_explicit_false(self) -> None:
        from end_of_line.config import load_project_config
        _write_config(self.project, quality={"verify_required": False})
        cfg = load_project_config(self.project)
        self.assertFalse(cfg.quality.verify_required)

    def test_verify_required_non_bool_rejected(self) -> None:
        from end_of_line.config import load_project_config, ConfigError
        _write_config(self.project, quality={"verify_required": "false"})  # string, not bool
        with self.assertRaises(ConfigError):
            load_project_config(self.project)


if __name__ == "__main__":
    unittest.main()
