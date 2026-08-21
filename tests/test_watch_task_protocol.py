"""Tests for watch.project_event_task — task-list protocol projector."""

import unittest

from end_of_line import state as st
from end_of_line.watch import _TASK_STATUS_MAP, project_event_task
from tests import must


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
        self.assertIn("parent=my-plan", must(out))

    def test_phase_completed_emits_completed(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_COMPLETED, phase="foundation", commits=["abc"]),
            "my-plan",
        )
        out = must(out)
        self.assertIn("status=completed", out)
        self.assertIn("task=my-plan/foundation", out)

    def test_phase_blocked_includes_blocker_id_in_msg(self):
        out = project_event_task(
            _evt(
                st.EVENT_PHASE_BLOCKED,
                phase="design",
                blocker_id="blk-42",
                question="Postgres or sqlite?",
            ),
            "my-plan",
        )
        out = must(out)
        self.assertIn("status=in_progress", out)
        self.assertIn("blk-42", out)

    def test_phase_max_attempts_emits_in_progress_with_halt_marker(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_MAX_ATTEMPTS, phase="build", attempts=3),
            "my-plan",
        )
        out = must(out)
        self.assertIn("status=in_progress", out)
        self.assertIn("HALTED", out)

    def test_systemic_failure_emits_in_progress_with_signature(self):
        out = project_event_task(
            _evt(st.EVENT_SYSTEMIC_FAILURE, signature="OOMKilled", log_path="/tmp/x.log"),
            "my-plan",
        )
        out = must(out)
        self.assertIn("status=in_progress", out)
        self.assertIn("OOMKilled", out)

    def test_plan_completed_uses_parent_task_id(self):
        out = must(project_event_task(_evt(st.EVENT_PLAN_COMPLETED), "my-plan"))
        self.assertIn("task=my-plan ", out)  # no /phase
        self.assertNotIn("my-plan/", out)
        self.assertNotIn("parent=", out)  # parent line itself has no parent
        self.assertIn("status=completed", out)

    def test_paused_uses_parent_task_id(self):
        out = must(project_event_task(_evt(st.EVENT_PAUSED, reason="operator"), "my-plan"))
        self.assertIn("task=my-plan ", out)
        self.assertNotIn("my-plan/", out)
        self.assertNotIn("parent=", out)
        self.assertIn("status=in_progress", out)
        self.assertIn("paused", out)

    def test_resumed_uses_parent_task_id(self):
        out = must(project_event_task(_evt(st.EVENT_RESUMED), "my-plan"))
        self.assertIn("task=my-plan ", out)
        self.assertNotIn("parent=", out)
        self.assertNotIn("my-plan/", out)
        self.assertIn("status=in_progress", out)

    def test_phase_stalled_msg_stalled(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_STALLED, phase="build", age_seconds=660.0),
            "my-plan",
        )
        out = must(out)
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
            _evt(
                st.EVENT_PHASE_BLOCKED,
                phase="design",
                blocker_id="blk-42",
                question="Postgres or sqlite?",
            ),
            "my-plan",
        )
        self.assertEqual(
            out,
            "TASK_UPDATE task=my-plan/design parent=my-plan "
            'status=in_progress msg="BLOCKED blk-42 — Postgres or sqlite?"',
        )

    def test_phase_max_attempts_full_line_shape(self):
        out = project_event_task(
            _evt(st.EVENT_PHASE_MAX_ATTEMPTS, phase="build", attempts=3),
            "my-plan",
        )
        self.assertEqual(
            out,
            "TASK_UPDATE task=my-plan/build parent=my-plan "
            'status=in_progress msg="HALTED (max attempts on build)"',
        )

    def test_systemic_failure_full_line_shape(self):
        out = project_event_task(
            _evt(
                st.EVENT_SYSTEMIC_FAILURE,
                signature="OOMKilled",
                phase="impl",
                log_path="/tmp/x.log",
            ),
            "my-plan",
        )
        self.assertEqual(
            out,
            "TASK_UPDATE task=my-plan/impl parent=my-plan "
            'status=in_progress msg="SYSTEMIC FAILURE — OOMKilled"',
        )


