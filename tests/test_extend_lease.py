"""Operator command: `clu extend-lease` bumps a live claim's lease_expires (#29)."""
from __future__ import annotations

import datetime as _dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
"""

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _stamp_claim(
    data: dict,
    *,
    phase: str = "A",
    token: str = "session-abc",
    lease_expires: _dt.datetime | None = None,
) -> None:
    now = _dt.datetime.now(_dt.timezone.utc)
    if lease_expires is None:
        lease_expires = now + _dt.timedelta(minutes=30)
    started = now.strftime(_FMT)
    data["current_claim"] = {
        "phase_id": phase,
        "claimed_by": token,
        "lease_expires": lease_expires.strftime(_FMT),
        "started_at": started,
        "last_heartbeat_at": started,
        "attempts": 1,
    }
    data["status"] = st.STATUS_RUNNING


class ExtendLeaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        self.assertEqual(
            main(["init", "--project", str(self.project), "--plan", "test-plan"]),
            0,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def _write(self, mut) -> None:
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            mut(data)
            st.save_atomic(self.state_path, data)

    def _argv(self, minutes: str, *extra: str) -> list[str]:
        return [
            "extend-lease",
            "--project", str(self.project),
            "--plan", "test-plan",
            minutes,
            *extra,
        ]

    def _lease_extended_events(self) -> list[dict]:
        return [
            e for e in self._read()["events"]
            if e["type"] == st.EVENT_LEASE_EXTENDED
        ]

    # ---- happy path -----------------------------------------------------------

    def test_extend_lease_happy_path_bumps_lease_expires(self) -> None:
        now = _dt.datetime.now(_dt.timezone.utc)
        original_expires = now + _dt.timedelta(minutes=30)
        self._write(lambda d: _stamp_claim(d, lease_expires=original_expires))

        rc = main(self._argv("60"))
        self.assertEqual(rc, 0)

        claim = self._read()["current_claim"]
        new_expires = _dt.datetime.strptime(claim["lease_expires"], _FMT).replace(
            tzinfo=_dt.timezone.utc
        )
        # new_expires should be ~90 min from now (30 remaining + 60 added)
        expected = original_expires + _dt.timedelta(minutes=60)
        diff = abs((new_expires - expected).total_seconds())
        self.assertLess(diff, 5, f"lease_expires off by {diff}s")

    def test_extend_lease_happy_path_appends_event(self) -> None:
        now = _dt.datetime.now(_dt.timezone.utc)
        self._write(lambda d: _stamp_claim(
            d, phase="A", lease_expires=now + _dt.timedelta(minutes=30),
        ))

        rc = main(self._argv("60"))
        self.assertEqual(rc, 0)

        evts = self._lease_extended_events()
        self.assertEqual(len(evts), 1)
        evt = evts[0]
        self.assertEqual(evt["phase"], "A")
        self.assertEqual(evt["extended_by_minutes"], 60)
        self.assertTrue(evt["operator"])
        self.assertIn("new_expires", evt)

    def test_extend_lease_happy_path_event_new_expires_matches_claim(self) -> None:
        now = _dt.datetime.now(_dt.timezone.utc)
        self._write(lambda d: _stamp_claim(
            d, lease_expires=now + _dt.timedelta(minutes=30),
        ))
        main(self._argv("45"))
        data = self._read()
        evt = self._lease_extended_events()[0]
        self.assertEqual(evt["new_expires"], data["current_claim"]["lease_expires"])

    # ---- refuse: no current_claim ---------------------------------------------

    def test_refuses_when_no_claim_exit_code(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("30"))
        self.assertNotEqual(rc, 0)
        self.assertIn("no claim", buf.getvalue())

    def test_refuses_when_no_claim_does_not_append_event(self) -> None:
        with redirect_stderr(io.StringIO()):
            main(self._argv("30"))
        self.assertEqual(self._lease_extended_events(), [])

    # ---- refuse: invalid minutes ----------------------------------------------

    def test_refuses_on_zero_minutes(self) -> None:
        now = _dt.datetime.now(_dt.timezone.utc)
        self._write(lambda d: _stamp_claim(
            d, lease_expires=now + _dt.timedelta(minutes=30),
        ))
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("0"))
        self.assertEqual(rc, ExitCode.INVALID_VALUE)

    def test_refuses_on_negative_minutes(self) -> None:
        now = _dt.datetime.now(_dt.timezone.utc)
        self._write(lambda d: _stamp_claim(
            d, lease_expires=now + _dt.timedelta(minutes=30),
        ))
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("-10"))
        self.assertEqual(rc, ExitCode.INVALID_VALUE)

    # ---- past-lease claim (stalled) -------------------------------------------

    def test_extend_from_past_lease_uses_now_as_baseline(self) -> None:
        # lease_expires is already in the past (stalled worker)
        past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)
        self._write(lambda d: _stamp_claim(d, lease_expires=past))

        before = _dt.datetime.now(_dt.timezone.utc)
        rc = main(self._argv("30"))
        after = _dt.datetime.now(_dt.timezone.utc)
        self.assertEqual(rc, 0)

        claim = self._read()["current_claim"]
        new_expires = _dt.datetime.strptime(claim["lease_expires"], _FMT).replace(
            tzinfo=_dt.timezone.utc
        )
        # Must be ~now+30, not past+30 (which would still be in the past).
        # Allow 1s downward slack: the ISO format truncates to whole seconds.
        lower_bound = before + _dt.timedelta(minutes=30, seconds=-1)
        upper_bound = after + _dt.timedelta(minutes=30, seconds=5)
        self.assertGreaterEqual(new_expires, lower_bound)
        self.assertLessEqual(new_expires, upper_bound)


if __name__ == "__main__":
    unittest.main()
