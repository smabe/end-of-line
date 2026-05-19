"""Tests for watch.project_event — pure event projector."""
import unittest
import importlib

from end_of_line import state as st
from end_of_line.watch import project_event


def _evt(type_, **fields):
    return {"type": type_, "ts": "2026-05-17T10:00:00Z", **fields}


class DefaultVisibleEventsTest(unittest.TestCase):
    # --- phase-scoped events ---

    def test_phase_started(self):
        out = project_event(
            _evt(st.EVENT_PHASE_STARTED, phase="foundation", attempts=1), "my-plan"
        )
        self.assertEqual(out, "my-plan/foundation: started (attempt 1)")

    def test_phase_started_attempt_2(self):
        out = project_event(
            _evt(st.EVENT_PHASE_STARTED, phase="foundation", attempts=2), "my-plan"
        )
        self.assertEqual(out, "my-plan/foundation: started (attempt 2)")

    def test_phase_started_missing_attempts_defaults_to_1(self):
        out = project_event(_evt(st.EVENT_PHASE_STARTED, phase="p"), "s")
        self.assertIn("attempt 1", out)

    def test_phase_completed(self):
        out = project_event(
            _evt(st.EVENT_PHASE_COMPLETED, phase="foundation", commits=["abc"]),
            "my-plan",
        )
        self.assertEqual(out, "my-plan/foundation: completed")

    def test_phase_blocked_basic(self):
        out = project_event(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1"),
            "my-plan",
        )
        self.assertEqual(out, "my-plan/design: BLOCKED blk-1")

    def test_phase_blocked_includes_blocker_id(self):
        out = project_event(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-42"),
            "my-plan",
        )
        self.assertIn("blk-42", out)

    def test_phase_blocked_with_question(self):
        out = project_event(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1",
                 question="Use postgres or sqlite?"),
            "my-plan",
        )
        self.assertIn("blk-1", out)
        self.assertIn("Use postgres or sqlite?", out)

    def test_phase_blocked_truncates_long_question(self):
        long_q = "A" * 120
        out = project_event(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1",
                 question=long_q),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("blk-1", out)
        # question field truncated to 100 chars + ellipsis
        self.assertIn("…", out)
        # line should not contain the full 120-char question
        self.assertNotIn("A" * 101, out)

    def test_blocker_answered(self):
        out = project_event(
            _evt(st.EVENT_BLOCKER_ANSWERED, blocker_id="blk-1", answer="postgres"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("blk-1", out)
        self.assertIn("postgres", out)

    def test_blocker_consumed(self):
        out = project_event(
            _evt(st.EVENT_BLOCKER_CONSUMED, blocker_id="blk-1"), "my-plan"
        )
        self.assertIsNotNone(out)
        self.assertIn("blk-1", out)

    def test_blocker_sla_exceeded(self):
        out = project_event(
            _evt(st.EVENT_BLOCKER_SLA_EXCEEDED, blocker_id="blk-1", age_hours=25.5),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("blk-1", out)

    def test_phase_max_attempts(self):
        out = project_event(
            _evt(st.EVENT_PHASE_MAX_ATTEMPTS, phase="build", attempts=3), "my-plan"
        )
        self.assertIsNotNone(out)
        self.assertIn("build", out)

    def test_phase_stalled(self):
        out = project_event(
            _evt(st.EVENT_PHASE_STALLED, phase="build", claimed_by="sess-abc",
                 age_seconds=660.0),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("build", out)

    def test_task_spawned(self):
        out = project_event(
            _evt(st.EVENT_TASK_SPAWNED, task="task-1", source="gh",
                 spawned_by_phase="impl"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("task-1", out)

    def test_task_completed(self):
        out = project_event(
            _evt(st.EVENT_TASK_COMPLETED, task="task-1", commits=[], forced=False),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("task-1", out)

    def test_dispatch_failed(self):
        out = project_event(
            _evt(st.EVENT_DISPATCH_FAILED, phase="impl", token="sess-x",
                 reason="binary not found"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("impl", out)

    def test_worktree_missing(self):
        out = project_event(
            _evt(st.EVENT_WORKTREE_MISSING, worktree_path="/tmp/wt"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("/tmp/wt", out)

    def test_worktree_conflict_warning(self):
        out = project_event(
            _evt(st.EVENT_WORKTREE_CONFLICT_WARNING, other_slug="other-plan"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("other-plan", out)

    def test_retry_requested(self):
        out = project_event(
            _evt(st.EVENT_RETRY_REQUESTED, phase="impl"), "my-plan"
        )
        self.assertIsNotNone(out)
        self.assertIn("impl", out)

    # --- plan-scoped events (no /phase segment) ---

    def test_plan_completed_no_phase_segment(self):
        out = project_event(_evt(st.EVENT_PLAN_COMPLETED), "my-plan")
        self.assertIsNotNone(out)
        self.assertTrue(out.startswith("my-plan:"))
        # no slash after the slug
        self.assertNotIn("my-plan/", out)

    def test_paused_drops_phase_segment(self):
        out = project_event(_evt(st.EVENT_PAUSED, reason="operator"), "my-plan")
        self.assertIsNotNone(out)
        self.assertTrue(out.startswith("my-plan:"))
        self.assertNotIn("my-plan/", out)

    def test_paused_no_reason(self):
        out = project_event(_evt(st.EVENT_PAUSED), "my-plan")
        self.assertIsNotNone(out)

    def test_resumed(self):
        out = project_event(_evt(st.EVENT_RESUMED), "my-plan")
        self.assertIsNotNone(out)
        self.assertTrue(out.startswith("my-plan:"))
        self.assertNotIn("my-plan/", out)

    def test_queue_popped(self):
        out = project_event(
            _evt(st.EVENT_QUEUE_POPPED, slug="next-plan", added_by="operator",
                 position=1),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("next-plan", out)

    def test_queue_appended(self):
        out = project_event(
            _evt(st.EVENT_QUEUE_APPENDED, slug="follow-up", source_phase="impl",
                 token_fp="abc123"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("follow-up", out)

    def test_queue_rejected(self):
        out = project_event(
            _evt(st.EVENT_QUEUE_REJECTED, slug="follow-up", source_phase="impl",
                 reason="cap"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("follow-up", out)


class VerboseOnlyEventsTest(unittest.TestCase):

    def test_lease_expired_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_LEASE_EXPIRED, phase="impl", claimed_by="sess-abc"),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_lease_expired_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_LEASE_EXPIRED, phase="impl", claimed_by="sess-abc"),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)

    def test_lease_extended_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_LEASE_EXTENDED, phase="impl", extended_by_minutes=15,
                 new_expires="2026-05-17T11:00:00Z", operator=True),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_lease_extended_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_LEASE_EXTENDED, phase="impl", extended_by_minutes=15,
                 new_expires="2026-05-17T11:00:00Z", operator=True),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)

    def test_claim_force_released_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_CLAIM_FORCE_RELEASED, phase="impl", token="sess-x",
                 forced=True, released_by_operator=True),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_claim_force_released_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_CLAIM_FORCE_RELEASED, phase="impl", token="sess-x",
                 forced=True, released_by_operator=True),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)

    def test_attempts_reset_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_ATTEMPTS_RESET, phase="impl", operator=True), "my-plan"
        )
        self.assertIsNone(out)

    def test_attempts_reset_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_ATTEMPTS_RESET, phase="impl", operator=True),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)

    def test_stuck_blocker_repinged_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_STUCK_BLOCKER_REPINGED, blocker_id="blk-1",
                 phase="impl", age_min=45),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_stuck_blocker_repinged_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_STUCK_BLOCKER_REPINGED, blocker_id="blk-1",
                 phase="impl", age_min=45),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)

    def test_stalled_claim_notified_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_STALLED_CLAIM_NOTIFIED, phase="impl", stalled_min=12),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_stalled_claim_notified_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_STALLED_CLAIM_NOTIFIED, phase="impl", stalled_min=12),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)

    def test_worktree_attached_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_WORKTREE_ATTACHED, path="/tmp/wt", branch="clu/foo",
                 base_ref="abc"),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_worktree_attached_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_WORKTREE_ATTACHED, path="/tmp/wt", branch="clu/foo",
                 base_ref="abc"),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)

    def test_worktree_cleaned_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_WORKTREE_CLEANED, path="/tmp/wt", branch="clu/foo",
                 worktree_removed=True, branch_removed=True,
                 worktree_error=None, branch_error=None, trigger="complete"),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_worktree_cleaned_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_WORKTREE_CLEANED, path="/tmp/wt", branch="clu/foo",
                 worktree_removed=True, branch_removed=True,
                 worktree_error=None, branch_error=None, trigger="complete"),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)

    def test_worktree_retained_ahead_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_WORKTREE_RETAINED_AHEAD, path="/tmp/wt",
                 branch="clu/foo", reason="ahead", ahead_commits=["abc"],
                 trigger="gc"),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_worktree_retained_ahead_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_WORKTREE_RETAINED_AHEAD, path="/tmp/wt",
                 branch="clu/foo", reason="ahead", ahead_commits=["abc"],
                 trigger="gc"),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)


