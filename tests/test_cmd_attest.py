"""Tests for `clu attest` — pure self-attestation; stamps attestations.simplify
with current HEAD on --simplify flag; extensible to future --lint / --type-check.
"""
from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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


def _write_config(project: Path) -> None:
    cfg: dict = {"dispatch": {"command": "echo hi"}}
    (project / CONFIG_FILENAME).write_text(json.dumps(cfg))


class CmdAttestTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        _write_config(self.project)
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
        return ["attest", "--project", str(self.project), "--plan", "test-plan", *extra]

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
        token = self._claim()
        rc = main(self._argv("--phase", "phase-a", "--token", token, "--simplify"))
        self.assertEqual(rc, ExitCode.OK)
        stamp = self._simplify_stamp()
        self.assertIsNotNone(stamp)
        self.assertEqual(stamp["commit_sha"], self.head_sha)

    def test_attest_simplify_overwrites_prior_stamp(self) -> None:
        token = self._claim()
        main(self._argv("--phase", "phase-a", "--token", token, "--simplify"))
        second_sha = self._make_commit()
        rc = main(self._argv("--phase", "phase-a", "--token", token, "--simplify"))
        self.assertEqual(rc, ExitCode.OK)
        stamp = self._simplify_stamp()
        self.assertIsNotNone(stamp)
        self.assertEqual(stamp["commit_sha"], second_sha)

    # ---- no-flag guard ---------------------------------------------------------

    def test_attest_no_flag_errors(self) -> None:
        token = self._claim()
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--phase", "phase-a", "--token", token))
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("flag", buf.getvalue().lower())

    # ---- token / auth ----------------------------------------------------------

    def test_attest_simplify_token_validated(self) -> None:
        self._claim()
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv(
                "--phase", "phase-a",
                "--token", "forged-token-xyz",
                "--simplify",
            ))
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertIsNone(self._simplify_stamp())

    def test_attest_simplify_requires_token(self) -> None:
        self._claim()
        with self.assertRaises(SystemExit):
            main(self._argv("--phase", "phase-a", "--simplify"))

    def test_attest_simplify_requires_live_claim(self) -> None:
        # No current_claim — assert_claim_match raises ClaimMismatch.
        token = "session-no-claim"
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--phase", "phase-a", "--token", token, "--simplify"))
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertIsNone(self._simplify_stamp())

    # ---- event log -------------------------------------------------------------

    def test_attest_emits_event(self) -> None:
        token = self._claim()
        rc = main(self._argv("--phase", "phase-a", "--token", token, "--simplify"))
        self.assertEqual(rc, ExitCode.OK)
        events = [
            e for e in self._read()["events"]
            if e["type"] == st.EVENT_SIMPLIFY_STAMPED
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["commit_sha"], self.head_sha)

    # ---- extensible flag surface -----------------------------------------------

    def test_attest_extensible_flag_surface(self) -> None:
        # argparse accepts --simplify and help text mentions extensibility.
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                main(self._argv("--help"))
        except SystemExit:
            pass
        help_text = buf.getvalue()
        self.assertIn("--simplify", help_text)

    # ---- worktree-mode --------------------------------------------------------

    def test_attest_stamps_worktree_head_not_canonical(self) -> None:
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
            token = self._claim()
            rc = main(self._argv("--phase", "phase-a", "--token", token, "--simplify"))
            self.assertEqual(rc, ExitCode.OK)
            stamp = self._simplify_stamp()
            self.assertIsNotNone(stamp)
            self.assertEqual(stamp["commit_sha"], wt_sha,
                             "stamp must use worktree HEAD, not canonical HEAD")
        finally:
            wt_tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
