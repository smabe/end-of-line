"""State-level helpers for stuck-tool detection (worker-watchdog P1).

The supervisor needs to dedupe TOOL_STUCK events per descendant_pid so we
don't re-emit on every tick once a wedge is detected. The dedup map lives
on `current_claim.stuck_tool_emitted_at` and auto-clears when the claim is
released (release_claim wipes current_claim entirely).
"""
from __future__ import annotations

import unittest

from end_of_line import state as st


class EventConstantTestCase(unittest.TestCase):
    def test_event_constant_value(self) -> None:
        self.assertEqual(st.EVENT_TOOL_STUCK, "tool_stuck")


class StuckToolDedupTestCase(unittest.TestCase):
    def _claim(self) -> dict:
        return {
            "phase_id": "A",
            "claimed_by": "session-abc",
            "lease_expires": "2026-05-21T15:00:00Z",
            "started_at": "2026-05-21T14:00:00Z",
            "last_heartbeat_at": "2026-05-21T14:00:00Z",
            "attempts": 1,
        }

    def test_already_emitted_false_on_fresh_claim(self) -> None:
        claim = self._claim()
        self.assertFalse(st.tool_stuck_already_emitted(claim, 12345))

    def test_mark_then_already_emitted_true(self) -> None:
        claim = self._claim()
        st.mark_tool_stuck_emitted(claim, 12345, "2026-05-21T14:05:00Z")
        self.assertTrue(st.tool_stuck_already_emitted(claim, 12345))

    def test_mark_is_per_descendant_pid(self) -> None:
        claim = self._claim()
        st.mark_tool_stuck_emitted(claim, 12345, "2026-05-21T14:05:00Z")
        self.assertTrue(st.tool_stuck_already_emitted(claim, 12345))
        self.assertFalse(st.tool_stuck_already_emitted(claim, 67890))

    def test_mark_stores_timestamp(self) -> None:
        claim = self._claim()
        st.mark_tool_stuck_emitted(claim, 12345, "2026-05-21T14:05:00Z")
        self.assertEqual(
            claim["stuck_tool_emitted_at"]["12345"],
            "2026-05-21T14:05:00Z",
        )

    def test_dedup_map_lazy_initialized(self) -> None:
        claim = self._claim()
        self.assertNotIn("stuck_tool_emitted_at", claim)
        st.mark_tool_stuck_emitted(claim, 12345, "2026-05-21T14:05:00Z")
        self.assertIn("stuck_tool_emitted_at", claim)

    def test_already_emitted_handles_missing_map(self) -> None:
        # A claim that's never seen a stuck-tool emit must not raise.
        claim = self._claim()
        self.assertFalse(st.tool_stuck_already_emitted(claim, 12345))

    def test_release_claim_clears_dedup_map(self) -> None:
        # release_claim wipes current_claim entirely, so the dedup map dies
        # with it. New worker on the same phase starts with a fresh map.
        data = st.empty_state("plan-x", "/tmp/plan-x")
        data["current_claim"] = self._claim()
        st.mark_tool_stuck_emitted(
            data["current_claim"], 12345, "2026-05-21T14:05:00Z",
        )
        st.release_claim(data)
        self.assertIsNone(data["current_claim"])


class ActiveToolMarkerTestCase(unittest.TestCase):
    """`current_claim.active_tool_started_at` — the per-Bash-tool-call window
    the supervisor uses to scope stuck-tool detection (#67 follow-up)."""

    def _claim(self) -> dict:
        return {
            "phase_id": "A",
            "claimed_by": "session-abc",
            "lease_expires": "2026-05-21T15:00:00Z",
            "started_at": "2026-05-21T14:00:00Z",
            "last_heartbeat_at": "2026-05-21T14:00:00Z",
            "attempts": 1,
        }

    def test_mark_active_tool_start_sets_field(self) -> None:
        claim = self._claim()
        st.mark_active_tool_start(claim, "2026-05-22T10:00:00Z")
        self.assertEqual(claim["active_tool_started_at"], "2026-05-22T10:00:00Z")

    def test_mark_overwrites_previous(self) -> None:
        # PreToolUse fires before every Bash call; later calls just slide the
        # window forward without erroring on a still-open prior window.
        claim = self._claim()
        st.mark_active_tool_start(claim, "2026-05-22T10:00:00Z")
        st.mark_active_tool_start(claim, "2026-05-22T10:05:00Z")
        self.assertEqual(claim["active_tool_started_at"], "2026-05-22T10:05:00Z")

    def test_clear_active_tool_removes_field(self) -> None:
        claim = self._claim()
        st.mark_active_tool_start(claim, "2026-05-22T10:00:00Z")
        st.clear_active_tool(claim)
        self.assertNotIn("active_tool_started_at", claim)

    def test_clear_is_idempotent(self) -> None:
        # PostToolUse without a matching PreToolUse (worker crash, stale
        # hook state) must not raise.
        claim = self._claim()
        st.clear_active_tool(claim)
        self.assertNotIn("active_tool_started_at", claim)

    def test_release_claim_clears_active_marker(self) -> None:
        data = st.empty_state("plan-x", "/tmp/plan-x")
        data["current_claim"] = self._claim()
        st.mark_active_tool_start(data["current_claim"], "2026-05-22T10:00:00Z")
        st.release_claim(data)
        self.assertIsNone(data["current_claim"])


if __name__ == "__main__":
    unittest.main()