class FilteredEventsTest(unittest.TestCase):
    def test_task_spawned_returns_none(self):
        out = project_event_task(
            _evt(st.EVENT_TASK_SPAWNED, task="task-1", source="gh", spawned_by_phase="impl"),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_worktree_attached_returns_none_default(self):
        out = project_event_task(
            _evt(st.EVENT_WORKTREE_ATTACHED, path="/tmp/wt", branch="clu/foo", base_ref="abc"),
            "my-plan",
        )
        self.assertIsNone(out)

    def test_worktree_attached_returns_in_progress_with_verbose(self):
        out = project_event_task(
            _evt(st.EVENT_WORKTREE_ATTACHED, path="/tmp/wt", branch="clu/foo", base_ref="abc"),
            "my-plan",
            verbose=True,
        )
        self.assertIn("status=in_progress", must(out))

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
            _evt(
                st.EVENT_PHASE_BLOCKED,
                phase="design",
                blocker_id="blk-1",
                question='Use "postgres" or sqlite?',
            ),
            "my-plan",
        )
        # Inner double-quotes must be backslash-escaped
        self.assertIn('\\"', must(out))

    def test_msg_with_backslash_escaped(self):
        out = project_event_task(
            _evt(
                st.EVENT_PHASE_BLOCKED,
                phase="design",
                blocker_id="blk-1",
                question="path\\to\\file",
            ),
            "my-plan",
        )
        self.assertIn("\\\\", must(out))


class MsgNewlineFramingTest(unittest.TestCase):
    """A newline in operator text must not break the one-line msg="…" framing.

    `_task_line` interpolates msg raw into `msg="{msg}"`; a raw newline closes
    the record early and the consumer drops the tail as a non-TASK_* line.
    """

    def test_a_newline_in_a_question_stays_on_one_line(self):
        out = must(
            project_event_task(
                _evt(
                    st.EVENT_PHASE_BLOCKED,
                    phase="design",
                    blocker_id="blk-1",
                    question="line one\nline two",
                ),
                "my-plan",
            )
        )
        # No raw newline survived into the emitted record.
        self.assertNotIn("\n", out)
        # The newline is present as its two-character escape instead.
        self.assertIn("\\n", out)
        # msg=" is closed by a matching quote — the record's last char.
        self.assertTrue(out.endswith('"'))

    def test_a_carriage_return_is_escaped_too(self):
        out = must(
            project_event_task(
                _evt(
                    st.EVENT_PHASE_BLOCKED,
                    phase="design",
                    blocker_id="blk-1",
                    question="line one\rline two",
                ),
                "my-plan",
            )
        )
        self.assertNotIn("\r", out)
        self.assertIn("\\r", out)

    def test_escaping_survives_a_quote_and_a_newline_together(self):
        # _escape_msg chains str.replace; a newline emits a backslash, so the
        # backslash pass must run first or the escape is itself double-escaped.
        out = must(
            project_event_task(
                _evt(
                    st.EVENT_PHASE_BLOCKED,
                    phase="design",
                    blocker_id="blk-1",
                    question='say "hi"\nthen leave',
                ),
                "my-plan",
            )
        )
        self.assertNotIn("\n", out)
        self.assertIn('\\"', out)
        self.assertIn("\\n", out)
        self.assertTrue(out.endswith('"'))

    def test_every_task_status_map_key_emits_exactly_one_line(self):
        # Iterate the live map so a newly-added key without escaping is a visible
        # failure, not a silent gap. Newlines go in every operator-text field
        # _task_msg_for interpolates (question / signature / reason); phase and
        # blocker_id are validated slugs and never carry a newline.
        for event_type in _TASK_STATUS_MAP:
            evt = _evt(
                event_type,
                phase="design",
                blocker_id="blk-1",
                question="q line\ntwo",
                signature="sig line\ntwo",
                reason="reason line\ntwo",
                attempts=2,
            )
            out = project_event_task(evt, "my-plan")
            self.assertIsNotNone(out, f"{event_type} produced no line")
            self.assertNotIn("\n", must(out), f"{event_type} leaked a raw newline")


class MsgTruncationTest(unittest.TestCase):
    def test_existing_msg_cap_is_unchanged(self):
        # The cap is pinned unchanged by the neighbor below on plain input; this
        # phase must not move it. What IS new is the interaction: _trunc runs on
        # the raw question (a newline is one char) BEFORE _escape_msg expands it
        # to two, so a long, newline-bearing question stays both capped and on
        # one line. Guarding that interaction — not re-duplicating the plain cap.
        long_q = "X" * 50 + "\n" + "Y" * 100
        out = must(
            project_event_task(
                _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1", question=long_q),
                "my-plan",
            )
        )
        self.assertIn("…", out)  # still truncated
        self.assertNotIn("\n", out)  # still one line
        self.assertIn("\\n", out)  # the newline survived as its escape
        msg_content = out.split('msg="', 1)[1].rstrip('"')
        self.assertLessEqual(len(msg_content), 120)  # loose bound intact, not tightened

    def test_long_question_truncated_to_100_chars(self):
        long_q = "X" * 120
        out = project_event_task(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1", question=long_q),
            "my-plan",
        )
        out = must(out)
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
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1", question=""),
            "my-plan",
        )
        self.assertIsNotNone(out)


if __name__ == "__main__":
    unittest.main()
