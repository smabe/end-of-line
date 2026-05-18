"""Tests for end_of_line.state_locator — find_blocker_for_reply."""
from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from end_of_line import state as st
from end_of_line.registry import PlanEntry
from end_of_line.state_locator import LocatorResult, find_blocker_for_reply
from tests import isolate_registry


def _make_project(tmp: Path, slug: str) -> tuple[Path, Path, PlanEntry]:
    """Return (project_root, state_path, entry) for a minimal plan project."""
    project = tmp / slug
    project.mkdir()
    state_dir = project / "plans" / ".orchestrator"
    state_dir.mkdir(parents=True)
    state_path = state_dir / f"{slug}.state.json"
    data = st.empty_state(slug, "plans")
    st.save_atomic(state_path, data)
    entry = PlanEntry(
        project_root=str(project),
        plan_slug=slug,
        registered_at="2026-01-01T00:00:00Z",
    )
    return project, state_path, entry


def _add_blocker_raw(state_path: Path, options: list[str] | None = None) -> None:
    """Append a bare open blocker directly — avoids touching EVENT_PHASE_BLOCKED
    so last_notified_at stays '' for all plans (stable tie in ambiguity tests)."""
    data = json.loads(state_path.read_text())
    opts = options if options is not None else ["yes", "no"]
    blocker_id = f"q-{len(data['blockers']) + 1}"
    data["blockers"].append({
        "id": blocker_id,
        "phase_id": "p1",
        "type": "blocked_input",
        "question": "Question?",
        "options": opts,
        "context": "",
        "asked_at": "2026-01-01T00:00:00Z",
        "answer": None,
        "answered_at": None,
    })
    st.save_atomic(state_path, data)


class StateLocatorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        isolate_registry(self, self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------ #
    # Core matching                                                         #
    # ------------------------------------------------------------------ #

    def test_no_registered_plans_returns_not_found(self) -> None:
        result = find_blocker_for_reply([], "1")
        self.assertEqual(result.variant, "NOT_FOUND")

    def test_one_open_blocker_bare_digit_returns_found(self) -> None:
        _, state_path, entry = _make_project(self.tmp, "plan-a")
        _add_blocker_raw(state_path)
        result = find_blocker_for_reply([entry], "1")
        self.assertEqual(result.variant, "FOUND")
        self.assertEqual(result.blocker_id, "q-1")
        self.assertEqual(result.answer_index, 1)

    def test_two_open_blockers_bare_digit_returns_ambiguous(self) -> None:
        _, sp_a, entry_a = _make_project(self.tmp, "plan-a")
        _, sp_b, entry_b = _make_project(self.tmp, "plan-b")
        _add_blocker_raw(sp_a)
        _add_blocker_raw(sp_b)
        result = find_blocker_for_reply([entry_a, entry_b], "1")
        self.assertEqual(result.variant, "AMBIGUOUS")
        self.assertEqual(len(result.candidates), 2)

    def test_slug_qualified_reply_returns_found(self) -> None:
        _, sp_a, entry_a = _make_project(self.tmp, "plan-a")
        _, sp_b, entry_b = _make_project(self.tmp, "plan-b")
        _add_blocker_raw(sp_a)
        _add_blocker_raw(sp_b)
        result = find_blocker_for_reply([entry_a, entry_b], "plan-a 1")
        self.assertEqual(result.variant, "FOUND")
        self.assertEqual(result.blocker_id, "q-1")

    def test_unknown_slug_returns_not_found(self) -> None:
        _, sp_a, entry_a = _make_project(self.tmp, "plan-a")
        _add_blocker_raw(sp_a)
        result = find_blocker_for_reply([entry_a], "nonexistent 1")
        self.assertEqual(result.variant, "NOT_FOUND")

    def test_unrelated_text_returns_not_found(self) -> None:
        _, sp_a, entry_a = _make_project(self.tmp, "plan-a")
        _add_blocker_raw(sp_a)
        result = find_blocker_for_reply([entry_a], "hello world")
        self.assertEqual(result.variant, "NOT_FOUND")

    def test_no_open_blockers_returns_not_found(self) -> None:
        _, sp_a, entry_a = _make_project(self.tmp, "plan-a")
        # State file exists but has no open blockers
        result = find_blocker_for_reply([entry_a], "1")
        self.assertEqual(result.variant, "NOT_FOUND")

    def test_answered_blocker_not_open(self) -> None:
        _, state_path, entry = _make_project(self.tmp, "plan-a")
        # Add then immediately answer the blocker
        _add_blocker_raw(state_path)
        data = json.loads(state_path.read_text())
        data["blockers"][0]["answer"] = "1"
        st.save_atomic(state_path, data)
        result = find_blocker_for_reply([entry], "1")
        self.assertEqual(result.variant, "NOT_FOUND")

    # ------------------------------------------------------------------ #
    # Fault-tolerance                                                       #
    # ------------------------------------------------------------------ #

    def test_unreadable_state_file_logs_and_skips(self) -> None:
        _, sp_a, entry_a = _make_project(self.tmp, "plan-a")
        _, sp_b, entry_b = _make_project(self.tmp, "plan-b")
        _, sp_c, entry_c = _make_project(self.tmp, "plan-c")
        _add_blocker_raw(sp_a)
        # plan-b has no blockers, plan-c has corrupt JSON
        sp_c.write_bytes(b"not valid json{{{")
        with self.assertLogs("end_of_line.state_locator", level=logging.WARNING) as cm:
            result = find_blocker_for_reply([entry_a, entry_b, entry_c], "plan-a 1")
        self.assertEqual(result.variant, "FOUND")
        self.assertTrue(any("plan-c" in line for line in cm.output))

    def test_schema_mismatch_skipped(self) -> None:
        _, sp_a, entry_a = _make_project(self.tmp, "plan-a")
        _, sp_b, entry_b = _make_project(self.tmp, "plan-b")
        _add_blocker_raw(sp_a)
        # Write wrong schema_version for plan-b
        data = json.loads(sp_b.read_text())
        data["schema_version"] = 999
        st.save_atomic(sp_b, data)
        _add_blocker_raw(sp_b)
        with self.assertLogs("end_of_line.state_locator", level=logging.WARNING):
            result = find_blocker_for_reply([entry_a, entry_b], "plan-a 1")
        self.assertEqual(result.variant, "FOUND")

    def test_missing_state_file_skipped(self) -> None:
        _, sp_a, entry_a = _make_project(self.tmp, "plan-a")
        _, sp_b, entry_b = _make_project(self.tmp, "plan-b")
        _add_blocker_raw(sp_a)
        sp_b.unlink()  # delete state file
        with self.assertLogs("end_of_line.state_locator", level=logging.WARNING):
            result = find_blocker_for_reply([entry_a, entry_b], "plan-a 1")
        self.assertEqual(result.variant, "FOUND")

    # ------------------------------------------------------------------ #
    # FOUND carries state_path                                              #
    # ------------------------------------------------------------------ #

    def test_returns_state_path_for_writer(self) -> None:
        _, state_path, entry = _make_project(self.tmp, "plan-a")
        _add_blocker_raw(state_path)
        result = find_blocker_for_reply([entry], "1")
        self.assertEqual(result.variant, "FOUND")
        self.assertEqual(result.state_path, state_path.resolve())


if __name__ == "__main__":
    unittest.main()