class EdgeCasesTest(unittest.TestCase):

    def test_unknown_event_type_returns_none(self):
        out = project_event(_evt("garbage_event_xyz"), "my-plan")
        self.assertIsNone(out)

    def test_unknown_type_with_verbose_still_none(self):
        out = project_event(_evt("unknown_future_event"), "my-plan", verbose=True)
        self.assertIsNone(out)

    def test_minimal_event_phase_started(self):
        # Only type + ts — no phase, no attempts
        out = project_event({"type": st.EVENT_PHASE_STARTED, "ts": "2026-01-01T00:00:00Z"},
                            "p")
        self.assertIsNotNone(out)
        self.assertIn("attempt 1", out)

    def test_minimal_event_phase_completed(self):
        out = project_event({"type": st.EVENT_PHASE_COMPLETED, "ts": "2026-01-01T00:00:00Z"},
                            "p")
        self.assertIsNotNone(out)

    def test_minimal_event_phase_blocked(self):
        out = project_event({"type": st.EVENT_PHASE_BLOCKED, "ts": "2026-01-01T00:00:00Z"},
                            "p")
        self.assertIsNotNone(out)

    def test_minimal_event_plan_completed(self):
        out = project_event({"type": st.EVENT_PLAN_COMPLETED, "ts": "2026-01-01T00:00:00Z"},
                            "p")
        self.assertIsNotNone(out)

    def test_minimal_event_paused(self):
        out = project_event({"type": st.EVENT_PAUSED, "ts": "2026-01-01T00:00:00Z"}, "p")
        self.assertIsNotNone(out)

    def test_minimal_event_resumed(self):
        out = project_event({"type": st.EVENT_RESUMED, "ts": "2026-01-01T00:00:00Z"}, "p")
        self.assertIsNotNone(out)

    def test_dispatch_failed_truncates_long_reason(self):
        long_reason = "B" * 120
        out = project_event(
            _evt(st.EVENT_DISPATCH_FAILED, phase="impl", token="t", reason=long_reason),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("…", out)

    def test_systemic_failure(self):
        out = project_event(
            _evt(st.EVENT_SYSTEMIC_FAILURE, signature="OOMKilled",
                 log_path="/tmp/x.log"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("OOMKilled", out)

    def test_systemic_failure_minimal(self):
        out = project_event({"type": st.EVENT_SYSTEMIC_FAILURE, "ts": "2026-01-01T00:00:00Z"},
                            "p")
        self.assertIsNotNone(out)

    def test_orphan_reaped_filtered_default(self):
        out = project_event(
            _evt(st.EVENT_PHASE_ORPHAN_REAPED, phase="impl", pid=12345,
                 signaled="SIGTERM", cmdline_mismatch=False),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_orphan_reaped_shown_with_verbose(self):
        out = project_event(
            _evt(st.EVENT_PHASE_ORPHAN_REAPED, phase="impl", pid=12345,
                 signaled="SIGTERM", cmdline_mismatch=False),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)
        self.assertIn("orphan reaped", out)
        self.assertIn("pid=12345", out)
        self.assertIn("signaled=SIGTERM", out)


if __name__ == "__main__":
    unittest.main()
