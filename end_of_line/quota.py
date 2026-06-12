"""Quota-message classification + reset-time parsing (#94).

Workers killed by the operator's subscription session limit print a
recognizable line ("You've hit your session limit · resets 1:50am
(America/New_York)") and exit. Classification feeds attempt forgiveness
and the project-level quota pause: a classified death never burns a
`phase_started` attempt, and a parseable reset time schedules an
auto-resume. A quota match whose reset time does NOT parse routes to
the stuck-pause bucket — no auto-resume, loud notify — so the parser
deliberately returns None for anything it can't read confidently
(weekly `resets Mon 12:00am`, date forms, future wordings).

Pure functions, stdlib-only. The signature table mirrors the systemic
table in dispatch.py: hard-coded, grows via PR only, first match wins.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Pause-file plumbing shared with later phases (P2 writes the pause,
# P3 gates dispatch on it). Defined here so the schema constants have
# one home from day one.
PAUSE_BUFFER_SEC = 120  # paused_until = reset + buffer; absorbs clock skew
CANARY_WINDOW_SEC = 180  # canary plan must survive this long post-resume
QUOTA_FILE_NAME = "quota.json"  # lives in plans/.orchestrator/

# Hard-coded signature list. Grows via PR only; no config field. Order
# matters — first match wins. The apostrophe class covers ASCII ',
# typographic U+2019, and U+FFFD (the log is read with errors="replace"
# upstream, so a mangled byte becomes the replacement char). Model names
# are enumerated, not wildcarded, so "You've hit your rate limit" stays
# with the systemic table in dispatch.py.
_APOS = "['’�]"
_QUOTA_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("session_limit", re.compile(rf"you{_APOS}ve hit your session limit", re.IGNORECASE)),
    ("weekly_limit", re.compile(rf"you{_APOS}ve hit your weekly limit", re.IGNORECASE)),
    ("model_limit", re.compile(rf"you{_APOS}ve hit your (?:opus|sonnet|haiku) limit", re.IGNORECASE)),
    ("usage_credits", re.compile(rf"you{_APOS}re out of usage credits", re.IGNORECASE)),
    (
        "extra_usage",
        re.compile(
            rf"(?:you{_APOS}ve used.*extra usage|you{_APOS}re out of extra usage)",
            re.IGNORECASE,
        ),
    ),
)

# The reset fragment: `resets <time> [(IANA-tz)]`. Time is 12h with
# optional minutes (`1:50am`, `12pm`) or 24h (`22:30`); strptime does
# the real validation, this just carves out the token. Forms where a
# word follows `resets` (weekly `Mon 12:00am`, date `Oct 31, 9am`)
# don't match at all — that's the stuck bucket, by design.
_RESET_RE = re.compile(
    r"resets\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:[ap]m)?)\s*(?:\((?P<tz>[^)]+)\))?",
    re.IGNORECASE,
)

# strptime attempts, in order: 12h with minutes, 12h bare hour, 24h.
_TIME_FORMATS = ("%I:%M%p", "%I%p", "%H:%M")


class QuotaMatch(NamedTuple):
    signature: str
    line: str  # the matched line, for events/notify bodies


def classify_quota(tail: str) -> QuotaMatch | None:
    """Return the first quota signature matching a line of `tail`, or None.

    Callers pass the worker-log tail (same 50-line discipline as the
    systemic matcher). Table order is the priority order.
    """
    lines = tail.splitlines()
    for name, pattern in _QUOTA_SIGNATURES:
        for line in lines:
            if pattern.search(line):
                return QuotaMatch(name, line.strip())
    return None


def parse_reset(line: str, now: dt.datetime) -> dt.datetime | None:
    """Parse the `resets <time> [(tz)]` fragment of `line` into aware UTC.

    `now` must be an aware datetime; the reset is the next occurrence of
    the parsed wall-clock time (candidate <= now rolls to tomorrow). No
    timezone parens → system local. Returns None for anything that
    doesn't parse cleanly — unknown tz, weekly/date forms, no fragment —
    which callers treat as the stuck bucket. Default fold handling: a
    reset inside a DST fold can be off by an hour twice a year, accepted
    for a multi-hour pause window.
    """
    frag = _RESET_RE.search(line)
    if frag is None:
        return None
    token = re.sub(r"\s+", "", frag.group("time"))
    parsed = None
    for fmt in _TIME_FORMATS:
        try:
            parsed = dt.datetime.strptime(token, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    tz_name = frag.group("tz")
    tz = None  # astimezone(None) == system local
    if tz_name is not None:
        try:
            tz = ZoneInfo(tz_name.strip())
        except (ZoneInfoNotFoundError, ValueError):
            return None
    local_now = now.astimezone(tz)
    candidate = local_now.replace(
        hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    return candidate.astimezone(dt.UTC)
