"""Phase `dispatch` tests: worker-mode body + claim validation.

Covers happy path, claim-mismatch, wrong-phase, no-live-claim,
unknown-source-plan, and token-not-in-queue cases.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from end_of_line import db, queue, registry
from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import ProjectConfig
from tests import isolate_queue, write_state

_PLAN_BODY = "# placeholder plan\n"
_TOKEN = "session-deadbeef0000"


def _write_plan(project: Path, slug: str) -> Path:
    plans_dir = project / "plans"
    plans_dir.mkdir(exist_ok=True)
    path = plans_dir / f"{slug}.md"
    path.write_text(_PLAN_BODY)
    return path


def _seed_source_plan(project: Path, slug: str, phase: str, token: str) -> Path:
    """Create plan file, register it, and write state.json with a live claim."""
    _write_plan(project, slug)
    registry.register(project, slug)
    cfg = ProjectConfig(project_root=project)
    state_path = cfg.state_path(slug)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = st.empty_state(slug, "plans")
    st.claim_phase(data, phase, 30, token)
    write_state(state_path, data)
    return state_path


class WorkerDispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        isolate_queue(self, self.project)
        self.cfg = ProjectConfig(project_root=self.project)
        self.orch = self.cfg.orchestrator_dir()

    def _run(self, args: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = main(args)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return rc, out.getvalue(), err.getvalue()

    def test_worker_add_happy_path(self) -> None:
        _seed_source_plan(self.project, "feature-b", "c-extract", _TOKEN)
        _write_plan(self.project, "feature-c")
        rc, out, _ = self._run(
            [
                "queue",
                "add",
                "feature-c",
                "--token",
                _TOKEN,
                "--plan",
                "feature-b",
                "--phase",
                "c-extract",
                "--reason",
                "chained follow-up",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.OK)
        pending = queue.pending(self.orch)
        self.assertEqual(len(pending), 1)
        entry = pending[0]
        self.assertEqual(entry["slug"], "feature-c")
        self.assertEqual(entry["added_by"], "worker")
        self.assertEqual(entry["source_plan"], "feature-b")
        self.assertEqual(entry["source_phase"], "c-extract")
        self.assertEqual(len(entry["source_token_fp"]), 8)
        self.assertTrue(all(c in "0123456789abcdef" for c in entry["source_token_fp"]))
        self.assertEqual(entry["reason"], "chained follow-up")
        state_data = st.load(self.cfg.state_path("feature-b"))
        appended = [e for e in state_data["events"] if e["type"] == st.EVENT_QUEUE_APPENDED]
        self.assertEqual(len(appended), 1)
        evt = appended[0]
        self.assertEqual(evt["slug"], "feature-c")
        self.assertEqual(evt["source_phase"], "c-extract")

    def test_worker_add_no_reason_still_works(self) -> None:
        _seed_source_plan(self.project, "feature-b", "c-extract", _TOKEN)
        _write_plan(self.project, "feature-c")
        rc, _, _ = self._run(
            [
                "queue",
                "add",
                "feature-c",
                "--token",
                _TOKEN,
                "--plan",
                "feature-b",
                "--phase",
                "c-extract",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.OK)
        entry = queue.pending(self.orch)[0]
        self.assertIsNone(entry["reason"])
        state_data = st.load(self.cfg.state_path("feature-b"))
        evt = next(e for e in state_data["events"] if e["type"] == st.EVENT_QUEUE_APPENDED)
        # reason=None is not forwarded to append_event; key absent from event
        self.assertNotIn("reason", evt)

    def test_worker_add_token_fingerprint_is_sha256_prefix(self) -> None:
        _seed_source_plan(self.project, "feature-b", "c-extract", _TOKEN)
        _write_plan(self.project, "feature-c")
        self._run(
            [
                "queue",
                "add",
                "feature-c",
                "--token",
                _TOKEN,
                "--plan",
                "feature-b",
                "--phase",
                "c-extract",
                "--project",
                str(self.project),
            ]
        )
        entry = queue.pending(self.orch)[0]
        expected_fp = hashlib.sha256(_TOKEN.encode()).hexdigest()[:8]
        self.assertEqual(entry["source_token_fp"], expected_fp)

    def test_worker_add_claim_mismatch(self) -> None:
        _seed_source_plan(self.project, "feature-b", "c-extract", _TOKEN)
        _write_plan(self.project, "feature-c")
        rc, _, _ = self._run(
            [
                "queue",
                "add",
                "feature-c",
                "--token",
                "wrong-token",
                "--plan",
                "feature-b",
                "--phase",
                "c-extract",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertEqual(queue.pending(self.orch), [])
        state_data = st.load(self.cfg.state_path("feature-b"))
        appended = [e for e in state_data["events"] if e["type"] == st.EVENT_QUEUE_APPENDED]
        self.assertEqual(appended, [])

    def test_worker_add_wrong_phase(self) -> None:
        _seed_source_plan(self.project, "feature-b", "c-extract", _TOKEN)
        _write_plan(self.project, "feature-c")
        rc, _, _ = self._run(
            [
                "queue",
                "add",
                "feature-c",
                "--token",
                _TOKEN,
                "--plan",
                "feature-b",
                "--phase",
                "wrong-phase",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertEqual(queue.pending(self.orch), [])
        state_data = st.load(self.cfg.state_path("feature-b"))
        appended = [e for e in state_data["events"] if e["type"] == st.EVENT_QUEUE_APPENDED]
        self.assertEqual(appended, [])

    def test_worker_add_no_live_claim(self) -> None:
        _write_plan(self.project, "feature-b")
        registry.register(self.project, "feature-b")
        state_path = self.cfg.state_path("feature-b")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        data = st.empty_state("feature-b", "plans")
        write_state(state_path, data)
        _write_plan(self.project, "feature-c")
        rc, _, _ = self._run(
            [
                "queue",
                "add",
                "feature-c",
                "--token",
                _TOKEN,
                "--plan",
                "feature-b",
                "--phase",
                "c-extract",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertEqual(queue.pending(self.orch), [])

    def test_worker_add_unknown_source_plan(self) -> None:
        _write_plan(self.project, "feature-c")
        registry.register(self.project, "feature-c")
        rc, _, _ = self._run(
            [
                "queue",
                "add",
                "feature-c",
                "--token",
                _TOKEN,
                "--plan",
                "no-state-plan",
                "--phase",
                "c-extract",
                "--project",
                str(self.project),
            ]
        )
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)
        self.assertEqual(queue.pending(self.orch), [])

    def test_worker_add_raw_token_not_in_queue(self) -> None:
        _seed_source_plan(self.project, "feature-b", "c-extract", _TOKEN)
        _write_plan(self.project, "feature-c")
        self._run(
            [
                "queue",
                "add",
                "feature-c",
                "--token",
                _TOKEN,
                "--plan",
                "feature-b",
                "--phase",
                "c-extract",
                "--project",
                str(self.project),
            ]
        )
        # The queue keeps a FINGERPRINT of the token, never the token itself.
        # Checked over every COLUMN of both queue tables rather than the whole
        # database file: the plan's claim row legitimately holds the token (it
        # always did, in the state file), and they share one database now — so
        # a file-wide byte scan would pass or fail for the wrong reason.
        conn = sqlite3.connect(str(db.project_db_path(self.orch)))
        try:
            rows = conn.execute("SELECT * FROM queue").fetchall()
            rows += conn.execute("SELECT * FROM queue_history").fetchall()
        finally:
            conn.close()
        self.assertTrue(rows, "no queue rows to check")
        for row in rows:
            for value in row:
                self.assertNotIn(_TOKEN, str(value))
