"""Registry-independent zombie sweep (#75 phase 3).

`is_zombie_state` classifies an UNREGISTERED `status=running` state file whose
worker is gone; `sweep_zombie_states` terminalizes + reaps them. This is the
backstop for the `fm-docs-sweep` shape — a plan unregistered while running that
tick-all's registry walk can never revisit.
"""

from __future__ import annotations

import subprocess
import sys
import time

from end_of_line import registry, supervisor
from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase, write_config


def _group_alive(pgid: int) -> bool:
    return subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True).returncode == 0


class IsZombieStateTest(GitProjectTestCase):
    def test_claimless_running_is_zombie(self):
        data = {"plan_slug": "z", "status": st.STATUS_RUNNING, "current_claim": None}
        self.assertTrue(st.is_zombie_state(data))

    def test_terminal_status_is_not_zombie(self):
        for s in (st.STATUS_DONE, st.STATUS_HALTED, st.STATUS_PAUSED):
            data = {"plan_slug": "z", "status": s, "current_claim": None}
            self.assertFalse(st.is_zombie_state(data), s)

    def test_running_with_dead_worker_is_zombie(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait(timeout=5)
        data = {
            "plan_slug": "z",
            "status": st.STATUS_RUNNING,
            "current_claim": {"phase_id": "a", "pid": p.pid, "claimed_by": "t"},
        }
        self.assertTrue(st.is_zombie_state(data))

    def test_running_with_live_worker_is_not_zombie(self):
        leader = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", "z-slug"]
        )
        time.sleep(0.3)
        try:
            data = {
                "plan_slug": "z-slug",
                "status": st.STATUS_RUNNING,
                "current_claim": {"phase_id": "a", "pid": leader.pid, "claimed_by": "t"},
            }
            self.assertFalse(st.is_zombie_state(data), "live worker → not a zombie")
        finally:
            leader.terminate()
            leader.wait()


class SweepZombieStatesTest(GitProjectTestCase):
    def _write_state(self, slug: str, *, status: str, claim: dict | None) -> None:
        base = st.load(self.state_path)
        base["plan_slug"] = slug
        base["status"] = status
        base["current_claim"] = claim
        base["events"] = []
        st.save_atomic(self.state_path.parent / f"{slug}.state.json", base)

    def _read_slug(self, slug: str) -> dict:
        import json

        return json.loads((self.state_path.parent / f"{slug}.state.json").read_text())

    def _registered_slugs(self) -> set[str]:
        return {e.plan_slug for e in registry.entries_for_project(self.project)}

    def test_claimless_zombie_terminalized(self):
        # The fm-docs-sweep shape: running, no claim, unregistered.
        self._write_state("fm-docs", status=st.STATUS_RUNNING, claim=None)
        out = supervisor.sweep_zombie_states(self.cfg(), self._registered_slugs())
        self.assertEqual([z.plan_slug for z in out], ["fm-docs"])
        self.assertTrue(out[0].terminalized)
        data = self._read_slug("fm-docs")
        self.assertEqual(data["status"], st.STATUS_HALTED)
        self.assertEqual(
            [e["type"] for e in data["events"] if e["type"] == st.EVENT_PLAN_ABANDONED],
            [st.EVENT_PLAN_ABANDONED],
        )

    def test_registered_plan_skipped(self):
        # test-plan is registered by setUp; even if running it must be skipped.
        with st.mutate(self.state_path) as d:
            d["status"] = st.STATUS_RUNNING
        out = supervisor.sweep_zombie_states(self.cfg(), self._registered_slugs())
        self.assertEqual(out, [])
        self.assertEqual(self._read()["status"], st.STATUS_RUNNING, "registered → untouched")

    def test_terminal_unregistered_skipped(self):
        self._write_state("done-plan", status=st.STATUS_DONE, claim=None)
        out = supervisor.sweep_zombie_states(self.cfg(), self._registered_slugs())
        self.assertEqual(out, [])

    def test_dry_run_reports_without_mutating(self):
        self._write_state("fm-docs", status=st.STATUS_RUNNING, claim=None)
        out = supervisor.sweep_zombie_states(self.cfg(), self._registered_slugs(), dry_run=True)
        self.assertEqual([z.plan_slug for z in out], ["fm-docs"])
        self.assertFalse(out[0].terminalized)
        self.assertEqual(self._read_slug("fm-docs")["status"], st.STATUS_RUNNING, "unchanged")

    def test_reaps_orphaned_heartbeat_group(self):
        # Worker (leader) exits, leaving only a heartbeat-shaped child alive in
        # the group, its cmdline carrying the slug via `--plan <slug>`.
        code = (
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)',"
            " 'clu', 'heartbeat', '--plan', 'orphan-z'])\n"
        )
        leader = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
        leader.wait(timeout=5)
        time.sleep(0.3)
        try:
            self.assertTrue(_group_alive(leader.pid))
            self._write_state(
                "orphan-z",
                status=st.STATUS_RUNNING,
                claim={"phase_id": "a", "pid": leader.pid, "pgid": leader.pid, "claimed_by": "t"},
            )
            out = supervisor.sweep_zombie_states(self.cfg(), self._registered_slugs())
            self.assertEqual([z.plan_slug for z in out], ["orphan-z"])
            self.assertTrue(out[0].reaped, "orphaned heartbeat group should be reaped")
            time.sleep(0.6)
            self.assertFalse(_group_alive(leader.pid))
        finally:
            import os

            try:
                os.killpg(leader.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass

    def cfg(self):
        from end_of_line.config import load_project_config

        return load_project_config(self.project)


class SweepIntegrationTest(GitProjectTestCase):
    def _write_zombie(self, slug: str) -> None:
        base = st.load(self.state_path)
        base["plan_slug"] = slug
        base["status"] = st.STATUS_RUNNING
        base["current_claim"] = None
        base["events"] = []
        st.save_atomic(self.state_path.parent / f"{slug}.state.json", base)

    def test_tick_all_auto_sweeps_zombie(self):
        # Park the registered plan in a terminal status so tick-all doesn't try
        # to dispatch a real worker; the sweep still runs in the post-loop.
        with st.mutate(self.state_path) as d:
            d["status"] = st.STATUS_DONE
        self._write_zombie("fm-docs")
        rc = main(["tick-all"])
        self.assertEqual(rc, ExitCode.OK)
        import json

        data = json.loads((self.state_path.parent / "fm-docs.state.json").read_text())
        self.assertEqual(data["status"], st.STATUS_HALTED, "tick-all should terminalize it")

    def test_doctor_reports_zombie_dry_run(self):
        write_config(self.project)  # doctor refuses without .orchestrator.json
        self._write_zombie("fm-docs")
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["doctor", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("fm-docs", buf.getvalue())
        # Dry-run: doctor must NOT have terminalized it.
        import json

        data = json.loads((self.state_path.parent / "fm-docs.state.json").read_text())
        self.assertEqual(data["status"], st.STATUS_RUNNING, "doctor is read-only")
