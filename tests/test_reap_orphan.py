"""Unit tests for state.reap_orphan_pid — orphan-PID reaping helper (#57).

TDD: these tests are written before the implementation. They should fail
until `reap_orphan_pid` and `ReapResult` land in `state.py`.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest

from end_of_line.state import (
    EVENT_PHASE_ORPHAN_REAPED,
    SIGNAL_TERM,
    ReapResult,
    reap_orphan_pid,
)


class TestReapOrphanPid(unittest.TestCase):
    def test_reap_terminates_live_subprocess(self):
        import subprocess

        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        # Background waiter reaps the zombie as soon as SIGTERM lands so that
        # os.kill(pid, 0) raises ProcessLookupError in the polling loop — same
        # behavior as real usage where the supervisor is not the process parent.
        _waiter = threading.Thread(target=p.wait, daemon=True)
        _waiter.start()
        try:
            result = reap_orphan_pid(p.pid)
            _waiter.join(timeout=10)
            self.assertEqual(result.signaled, SIGNAL_TERM)
            self.assertFalse(result.escalated_kill)
            self.assertFalse(result.cmdline_mismatch)
        finally:
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def test_reap_already_dead_pid_is_noop(self):
        import subprocess

        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait(timeout=5)
        dead_pid = p.pid
        # PID is now recycled or just gone — reap should not raise
        result = reap_orphan_pid(dead_pid)
        self.assertIsNone(result.signaled)
        self.assertFalse(result.escalated_kill)
        self.assertFalse(result.cmdline_mismatch)

    def test_reap_cmdline_mismatch_signals_nothing(self):
        import subprocess

        p = subprocess.Popen(["sleep", "30"])
        try:
            result = reap_orphan_pid(p.pid, cmdline_match="this-string-not-in-cmdline-xyz")
            # Process should still be alive
            time.sleep(0.5)
            self.assertIsNone(p.poll(), "process should still be alive after mismatch")
            self.assertTrue(result.cmdline_mismatch)
            self.assertIsNone(result.signaled)
        finally:
            p.terminate()
            p.wait()

    def test_reap_cmdline_match_substring_present(self):
        import subprocess

        marker = "REAP_TEST_MARKER_12345"
        p = subprocess.Popen([sys.executable, "-c", f"# {marker}\nimport time; time.sleep(30)"])
        _waiter = threading.Thread(target=p.wait, daemon=True)
        _waiter.start()
        try:
            result = reap_orphan_pid(p.pid, cmdline_match=marker)
            _waiter.join(timeout=10)
            self.assertFalse(result.cmdline_mismatch)
            self.assertIsNotNone(result.signaled)
        finally:
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def test_event_constant_exists(self):
        self.assertEqual(EVENT_PHASE_ORPHAN_REAPED, "phase_orphan_reaped")

    def test_reap_result_fields(self):
        r = ReapResult(signaled="SIGTERM", escalated_kill=False, cmdline_mismatch=False)
        self.assertEqual(r.signaled, "SIGTERM")
        self.assertFalse(r.escalated_kill)
        self.assertFalse(r.cmdline_mismatch)
