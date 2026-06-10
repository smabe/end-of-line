"""Tests for `clu verify` — runs the configured verify command and stamps
attestations.verify on success; exits non-zero without stamping on failure.
"""

from __future__ import annotations

import io
import stat
import subprocess
import unittest
from contextlib import redirect_stderr
from unittest import mock

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase, make_worktree, must, plan_body, write_config


class CmdVerifyTestCase(GitProjectTestCase):
    PLAN_BODY = plan_body("phase-a")

    def setUp(self) -> None:
        super().setUp()
        write_config(self.project, test_command="true")

    # ---- helpers ---------------------------------------------------------------

    def _attestation(self) -> dict | None:
        data = self._read()
        claim = data.get("current_claim") or {}
        return (claim.get("attestations") or {}).get("verify")

    # ---- happy path ------------------------------------------------------------

    def test_verify_runs_command_and_stamps_on_success(self) -> None:
        self._claim("phase-a")
        rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        stamp = must(self._attestation())
        self.assertEqual(stamp["commit_sha"], self.sha)

    def test_verify_does_not_stamp_on_failure(self) -> None:
        write_config(self.project, test_command="false")
        self._claim("phase-a")
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertNotEqual(rc, ExitCode.OK)
        self.assertIsNone(self._attestation())

    def test_verify_uses_quality_block_when_set(self) -> None:
        # quality.verify_command="true" wins over test_command="false"
        write_config(self.project, test_command="false", quality={"verify_command": "true"})
        self._claim("phase-a")
        rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNotNone(self._attestation())

    def test_verify_falls_back_to_test_command(self) -> None:
        # No quality block; test_command="true" is the fallback.
        write_config(self.project, test_command="true")
        self._claim("phase-a")
        rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNotNone(self._attestation())

    def test_verify_command_runs_through_shell(self) -> None:
        # `false || true` exits 0 only under shell semantics; without a
        # shell, `false` runs with `["||", "true"]` as argv and exits 1.
        write_config(self.project, quality={"verify_command": "false || true"})
        self._claim("phase-a")
        rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNotNone(self._attestation())

    def test_verify_chained_command_fails_when_second_stage_fails(self) -> None:
        # `true && false` exits 1 only under shell semantics; without a
        # shell, `true` swallows the rest of the line and exits 0.
        write_config(self.project, quality={"verify_command": "true && false"})
        self._claim("phase-a")
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertNotEqual(rc, ExitCode.OK)
        self.assertIsNone(self._attestation())

    def test_verify_failure_tail_includes_stdout(self) -> None:
        # basedpyright — the documented first stage of a chained
        # verify_command — reports errors on stdout, not stderr.
        write_config(
            self.project,
            quality={"verify_command": "echo typecheck-boom && false"},
        )
        self._claim("phase-a")
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertNotEqual(rc, ExitCode.OK)
        self.assertIn("typecheck-boom", buf.getvalue())

    def test_verify_errors_when_neither_configured(self) -> None:
        write_config(self.project)
        self._claim("phase-a")
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("no verify command configured", buf.getvalue())

    # ---- token / auth ----------------------------------------------------------

    def test_verify_worker_token_validated(self) -> None:
        token = self._claim("phase-a")
        # Pass a forged token → CLAIM_MISMATCH, no stamp.
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(
                self._argv(
                    "verify",
                    "--phase",
                    "phase-a",
                    "--token",
                    "forged-token-xyz",
                )
            )
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertIsNone(self._attestation())

    def test_verify_operator_no_token_works(self) -> None:
        # Operator omits --token; no validation, stamp lands.
        self._claim("phase-a")
        rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNotNone(self._attestation())

    def test_verify_worker_correct_token_stamps(self) -> None:
        token = self._claim("phase-a")
        rc = main(
            self._argv(
                "verify",
                "--phase",
                "phase-a",
                "--token",
                token,
            )
        )
        self.assertEqual(rc, ExitCode.OK)
        stamp = must(self._attestation())
        self.assertEqual(stamp["commit_sha"], self.sha)

    # ---- HEAD-before-run -------------------------------------------------------

    def test_verify_captures_head_before_running(self) -> None:
        # Create a script that makes a new commit mid-run, then exits 0.
        # The stamp's commit_sha must be the pre-run HEAD, not the new one.
        script = self.project / "mid_run_commit.sh"
        script.write_text(
            f"#!/bin/sh\ngit -C {self.project} commit --allow-empty -m mid-run\nexit 0\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        write_config(self.project, test_command=str(script))
        self._claim("phase-a")
        pre_sha = self.sha
        rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        post_sha = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertNotEqual(pre_sha, post_sha, "mid-run commit should have advanced HEAD")
        stamp = must(self._attestation())
        self.assertEqual(stamp["commit_sha"], pre_sha)

    # ---- timeout ---------------------------------------------------------------

    def test_verify_timeout_returns_non_zero(self) -> None:
        self._claim("phase-a")
        _real_run = subprocess.run

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and cmd and cmd[0] == "git":
                return _real_run(*args, **kwargs)
            raise subprocess.TimeoutExpired(cmd, 1)

        with mock.patch("subprocess.run", side_effect=_side_effect):
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertNotEqual(rc, ExitCode.OK)
        self.assertIsNone(self._attestation())
        self.assertIn("timed out", buf.getvalue().lower())

    # ---- event log -------------------------------------------------------------

    def test_verify_emits_event_on_success(self) -> None:
        self._claim("phase-a")
        rc = main(self._argv("verify", "--phase", "phase-a"))
        self.assertEqual(rc, ExitCode.OK)
        events = [e for e in self._read()["events"] if e["type"] == st.EVENT_VERIFY_STAMPED]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["commit_sha"], self.sha)

    # ---- worktree-mode --------------------------------------------------------

    def test_verify_stamps_worktree_head_not_canonical(self) -> None:
        """In worktree-mode dispatch, the stamp must record the worktree HEAD."""
        wt_tmp, wt_path, wt_sha = make_worktree(self.project)
        try:
            self.assertNotEqual(wt_sha, self.sha)
            with st.mutate(self.state_path) as data:
                data["worktree"] = {
                    "path": str(wt_path),
                    "branch": "clu/p",
                    "base_ref": self.sha,
                }
            self._claim("phase-a")
            rc = main(self._argv("verify", "--phase", "phase-a"))
            self.assertEqual(rc, ExitCode.OK)
            stamp = must(self._attestation())
            self.assertEqual(
                stamp["commit_sha"], wt_sha, "stamp must use worktree HEAD, not canonical HEAD"
            )
        finally:
            wt_tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
