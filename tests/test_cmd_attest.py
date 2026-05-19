"""Tests for `clu attest` — pure self-attestation; stamps attestations.simplify
with current HEAD on --simplify flag; extensible to future --lint / --type-check.
"""
from __future__ import annotations

import io
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase, make_worktree, plan_body, write_config


class CmdAttestTestCase(GitProjectTestCase):
    PLAN_BODY = plan_body("phase-a")

    def setUp(self) -> None:
        super().setUp()
        write_config(self.project)

    # ---- helpers ---------------------------------------------------------------

    def _simplify_stamp(self) -> dict | None:
        data = self._read()
        claim = data.get("current_claim") or {}
        return (claim.get("attestations") or {}).get("simplify")

    def _make_commit(self) -> str:
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "--allow-empty", "-m", "second"],
            check=True, capture_output=True,
        )
        return subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    # ---- happy path ------------------------------------------------------------

    def test_attest_simplify_stamps_attestation(self) -> None:
        token = self._claim("phase-a")
        rc = main(self._argv("attest", "--phase", "phase-a", "--token", token, "--simplify"))
        self.assertEqual(rc, ExitCode.OK)
        stamp = self._simplify_stamp()
        self.assertIsNotNone(stamp)
        self.assertEqual(stamp["commit_sha"], self.sha)

    def test_attest_simplify_overwrites_prior_stamp(self) -> None:
        token = self._claim("phase-a")
        main(self._argv("attest", "--phase", "phase-a", "--token", token, "--simplify"))
        second_sha = self._make_commit()
        rc = main(self._argv("attest", "--phase", "phase-a", "--token", token, "--simplify"))
        self.assertEqual(rc, ExitCode.OK)
        stamp = self._simplify_stamp()
        self.assertIsNotNone(stamp)
        self.assertEqual(stamp["commit_sha"], second_sha)

    # ---- no-flag guard ---------------------------------------------------------

    def test_attest_no_flag_errors(self) -> None:
        token = self._claim("phase-a")
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("attest", "--phase", "phase-a", "--token", token))
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("flag", buf.getvalue().lower())

    # ---- token / auth ----------------------------------------------------------

    def test_attest_simplify_token_validated(self) -> None:
        self._claim("phase-a")
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv(
                "attest", "--phase", "phase-a",
                "--token", "forged-token-xyz",
                "--simplify",
            ))
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertIsNone(self._simplify_stamp())

    def test_attest_simplify_requires_token(self) -> None:
        self._claim("phase-a")
        with self.assertRaises(SystemExit):
            main(self._argv("attest", "--phase", "phase-a", "--simplify"))

    def test_attest_simplify_requires_live_claim(self) -> None:
        # No current_claim — assert_claim_match raises ClaimMismatch.
        token = "session-no-claim"
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("attest", "--phase", "phase-a", "--token", token, "--simplify"))
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertIsNone(self._simplify_stamp())

    # ---- event log -------------------------------------------------------------

    def test_attest_emits_event(self) -> None:
        token = self._claim("phase-a")
        rc = main(self._argv("attest", "--phase", "phase-a", "--token", token, "--simplify"))
        self.assertEqual(rc, ExitCode.OK)
        events = [
            e for e in self._read()["events"]
            if e["type"] == st.EVENT_SIMPLIFY_STAMPED
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["commit_sha"], self.sha)

    # ---- extensible flag surface -----------------------------------------------

    def test_attest_extensible_flag_surface(self) -> None:
        # argparse accepts --simplify and help text mentions extensibility.
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                main(self._argv("attest", "--help"))
        except SystemExit:
            pass
        help_text = buf.getvalue()
        self.assertIn("--simplify", help_text)

    # ---- worktree-mode --------------------------------------------------------

    def test_attest_stamps_worktree_head_not_canonical(self) -> None:
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
            token = self._claim("phase-a")
            rc = main(self._argv("attest", "--phase", "phase-a", "--token", token, "--simplify"))
            self.assertEqual(rc, ExitCode.OK)
            stamp = self._simplify_stamp()
            self.assertIsNotNone(stamp)
            self.assertEqual(stamp["commit_sha"], wt_sha,
                             "stamp must use worktree HEAD, not canonical HEAD")
        finally:
            wt_tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
