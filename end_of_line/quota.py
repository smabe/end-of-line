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

Stdlib-only. The signature table mirrors the systemic table in
dispatch.py: hard-coded, grows via PR only, first match wins. Besides
the pure matcher/parser, this module owns the quota.json pause file
(`record_quota_pause`) and the shared death recorder all three
worker-death sites call (`record_quota_death`).
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from collections import deque
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import state as st

# Pause-file plumbing shared with later phases (P2 writes the pause,
# P3 gates dispatch on it). Defined here so the schema constants have
# one home from day one.
PAUSE_BUFFER_SEC = 120  # paused_until = reset + buffer; absorbs clock skew
CANARY_WINDOW_SEC = 180  # canary plan must survive this long post-resume
QUOTA_FILE_NAME = "quota.json"  # lives in plans/.orchestrator/
QUOTA_SCHEMA_VERSION = 1

# Worker-log tail discipline shared with the systemic matcher: a 50k-line
# stack trace shouldn't slow the supervisor, and the relevant signal is
# always at the end (the death was just observed).
LOG_TAIL_LINES = 50

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


def read_log_tail(log_path: Path, lines: int = LOG_TAIL_LINES) -> str:
    """Last `lines` lines of a worker log; "" when missing or unreadable.

    Streams via a bounded deque so a multi-MB worker log never sits in
    memory whole.
    """
    try:
        with open(log_path, errors="replace") as fh:
            return "".join(deque(fh, maxlen=lines))
    except OSError:
        return ""


def classify_log_tail(log_path: str | Path | None) -> QuotaMatch | None:
    """`classify_quota` over a log file's tail; None-safe on a missing path.

    Callers pass `claim["log_path"]`, which is absent in the
    Popen→stamp-pid race window — that's the None case.
    """
    if not log_path:
        return None
    return classify_quota(read_log_tail(Path(log_path)))


def _iso_or_none(ts: dt.datetime | None) -> str | None:
    return None if ts is None else ts.astimezone(dt.UTC).strftime(st._ISO_FMT)


def record_quota_pause(
    orchestrator_dir: Path,
    match: QuotaMatch,
    now: dt.datetime,
) -> dt.datetime | None:
    """Write the project-level pause file; return paused_until (None = stuck).

    `paused_until` = parsed reset + PAUSE_BUFFER_SEC. An unparseable reset
    writes a stuck pause (`paused_until: null`): no auto-resume, only the
    operator clears it (delete quota.json). Writing always resets the
    canary fields — a re-pause during a canary window is exactly the
    canary-failed case.
    """
    reset = parse_reset(match.line, now)
    paused_until = None if reset is None else reset + dt.timedelta(seconds=PAUSE_BUFFER_SEC)
    with st.locked_json(
        orchestrator_dir / QUOTA_FILE_NAME,
        expected_version=QUOTA_SCHEMA_VERSION,
        empty=lambda: {"schema_version": QUOTA_SCHEMA_VERSION},
    ) as data:
        data.clear()
        data.update(
            {
                "schema_version": QUOTA_SCHEMA_VERSION,
                "paused_until": _iso_or_none(paused_until),
                "signature": match.signature,
                "line": match.line,
                "canary_plan": None,
                "canary_deadline": None,
                "created_at": _iso_or_none(now),
            }
        )
    return paused_until


class GateDecision(NamedTuple):
    """Outcome of consulting the project quota pause before a dispatch.

    `dispatch=False` → the supervisor returns `TickResult("idle", detail)`
    without claiming. `dispatch=True` → proceed to `claim_phase`;
    `resumed=True` additionally means the canary survived its window and
    the supervisor must append `EVENT_QUOTA_RESUMED` in its open state
    mutation window.
    """

    dispatch: bool
    detail: str = ""
    resumed: bool = False


_DISPATCH = GateDecision(dispatch=True)


