"""`clu unregister` on a non-terminal plan terminalizes + reaps it (#75 phase 2).

Before #75, `unregister` was status-blind: it dropped the registry row and left
the state file untouched. A running plan unregistered that way became a zombie
(`status=running`, not in the registry, invisible to tick-all's registry walk)
whose orphaned worker/heartbeat processes survived. Now unregister reaps the
worker group, releases any claim, and flips status to a terminal value.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from end_of_line import registry
from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase


def _spawn_marked_group(marker: str) -> subprocess.Popen:
    code = "import subprocess, time; subprocess.Popen(['sleep', '30']); time.sleep(30)"
    leader = subprocess.Popen([sys.executable, "-c", code, marker], start_new_session=True)
    time.sleep(0.6)
    return leader


def _group_alive(pgid: int) -> bool:
    return subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True).returncode == 0


class UnregisterTerminalizeTestCase(GitProjectTestCase):
    def _set_running(self) -> None:
        with st.mutate(self.state_path) as data:
            data["status"] = st.STATUS_RUNNING

    def _registered(self) -> bool:
        proj = self.project.resolve()
        return any(
            e.plan_slug == "test-plan" and Path(e.project_root).resolve() == proj
            for e in registry.entries()
        )

    def test_running_plan_terminalized_and_row_removed(self) -> None:
        self._set_running()
        self.assertTrue(self._registered(), "init should have registered the plan")
        rc = main(self._argv("unregister"))
        self.assertEqual(rc, ExitCode.OK)
        data = self._read()
        self.assertIn(data["status"], st.TERMINAL_STATUSES)
        self.assertEqual(data["status"], st.STATUS_HALTED)
        evts = [e for e in data["events"] if e["type"] == st.EVENT_PLAN_ABANDONED]
        self.assertEqual(len(evts), 1)
        self.assertEqual(evts[0]["reason"], "unregister")
        self.assertFalse(self._registered(), "registry row should be gone")

    def test_running_plan_worker_group_reaped(self) -> None:
        self._set_running()
        self._claim("a")
        leader = _spawn_marked_group("/clu-phase test-plan a")
        try:
            with st.mutate(self.state_path) as data:
                data["current_claim"]["pgid"] = leader.pid
            _waiter = threading.Thread(target=leader.wait, daemon=True)
            _waiter.start()
            rc = main(self._argv("unregister"))
            _waiter.join(timeout=10)
            self.assertEqual(rc, ExitCode.OK)
            self.assertIsNone(self._read()["current_claim"], "claim released")
            time.sleep(0.6)
            self.assertFalse(_group_alive(leader.pid), "worker group should be reaped")
        finally:
            try:
                os.killpg(leader.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            leader.wait()

    def test_corrupt_state_still_unregisters(self) -> None:
        # Unregister is the operator's tool for broken plans — a corrupt state
        # file must not block registry-row removal.
        self.state_path.write_text("{ this is not valid json")
        rc = main(self._argv("unregister"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertFalse(self._registered(), "row removed despite unreadable state")

    def test_already_terminal_plan_not_re_terminalized(self) -> None:
        with st.mutate(self.state_path) as data:
            data["status"] = st.STATUS_DONE
        rc = main(self._argv("unregister"))
        self.assertEqual(rc, ExitCode.OK)
        data = self._read()
        self.assertEqual(data["status"], st.STATUS_DONE)
        self.assertEqual(
            [e for e in data["events"] if e["type"] == st.EVENT_PLAN_ABANDONED],
            [],
            "a terminal plan must not get a plan_abandoned event",
        )
        self.assertFalse(self._registered())
