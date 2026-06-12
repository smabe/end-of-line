"""Quota-message classification + reset-time parsing (#94, phase matcher).

`end_of_line.quota` is pure functions, no call sites yet: later phases
wire `classify_quota` into the three death paths and `parse_reset` into
the pause writer. Bucketing is by parseability — a quota match whose
reset time doesn't parse is the "stuck pause" bucket, so `parse_reset`
returning None on weekly/date forms is contract, not a gap.
"""

from __future__ import annotations

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from end_of_line import quota
from tests import must

NY = ZoneInfo("America/New_York")
LA = ZoneInfo("America/Los_Angeles")

# The two verbatim lines from the 2026-06-11 HealthData worker logs —
# the observed ground truth this module exists to match.
SESSION_LINE = "You've hit your session limit · resets 1:50am (America/New_York)"
CREDITS_LINE = "You're out of usage credits · resets 12:30pm (America/New_York)"


class ClassifyQuotaTests(unittest.TestCase):
    """The signature table is the single source of truth for what counts
    as a quota death. Hard-coded; new signatures land via PR with a test."""

    def test_verbatim_session_line(self) -> None:
        match = must(quota.classify_quota(SESSION_LINE))
        self.assertEqual(match.signature, "session_limit")
        self.assertEqual(match.line, SESSION_LINE)

    def test_verbatim_credits_line(self) -> None:
        match = must(quota.classify_quota(CREDITS_LINE))
        self.assertEqual(match.signature, "usage_credits")
        self.assertEqual(match.line, CREDITS_LINE)

    def test_weekly_limit_variant(self) -> None:
        match = must(quota.classify_quota("You've hit your weekly limit · resets Mon 12:00am"))
        self.assertEqual(match.signature, "weekly_limit")

    def test_model_limit_variants(self) -> None:
        # Every model name the table enumerates gets a case — the table
        # only grows with a test.
        for model in ("Opus", "Sonnet", "Haiku"):
            with self.subTest(model=model):
                line = f"You've hit your {model} limit · resets 3pm"
                match = must(quota.classify_quota(line))
                self.assertEqual(match.signature, "model_limit")

    def test_used_extra_usage_prefix(self) -> None:
        match = must(quota.classify_quota("You've used all your extra usage · resets 12pm"))
        self.assertEqual(match.signature, "extra_usage")

    def test_out_of_extra_usage_prefix(self) -> None:
        match = must(quota.classify_quota("You're out of extra usage · resets 9am"))
        self.assertEqual(match.signature, "extra_usage")

    def test_typographic_apostrophe(self) -> None:
        # Claude Code sometimes emits U+2019 instead of ASCII apostrophe.
        match = must(quota.classify_quota("You’ve hit your session limit · resets 1am"))
        self.assertEqual(match.signature, "session_limit")

    def test_separator_variants(self) -> None:
        # U+00B7, U+2219, U+2022, pipe, hyphen, and U+FFFD (the log is read
        # with errors="replace" upstream, so a mangled separator byte
        # becomes the replacement char).
        for sep in ("·", "∙", "•", "|", "-", "�"):
            with self.subTest(sep=sep):
                line = f"You've hit your session limit {sep} resets 1:50am (America/New_York)"
                match = must(quota.classify_quota(line))
                self.assertEqual(match.signature, "session_limit")

    def test_signature_buried_in_multiline_tail(self) -> None:
        tail = "\n".join(
            [
                "some tool output",
                "more output",
                SESSION_LINE,
                "shutting down",
            ]
        )
        match = must(quota.classify_quota(tail))
        self.assertEqual(match.signature, "session_limit")
        self.assertEqual(match.line, SESSION_LINE)

    def test_benign_traceback_no_match(self) -> None:
        tail = (
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\n'
            "ValueError: limit exceeded\n"
        )
        self.assertIsNone(quota.classify_quota(tail))

    def test_rate_limit_stays_systemic(self) -> None:
        # The systemic table owns API rate limits (dispatch.py); the quota
        # table must not swallow them — including a hypothetical
        # "hit your rate limit" wording.
        self.assertIsNone(quota.classify_quota("anthropic.RateLimitError: too many requests"))
        self.assertIsNone(quota.classify_quota("Error: rate limit exceeded, retrying"))
        self.assertIsNone(quota.classify_quota("You've hit your rate limit · resets 1am"))

    def test_empty_string_no_match(self) -> None:
        self.assertIsNone(quota.classify_quota(""))


