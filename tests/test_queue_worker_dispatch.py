"""Phase `dispatch` tests: worker-mode body + claim validation.

Covers happy path, claim-mismatch, wrong-phase, no-live-claim,
unknown-source-plan, and token-not-in-queue cases.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from end_of_line import queue, registry, state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import ProjectConfig
from tests import isolate_queue

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
    st.save_atomic(state_path, data)
    return state_path


class WorkerDispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        isolate_queue(self, self.project)
        self.cfg = ProjectConfig(project_root=self.project)
        self.queue_path = self.cfg.queue_path()

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
        data = queue.load(self.queue_path)
        self.assertEqual(len(data["queue"]), 1)
        entry = data["queue"][0]
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
        entry = queue.load(self.queue_path)["queue"][0]
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
        entry = queue.load(self.queue_path)["queue"][0]
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
        self.assertFalse(self.queue_path.exists())
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
        self.assertFalse(self.queue_path.exists())
        state_data = st.load(self.cfg.state_path("feature-b"))
        appended = [e for e in state_data["events"] if e["type"] == st.EVENT_QUEUE_APPENDED]
        self.assertEqual(appended, [])

    def test_worker_add_no_live_claim(self) -> None:
        _write_plan(self.project, "feature-b")
        registry.register(self.project, "feature-b")
        state_path = self.cfg.state_path("feature-b")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        data = st.empty_state("feature-b", "plans")
        st.save_atomic(state_path, data)
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
        self.assertFalse(self.queue_path.exists())

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
        self.assertFalse(self.queue_path.exists())

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
        raw_bytes = self.queue_path.read_bytes()
        self.assertNotIn(_TOKEN.encode(), raw_bytes)
