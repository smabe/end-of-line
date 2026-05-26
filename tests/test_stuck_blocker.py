"""Stuck-blocker re-ping detection — supervisor escalates unanswered blockers
every 30 minutes.

Companion to ``test_stalled_claim`` and ``test_inbox`` — both gap-fills shipped
as part of clu-inbox phase `gap-notifications` (closes #20).
"""

from __future__ import annotations

import datetime as _dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import inbox, notify
from end_of_line import state as st
from end_of_line.config import DispatchSpec, NotifySpec, ProjectConfig
from end_of_line.supervisor import tick
from tests import isolate_registry

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
| B | `test-plan-b.md` | thing | 1h |
"""


class StuckBlockerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.cfg = ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command="echo"),
        )
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        self.state_path.parent.mkdir(parents=True)
        with st.locked(self.state_path):
            st.save_atomic(self.state_path, st.empty_state("test-plan", "plans"))

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def _seed_blocker(self, age_minutes: int, last_repinged_minutes_ago: int | None = None) -> str:
        """Add a blocker on phase 'a' aged `age_minutes` (asked_at backdated)."""
        with st.mutate(self.state_path) as data:
            blocker_id = st.add_blocker(data, "a", "Pick framework?", ["FastAPI", "Flask"], "ctx")
            asked = (st._now_utc() - _dt.timedelta(minutes=age_minutes)).strftime(st._ISO_FMT)
            for b in data["blockers"]:
                if b["id"] == blocker_id:
                    b["asked_at"] = asked
                    if last_repinged_minutes_ago is not None:
                        b["last_repinged_at"] = (
                            st._now_utc() - _dt.timedelta(minutes=last_repinged_minutes_ago)
                        ).strftime(st._ISO_FMT)
        return blocker_id

    def test_blocker_under_30min_does_not_reping(self) -> None:
        self._seed_blocker(age_minutes=29)
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.side_notifies, [])
        data = self._read()
        self.assertNotIn(st.EVENT_STUCK_BLOCKER_REPINGED, [e["type"] for e in data["events"]])
        self.assertIsNone(data["blockers"][0].get("last_repinged_at"))

    def test_blocker_over_30min_first_reping_fires(self) -> None:
        self._seed_blocker(age_minutes=31)
        result = tick(self.state_path, self.cfg)
        kinds = [k for k, _ in result.side_notifies]
        self.assertIn(notify.KIND_STUCK_BLOCKER, kinds)
        data = self._read()
        types = [e["type"] for e in data["events"]]
        self.assertIn(st.EVENT_STUCK_BLOCKER_REPINGED, types)
        self.assertIsNotNone(data["blockers"][0].get("last_repinged_at"))

    def test_blocker_reping_does_not_fire_within_30min_of_last_reping(self) -> None:
        self._seed_blocker(age_minutes=60, last_repinged_minutes_ago=14)
        result = tick(self.state_path, self.cfg)
        kinds = [k for k, _ in result.side_notifies]
        self.assertNotIn(notify.KIND_STUCK_BLOCKER, kinds)

    def test_blocker_reping_repeats_after_another_30min(self) -> None:
        blocker_id = self._seed_blocker(age_minutes=60, last_repinged_minutes_ago=31)
        result = tick(self.state_path, self.cfg)
        kinds = [k for k, _ in result.side_notifies]
        self.assertIn(notify.KIND_STUCK_BLOCKER, kinds)
        data = self._read()
        # last_repinged_at stamped fresh (very recent, within seconds).
        last = st.parse_iso(data["blockers"][0]["last_repinged_at"])
        self.assertLess((st._now_utc() - last).total_seconds(), 60)

    def test_consumed_blocker_does_not_reping(self) -> None:
        blocker_id = self._seed_blocker(age_minutes=120)
        with st.mutate(self.state_path) as data:
            for b in data["blockers"]:
                if b["id"] == blocker_id:
                    b["answer"] = "FastAPI"
                    b["answered_at"] = st.utcnow()
                    b["consumed"] = True
        result = tick(self.state_path, self.cfg)
        kinds = [k for k, _ in result.side_notifies]
        self.assertNotIn(notify.KIND_STUCK_BLOCKER, kinds)

    def test_answered_but_unconsumed_blocker_does_not_reping(self) -> None:
        """Answered + not-yet-consumed is handled by the resumption branch, not re-ping."""
        blocker_id = self._seed_blocker(age_minutes=120)
        with st.mutate(self.state_path) as data:
            for b in data["blockers"]:
                if b["id"] == blocker_id:
                    b["answer"] = "FastAPI"
                    b["answered_at"] = st.utcnow()
        result = tick(self.state_path, self.cfg)
        kinds = [k for k, _ in result.side_notifies]
        self.assertNotIn(notify.KIND_STUCK_BLOCKER, kinds)

    def test_reping_renders_question_and_options_in_body(self) -> None:
        self._seed_blocker(age_minutes=45)
        result = tick(self.state_path, self.cfg)
        bodies = [body for k, body in result.side_notifies if k == notify.KIND_STUCK_BLOCKER]
        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        self.assertIn("Pick framework?", body)
        self.assertIn("FastAPI", body)
        self.assertIn("Flask", body)
        self.assertIn("test-plan", body)

    def test_reping_writes_inbox_event_with_rich_details(self) -> None:
        self._seed_blocker(age_minutes=45)
        tick(self.state_path, self.cfg)
        events = inbox.list_for_project(str(self.project))
        stuck = [e for e in events if e["type"] == "stuck_blocker"]
        self.assertEqual(len(stuck), 1)
        evt = stuck[0]
        self.assertEqual(evt["plan_slug"], "test-plan")
        self.assertIn("blocker_id", evt["details"])
        self.assertEqual(evt["details"]["phase_id"], "a")
        self.assertEqual(evt["details"]["question"], "Pick framework?")
        self.assertEqual(evt["details"]["options"], ["FastAPI", "Flask"])


if __name__ == "__main__":
    unittest.main()
