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
from contextlib import contextmanager, redirect_stderr
from unittest import mock

from end_of_line import cli, db
from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase, mutate_state, plan_body


class NoSubprocessInsideATransactionTest(GitProjectTestCase):
    """`clu force-complete` shells out three ways and holds no lock while it does.

    It was the THIRD site running foreign work inside the project's write
    window: a process-group reap that polls `ps` and `kill` for seconds, a
    coolant script, and the worktree teardown's git commands. That was
    tolerable when the lock was one plan's flock and is not now that one
    database — and one write lock — covers every plan in the project.
    """

    @contextmanager
    def _sql_trace(self):
        """Every SQL statement and every shell-out, in one ordered log."""
        log: list[str] = []
        real_connect = db.connect

        def _traced(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(lambda sql: log.append(f"SQL:{' '.join(sql.split())}"))
            return conn

        def _reap(*_a, **_kw):
            log.append("SEAM:reap_orphan_pgroup")
            return st.ReapResult(signaled=st.SIGNAL_TERM, cmdline_mismatch=False, escalated_kill=False)

        def _emit(*_a, **_kw):
            log.append("SEAM:coolant.emit_stop")

        def _run(*_a, **_kw):
            log.append("SEAM:subprocess.run")
            return subprocess.CompletedProcess([], 0, "", "")

        with (
            mock.patch.object(db, "connect", _traced),
            mock.patch.object(st, "reap_orphan_pgroup", _reap),
            mock.patch.object(cli.coolant, "emit_stop", _emit),
            mock.patch.object(cli.subprocess, "run", _run),
        ):
            yield log

    @staticmethod
    def _seams_inside_transactions(log: list[str]) -> list[str]:
        depth = 0
        offenders: list[str] = []
        for line in log:
            if line.startswith("SQL:"):
                stmt = line[4:].upper()
                if stmt.startswith("BEGIN"):
                    depth += 1
                elif stmt.startswith(("COMMIT", "ROLLBACK")):
                    depth = max(depth - 1, 0)
            elif depth:
                offenders.append(line)
        return offenders

    def _seed_worktree(self) -> None:
        with mutate_state(self.state_path) as data:
            data["worktree"] = {
                "path": str(self.project.parent / "wt"),
                "branch": "clu/test-plan",
                "base_ref": self.sha,
            }

    PLAN_BODY = plan_body("a")

    def test_force_complete_holds_no_lock_across_a_shell_out(self) -> None:
        # A one-phase plan, so force-completing it completes the PLAN — which
        # is what puts the worktree teardown's git commands on this path.
        self._claim("a")
        self._seed_worktree()
        with mutate_state(self.state_path) as data:
            data["current_claim"]["pid"] = 987654
            data["current_claim"]["pgid"] = 987654
        with self._sql_trace() as log:
            rc = main(self._argv("force-complete", "--phase", "a", "--reason", "worker died"))
        self.assertEqual(rc, ExitCode.OK)
        seams = [line for line in log if line.startswith("SEAM:")]
        # Non-vacuity first: a run that shelled out nowhere would satisfy an
        # "inside a transaction" check while proving nothing.
        self.assertIn("SEAM:reap_orphan_pgroup", seams)
        self.assertIn("SEAM:coolant.emit_stop", seams)
        self.assertIn("SEAM:subprocess.run", seams)
        self.assertEqual(
            self._seams_inside_transactions(log),
            [],
            f"force-complete shelled out inside a transaction; trace: {log}",
        )

    def test_archive_holds_no_lock_across_a_shell_out(self) -> None:
        # The archive path is the other half of the same criterion: the
        # worktree teardown alone is up to four git commands with 30s timeouts,
        # and `git mv` plus the archive commit follow it.
        self._seed_worktree()
        with mutate_state(self.state_path) as data:
            data["status"] = st.STATUS_DONE
        with self._sql_trace() as log:
            rc = main(self._argv("archive"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("SEAM:subprocess.run", [line for line in log if line.startswith("SEAM:")])
        self.assertEqual(
            self._seams_inside_transactions(log),
            [],
            f"archive shelled out inside a transaction; trace: {log}",
        )


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
            with mutate_state(self.state_path) as data:
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
        with mutate_state(self.state_path) as data:
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
        with mutate_state(self.state_path) as data:
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