def gate_decision(
    orchestrator_dir: Path,
    plan_slug: str,
    now: dt.datetime,
) -> GateDecision:
    """Decide whether `plan_slug` may dispatch given the project quota pause.

    File-absent is the hot path: one `Path.exists()` and no lock when
    nothing is paused (the overwhelmingly common tick). When the pause
    file is present, a single `locked` window reads it and resolves one
    of four outcomes (see plans/quota-pause.md "Phase 3"):

    1. `paused_until` set, `now` < it → idle.
    2. `now` >= `paused_until`, no canary stamped → this plan stamps
       itself the canary (deadline `now` + CANARY_WINDOW_SEC) and
       dispatches as the survival probe.
    3. A canary is stamped and `now` < its deadline → idle if it's
       another plan; dispatch if it's this plan (a non-quota fast-fail
       re-reaching the gate must retry, not idle against itself).
    4. `now` >= the canary deadline → the canary survived (no re-pause
       overwrote the file), so clear the pause (unlink, keeping
       "file absent == not paused" the single invariant) and resume.

    A stuck pause (`paused_until: null`) always idles — only operator file
    removal clears it. A corrupt/unreadable file is treated as absent (a
    malformed file must not freeze the fleet) with a stderr note.
    """
    quota_path = orchestrator_dir / QUOTA_FILE_NAME
    if not quota_path.exists():
        return _DISPATCH
    with st.locked(quota_path):
        # Read + decode the file's fields inside one guard: field-level
        # corruption (a hand-edited timestamp, an unpaired canary_deadline)
        # must degrade to "dispatch", same as JSON/schema corruption — the
        # contract is that no malformed file ever freezes the fleet. The
        # decision branches below stay outside the guard so a genuine logic
        # error isn't silently swallowed as "unreadable".
        try:
            data = st.load(quota_path, expected_version=QUOTA_SCHEMA_VERSION)
            paused_until_s = data.get("paused_until")
            paused_until = (
                None if paused_until_s is None else st.parse_iso(paused_until_s)
            )
            canary_deadline_s = data.get("canary_deadline")
            canary_deadline = (
                None if canary_deadline_s is None else st.parse_iso(canary_deadline_s)
            )
        except FileNotFoundError:
            # Benign race: a concurrent resume tick unlinked the file in the
            # window between exists() and the lock. "file absent == not
            # paused" — dispatch, and stay silent (not a corruption note).
            return _DISPATCH
        except (OSError, ValueError, TypeError, AttributeError, st.SchemaVersionMismatch):
            print(f"clu: ignoring unreadable {quota_path}", file=sys.stderr)
            return _DISPATCH
        if paused_until is None:
            return GateDecision(dispatch=False, detail="quota_stuck")
        if now < paused_until:
            return GateDecision(
                dispatch=False, detail=f"quota_paused until={paused_until_s}"
            )
        # now >= paused_until — the resume phase.
        canary_plan = data.get("canary_plan")
        if canary_plan is None:
            deadline = now + dt.timedelta(seconds=CANARY_WINDOW_SEC)
            data["canary_plan"] = plan_slug
            data["canary_deadline"] = _iso_or_none(deadline)
            st.save_atomic(quota_path, data)
            return _DISPATCH
        # A canary with no deadline is malformed; resuming clears the bad
        # state rather than freezing — self-healing past the same invariant.
        if canary_deadline is None or now >= canary_deadline:
            quota_path.unlink(missing_ok=True)
            return GateDecision(dispatch=True, resumed=True)
        if canary_plan == plan_slug:
            return _DISPATCH
        return GateDecision(dispatch=False, detail=f"quota_canary plan={canary_plan}")


def record_quota_death(
    data: dict,
    match: QuotaMatch,
    *,
    phase_id: str,
    token: str | None,
    orchestrator_dir: Path,
) -> dt.datetime | None:
    """Record a classified quota death: pause file + the two plan events.

    Shared by all three death sites (supervisor dead-PID, supervisor
    lease-expiry, dispatch fast-fail). The `phase`/`token` kwargs on
    EVENT_QUOTA_DEATH are the forgiveness contract —
    `state.attempts_for_phase` subtracts the matching phase_started.
    """
    paused_until = record_quota_pause(orchestrator_dir, match, st._now_utc())
    st.append_event(
        data,
        st.EVENT_QUOTA_DEATH,
        phase=phase_id,
        token=token,
        signature=match.signature,
        line=match.line,
    )
    st.append_event(
        data,
        st.EVENT_QUOTA_PAUSED,
        paused_until=_iso_or_none(paused_until),
        signature=match.signature,
    )
    return paused_until
