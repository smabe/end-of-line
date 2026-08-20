"""Phase `foundation` tests: ExitCode.QUEUE_CAP, event constants, config default.

The queue-entry round-trip that used to live here moved to
`test_queue_primitive.py` with the store it tests — the entry is rows in a
table now, not a document in a file.
"""

from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
