"""Tests for the #70 operator-dashboard instruction blocks in
clu_inbox_surface: attestation_refused and stalled_claim.

Mirrors test_inbox_hook_tool_stuck.py for the two new event classes.
When the supervisor / cmd_complete writes an attestation_refused or
stalled_claim event into the inbox, the hook surfaces it like any other
event PLUS appends an investigate-then-recommend instruction block
teaching the primary session what to do — investigate autonomously,
propose a recovery path, await operator approval before destructive
action.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from end_of_line import inbox

from tests import isolate_monitor_marker


def _run_hook(
    *, cwd: Path, xdg: Path, stdin_payload: str = "{}",
    timeout: float = 5.0,
) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(xdg)
    env["PYTHONPATH"] = str(
        Path(__file__).resolve().parent.parent,
    ) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "end_of_line.hooks.clu_inbox_surface"],
        cwd=str(cwd), env=env, input=stdin_payload,
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


class _DashboardHookBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        self.xdg = self.tmp
        self.proj = self.tmp / "proj"
        self.proj.mkdir()

    def _write_attestation_refused(self) -> None:
        inbox.write_event(
            type="attestation_refused",
            plan_slug="plan-x",
            project_root=str(self.proj),
            summary=(
                "Worker on plan-x/phase-a hit the verify gate "
                "(stamp missing or stale)"
            ),
            details={
                "phase_id": "phase-a",
                "gate": "verify",
                "stamped_at": None,
                "head_sha": "abc1234",
            },
        )

    def _write_stalled_claim(self) -> None:
        inbox.write_event(
            type="stalled_claim",
            plan_slug="plan-y",
            project_root=str(self.proj),
            summary=(
                "Claim on phase phase-b stalled 12min past lease"
            ),
            details={
                "phase_id": "phase-b",
                "stalled_min": 12,
                "claimed_by": {"pid": 99999, "token_fp": "deadbeef"},
            },
        )


class AttestationRefusedInstructionTest(_DashboardHookBase):
    def test_attestation_refused_event_surfaced_in_context(self) -> None:
        self._write_attestation_refused()
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("plan-x", ctx)
        self.assertIn("attestation_refused", ctx)

    def test_attestation_refused_appends_instruction_block(self) -> None:
        self._write_attestation_refused()
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        lowered = ctx.lower()
        # Investigate-then-recommend-then-await-approval contract.
        self.assertIn("investigate", lowered)
        self.assertIn("recommend", lowered)
        self.assertTrue(
            "operator-approval" in lowered or "operator approval" in lowered
            or "do not" in lowered,
            f"missing operator-approval guard:\n{ctx}",
        )
        # Must name the bypass commands the session shouldn't run unsanctioned.
        self.assertTrue(
            "--skip-verify" in lowered or "--skip-simplify" in lowered,
            f"instruction should name the destructive commands to gate:\n{ctx}",
        )

    def test_no_attestation_refused_no_block(self) -> None:
        inbox.write_event(
            type="halted", plan_slug="foo",
            project_root=str(self.proj), summary="just a halt",
        )
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("attestation gate refusal", ctx.lower())

    def test_instruction_block_appears_once_per_burst(self) -> None:
        self._write_attestation_refused()
        self._write_attestation_refused()
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(ctx.lower().count("attestation gate refusal"), 1)


class StalledClaimInstructionTest(_DashboardHookBase):
    def test_stalled_claim_event_surfaced_in_context(self) -> None:
        self._write_stalled_claim()
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("plan-y", ctx)
        self.assertIn("stalled_claim", ctx)

    def test_stalled_claim_appends_instruction_block(self) -> None:
        self._write_stalled_claim()
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        lowered = ctx.lower()
        self.assertIn("investigate", lowered)
        self.assertIn("recommend", lowered)
        self.assertTrue(
            "operator-approval" in lowered or "operator approval" in lowered
            or "do not" in lowered,
            f"missing operator-approval guard:\n{ctx}",
        )
        # Must name the recovery commands the operator may want gated.
        self.assertTrue(
            any(cmd in lowered for cmd in
                ("force-complete", "release-claim", "clu retry")),
            f"instruction should name recovery commands:\n{ctx}",
        )

    def test_no_stalled_claim_no_block(self) -> None:
        inbox.write_event(
            type="completed", plan_slug="foo",
            project_root=str(self.proj), summary="done",
        )
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("stalled-claim events", ctx.lower())

    def test_blocks_compose_when_multiple_wedge_types_present(self) -> None:
        # All three wedge types in the inbox at once — each block fires
        # exactly once.
        self._write_attestation_refused()
        self._write_stalled_claim()
        inbox.write_event(
            type="tool_stuck", plan_slug="plan-z",
            project_root=str(self.proj),
            summary="worker stuck",
            details={
                "phase_id": "phase-c", "worker_pid": 1, "descendant_pid": 2,
                "command": "x", "elapsed_seconds": 600, "cpu_seconds": 0,
            },
        )
        rc, out, err = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        lowered = ctx.lower()
        self.assertEqual(lowered.count("attestation gate refusal"), 1)
        self.assertEqual(lowered.count("stalled-claim events"), 1)
        self.assertEqual(lowered.count("stuck-tool events"), 1)


if __name__ == "__main__":
    unittest.main()
