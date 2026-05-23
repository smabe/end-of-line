"""Tests for `watch.project_event(..., operator=True)` — the #70
operator-dashboard filter mode.

The operator filter narrows visible events to the cross-plan-worth-
interrupting set: tool_stuck, phase_blocked, attestation_refused,
stalled_claim_notified. Default-visible noise (phase_started/completed,
queue/lease events, etc.) is suppressed. Verbose-only gating is bypassed
because the operator cares about wedges even at default volume.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from end_of_line.watch import project_event
from tests import CluTestCase


def _evt(type_, **fields):
    return {"type": type_, "ts": "2026-05-23T16:00:00Z", **fields}


class OperatorFilterEmitsTest(unittest.TestCase):
    """Each of the 4 operator-visible event types renders under operator=True."""

    def test_tool_stuck_emits(self) -> None:
        out = project_event(
            _evt(st.EVENT_TOOL_STUCK, phase="p", descendant_pid=123,
                 elapsed_seconds=600, command="xcodebuild test"),
            "my-plan", operator=True,
        )
        self.assertIsNotNone(out)
        self.assertIn("STUCK TOOL", out)

    def test_phase_blocked_emits(self) -> None:
        out = project_event(
            _evt(st.EVENT_PHASE_BLOCKED, phase="design", blocker_id="blk-1",
                 question="postgres or sqlite?"),
            "my-plan", operator=True,
        )
        self.assertIsNotNone(out)
        self.assertIn("BLOCKED", out)

    def test_attestation_refused_emits(self) -> None:
        out = project_event(
            _evt(st.EVENT_ATTESTATION_REFUSED, phase="p",
                 gate="verify", stamped_at=None, head_sha="abc1234"),
            "my-plan", operator=True,
        )
        self.assertIsNotNone(out)
        self.assertIn("ATTESTATION REFUSED", out)

    def test_stalled_claim_notified_emits_without_verbose(self) -> None:
        # _VERBOSE_ONLY normally suppresses this — operator mode bypasses.
        out = project_event(
            _evt(st.EVENT_STALLED_CLAIM_NOTIFIED, phase="p"),
            "my-plan", operator=True,
        )
        self.assertIsNotNone(out)


class OperatorFilterSuppressesTest(unittest.TestCase):
    """Default-visible noise is hidden under operator=True."""

    def test_phase_started_suppressed(self) -> None:
        self.assertIsNone(project_event(
            _evt(st.EVENT_PHASE_STARTED, phase="p", attempts=1),
            "my-plan", operator=True,
        ))

    def test_phase_completed_suppressed(self) -> None:
        self.assertIsNone(project_event(
            _evt(st.EVENT_PHASE_COMPLETED, phase="p"),
            "my-plan", operator=True,
        ))

    def test_plan_completed_suppressed(self) -> None:
        self.assertIsNone(project_event(
            _evt(st.EVENT_PLAN_COMPLETED), "my-plan", operator=True,
        ))

    def test_dispatch_failed_suppressed(self) -> None:
        # Even meaningful default-visible events get hidden — the operator
        # filter is intentionally narrow to the wedge set.
        self.assertIsNone(project_event(
            _evt(st.EVENT_DISPATCH_FAILED, phase="p", reason="oops"),
            "my-plan", operator=True,
        ))


class OperatorFilterVsOtherFlagsTest(unittest.TestCase):
    """Operator mode interacts cleanly with verbose."""

    def test_operator_does_not_add_unrelated_verbose_events(self) -> None:
        # lease_expired is in _VERBOSE_ONLY but NOT operator-visible.
        # Even with operator=True AND verbose=True, it stays hidden because
        # operator filter takes precedence.
        self.assertIsNone(project_event(
            _evt(st.EVENT_LEASE_EXPIRED, phase="p"),
            "my-plan", operator=True, verbose=True,
        ))

    def test_verbose_alone_does_not_unlock_operator_set(self) -> None:
        # phase_started without operator filter is visible (default-visible).
        # Sanity check: verbose mode keeps normal behavior when operator=False.
        out = project_event(
            _evt(st.EVENT_PHASE_STARTED, phase="p", attempts=1),
            "my-plan", verbose=True,
        )
        self.assertIsNotNone(out)


# ---- CLI surface -----------------------------------------------------------

_PLAN_BODY = """\
# placeholder
## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `a.md` | thing | 1h |
"""


def _init_plan(project: Path, slug: str) -> None:
    plans = project / "plans"
    plans.mkdir(exist_ok=True)
    (plans / f"{slug}.md").write_text(_PLAN_BODY)
    rc = main(["init", "--project", str(project), "--plan", slug])
    assert rc == 0


class OperatorCliFlagTest(CluTestCase):
    """--operator flag is accepted by the watch subparser and threaded
    through to stream_loop."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "project"
        self.project.mkdir()

    def test_operator_flag_passes_to_stream_loop(self) -> None:
        _init_plan(self.project, "myplan")
        with mock.patch("end_of_line.watch.stream_loop", return_value=0) as m:
            rc = main(["watch", "--project", str(self.project),
                       "--plan", "myplan", "--operator"])
        self.assertEqual(rc, 0)
        m.assert_called_once()
        kwargs = m.call_args.kwargs
        self.assertTrue(kwargs.get("operator"))

    def test_operator_flag_composes_with_all(self) -> None:
        _init_plan(self.project, "myplan")
        with mock.patch("end_of_line.watch.stream_loop", return_value=0) as m:
            rc = main(["watch", "--all", "--operator"])
        self.assertEqual(rc, 0)
        self.assertTrue(m.call_args.kwargs.get("operator"))

    def test_operator_flag_composes_with_json(self) -> None:
        _init_plan(self.project, "myplan")
        with mock.patch("end_of_line.watch.stream_loop", return_value=0) as m:
            rc = main(["watch", "--project", str(self.project),
                       "--plan", "myplan", "--operator", "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(m.call_args.kwargs.get("operator"))
        self.assertTrue(m.call_args.kwargs.get("json_mode"))

    def test_default_operator_kwarg_is_false(self) -> None:
        _init_plan(self.project, "myplan")
        with mock.patch("end_of_line.watch.stream_loop", return_value=0) as m:
            rc = main(["watch", "--project", str(self.project),
                       "--plan", "myplan"])
        self.assertEqual(rc, 0)
        self.assertFalse(m.call_args.kwargs.get("operator", False))


if __name__ == "__main__":
    unittest.main()
