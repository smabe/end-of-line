"""`clu activity --start-bash` / `--end-bash` worker callbacks.

Wired as Claude Code PreToolUse / PostToolUse hooks for the Bash tool, they
stamp `current_claim.active_tool_started_at` so the supervisor's stuck-tool
detector can scope to descendants spawned during the active tool call.
"""

from __future__ import annotations

import subprocess
import unittest

from end_of_line import state as st
from end_of_line.cli import main
from tests import CluTestCase

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""


class ActivityCallbackTestCase(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        main(["init", "--project", str(self.project), "--plan", "test-plan"])
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def _argv(self, *extra: str) -> list[str]:
        return [
            "activity",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            self.token,
            *extra,
        ]

    def test_start_bash_stamps_active_marker(self) -> None:
        rc = main(self._argv("--start-bash"))
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertIn("active_tool_started_at", data["current_claim"])
        # Stamp should be ISO8601 with year 2026 (or later).
        stamp = data["current_claim"]["active_tool_started_at"]
        self.assertGreaterEqual(stamp[:4], "2026")

    def test_end_bash_clears_active_marker(self) -> None:
        main(self._argv("--start-bash"))
        rc = main(self._argv("--end-bash"))
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertNotIn("active_tool_started_at", data["current_claim"])

    def test_start_overwrites_previous_window(self) -> None:
        # Two PreToolUse calls back-to-back (e.g. nested Bash invocations
        # from a subagent racing with the parent) just slide the window
        # forward — last start wins, no error.
        from unittest.mock import patch

        with patch.object(
            st,
            "utcnow",
            side_effect=[
                "2026-05-22T10:00:00Z",
                "2026-05-22T10:00:05Z",
            ],
        ):
            main(self._argv("--start-bash"))
            first = st.load(self.state_path)["current_claim"]["active_tool_started_at"]
            main(self._argv("--start-bash"))
            second = st.load(self.state_path)["current_claim"]["active_tool_started_at"]
        self.assertEqual(first, "2026-05-22T10:00:00Z")
        self.assertEqual(second, "2026-05-22T10:00:05Z")

    def test_end_without_matching_start_is_idempotent(self) -> None:
        # Worker may have died mid-Bash and the next session sees a stale
        # PostToolUse with no matching Pre. Must not raise.
        rc = main(self._argv("--end-bash"))
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertNotIn("active_tool_started_at", data["current_claim"])

    def test_wrong_token_rejected(self) -> None:
        bad = [
            "activity",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            "session-wrong00000000",
            "--start-bash",
        ]
        rc = main(bad)
        self.assertNotEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertNotIn("active_tool_started_at", data["current_claim"])

    def test_wrong_phase_rejected(self) -> None:
        bad = [
            "activity",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "b",
            "--token",
            self.token,
            "--start-bash",
        ]
        rc = main(bad)
        self.assertNotEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertNotIn("active_tool_started_at", data["current_claim"])

    def test_no_action_flag_rejected(self) -> None:
        # The subcommand needs one of --start-bash / --end-bash; neither
        # passed should exit non-zero rather than silently no-op.
        rc = main(
            [
                "activity",
                "--project",
                str(self.project),
                "--plan",
                "test-plan",
                "--phase",
                "a",
                "--token",
                self.token,
            ]
        )
        self.assertNotEqual(rc, 0)

    def test_drops_silently_on_lock_contention(self) -> None:
        # PreToolUse hook fires under load; if the supervisor or another
        # callback is holding the state lock, we'd rather drop the marker
        # update than freeze the worker's Bash invocation. clu activity
        # exits 0 on lock-timeout; the marker just stays whatever it was.

        lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        # Hold the lock from a subprocess so flock contention is real
        # (BSD flock is per-file; another FD in the same process is enough
        # on macOS but subprocess is safer across platforms).
        import subprocess as _sp

        holder = _sp.Popen(
            [
                "python3",
                "-c",
                "import fcntl,os,sys,time;"
                f"fd=os.open(r'{lock_path}',os.O_RDWR|os.O_CREAT,0o600);"
                "fcntl.flock(fd,fcntl.LOCK_EX);"
                "sys.stdout.write('locked\\n');sys.stdout.flush();"
                "time.sleep(5)",
            ],
            stdout=_sp.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            rc = main(self._argv("--start-bash"))
            self.assertEqual(rc, 0)
            # Marker should NOT be set — we dropped the update.
            data = st.load(self.state_path)
            self.assertNotIn("active_tool_started_at", data["current_claim"])
        finally:
            holder.terminate()
            holder.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
