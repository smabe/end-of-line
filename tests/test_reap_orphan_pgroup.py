"""Unit tests for state.reap_orphan_pgroup — process-GROUP reaping helper (#75).

TDD: written before the implementation. They should fail until
`reap_orphan_pgroup` lands in `state.py`.

Why reap the whole GROUP: the clu worker is spawned with
`start_new_session=True`, so its PGID == its PID and the backgrounded
`clu heartbeat` subshell inherits that group. A single-PID SIGTERM kills only
the worker — the heartbeat then reparents to launchd and loops for hours
(the #75 orphan). `os.killpg` takes the whole group at once.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import unittest

from end_of_line.state import (
    SIGNAL_TERM,
    ReapResult,
    reap_orphan_pgroup,
)


def _group_pids(pgid: int) -> list[int]:
    """PIDs currently in process group `pgid` (empty list if none)."""
    result = subprocess.run(
        ["pgrep", "-g", str(pgid)],
        capture_output=True,
        text=True,
    )
    return [int(x) for x in result.stdout.split()] if result.stdout.strip() else []


class TestReapOrphanPgroup(unittest.TestCase):
    def _spawn_group(self, marker: str = "") -> subprocess.Popen:
        """Leader in its own session (pgid == pid) that spawns a same-group
        child — mirrors worker + heartbeat loop. The leader sleeps so the
        group stays alive until we reap it."""
        code = (
            f"# {marker}\n"
            "import subprocess, time\n"
            "subprocess.Popen(['sleep', '30'])\n"  # same-group child (heartbeat stand-in)
            "time.sleep(30)\n"
        )
        leader = subprocess.Popen(
            [sys.executable, "-c", code],
            start_new_session=True,
        )
        time.sleep(0.6)  # let the child spawn before we inspect the group
        return leader

    def test_reap_terminates_whole_group(self):
        leader = self._spawn_group()
        try:
            pgid = os.getpgid(leader.pid)
            self.assertEqual(pgid, leader.pid, "start_new_session ⇒ pgid == pid")
            self.assertGreaterEqual(
                len(_group_pids(pgid)), 2, "leader + child should both be in the group"
            )
            _waiter = threading.Thread(target=leader.wait, daemon=True)
            _waiter.start()
            result = reap_orphan_pgroup(pgid)
            _waiter.join(timeout=10)
            self.assertIsNotNone(result.signaled)
            self.assertFalse(result.cmdline_mismatch)
            time.sleep(0.6)
            self.assertEqual(
                _group_pids(pgid), [], "no process should remain in the group after reap"
            )
        finally:
            self._cleanup(leader)

    def test_self_group_guard_never_signals_caller(self):
        # Reaping our own process group would SIGTERM the test runner itself.
        # The guard must make this a no-op — the fact this test keeps running
        # and reaches its assertions proves the caller survived.
        own = os.getpgid(0)
        result = reap_orphan_pgroup(own)
        self.assertIsNone(result.signaled)
        self.assertFalse(result.escalated_kill)

    def test_nonpositive_pgid_is_noop(self):
        # pgid 0 means "the caller's own group" to killpg; negatives are bogus.
        for bad in (0, -1):
            result = reap_orphan_pgroup(bad)
            self.assertIsNone(result.signaled)
            self.assertFalse(result.escalated_kill)

    def test_cmdline_mismatch_signals_nothing(self):
        leader = self._spawn_group(marker="REAP_PG_MARKER_REAL")
        try:
            pgid = os.getpgid(leader.pid)
            result = reap_orphan_pgroup(pgid, cmdline_match="not-in-any-cmdline-zzz")
            time.sleep(0.4)
            self.assertIsNone(leader.poll(), "group should survive a marker mismatch")
            self.assertTrue(result.cmdline_mismatch)
            self.assertIsNone(result.signaled)
        finally:
            self._cleanup(leader)

    def test_cmdline_match_prefix_collision_signals_nothing(self):
        # #76: a group whose only marker is `w1-foo` must NOT be reaped when we
        # search for slug `w1` — the hyphen is a token boundary, not a match.
        leader = self._spawn_group(marker="w1-foo")
        try:
            pgid = os.getpgid(leader.pid)
            result = reap_orphan_pgroup(pgid, cmdline_match="w1")
            time.sleep(0.4)
            self.assertIsNone(leader.poll(), "group should survive a prefix collision")
            self.assertTrue(result.cmdline_mismatch)
            self.assertIsNone(result.signaled)
        finally:
            self._cleanup(leader)

    def test_cmdline_match_reaps(self):
        marker = "REAP_PG_MARKER_12345"
        leader = self._spawn_group(marker=marker)
        try:
            pgid = os.getpgid(leader.pid)
            _waiter = threading.Thread(target=leader.wait, daemon=True)
            _waiter.start()
            result = reap_orphan_pgroup(pgid, cmdline_match=marker)
            _waiter.join(timeout=10)
            self.assertFalse(result.cmdline_mismatch)
            self.assertIsNotNone(result.signaled)
        finally:
            self._cleanup(leader)

    def test_already_dead_group_is_noop(self):
        leader = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        pgid = leader.pid
        leader.wait(timeout=5)
        time.sleep(0.3)
        result = reap_orphan_pgroup(pgid)  # group is empty / recycled — must not raise
        self.assertIsNone(result.signaled)
        self.assertFalse(result.escalated_kill)

    def test_reap_result_type(self):
        r = reap_orphan_pgroup(0)
        self.assertIsInstance(r, ReapResult)

    def _cleanup(self, leader: subprocess.Popen) -> None:
        try:
            os.killpg(leader.pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            leader.wait(timeout=2)
        except subprocess.TimeoutExpired:
            leader.kill()
            leader.wait()


if __name__ == "__main__":
    unittest.main()
