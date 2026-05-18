"""Operator command: `clu force-complete` recovers a stalled phase whose
worker died after writing code but before calling `clu complete` (#48).

Distinct from `cmd_complete` (worker, token-gated) and `cmd_release_claim`
(operator, leaves the phase incomplete). Releases the claim, validates
commits, and emits both `EVENT_OPERATOR_FORCE_COMPLETE` (audit) and
`EVENT_PHASE_COMPLETED` (state) so the supervisor's plan_done detection
fires normally on the next tick.
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
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
| B | `test-plan-b.md` | other | 1h |
"""


class ForceCompleteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        # Real git repo so commit SHA validation can succeed.
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
        self.sha = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        self.assertEqual(
            main(["init", "--project", str(self.project), "--plan", "test-plan"]),
            0,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- helpers --------------------------------------------------------------

    def _claim(self, phase: str = "a") -> str:
        with st.mutate(self.state_path) as data:
            return st.claim_phase(data, phase, lease_minutes=30)

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def _argv(self, *extra: str) -> list[str]:
        return [
            "force-complete",
            "--project", str(self.project),
            "--plan", "test-plan",
            *extra,
        ]

    def _events(self, *types: str) -> list[dict]:
        return [e for e in self._read()["events"] if e["type"] in types]

    # ---- happy path -----------------------------------------------------------

    def test_force_complete_marks_phase_completed(self) -> None:
        self._claim("a")
        rc = main(self._argv("--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.OK)
        data = self._read()
        self.assertIn("a", st.completed_phase_ids(data))

    def test_force_complete_releases_claim(self) -> None:
        self._claim("a")
        rc = main(self._argv("--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNone(self._read()["current_claim"])

    def test_force_complete_appends_operator_force_event(self) -> None:
        self._claim("a")
        rc = main(self._argv(
            "--phase", "a", "--commit", self.sha, "--reason", "zombie worker",
        ))
        self.assertEqual(rc, ExitCode.OK)
        evts = self._events(st.EVENT_OPERATOR_FORCE_COMPLETE)
        self.assertEqual(len(evts), 1)
        evt = evts[0]
        self.assertEqual(evt["phase"], "a")
        self.assertEqual(evt["commits"], [self.sha])
        self.assertEqual(evt["reason"], "zombie worker")
        self.assertTrue(evt["operator"])

    def test_force_complete_appends_phase_completed_event(self) -> None:
        self._claim("a")
        main(self._argv("--phase", "a", "--commit", self.sha))
        evts = self._events(st.EVENT_PHASE_COMPLETED)
        self.assertEqual(len(evts), 1)
        self.assertEqual(evts[0]["phase"], "a")
        self.assertEqual(evts[0]["commits"], [self.sha])

    def test_force_complete_works_without_active_claim(self) -> None:
        # Lease expired; supervisor released the claim. Phase started events
        # are still in the log, so --really is NOT required.
        self._claim("a")
        with st.mutate(self.state_path) as data:
            st.release_claim(data)
        rc = main(self._argv("--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("a", st.completed_phase_ids(self._read()))

    def test_force_complete_accepts_no_commit(self) -> None:
        # Operator might force-complete a doc-only phase with no SHA pointer.
        self._claim("a")
        rc = main(self._argv("--phase", "a"))
        self.assertEqual(rc, ExitCode.OK)
        evt = self._events(st.EVENT_OPERATOR_FORCE_COMPLETE)[0]
        self.assertEqual(evt["commits"], [])

    # ---- refusals -------------------------------------------------------------

    def test_refuses_when_phase_already_completed(self) -> None:
        self._claim("a")
        main(self._argv("--phase", "a", "--commit", self.sha))
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("already", buf.getvalue().lower())

    def test_refuses_unknown_phase(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--phase", "nope", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)

    def test_refuses_never_started_phase_without_really(self) -> None:
        # phase b exists in sub-plans but no phase_started event for it.
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--phase", "b", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("--really", buf.getvalue())

    def test_really_bypasses_never_started_check(self) -> None:
        rc = main(self._argv(
            "--phase", "b", "--commit", self.sha, "--really",
        ))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("b", st.completed_phase_ids(self._read()))

    def test_refuses_bogus_commit_sha(self) -> None:
        self._claim("a")
        bogus = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--phase", "a", "--commit", bogus))
        self.assertEqual(rc, ExitCode.BAD_SHA)
        # No state mutation on rejected SHA.
        self.assertNotIn("a", st.completed_phase_ids(self._read()))

    def test_refuses_invalid_phase_slug(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("--phase", "Bad/Slug", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.INVALID_SLUG)

    # ---- claim release semantics ---------------------------------------------

    def test_force_complete_releases_foreign_token_claim(self) -> None:
        # Claim is held by some other token; force-complete should clear it
        # without token validation (operator override).
        token = self._claim("a")
        # Confirm the claim exists with this token first.
        self.assertEqual(self._read()["current_claim"]["claimed_by"], token)
        rc = main(self._argv("--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNone(self._read()["current_claim"])

    def test_force_complete_releases_claim_on_different_phase(self) -> None:
        # Edge case: claim is on phase b but operator force-completes a (which
        # was already worked on prior — phase_started in log, claim moved on).
        # Phase a needs phase_started in events; simulate by claim/release.
        self._claim("a")
        with st.mutate(self.state_path) as data:
            st.release_claim(data)
        self._claim("b")
        rc = main(self._argv("--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.OK)
        # Claim on phase b should NOT be touched — only matching-phase claims clear.
        claim = self._read()["current_claim"]
        self.assertIsNotNone(claim)
        self.assertEqual(claim["phase_id"], "b")


if __name__ == "__main__":
    unittest.main()
