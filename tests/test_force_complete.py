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
import os
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase


class ForceCompleteTestCase(GitProjectTestCase):
    def _events(self, *types: str) -> list[dict]:
        return [e for e in self._read()["events"] if e["type"] in types]

    # ---- happy path -----------------------------------------------------------

    def test_force_complete_marks_phase_completed(self) -> None:
        self._claim("a")
        rc = main(self._argv("force-complete", "--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.OK)
        data = self._read()
        self.assertIn("a", st.completed_phase_ids(data))

    def test_force_complete_releases_claim(self) -> None:
        self._claim("a")
        rc = main(self._argv("force-complete", "--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNone(self._read()["current_claim"])

    def test_force_complete_reaps_worker_group(self) -> None:
        # Worker died after writing code; its process group (worker + heartbeat
        # loop stand-in) must be reaped so it can't orphan past the claim (#75).
        self._claim("a")
        code = "import subprocess, time; subprocess.Popen(['sleep', '30']); time.sleep(30)"
        leader = subprocess.Popen(
            [sys.executable, "-c", code, "/clu-phase", "test-plan", "a"],
            start_new_session=True,
        )
        time.sleep(0.6)
        try:
            with st.mutate(self.state_path) as data:
                data["current_claim"]["pgid"] = leader.pid
            waiter = threading.Thread(target=leader.wait, daemon=True)
            waiter.start()
            rc = main(self._argv("force-complete", "--phase", "a", "--commit", self.sha))
            waiter.join(timeout=10)
            self.assertEqual(rc, ExitCode.OK)
            time.sleep(0.6)
            alive = subprocess.run(
                ["pgrep", "-g", str(leader.pid)], capture_output=True
            ).returncode == 0
            self.assertFalse(alive, "worker group should be reaped on force-complete")
        finally:
            try:
                os.killpg(leader.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            leader.wait()

    def test_force_complete_appends_operator_force_event(self) -> None:
        self._claim("a")
        rc = main(
            self._argv(
                "force-complete",
                "--phase",
                "a",
                "--commit",
                self.sha,
                "--reason",
                "zombie worker",
            )
        )
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
        main(self._argv("force-complete", "--phase", "a", "--commit", self.sha))
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
        rc = main(self._argv("force-complete", "--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("a", st.completed_phase_ids(self._read()))

    def test_force_complete_accepts_no_commit(self) -> None:
        # Operator might force-complete a doc-only phase with no SHA pointer.
        self._claim("a")
        rc = main(self._argv("force-complete", "--phase", "a"))
        self.assertEqual(rc, ExitCode.OK)
        evt = self._events(st.EVENT_OPERATOR_FORCE_COMPLETE)[0]
        self.assertEqual(evt["commits"], [])

    # ---- refusals -------------------------------------------------------------

    def test_refuses_when_phase_already_completed(self) -> None:
        self._claim("a")
        main(self._argv("force-complete", "--phase", "a", "--commit", self.sha))
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("force-complete", "--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("already", buf.getvalue().lower())

    def test_refuses_unknown_phase(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("force-complete", "--phase", "nope", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)

    def test_refuses_never_started_phase_without_really(self) -> None:
        # phase b exists in sub-plans but no phase_started event for it.
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("force-complete", "--phase", "b", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("--really", buf.getvalue())

    def test_really_bypasses_never_started_check(self) -> None:
        rc = main(
            self._argv(
                "force-complete",
                "--phase",
                "b",
                "--commit",
                self.sha,
                "--really",
            )
        )
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("b", st.completed_phase_ids(self._read()))

    def test_refuses_bogus_commit_sha(self) -> None:
        self._claim("a")
        bogus = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("force-complete", "--phase", "a", "--commit", bogus))
        self.assertEqual(rc, ExitCode.BAD_SHA)
        # No state mutation on rejected SHA.
        self.assertNotIn("a", st.completed_phase_ids(self._read()))

    def test_refuses_invalid_phase_slug(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(
                self._argv(
                    "force-complete",
                    "--phase",
                    "Bad/Slug",
                    "--commit",
                    self.sha,
                )
            )
        self.assertEqual(rc, ExitCode.INVALID_SLUG)

    # ---- claim release semantics ---------------------------------------------

    def test_force_complete_releases_foreign_token_claim(self) -> None:
        # Claim is held by some other token; force-complete should clear it
        # without token validation (operator override).
        token = self._claim("a")
        # Confirm the claim exists with this token first.
        self.assertEqual(self._read()["current_claim"]["claimed_by"], token)
        rc = main(self._argv("force-complete", "--phase", "a", "--commit", self.sha))
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
        rc = main(self._argv("force-complete", "--phase", "a", "--commit", self.sha))
        self.assertEqual(rc, ExitCode.OK)
        # Claim on phase b should NOT be touched — only matching-phase claims clear.
        claim = self._read()["current_claim"]
        self.assertIsNotNone(claim)
        self.assertEqual(claim["phase_id"], "b")


if __name__ == "__main__":
    unittest.main()
