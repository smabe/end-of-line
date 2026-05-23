"""Tests for watch.stream_loop task_list_mode — bootstrap ordering + event routing."""
from __future__ import annotations

import io
import json
from pathlib import Path

from end_of_line import state as st
from end_of_line.config import ProjectConfig
from end_of_line.watch import stream_loop
from tests import CluTestCase


TS = "2026-05-17T10:00:00Z"


def _evt(type_: str, **fields) -> dict:
    return {"type": type_, "ts": TS, **fields}


def _make_state(path: Path, slug: str, *, status: str = "running",
                events: list | None = None,
                current_claim: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": st.SCHEMA_VERSION,
        "plan_slug": slug,
        "plan_dir": "plans",
        "status": status,
        "current_claim": current_claim,
        "blockers": [],
        "spawned_tasks": [],
        "config": {
            "lease_ttl_minutes": 30,
            "blocked_question_sla_hours": 24,
            "max_attempts_per_phase": 3,
            "max_spawns_per_phase": 5,
            "max_queue_adds_per_phase": 5,
            "stalled_heartbeat_minutes": 10,
        },
        "events": events or [],
        "created_at": TS,
    }
    path.write_text(json.dumps(data))


def _append_event(path: Path, event: dict) -> None:
    data = json.loads(path.read_text())
    data["events"].append(event)
    path.write_text(json.dumps(data))


def _write_master(project: Path, slug: str, phases: list[str]) -> None:
    rows = "\n".join(
        f"| {ph} | `{slug}-{ph}.md` | scope | 1h |"
        for ph in phases
    )
    content = (
        f"# {slug}\n\n"
        "## Sessions index\n\n"
        "| Session | Plan file | Scope | Effort |\n"
        "|---|---|---|---|\n"
        f"{rows}\n"
    )
    plan_path = project / "plans" / f"{slug}.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(content)


def _cfg_loader(project: Path):
    def loader(state_path: Path) -> ProjectConfig:
        return ProjectConfig(project_root=project)
    return loader


