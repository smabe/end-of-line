"""Tests for `clu logs <plan>` — tail the most recent worker log."""

from __future__ import annotations

import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import ExitCode, _follow_log, _resolve_log_path, main
from tests import isolate_registry

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""


class LogsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        self.logs_dir = self.state_path.parent / "logs"
        rc = main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _argv(self, *extra: str) -> list[str]:
        return [
            "logs",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            *extra,
        ]

    def _run(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(self._argv(*extra))
        return rc, out.getvalue(), err.getvalue()

    def _stamp_claim(self, log_path: Path) -> None:
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            data["current_claim"] = {
                "phase_id": "a",
                "claimed_by": "session-xyz",
                "claimed_at": st.utcnow(),
                "lease_expires": st.utcnow(),
                "attempts": 1,
                "log_path": str(log_path),
            }
            st.save_atomic(self.state_path, data)

    def _write_log(self, name: str, body: str, *, mtime: float | None = None) -> Path:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        path = self.logs_dir / name
        path.write_text(body)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_active_claim_log_path_is_dumped(self) -> None:
        path = self._write_log("a.session-xyz.log", "active-worker-output\n")
        self._stamp_claim(path)

        rc, out, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("active-worker-output", out)

    def test_no_claim_falls_back_to_newest_file(self) -> None:
        older = self._write_log("a.old.log", "older-content\n", mtime=1000.0)
        newer = self._write_log("a.new.log", "newer-content\n", mtime=2000.0)
        # Sanity check the stamping took.
        self.assertGreater(newer.stat().st_mtime, older.stat().st_mtime)

        rc, out, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("newer-content", out)
        self.assertNotIn("older-content", out)

    def test_active_claim_wins_over_newer_file(self) -> None:
        # Claim's log_path always wins, even if a newer file exists in the dir.
        claim_log = self._write_log(
            "a.session-xyz.log",
            "claim-content\n",
            mtime=1000.0,
        )
        self._write_log("a.newer.log", "fallback-content\n", mtime=9999.0)
        self._stamp_claim(claim_log)

        rc, out, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("claim-content", out)
        self.assertNotIn("fallback-content", out)

    def test_claim_without_log_path_falls_through(self) -> None:
        # Old state predates the log_path field — must not crash.
        self._write_log("a.tail.log", "fallback-after-no-log-path\n")
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            data["current_claim"] = {
                "phase_id": "a",
                "claimed_by": "session-xyz",
                "claimed_at": st.utcnow(),
                "lease_expires": st.utcnow(),
                "attempts": 1,
            }
            st.save_atomic(self.state_path, data)

        rc, out, _ = self._run()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("fallback-after-no-log-path", out)

    def test_empty_logs_dir_errors_cleanly(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        rc, out, err = self._run()
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertIn("no logs", err.lower())

    def test_missing_logs_dir_errors_cleanly(self) -> None:
        # Don't create logs_dir at all.
        self.assertFalse(self.logs_dir.exists())
        rc, _, err = self._run()
        self.assertNotEqual(rc, 0)
        self.assertIn("no logs", err.lower())

    def test_rejects_invalid_plan_slug(self) -> None:
        rc = main(["logs", "--project", str(self.project), "--plan", "../etc/passwd"])
        self.assertEqual(rc, ExitCode.INVALID_SLUG)

    def test_resolve_log_path_helper_prefers_claim(self) -> None:
        claim_log = self._write_log("from-claim.log", "x", mtime=1.0)
        self._write_log("newer.log", "y", mtime=9999.0)
        self._stamp_claim(claim_log)
        cfg_like = type("Cfg", (), {})()
        resolved = _resolve_log_path(self.state_path, cfg_like)
        self.assertEqual(resolved, claim_log)

    def test_follow_smoke_returns_when_file_idle(self) -> None:
        # Follow happy path: file isn't growing → helper exits when the
        # stop-after deadline is hit. Asserts no flakiness, no hang.
        path = self._write_log("idle.log", "seed-line\n")
        out = io.StringIO()
        start = time.monotonic()
        with redirect_stdout(out):
            rc = _follow_log(path, stop_after_seconds=0.15, poll_interval=0.01)
        elapsed = time.monotonic() - start
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("seed-line", out.getvalue())
        self.assertLess(elapsed, 2.0, "follow should not hang on idle file")

    def test_follow_streams_appended_content(self) -> None:
        path = self._write_log("growing.log", "initial\n")
        # Helper polls every 20ms; append in a background thread.
        import threading

        def appender() -> None:
            time.sleep(0.05)
            with open(path, "a") as f:
                f.write("appended\n")
                f.flush()

        thread = threading.Thread(target=appender)
        thread.start()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = _follow_log(path, stop_after_seconds=0.3, poll_interval=0.02)
        thread.join()
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("initial", out.getvalue())
        self.assertIn("appended", out.getvalue())


if __name__ == "__main__":
    unittest.main()
