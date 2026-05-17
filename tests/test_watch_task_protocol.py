"""Tests for watch.project_event_task — task-list protocol projector."""
import unittest

from end_of_line import state as st
from end_of_line.watch import project_event_task


def _evt(type_, **fields):
    return {"type": type_, "ts": "2026-05-17T10:00:00Z", **fields}


class PerEventCoverageTest(unittest.TestCase):

    def test_phase_started_emits_task_update_in_progress(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_STARTED, phase="foundation", claimed_by="sess-x"),
            "my-plan",
        )
        self.assertEqual(
            out,
            'TASK_UPDATE task=my-plan/foundation parent=my-plan status=in_progress msg="started (attempt 1)"',
        )

    def test_phase_scoped_events_include_parent_field(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_COMPLETED, phase="foundation"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("parent=my-plan", out)

    def test_phase_completed_emits_completed(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_COMPLETED, phase="foundation", commits=["abc"]),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("status=completed", out)
        self.assertIn("task=my-plan/foundation", out)

    def test_phase_blocked_includes_blocker_id_in_msg(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-42",
                 question="Postgres or sqlite?"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("status=in_progress", out)
        self.assertIn("blk-42", out)

    def test_phase_max_attempts_emits_in_progress_with_halt_marker(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_MAX_ATTEMPTS, phase="build", attempts=3),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("status=in_progress", out)
        self.assertIn("HALTED", out)

    def test_systemic_failure_emits_in_progress_with_signature(self):
        out = project_event_task(
            _evt(st.EVENT_SYSTEMIC_FAILURE, signature="OOMKilled",
                 log_path="/tmp/x.log"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("status=in_progress", out)
        self.assertIn("OOMKilled", out)

    def test_plan_completed_uses_parent_task_id(self):
        out = project_event_task(_evt(st.EVENT_PLAN_COMPLETED), "my-plan")
        self.assertIsNotNone(out)
        self.assertIn("task=my-plan ", out)  # no /phase
        self.assertNotIn("my-plan/", out)
        self.assertNotIn("parent=", out)  # parent line itself has no parent
        self.assertIn("status=completed", out)

    def test_paused_uses_parent_task_id(self):
        out = project_event_task(_evt(st.EVENT_PAUSED, reason="operator"), "my-plan")
        self.assertIsNotNone(out)
        self.assertIn("task=my-plan ", out)
        self.assertNotIn("my-plan/", out)
        self.assertNotIn("parent=", out)
        self.assertIn("status=in_progress", out)
        self.assertIn("paused", out)

    def test_resumed_uses_parent_task_id(self):
        out = project_event_task(_evt(st.EVENT_RESUMED), "my-plan")
        self.assertIsNotNone(out)
        self.assertIn("task=my-plan ", out)
        self.assertNotIn("parent=", out)
        self.assertNotIn("my-plan/", out)
        self.assertIn("status=in_progress", out)

    def test_phase_stalled_msg_stalled(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_STALLED, phase="build", age_seconds=660.0),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("task=my-plan/build", out)
        self.assertIn("stalled", out)
        self.assertIn("status=in_progress", out)


class FullLineShapeTest(unittest.TestCase):
    """Freeze the exact TASK_UPDATE line shape for the operationally
    significant msg paths: BLOCKED, MAX_ATTEMPTS, SYSTEMIC_FAILURE.
    The msg content is the operator's signal-to-act trigger via
    PushNotification, per /clu-plan SKILL.md."""

    def test_phase_blocked_full_line_shape(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design",
                 blocker_id="blk-42",
                 question="Postgres or sqlite?"),
            "my-plan",
        )
        self.assertEqual(
            out,
            'TASK_UPDATE task=my-plan/design parent=my-plan '
            'status=in_progress msg="BLOCKED blk-42 — Postgres or sqlite?"',
        )

    def test_phase_max_attempts_full_line_shape(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_MAX_ATTEMPTS, phase="build", attempts=3),
            "my-plan",
        )
        self.assertEqual(
            out,
            'TASK_UPDATE task=my-plan/build parent=my-plan '
            'status=in_progress msg="HALTED (max attempts on build)"',
        )

    def test_systemic_failure_full_line_shape(self):
        out = project_event_task(
            _evt(st.EVENT_SYSTEMIC_FAILURE, signature="OOMKilled",
                 phase="impl", log_path="/tmp/x.log"),
            "my-plan",
        )
        self.assertEqual(
            out,
            'TASK_UPDATE task=my-plan/impl parent=my-plan '
            'status=in_progress msg="SYSTEMIC FAILURE — OOMKilled"',
        )


class FilteredEventsTest(unittest.TestCase):

    def test_task_spawned_returns_none(self):
        out = project_event_task(
            _evt(st.EVENT_TASK_SPAWNED, task="task-1", source="gh",
                 spawned_by_phase="impl"),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_worktree_attached_returns_none_default(self):
        out = project_event_task(
            _evt(st.EVENT_WORKTREE_ATTACHED, path="/tmp/wt", branch="clu/foo",
                 base_ref="abc"),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_worktree_attached_returns_in_progress_with_verbose(self):
        out = project_event_task(
            _evt(st.EVENT_WORKTREE_ATTACHED, path="/tmp/wt", branch="clu/foo",
                 base_ref="abc"),
            "my-plan",
            verbose=True,
        )
        self.assertIsNotNone(out)
        self.assertIn("status=in_progress", out)

    def test_unknown_event_returns_none(self):
        out = project_event_task(_evt("garbage_event_xyz"), "my-plan")
        self.assertIsNone(out)

    def test_blocker_answered_returns_none(self):
        out = project_event_task(
            _evt(st.EVENT_BLOCKER_ANSWERED, blocker_id="blk-1", answer="yes"),
            "my-plan",
        )
        self.assertIsNone(out)


class MsgEscapingTest(unittest.TestCase):

    def test_msg_with_quotes_escaped(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1",
                 question='Use "postgres" or sqlite?'),
            "my-plan",
        )
        self.assertIsNotNone(out)
        # Inner double-quotes must be backslash-escaped
        self.assertIn('\\"', out)

    def test_msg_with_backslash_escaped(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1",
                 question="path\\to\\file"),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("\\\\", out)


class MsgTruncationTest(unittest.TestCase):

    def test_long_question_truncated_to_100_chars(self):
        long_q = "X" * 120
        out = project_event_task(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1",
                 question=long_q),
            "my-plan",
        )
        self.assertIsNotNone(out)
        self.assertIn("…", out)
        # Extract msg content between the outer quotes
        msg_content = out.split('msg="', 1)[1].rstrip('"')
        self.assertLessEqual(len(msg_content), 120)  # truncated somewhere

    def test_short_msg_passes_through(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_STARTED, phase="p", claimed_by="s"),
            "short-plan",
        )
        self.assertIsNotNone(out)

    def test_empty_question_ok(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1",
                 question=""),
            "my-plan",
        )
        self.assertIsNotNone(out)


if __name__ == "__main__":
    unittest.main()
