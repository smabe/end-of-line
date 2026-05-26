"""Tests for end_of_line.state_blocker — pure blocker state machine."""

from __future__ import annotations

import datetime as _dt
import unittest

from end_of_line import state as st
from end_of_line.state_blocker import (
    KIND_STUCK_BLOCKER,
    process_answered_blockers,
    render_blocker,
    render_stalled,
    stuck_blocker_repings,
)


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _minutes_ago(n: int) -> _dt.datetime:
    return _utcnow() - _dt.timedelta(minutes=n)


def _iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _base_data() -> dict:
    return {"plan_slug": "test-plan", "blockers": [], "events": []}


def _blocker(
    bid: str = "q-1",
    phase: str = "phase-a",
    question: str = "Which approach?",
    options: list[str] | None = None,
    answer: str | None = None,
    consumed: bool = False,
    asked_minutes_ago: int = 0,
    last_repinged_minutes_ago: int | None = None,
) -> dict:
    b: dict = {
        "id": bid,
        "phase_id": phase,
        "question": question,
        "options": options if options is not None else ["Alpha", "Beta"],
        "asked_at": _iso(_minutes_ago(asked_minutes_ago)),
        "answer": answer,
        "answered_at": None,
    }
    if consumed:
        b["consumed"] = True
    if last_repinged_minutes_ago is not None:
        b["last_repinged_at"] = _iso(_minutes_ago(last_repinged_minutes_ago))
    return b


class TestProcessAnsweredBlockers(unittest.TestCase):
    def test_no_blockers_returns_empty(self) -> None:
        data = _base_data()
        events, status = process_answered_blockers(data)
        self.assertEqual(events, [])
        self.assertIsNone(status)

    def test_missing_blockers_key_returns_empty(self) -> None:
        data = {"plan_slug": "test", "events": []}
        events, status = process_answered_blockers(data)
        self.assertEqual(events, [])
        self.assertIsNone(status)

    def test_only_consumed_blockers_returns_empty(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(answer="Alpha", consumed=True))
        events, status = process_answered_blockers(data)
        self.assertEqual(events, [])
        self.assertIsNone(status)

    def test_only_unanswered_blockers_returns_empty(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(answer=None))
        events, status = process_answered_blockers(data)
        self.assertEqual(events, [])
        self.assertIsNone(status)

    def test_one_answered_unconsumed_returns_event_and_running(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(bid="q-1", answer="Alpha"))
        events, status = process_answered_blockers(data)
        self.assertEqual(events, [(st.EVENT_BLOCKER_CONSUMED, "q-1")])
        self.assertEqual(status, st.STATUS_RUNNING)

    def test_multiple_answered_returns_one_event_per_blocker(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(bid="q-1", answer="Alpha"))
        data["blockers"].append(_blocker(bid="q-2", answer="Beta"))
        events, status = process_answered_blockers(data)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0], (st.EVENT_BLOCKER_CONSUMED, "q-1"))
        self.assertEqual(events[1], (st.EVENT_BLOCKER_CONSUMED, "q-2"))
        self.assertEqual(status, st.STATUS_RUNNING)

    def test_answered_after_consumed_is_skipped(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(bid="q-1", answer="Alpha", consumed=True))
        events, status = process_answered_blockers(data)
        self.assertEqual(events, [])
        self.assertIsNone(status)


class TestStuckBlockerRepings(unittest.TestCase):
    def _now(self) -> _dt.datetime:
        return _utcnow()

    def test_no_open_blockers_returns_empty(self) -> None:
        data = _base_data()
        result = stuck_blocker_repings(data, self._now())
        self.assertEqual(result, [])

    def test_recently_pinged_blocker_skipped(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(asked_minutes_ago=31, last_repinged_minutes_ago=5))
        result = stuck_blocker_repings(data, self._now())
        self.assertEqual(result, [])

    def test_never_pinged_old_blocker_repings(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(bid="q-1", asked_minutes_ago=31))
        result = stuck_blocker_repings(data, self._now())
        self.assertEqual(len(result), 1)
        bid, kind, body = result[0]
        self.assertEqual(kind, KIND_STUCK_BLOCKER)

    def test_stale_ping_repings(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(asked_minutes_ago=62, last_repinged_minutes_ago=31))
        result = stuck_blocker_repings(data, self._now())
        self.assertEqual(len(result), 1)

    def test_under_thirty_min_not_repinged(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(asked_minutes_ago=20))
        result = stuck_blocker_repings(data, self._now())
        self.assertEqual(result, [])

    def test_consumed_blocker_not_repinged(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(asked_minutes_ago=31, consumed=True, answer="Alpha"))
        result = stuck_blocker_repings(data, self._now())
        self.assertEqual(result, [])

    def test_returns_blocker_id_for_stamping(self) -> None:
        data = _base_data()
        data["blockers"].append(_blocker(bid="q-7", asked_minutes_ago=31))
        result = stuck_blocker_repings(data, self._now())
        self.assertEqual(len(result), 1)
        bid, kind, body = result[0]
        self.assertEqual(bid, "q-7")
        self.assertIsInstance(kind, str)
        self.assertIsInstance(body, str)


class TestRenderFunctions(unittest.TestCase):
    def test_render_blocker_includes_question_and_options(self) -> None:
        body = render_blocker(
            "my-plan",
            "q-1",
            "phase-a",
            "Which approach?",
            ["Alpha", "Beta"],
        )
        self.assertIn("Which approach?", body)
        self.assertIn("Alpha", body)
        self.assertIn("Beta", body)
        self.assertIn("my-plan", body)

    def test_render_stalled_includes_phase_and_plan_slug(self) -> None:
        body = render_stalled("my-plan", "phase-a", 1800.0)
        self.assertIn("my-plan", body)
        self.assertIn("phase-a", body)
        self.assertIn("30", body)  # 1800s = 30 min


if __name__ == "__main__":
    unittest.main()