class TaskListStreamTest(CluTestCase):

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "my-plan.state.json"
        )
        _make_state(self.state_path, "my-plan")
        _write_master(self.project, "my-plan", ["phase-a", "phase-b"])

    def test_task_list_mode_emits_bootstrap_before_baseline(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            task_list_mode=True,
            sink=sink, poll_interval=0, max_ticks=0,
            cfg_loader=_cfg_loader(self.project),
        )
        lines = sink.getvalue().splitlines()
        task_creates = [l for l in lines if l.startswith("TASK_CREATE")]
        self.assertEqual(len(task_creates), 3,
                         f"expected parent + 2 phase TASK_CREATEs; got: {lines}")
        snapshot_idx = next(i for i, l in enumerate(lines) if "[snapshot]" in l)
        for i, line in enumerate(lines):
            if line.startswith("TASK_CREATE"):
                self.assertLess(i, snapshot_idx,
                    f"TASK_CREATE at index {i} must precede [snapshot] at {snapshot_idx}")

    def test_task_list_mode_projects_events_as_task_update(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            task_list_mode=True,
            sink=sink, poll_interval=0, max_ticks=1,
            cfg_loader=_cfg_loader(self.project),
            _before_first_tick=lambda: _append_event(
                self.state_path, _evt(st.EVENT_PHASE_COMPLETED, phase="foundation")
            ),
        )
        out = sink.getvalue()
        self.assertIn('TASK_UPDATE task=my-plan/foundation parent=my-plan status=completed msg="completed"', out)

    def test_task_list_mode_emits_blocked_msg_through_stream(self) -> None:
        """End-to-end: BLOCKED event routed through stream_loop produces
        the full TASK_UPDATE line including blocker_id + question in msg.
        Regression guard for the operationally significant msg path —
        #42 receipt."""
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            task_list_mode=True,
            sink=sink, poll_interval=0, max_ticks=1,
            cfg_loader=_cfg_loader(self.project),
            _before_first_tick=lambda: _append_event(
                self.state_path,
                _evt(st.EVENT_PHASE_BLOCKED, phase="design",
                     blocker_id="blk-99",
                     question="Postgres or sqlite?")
            ),
        )
        out = sink.getvalue()
        self.assertIn(
            'TASK_UPDATE task=my-plan/design parent=my-plan '
            'status=in_progress msg="BLOCKED blk-99 — Postgres or sqlite?"',
            out,
        )

    def test_task_list_mode_skips_default_text_lines(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            task_list_mode=True,
            sink=sink, poll_interval=0, max_ticks=1,
            cfg_loader=_cfg_loader(self.project),
            _before_first_tick=lambda: _append_event(
                self.state_path, _evt(st.EVENT_TASK_SPAWNED, phase="p", task="t")
            ),
        )
        out = sink.getvalue()
        self.assertNotIn("TASK_UPDATE", out)

    def test_task_list_mode_with_verbose_emits_lease_extended(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            task_list_mode=True, verbose=True,
            sink=sink, poll_interval=0, max_ticks=1,
            cfg_loader=_cfg_loader(self.project),
            _before_first_tick=lambda: _append_event(
                self.state_path, _evt(
                    st.EVENT_LEASE_EXTENDED,
                    phase="p", extended_by_minutes=30,
                    new_expires="2099-01-01T01:00:00Z",
                )
            ),
        )
        out = sink.getvalue()
        self.assertIn("TASK_UPDATE", out)
        self.assertIn("lease extended", out)

    def test_task_list_mode_off_unchanged(self) -> None:
        sink = io.StringIO()
        stream_loop(
            [self.state_path],
            task_list_mode=False,
            sink=sink, poll_interval=0, max_ticks=1,
            _before_first_tick=lambda: _append_event(
                self.state_path, _evt(st.EVENT_PHASE_COMPLETED, phase="p")
            ),
        )
        out = sink.getvalue()
        self.assertNotIn("TASK_CREATE", out)
        self.assertNotIn("TASK_UPDATE", out)
        self.assertIn("completed", out)

    def test_bootstrap_missing_master_skips_silently(self) -> None:
        project = self.tmp_path / "no-master-project"
        state_path = project / "plans" / ".orchestrator" / "no-plan.state.json"
        _make_state(state_path, "no-plan")

        sink = io.StringIO()
        stream_loop(
            [state_path],
            task_list_mode=True,
            sink=sink, poll_interval=0, max_ticks=0,
            cfg_loader=_cfg_loader(project),
        )
        self.assertNotIn("TASK_CREATE", sink.getvalue())

    def test_bootstrap_ordering_when_phase_active(self) -> None:
        """TASK_CREATE batch → TASK_UPDATE plan → TASK_UPDATE phase → [snapshot]."""
        project = self.tmp_path / "ordering-project"
        state_path = project / "plans" / ".orchestrator" / "ord-plan.state.json"
        _make_state(state_path, "ord-plan",
                    current_claim={"phase_id": "phase-a"})
        _write_master(project, "ord-plan", ["phase-a", "phase-b"])
        sink = io.StringIO()
        stream_loop(
            [state_path],
            task_list_mode=True,
            sink=sink, poll_interval=0, max_ticks=0,
            cfg_loader=_cfg_loader(project),
        )
        lines = sink.getvalue().splitlines()
        create_plan = next(i for i, l in enumerate(lines)
                           if "TASK_CREATE" in l and "task=ord-plan " in l)
        update_plan = next(i for i, l in enumerate(lines)
                           if "TASK_UPDATE" in l and "task=ord-plan " in l)
        update_phase = next(i for i, l in enumerate(lines)
                            if "TASK_UPDATE" in l and "task=ord-plan/phase-a" in l)
        snapshot = next(i for i, l in enumerate(lines) if "[snapshot]" in l)
        self.assertLess(create_plan, update_plan)
        self.assertLess(update_plan, update_phase)
        self.assertLess(update_phase, snapshot)
