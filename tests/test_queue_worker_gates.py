"""Phase `gates` tests: cap + idempotency + missing-file refusals.

Covers:
- per-phase cap (queue + history combined)
- cap counts history entries
- cap is independent per (source_plan, source_phase) pair
- operator entries don't count toward worker cap
- pending slug → OK no-op
- running slug (popped, live claim) → OK no-op
- done slug (in history) → STATUS_TRANSITION
- missing plan file → UNKNOWN_TASK + EVENT_QUEUE_REJECTED
"""

from __future__ import annotations

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
_SOURCE_PLAN = "feature-b"
_SOURCE_PHASE = "c-extract"


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


def _worker_add(
    project: Path,
    slug: str,
    *,
    phase: str = _SOURCE_PHASE,
    token: str = _TOKEN,
    source_plan: str = _SOURCE_PLAN,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = main(
            [
                "queue",
                "add",
                slug,
                "--token",
                token,
                "--plan",
                source_plan,
                "--phase",
                phase,
                "--project",
                str(project),
            ]
        )
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return rc, out.getvalue(), err.getvalue()


def _source_tagged_entry(slug: str, phase: str = _SOURCE_PHASE) -> dict:
    return {
        "slug": slug,
        "added_at": st.utcnow(),
        "added_by": "worker",
        "position_at_add": "tail",
        "source_plan": _SOURCE_PLAN,
        "source_phase": phase,
        "source_token_fp": "xxxxxxxx",
        "reason": None,
    }


class WorkerGatesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        isolate_queue(self, self.project)
        self.cfg = ProjectConfig(project_root=self.project)
        self.queue_path = self.cfg.queue_path()
        _seed_source_plan(self.project, _SOURCE_PLAN, _SOURCE_PHASE, _TOKEN)

    def _rejected_events(self) -> list[dict]:
        state_data = st.load(self.cfg.state_path(_SOURCE_PLAN))
        return [e for e in state_data["events"] if e["type"] == st.EVENT_QUEUE_REJECTED]

    def test_cap_exceeded_at_default_three(self) -> None:
        for i in range(1, 4):
            _write_plan(self.project, f"target-{i}")
            rc, _, _ = _worker_add(self.project, f"target-{i}")
            self.assertEqual(rc, ExitCode.OK, f"expected OK on add #{i}")
        _write_plan(self.project, "target-4")
        rc, _, _ = _worker_add(self.project, "target-4")
        self.assertEqual(rc, ExitCode.QUEUE_CAP)
        rejected = self._rejected_events()
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "cap")

    def test_cap_counts_history_too(self) -> None:
        # 1 history entry + 2 pending = 3 total; next add (4th) hits cap.
        for slug in ("hist-target", "q-target-1", "q-target-2", "q-target-3"):
            _write_plan(self.project, slug)
        with queue.mutate(self.queue_path) as qdata:
            qdata["history"].append(_source_tagged_entry("hist-target"))
            qdata["queue"].extend(
                [
                    _source_tagged_entry("q-target-1"),
                    _source_tagged_entry("q-target-2"),
                ]
            )
        rc, _, _ = _worker_add(self.project, "q-target-3")
        self.assertEqual(rc, ExitCode.QUEUE_CAP)
        rejected = self._rejected_events()
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "cap")

    def test_cap_per_phase_independent(self) -> None:
        # Fill c-extract cap (3 entries), then swap claim to d-extract.
        for i in range(1, 4):
            _write_plan(self.project, f"target-c-{i}")
            rc, _, _ = _worker_add(self.project, f"target-c-{i}")
            self.assertEqual(rc, ExitCode.OK)

        d_token = "session-d-extract-token"
        state_path = self.cfg.state_path(_SOURCE_PLAN)
        with st.mutate(state_path) as data:
            st.release_claim(data)
            st.claim_phase(data, "d-extract", 30, d_token)

        for i in range(1, 4):
            _write_plan(self.project, f"target-d-{i}")
            rc, _, _ = _worker_add(self.project, f"target-d-{i}", phase="d-extract", token=d_token)
            self.assertEqual(rc, ExitCode.OK, f"expected d-extract add #{i} to succeed")

    def test_cap_doesnt_count_operator_entries(self) -> None:
        # Operator adds 5 entries (source_phase=None).
        for i in range(1, 6):
            _write_plan(self.project, f"op-target-{i}")
        with queue.mutate(self.queue_path) as qdata:
            for i in range(1, 6):
                qdata["queue"].append(
                    {
                        "slug": f"op-target-{i}",
                        "added_at": st.utcnow(),
                        "added_by": "operator",
                        "position_at_add": "tail",
                        "source_plan": None,
                        "source_phase": None,
                        "source_token_fp": None,
                        "reason": None,
                    }
                )
        # Worker cap counter is still 0; all 3 adds should succeed.
        for i in range(1, 4):
            _write_plan(self.project, f"worker-target-{i}")
            rc, _, _ = _worker_add(self.project, f"worker-target-{i}")
            self.assertEqual(rc, ExitCode.OK, f"expected OK on worker add #{i}")

    def test_pending_slug_noop_worker(self) -> None:
        _write_plan(self.project, "foo")
        rc1, _, _ = _worker_add(self.project, "foo")
        self.assertEqual(rc1, ExitCode.OK)

        rc2, out2, _ = _worker_add(self.project, "foo")
        self.assertEqual(rc2, ExitCode.OK)
        self.assertIn("already queued", out2)

        # Still exactly one queue entry.
        data = queue.load(self.queue_path)
        self.assertEqual(len(data["queue"]), 1)

        # No second EVENT_QUEUE_APPENDED.
        state_data = st.load(self.cfg.state_path(_SOURCE_PLAN))
        appended = [e for e in state_data["events"] if e["type"] == st.EVENT_QUEUE_APPENDED]
        self.assertEqual(len(appended), 1)

    def test_running_slug_noop_worker(self) -> None:
        # "target" has been popped from queue (not in queue or history)
        # but is now registered with a live claim.
        _write_plan(self.project, "target")
        registry.register(self.project, "target")
        target_state_path = self.cfg.state_path("target")
        target_state_path.parent.mkdir(parents=True, exist_ok=True)
        target_state = st.empty_state("target", "plans")
        st.claim_phase(target_state, "some-phase", 30, "session-running-token")
        st.save_atomic(target_state_path, target_state)

        rc, out, _ = _worker_add(self.project, "target")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("already queued", out)

        # Target was not added to the pending queue.
        if self.queue_path.exists():
            data = queue.load(self.queue_path)
            pending_slugs = {e["slug"] for e in data["queue"]}
            self.assertNotIn("target", pending_slugs)

    def test_done_slug_rejected_worker(self) -> None:
        _write_plan(self.project, "foo")
        with queue.mutate(self.queue_path) as qdata:
            qdata["history"].append(_source_tagged_entry("foo"))

        rc, _, err = _worker_add(self.project, "foo")
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("already ran", err)

    def test_missing_plan_file_emits_rejected_event(self) -> None:
        # "nonexistent" has no plan file.
        rc, _, _ = _worker_add(self.project, "nonexistent")
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)
        rejected = self._rejected_events()
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "missing_plan_file")
