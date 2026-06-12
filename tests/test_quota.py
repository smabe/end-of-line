"""Quota-message classification + reset-time parsing (#94, phase matcher).

`end_of_line.quota` is pure functions, no call sites yet: later phases
wire `classify_quota` into the three death paths and `parse_reset` into
the pause writer. Bucketing is by parseability — a quota match whose
reset time doesn't parse is the "stuck pause" bucket, so `parse_reset`
returning None on weekly/date forms is contract, not a gap.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from end_of_line import quota
from end_of_line import state as st
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


class ReadLogTailTests(unittest.TestCase):
    """One shared tail helper for every death-classification site — the
    systemic matcher reads through it too (50-line discipline)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "worker.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(quota.read_log_tail(self.log), "")

    def test_returns_only_the_tail(self) -> None:
        self.log.write_text("head\n" * 100 + "tail-marker\n")
        tail = quota.read_log_tail(self.log)
        self.assertIn("tail-marker", tail)
        self.assertEqual(len(tail.splitlines()), 50)


class QuotaPauseFileTests(unittest.TestCase):
    """`record_quota_pause` owns the quota.json schema — single writer,
    under locked_json, always clearing the canary slot."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.orch_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read(self) -> dict:
        return json.loads((self.orch_dir / quota.QUOTA_FILE_NAME).read_text())

    def test_parseable_reset_writes_auto_resume_pause(self) -> None:
        now = dt.datetime(2026, 6, 11, 23, 0, tzinfo=NY)
        match = must(quota.classify_quota(SESSION_LINE))
        paused_until = must(quota.record_quota_pause(self.orch_dir, match, now))
        # reset = 2026-06-12T05:50Z (1:50am EDT), +120s buffer.
        self.assertEqual(paused_until, dt.datetime(2026, 6, 12, 5, 52, tzinfo=dt.UTC))
        data = self._read()
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["signature"], "session_limit")
        self.assertEqual(data["line"], SESSION_LINE)
        self.assertEqual(st.parse_iso(data["paused_until"]), paused_until)
        self.assertIsNone(data["canary_plan"])
        self.assertIsNone(data["canary_deadline"])
        self.assertEqual(st.parse_iso(data["created_at"]), now.astimezone(dt.UTC))

    def test_unparseable_reset_writes_stuck_pause(self) -> None:
        # Weekly form doesn't parse (locked decision) → stuck pause:
        # paused_until null, no auto-resume, operator clears it.
        match = must(quota.classify_quota("You've hit your weekly limit · resets Mon 12:00am"))
        now = dt.datetime(2026, 6, 12, 3, 0, tzinfo=dt.UTC)
        self.assertIsNone(quota.record_quota_pause(self.orch_dir, match, now))
        data = self._read()
        self.assertIsNone(data["paused_until"])
        self.assertEqual(data["signature"], "weekly_limit")

    def test_re_pause_clears_canary_fields(self) -> None:
        # A re-pause during a canary window is exactly the canary-failed
        # case — the fresh write must clear the canary slot.
        (self.orch_dir / quota.QUOTA_FILE_NAME).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "paused_until": "2026-06-12T05:52:00Z",
                    "signature": "session_limit",
                    "line": SESSION_LINE,
                    "canary_plan": "some-plan",
                    "canary_deadline": "2026-06-12T05:55:00Z",
                    "created_at": "2026-06-12T03:00:00Z",
                }
            )
        )
        match = must(quota.classify_quota(CREDITS_LINE))
        quota.record_quota_pause(self.orch_dir, match, dt.datetime(2026, 6, 12, 9, 0, tzinfo=NY))
        data = self._read()
        self.assertIsNone(data["canary_plan"])
        self.assertIsNone(data["canary_deadline"])
        self.assertEqual(data["signature"], "usage_credits")


class GateDecisionTests(unittest.TestCase):
    """The dispatch gate state machine (#94, phase gate). Four outcomes
    decided under one lock: idle while paused, the first plan past reset
    becomes the canary and dispatches, others idle during its window, and
    the fleet resumes (file cleared) when the canary survives."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.orch_dir = Path(self._tmp.name)
        self.quota_path = self.orch_dir / quota.QUOTA_FILE_NAME

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, **over: object) -> None:
        base = {
            "schema_version": 1,
            "paused_until": "2026-06-12T05:52:00Z",
            "signature": "session_limit",
            "line": SESSION_LINE,
            "canary_plan": None,
            "canary_deadline": None,
            "created_at": "2026-06-12T03:00:00Z",
        }
        base.update(over)
        self.quota_path.write_text(json.dumps(base))

    def _read(self) -> dict:
        return json.loads(self.quota_path.read_text())

    def test_no_file_dispatches(self) -> None:
        # Hot path: no quota.json → dispatch, no lock taken.
        d = quota.gate_decision(
            self.orch_dir, "plan-a", dt.datetime(2026, 6, 12, 6, 0, tzinfo=dt.UTC)
        )
        self.assertTrue(d.dispatch)
        self.assertFalse(d.resumed)

    def test_active_pause_idles(self) -> None:
        self._write(paused_until="2026-06-12T05:52:00Z")
        now = dt.datetime(2026, 6, 12, 5, 0, tzinfo=dt.UTC)  # before reset
        d = quota.gate_decision(self.orch_dir, "plan-a", now)
        self.assertFalse(d.dispatch)
        self.assertIn("quota_paused", d.detail)
        self.assertIn("05:52", d.detail)
        # Idle must not mutate the file.
        self.assertIsNone(self._read()["canary_plan"])

    def test_stuck_pause_idles_indefinitely(self) -> None:
        self._write(paused_until=None)
        now = dt.datetime(2026, 6, 20, 0, 0, tzinfo=dt.UTC)  # days later
        d = quota.gate_decision(self.orch_dir, "plan-a", now)
        self.assertFalse(d.dispatch)
        self.assertEqual(d.detail, "quota_stuck")

    def test_past_reset_no_canary_stamps_and_dispatches(self) -> None:
        self._write(paused_until="2026-06-12T05:52:00Z")
        now = dt.datetime(2026, 6, 12, 6, 0, tzinfo=dt.UTC)  # past reset
        d = quota.gate_decision(self.orch_dir, "plan-a", now)
        self.assertTrue(d.dispatch)
        self.assertFalse(d.resumed)
        data = self._read()
        self.assertEqual(data["canary_plan"], "plan-a")
        self.assertEqual(
            st.parse_iso(data["canary_deadline"]),
            now + dt.timedelta(seconds=quota.CANARY_WINDOW_SEC),
        )

    def test_second_plan_idles_during_canary_window(self) -> None:
        self._write(
            paused_until="2026-06-12T05:52:00Z",
            canary_plan="plan-a",
            canary_deadline="2026-06-12T06:03:00Z",
        )
        now = dt.datetime(2026, 6, 12, 6, 1, tzinfo=dt.UTC)  # within window
        d = quota.gate_decision(self.orch_dir, "plan-b", now)
        self.assertFalse(d.dispatch)
        self.assertIn("canary", d.detail)

    def test_canary_plan_redispatches_in_window(self) -> None:
        # The canary itself re-reaching the gate before its deadline (a
        # non-quota fast-fail) must dispatch again, not idle against itself.
        self._write(
            paused_until="2026-06-12T05:52:00Z",
            canary_plan="plan-a",
            canary_deadline="2026-06-12T06:03:00Z",
        )
        now = dt.datetime(2026, 6, 12, 6, 1, tzinfo=dt.UTC)
        d = quota.gate_decision(self.orch_dir, "plan-a", now)
        self.assertTrue(d.dispatch)
        self.assertFalse(d.resumed)

    def test_past_deadline_clears_file_and_resumes(self) -> None:
        self._write(
            paused_until="2026-06-12T05:52:00Z",
            canary_plan="plan-a",
            canary_deadline="2026-06-12T06:03:00Z",
        )
        now = dt.datetime(2026, 6, 12, 6, 5, tzinfo=dt.UTC)  # past deadline
        d = quota.gate_decision(self.orch_dir, "plan-b", now)
        self.assertTrue(d.dispatch)
        self.assertTrue(d.resumed)
        self.assertFalse(self.quota_path.exists())  # file == "not paused"

    def test_two_plans_race_only_first_stamps_canary(self) -> None:
        # Sequential gate calls model two plans ticking the same cron pass:
        # the locked window makes stamping atomic — the second reader sees
        # the first plan's stamp and idles.
        self._write(paused_until="2026-06-12T05:52:00Z")
        now = dt.datetime(2026, 6, 12, 6, 0, tzinfo=dt.UTC)
        first = quota.gate_decision(self.orch_dir, "plan-a", now)
        second = quota.gate_decision(self.orch_dir, "plan-b", now)
        self.assertTrue(first.dispatch)
        self.assertFalse(second.dispatch)
        self.assertEqual(self._read()["canary_plan"], "plan-a")

    def test_re_pause_gates_against_new_paused_until(self) -> None:
        # The canary died from quota → phase-classify overwrote the file with
        # a fresh paused_until and cleared the canary. Other plans now gate
        # against the NEW horizon — no resume.
        self._write(
            paused_until="2026-06-12T07:52:00Z",
            canary_plan=None,
            canary_deadline=None,
        )
        now = dt.datetime(2026, 6, 12, 6, 5, tzinfo=dt.UTC)  # before new reset
        d = quota.gate_decision(self.orch_dir, "plan-b", now)
        self.assertFalse(d.dispatch)
        self.assertFalse(d.resumed)
        self.assertIn("quota_paused", d.detail)

    def test_corrupt_file_treated_as_absent(self) -> None:
        self.quota_path.write_text("{not valid json")
        now = dt.datetime(2026, 6, 12, 6, 0, tzinfo=dt.UTC)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            d = quota.gate_decision(self.orch_dir, "plan-a", now)
        self.assertTrue(d.dispatch)
        self.assertFalse(d.resumed)
        self.assertIn(quota.QUOTA_FILE_NAME, err.getvalue())

    def test_malformed_paused_until_dispatches_not_freezes(self) -> None:
        # Valid JSON, garbage timestamp field: the "malformed file must
        # not freeze the fleet" contract covers field-level corruption too,
        # not only unparseable JSON. A hand-edited paused_until must not
        # crash every tick.
        self._write(paused_until="not-a-timestamp")
        now = dt.datetime(2026, 6, 12, 6, 0, tzinfo=dt.UTC)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            d = quota.gate_decision(self.orch_dir, "plan-a", now)
        self.assertTrue(d.dispatch)
        self.assertIn(quota.QUOTA_FILE_NAME, err.getvalue())

    def test_canary_without_deadline_resumes(self) -> None:
        # canary_plan set but canary_deadline null (malformed pairing) must
        # self-heal — clear the file and resume rather than raise.
        self._write(
            paused_until="2026-06-12T05:52:00Z",
            canary_plan="plan-a",
            canary_deadline=None,
        )
        now = dt.datetime(2026, 6, 12, 6, 0, tzinfo=dt.UTC)
        d = quota.gate_decision(self.orch_dir, "plan-b", now)
        self.assertTrue(d.dispatch)
        self.assertTrue(d.resumed)
        self.assertFalse(self.quota_path.exists())

    def test_concurrent_resume_unlink_is_silent(self) -> None:
        # Benign race: another tick process unlinked the file in the window
        # between exists() and the lock. load → FileNotFoundError must NOT
        # log "ignoring unreadable" (that wording implies corruption).
        self._write(paused_until="2026-06-12T05:52:00Z")
        now = dt.datetime(2026, 6, 12, 6, 0, tzinfo=dt.UTC)
        with mock.patch(
            "end_of_line.state.load", side_effect=FileNotFoundError()
        ):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                d = quota.gate_decision(self.orch_dir, "plan-a", now)
        self.assertTrue(d.dispatch)
        self.assertFalse(d.resumed)
        self.assertEqual(err.getvalue(), "")


class ConstantsTests(unittest.TestCase):
    """P2/P3 import these from quota.py; pin them so a rename or value
    drift breaks loudly here instead of silently in a later phase."""

    def test_pause_constants(self) -> None:
        self.assertEqual(quota.PAUSE_BUFFER_SEC, 120)
        self.assertEqual(quota.CANARY_WINDOW_SEC, 180)
        self.assertEqual(quota.QUOTA_FILE_NAME, "quota.json")


if __name__ == "__main__":
    unittest.main()
