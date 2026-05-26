"""Phase `foundation` tests: ExitCode.QUEUE_CAP, event constants, config default,
and queue-entry schema extension for worker-enqueue lineage fields."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from end_of_line import queue
from end_of_line import state as st
from end_of_line.cli import ExitCode


class ExitCodeTests(unittest.TestCase):
    def test_exit_code_queue_cap_value(self) -> None:
        self.assertEqual(ExitCode.QUEUE_CAP, 11)

    def test_no_duplicate_exit_code_values(self) -> None:
        values = [e.value for e in ExitCode]
        self.assertEqual(len(values), len(set(values)))


class EventConstantTests(unittest.TestCase):
    def test_event_queue_appended_value(self) -> None:
        self.assertEqual(st.EVENT_QUEUE_APPENDED, "queue_appended")

    def test_event_queue_rejected_value(self) -> None:
        self.assertEqual(st.EVENT_QUEUE_REJECTED, "queue_rejected")


class DefaultConstantTests(unittest.TestCase):
    def test_default_constant_exposed(self) -> None:
        self.assertEqual(st.DEFAULT_MAX_QUEUE_ADDS_PER_PHASE, 3)

    def test_empty_state_includes_queue_adds_cap(self) -> None:
        data = st.empty_state("test-plan", "/tmp/plans")
        self.assertEqual(data["config"]["max_queue_adds_per_phase"], 3)


class QueueEntrySchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "queue.json"

    def test_queue_entry_extra_fields_roundtrip(self) -> None:
        entry = {
            "slug": "next-plan",
            "added_at": "2026-05-17T12:00:00Z",
            "added_by": "worker",
            "position_at_add": 1,
            "source_plan": "current-plan",
            "source_phase": "impl",
            "source_token_fp": "ab12cd34",
            "reason": "chain follow-up",
        }
        with queue.mutate(self.path) as data:
            data["queue"].append(entry)

        loaded = queue.load(self.path)
        loaded_entry = loaded["queue"][0]
        self.assertEqual(loaded_entry["source_plan"], "current-plan")
        self.assertEqual(loaded_entry["source_phase"], "impl")
        self.assertEqual(loaded_entry["source_token_fp"], "ab12cd34")
        self.assertEqual(loaded_entry["reason"], "chain follow-up")

    def test_operator_entry_fields_none_after_load(self) -> None:
        # v1-shaped entry without the new lineage fields — forward compat.
        v1_entry = {
            "slug": "old-plan",
            "added_at": "2026-05-17T11:00:00Z",
            "added_by": "operator",
            "position_at_add": 0,
        }
        with queue.mutate(self.path) as data:
            data["queue"].append(v1_entry)

        loaded = queue.load(self.path)
        loaded_entry = loaded["queue"][0]
        self.assertIsNone(loaded_entry.get("source_plan"))
        self.assertIsNone(loaded_entry.get("source_phase"))
        self.assertIsNone(loaded_entry.get("source_token_fp"))
        self.assertIsNone(loaded_entry.get("reason"))
