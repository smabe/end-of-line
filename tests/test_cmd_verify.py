"""Tests for `clu verify` — runs the configured verify command and stamps
attestations.verify on success; exits non-zero without stamping on failure.
"""
from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import CONFIG_FILENAME
from tests import isolate_registry, make_worktree


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| phase-a | `test-plan-phase-a.md` | thing | 1h |
"""


def _write_config(project: Path, *, test_command: str | None = None,
                  quality_verify: str | None = None) -> None:
    cfg: dict = {"dispatch": {"command": "echo hi"}}
    if test_command is not None:
        cfg["test_command"] = test_command
    if quality_verify is not None:
        cfg.setdefault("quality", {})["verify_command"] = quality_verify
    (project / CONFIG_FILENAME).write_text(json.dumps(cfg))


class CmdVerifyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        _write_config(self.project, test_command="true")
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.email", "t@t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.name", "t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "--allow-empty", "-m", "init"],
            check=True, capture_output=True,
        )
        self.head_sha = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        self.assertEqual(
            main(["init", "--project", str(self.project), "--plan", "test-plan"]), 0,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- helpers ---------------------------------------------------------------

    def _claim(self, phase: str = "phase-a") -> str:
        with st.mutate(self.state_path) as data:
            return st.claim_phase(data, phase, lease_minutes=30)

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def _argv(self, *extra: str) -> list[str]:
        return ["verify", "--project", str(self.project), "--plan", "test-plan", *extra]

    def _attestation(self) -> dict | None:
        data = self._read()
        claim = data.get("current_claim") or {}
        return (claim.get("attestations") or {}).get("verify")

    # ---- happy path ------------------------------------------------------------

    def test_verify_runs_command_and_stamps_on_success(self) -> None:
        self._claim()
        rc = main(self._argv("--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        stamp = self._attestation()
        self.assertIsNotNone(stamp)
        self.assertEqual(stamp["commit_sha"], self.head_sha)

    def test_verify_does_not_stamp_on_failure(self) -> None:
        _write_config(self.project, test_command="false")
        self._claim()
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--phase", "phase-a"))
        self.assertNotEqual(rc, ExitCode.OK)
        self.assertIsNone(self._attestation())

    def test_verify_uses_quality_block_when_set(self) -> None:
        # quality.verify_command="true" wins over test_command="false"
        _write_config(self.project, test_command="false", quality_verify="true")
        self._claim()
        rc = main(self._argv("--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNotNone(self._attestation())

    def test_verify_falls_back_to_test_command(self) -> None:
        # No quality block; test_command="true" is the fallback.
        _write_config(self.project, test_command="true")
        self._claim()
        rc = main(self._argv("--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNotNone(self._attestation())

    def test_verify_errors_when_neither_configured(self) -> None:
        _write_config(self.project)
        self._claim()
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("no verify command configured", buf.getvalue())

    # ---- token / auth ----------------------------------------------------------

    def test_verify_worker_token_validated(self) -> None:
        token = self._claim()
        # Pass a forged token → CLAIM_MISMATCH, no stamp.
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv(
                "--phase", "phase-a",
                "--token", "forged-token-xyz",
            ))
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertIsNone(self._attestation())

    def test_verify_operator_no_token_works(self) -> None:
        # Operator omits --token; no validation, stamp lands.
        self._claim()
        rc = main(self._argv("--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNotNone(self._attestation())

    def test_verify_worker_correct_token_stamps(self) -> None:
        token = self._claim()
        rc = main(self._argv(
            "--phase", "phase-a",
            "--token", token,
        ))
        self.assertEqual(rc, ExitCode.OK)
        stamp = self._attestation()
        self.assertIsNotNone(stamp)
        self.assertEqual(stamp["commit_sha"], self.head_sha)

    # ---- HEAD-before-run -------------------------------------------------------

    def test_verify_captures_head_before_running(self) -> None:
        # Create a script that makes a new commit mid-run, then exits 0.
        # The stamp's commit_sha must be the pre-run HEAD, not the new one.
        script = self.project / "mid_run_commit.sh"
        script.write_text(
            "#!/bin/sh\n"
            f"git -C {self.project} commit --allow-empty -m mid-run\n"
            "exit 0\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        _write_config(self.project, test_command=str(script))
        self._claim()
        pre_sha = self.head_sha
        rc = main(self._argv("--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        post_sha = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertNotEqual(pre_sha, post_sha, "mid-run commit should have advanced HEAD")
        stamp = self._attestation()
        self.assertIsNotNone(stamp)
        self.assertEqual(stamp["commit_sha"], pre_sha)

    # ---- timeout ---------------------------------------------------------------

    def test_verify_timeout_returns_non_zero(self) -> None:
        self._claim()
        _real_run = subprocess.run

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and cmd and cmd[0] == "git":
                return _real_run(*args, **kwargs)
            raise subprocess.TimeoutExpired(cmd, 1)

        with mock.patch("subprocess.run", side_effect=_side_effect):
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = main(self._argv("--phase", "phase-a"))
        self.assertNotEqual(rc, ExitCode.OK)
        self.assertIsNone(self._attestation())
        self.assertIn("timed out", buf.getvalue().lower())

    # ---- event log -------------------------------------------------------------

    def test_verify_emits_event_on_success(self) -> None:
        self._claim()
        rc = main(self._argv("--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        events = [
            e for e in self._read()["events"]
            if e["type"] == st.EVENT_VERIFY_STAMPED
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["commit_sha"], self.head_sha)

    # ---- worktree-mode --------------------------------------------------------

    def test_verify_stamps_worktree_head_not_canonical(self) -> None:
        """In worktree-mode dispatch, the stamp must record the worktree HEAD."""
        wt_tmp, wt_path, wt_sha = make_worktree(self.project)
        try:
            self.assertNotEqual(wt_sha, self.head_sha)
            with st.mutate(self.state_path) as data:
                data["worktree"] = {
                    "path": str(wt_path),
                    "branch": "clu/p",
                    "base_ref": self.head_sha,
                }
            self._claim()
            rc = main(self._argv("--phase", "phase-a"))
            self.assertEqual(rc, ExitCode.OK)
            stamp = self._attestation()
            self.assertIsNotNone(stamp)
            self.assertEqual(stamp["commit_sha"], wt_sha,
                             "stamp must use worktree HEAD, not canonical HEAD")
        finally:
            wt_tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