class ParseResetTests(unittest.TestCase):
    """`parse_reset` returns an aware-UTC datetime, or None for any form
    it can't parse confidently — None routes callers to the stuck bucket."""

    def test_verbatim_session_line_rolls_over_to_tomorrow(self) -> None:
        # 1:50am has already passed today at 23:00 ET → next occurrence
        # is tomorrow. June = EDT (UTC-4), so 1:50am ET = 05:50 UTC.
        now = dt.datetime(2026, 6, 11, 23, 0, tzinfo=NY)
        result = must(quota.parse_reset(SESSION_LINE, now))
        self.assertEqual(result, dt.datetime(2026, 6, 12, 5, 50, tzinfo=dt.UTC))

    def test_verbatim_credits_line_same_day(self) -> None:
        now = dt.datetime(2026, 6, 12, 9, 0, tzinfo=NY)
        result = must(quota.parse_reset(CREDITS_LINE, now))
        self.assertEqual(result, dt.datetime(2026, 6, 12, 16, 30, tzinfo=dt.UTC))

    def test_no_minutes_form(self) -> None:
        now = dt.datetime(2026, 6, 12, 8, 0, tzinfo=LA)
        line = "You've hit your session limit · resets 12pm (America/Los_Angeles)"
        result = must(quota.parse_reset(line, now))
        # June = PDT (UTC-7), so noon PT = 19:00 UTC.
        self.assertEqual(result, dt.datetime(2026, 6, 12, 19, 0, tzinfo=dt.UTC))

    def test_uppercase_meridiem(self) -> None:
        now = dt.datetime(2026, 6, 12, 3, 0, tzinfo=dt.UTC)
        line = "You've hit your session limit · resets 9:00AM (UTC)"
        result = must(quota.parse_reset(line, now))
        self.assertEqual(result, dt.datetime(2026, 6, 12, 9, 0, tzinfo=dt.UTC))

    def test_24h_form(self) -> None:
        now = dt.datetime(2026, 6, 12, 12, 0, tzinfo=dt.UTC)
        line = "You're out of usage credits · resets 22:30 (UTC)"
        result = must(quota.parse_reset(line, now))
        self.assertEqual(result, dt.datetime(2026, 6, 12, 22, 30, tzinfo=dt.UTC))

    def test_no_timezone_assumes_local(self) -> None:
        # Host-tz-independent assertions: the result is aware UTC, lands
        # at 9:15 on the local wall clock, and is in the future.
        now = dt.datetime(2026, 6, 12, 3, 0, tzinfo=dt.UTC)
        line = "You've hit your session limit · resets 9:15am"
        result = must(quota.parse_reset(line, now))
        self.assertEqual(result.tzinfo, dt.UTC)
        local = result.astimezone()
        self.assertEqual((local.hour, local.minute), (9, 15))
        self.assertGreater(result, now)

    def test_candidate_equal_to_now_rolls_over(self) -> None:
        # candidate <= now → +1 day; the boundary itself rolls over.
        now = dt.datetime(2026, 6, 12, 12, 30, 0, tzinfo=NY)
        result = must(quota.parse_reset(CREDITS_LINE, now))
        self.assertEqual(result, dt.datetime(2026, 6, 13, 16, 30, tzinfo=dt.UTC))

    def test_weekly_form_returns_none(self) -> None:
        # Deliberately unparsed (locked decision) — stuck bucket.
        line = "You've hit your weekly limit · resets Mon 12:00am"
        self.assertIsNone(quota.parse_reset(line, dt.datetime(2026, 6, 12, tzinfo=dt.UTC)))

    def test_date_form_returns_none(self) -> None:
        line = "You're out of usage credits · resets Oct 31, 9am"
        self.assertIsNone(quota.parse_reset(line, dt.datetime(2026, 6, 12, tzinfo=dt.UTC)))

    def test_no_resets_fragment_returns_none(self) -> None:
        line = "You've hit your session limit"
        self.assertIsNone(quota.parse_reset(line, dt.datetime(2026, 6, 12, tzinfo=dt.UTC)))

    def test_unknown_timezone_returns_none(self) -> None:
        line = "You've hit your session limit · resets 9am (Mars/Olympus_Mons)"
        self.assertIsNone(quota.parse_reset(line, dt.datetime(2026, 6, 12, tzinfo=dt.UTC)))

    def test_result_is_aware_utc(self) -> None:
        now = dt.datetime(2026, 6, 11, 23, 0, tzinfo=NY)
        result = must(quota.parse_reset(SESSION_LINE, now))
        self.assertEqual(result.utcoffset(), dt.timedelta(0))


class ConstantsTests(unittest.TestCase):
    """P2/P3 import these from quota.py; pin them so a rename or value
    drift breaks loudly here instead of silently in a later phase."""

    def test_pause_constants(self) -> None:
        self.assertEqual(quota.PAUSE_BUFFER_SEC, 120)
        self.assertEqual(quota.CANARY_WINDOW_SEC, 180)
        self.assertEqual(quota.QUOTA_FILE_NAME, "quota.json")


if __name__ == "__main__":
    unittest.main()
