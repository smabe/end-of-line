"""Quota notification surface (#94, phase notify-docs).

The quota machinery's operator-facing layer: three KINDs, their renders,
and the parseability-keyed kind selector the three death sites + the gate
resume share. Quiet-hours placement is contract — STUCK bypasses (a
frozen fleet with no horizon is halt-equivalent), PAUSED/RESUMED defer
(auto-resume means no overnight action is needed).
"""

from __future__ import annotations

import datetime as dt
import unittest

from end_of_line import notify

PLAN = "quota-plan"
LINE = "You've hit your session limit · resets 1:50am (America/New_York)"
QUOTA_FILE = "/proj/plans/.orchestrator/quota.json"
PAUSED_UNTIL = dt.datetime(2026, 6, 12, 5, 52, tzinfo=dt.UTC)


def _local_resume(paused_until: dt.datetime) -> str:
    """Mirror render_quota_paused's local-time formatting for assertions."""
    local = paused_until.astimezone()
    return local.strftime("%I:%M%p").lstrip("0").lower()


class RenderQuotaTests(unittest.TestCase):
    def test_paused_includes_plan_line_and_resume_time(self) -> None:
        body = notify.render_quota_paused(PLAN, LINE, PAUSED_UNTIL)
        self.assertIn(PLAN, body)
        self.assertIn(LINE, body)
        self.assertIn(_local_resume(PAUSED_UNTIL), body)

    def test_stuck_includes_plan_and_escape_hatch(self) -> None:
        body = notify.render_quota_stuck(PLAN, LINE, QUOTA_FILE)
        self.assertIn(PLAN, body)
        self.assertIn(LINE, body)
        # The operator's one-line recovery: delete the pause file.
        self.assertIn(QUOTA_FILE, body)
        self.assertIn("rm", body)

    def test_resumed_includes_plan(self) -> None:
        body = notify.render_quota_resumed(PLAN)
        self.assertIn(PLAN, body)


class QuotaPauseNotificationTests(unittest.TestCase):
    """The single source of truth for paused-vs-stuck → KIND, shared by
    both supervisor death sites and the dispatch fast-fail."""

    def test_parseable_reset_routes_to_paused(self) -> None:
        kind, body = notify.quota_pause_notification(
            PLAN, LINE, PAUSED_UNTIL, QUOTA_FILE
        )
        self.assertEqual(kind, notify.KIND_QUOTA_PAUSED)
        self.assertIn(PLAN, body)
        self.assertIn(_local_resume(PAUSED_UNTIL), body)

    def test_unparseable_reset_routes_to_stuck(self) -> None:
        kind, body = notify.quota_pause_notification(PLAN, LINE, None, QUOTA_FILE)
        self.assertEqual(kind, notify.KIND_QUOTA_STUCK)
        self.assertIn(QUOTA_FILE, body)


class QuotaQuietHoursPlacementTests(unittest.TestCase):
    def test_stuck_bypasses_quiet_hours(self) -> None:
        self.assertIn(notify.KIND_QUOTA_STUCK, notify.QUIET_HOURS_BYPASS_KINDS)

    def test_paused_and_resumed_defer_in_quiet_hours(self) -> None:
        self.assertNotIn(notify.KIND_QUOTA_PAUSED, notify.QUIET_HOURS_BYPASS_KINDS)
        self.assertNotIn(notify.KIND_QUOTA_RESUMED, notify.QUIET_HOURS_BYPASS_KINDS)


if __name__ == "__main__":
    unittest.main()
