"""Operator escape hatch: `clu release-claim` clears a stuck claim (closes #8).

When a worker dies without calling `clu complete` / `block` / `heartbeat`
(OOM, budget cap, user-killed, segfault), its claim sits until the
30-minute lease expires. `release-claim` is the operator's escape hatch.

Safety: refuse to clear a live claim (running plan + fresh heartbeat)
unless `--force` is passed. The new EVENT_CLAIM_FORCE_RELEASED event
distinguishes operator recovery from automatic lease expiry in the
audit log.
"""

from __future__ import annotations

import datetime as _dt
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import GitProjectTestCase, plan_body


def _stamp_claim(
    data: dict,
    *,
    phase: str = "A",
    token: str = "session-abc",
    heartbeat_age_seconds: float = 0,
    lease_minutes_remaining: int = 30,
    pid: int | None = None,
) -> None:
    """Inject a current_claim with a controllable heartbeat age."""
    now = _dt.datetime.now(_dt.UTC)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    started = (now - _dt.timedelta(seconds=heartbeat_age_seconds)).strftime(fmt)
    heartbeat = (now - _dt.timedelta(seconds=heartbeat_age_seconds)).strftime(fmt)
    expires = (now + _dt.timedelta(minutes=lease_minutes_remaining)).strftime(fmt)
    claim: dict = {
        "phase_id": phase,
        "claimed_by": token,
        "lease_expires": expires,
        "started_at": started,
        "last_heartbeat_at": heartbeat,
        "attempts": 1,
    }
    if pid is not None:
        claim["pid"] = pid
    data["current_claim"] = claim


class ReleaseClaimTestCase(GitProjectTestCase):
    PLAN_BODY = plan_body("A")
    NEEDS_GIT = False  # tests inject claims via _stamp_claim; no git ops needed

    def _write(self, mut) -> None:
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            mut(data)
            st.save_atomic(self.state_path, data)

    def _force_release_events(self) -> list[dict]:
        return [e for e in self._read()["events"] if e["type"] == st.EVENT_CLAIM_FORCE_RELEASED]

    # ---- paused-plan release (allowed, no --force needed) ---------------------

    def test_paused_plan_release_clears_claim(self) -> None:
        def setup(d: dict) -> None:
            d["status"] = st.STATUS_PAUSED
            _stamp_claim(d, heartbeat_age_seconds=10)

        self._write(setup)
        rc = main(self._argv("release-claim"))
        self.assertEqual(rc, 0)
        self.assertIsNone(self._read()["current_claim"])

    def test_paused_plan_release_appends_event(self) -> None:
        def setup(d: dict) -> None:
            d["status"] = st.STATUS_PAUSED
            _stamp_claim(d, phase="A", token="session-abc", heartbeat_age_seconds=10)

        self._write(setup)
        rc = main(self._argv("release-claim"))
        self.assertEqual(rc, 0)
        evts = self._force_release_events()
        self.assertEqual(len(evts), 1)
        self.assertEqual(evts[0]["phase"], "A")
        self.assertEqual(evts[0]["token"], "session-abc")
        self.assertFalse(evts[0]["forced"])

    def test_paused_plan_release_preserves_status(self) -> None:
        # release-claim is a recovery action, not a state transition.
        def setup(d: dict) -> None:
            d["status"] = st.STATUS_PAUSED
            _stamp_claim(d, heartbeat_age_seconds=10)

        self._write(setup)
        main(self._argv("release-claim"))
        self.assertEqual(self._read()["status"], st.STATUS_PAUSED)

    # ---- running-plan with STALE heartbeat (allowed) --------------------------

    def test_stale_heartbeat_release_clears_claim(self) -> None:
        # Threshold derives from lease TTL: max(15, lease_ttl//2).
        # Default 60-min lease → 30-min threshold; 35 min is decisively stale.
        self._write(lambda d: _stamp_claim(d, heartbeat_age_seconds=35 * 60))
        rc = main(self._argv("release-claim"))
        self.assertEqual(rc, 0)
        self.assertIsNone(self._read()["current_claim"])
        self.assertEqual(len(self._force_release_events()), 1)

    def test_stale_heartbeat_release_is_not_forced(self) -> None:
        self._write(lambda d: _stamp_claim(d, heartbeat_age_seconds=35 * 60))
        main(self._argv("release-claim"))
        evts = self._force_release_events()
        self.assertFalse(evts[0]["forced"])

    # ---- running-plan with FRESH heartbeat refused without --force ------------

    def test_fresh_heartbeat_refused_without_force(self) -> None:
        self._write(lambda d: _stamp_claim(d, heartbeat_age_seconds=30))
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("release-claim"))
        self.assertNotEqual(rc, 0)
        # No mutation: claim still there, no event.
        self.assertIsNotNone(self._read()["current_claim"])
        self.assertEqual(self._force_release_events(), [])
        self.assertIn("--force", buf.getvalue())

    def test_fresh_heartbeat_refused_exit_code(self) -> None:
        self._write(lambda d: _stamp_claim(d, heartbeat_age_seconds=30))
        with redirect_stderr(io.StringIO()):
            rc = main(self._argv("release-claim"))
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)

    # ---- --force overrides fresh heartbeat ------------------------------------

    def test_force_release_on_fresh_heartbeat_clears_claim(self) -> None:
        self._write(lambda d: _stamp_claim(d, heartbeat_age_seconds=30))
        rc = main(self._argv("release-claim", "--force"))
        self.assertEqual(rc, 0)
        self.assertIsNone(self._read()["current_claim"])

    def test_force_release_event_carries_forced_flag(self) -> None:
        self._write(lambda d: _stamp_claim(d, heartbeat_age_seconds=30))
        main(self._argv("release-claim", "--force"))
        evts = self._force_release_events()
        self.assertEqual(len(evts), 1)
        self.assertTrue(evts[0]["forced"])

    # ---- no active claim is a clean no-op (no event polluted) -----------------

    def test_no_claim_noop_exits_zero(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(self._argv("release-claim"))
        self.assertEqual(rc, 0)
        self.assertIn("no claim", buf.getvalue())

    def test_no_claim_noop_does_not_append_event(self) -> None:
        with redirect_stderr(io.StringIO()):
            main(self._argv("release-claim"))
        # Audit trail must not grow a no-op entry.
        self.assertEqual(self._force_release_events(), [])

    # ---- --reason persists in the event payload -------------------------------

    def test_reason_recorded_in_event(self) -> None:
        self._write(lambda d: _stamp_claim(d, heartbeat_age_seconds=35 * 60))
        rc = main(self._argv("release-claim", "--reason", "worker OOM'd"))
        self.assertEqual(rc, 0)
        evts = self._force_release_events()
        self.assertEqual(evts[0]["reason"], "worker OOM'd")

    def test_no_reason_omits_reason_field(self) -> None:
        # When the operator declines to explain, the event simply has no
        # `reason` key — better than a placeholder that pretends to be content.
        self._write(lambda d: _stamp_claim(d, heartbeat_age_seconds=35 * 60))
        main(self._argv("release-claim"))
        evt = self._force_release_events()[0]
        self.assertNotIn("reason", evt)

    # ---- worker callback after force-release fails cleanly --------------------

    def test_worker_complete_after_force_release_returns_claim_mismatch(self) -> None:
        # Regression: if the operator --forces while a worker is genuinely
        # alive, a subsequent `clu complete` callback must fail cleanly with
        # CLAIM_MISMATCH rather than crash on a None claim.
        self._write(
            lambda d: _stamp_claim(
                d,
                phase="A",
                token="session-abc",
                heartbeat_age_seconds=30,
            )
        )
        self.assertEqual(main(self._argv("release-claim", "--force")), 0)
        with redirect_stderr(io.StringIO()):
            rc = main(
                self._argv(
                    "complete",
                    "--token",
                    "session-abc",
                    "--phase",
                    "A",
                )
            )
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)

    # ---- --reset-attempts flag -------------------------------------------------

    def test_release_claim_reset_attempts_emits_event(self) -> None:
        # --force required to bypass fresh-heartbeat safety check.
        self._write(lambda d: _stamp_claim(d, phase="A", heartbeat_age_seconds=30))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(self._argv("release-claim", "--force", "--reset-attempts"))
        self.assertEqual(rc, 0)
        events = self._read()["events"]
        reset_evts = [e for e in events if e["type"] == st.EVENT_ATTEMPTS_RESET]
        self.assertEqual(len(reset_evts), 1)
        self.assertEqual(reset_evts[0]["phase"], "A")
        self.assertTrue(reset_evts[0]["operator"])
        self.assertIn("Attempts reset", buf.getvalue())

    def test_attempts_for_phase_zeros_after_reset_event(self) -> None:
        # STARTED, STARTED, ATTEMPTS_RESET, STARTED → attempts = 1
        data = {
            "events": [
                {"type": st.EVENT_PHASE_STARTED, "phase": "A", "claimed_by": "s1"},
                {"type": st.EVENT_PHASE_STARTED, "phase": "A", "claimed_by": "s2"},
                {"type": st.EVENT_ATTEMPTS_RESET, "phase": "A", "operator": True},
                {"type": st.EVENT_PHASE_STARTED, "phase": "A", "claimed_by": "s3"},
            ],
        }
        self.assertEqual(st.attempts_for_phase(data, "A"), 1)

    def test_attempts_for_phase_interleaved_reset_and_retry(self) -> None:
        # STARTED, RETRY_REQUESTED, STARTED, ATTEMPTS_RESET, STARTED → 1
        data = {
            "events": [
                {"type": st.EVENT_PHASE_STARTED, "phase": "A", "claimed_by": "s1"},
                {"type": st.EVENT_RETRY_REQUESTED, "phase": "A"},
                {"type": st.EVENT_PHASE_STARTED, "phase": "A", "claimed_by": "s2"},
                {"type": st.EVENT_ATTEMPTS_RESET, "phase": "A", "operator": True},
                {"type": st.EVENT_PHASE_STARTED, "phase": "A", "claimed_by": "s3"},
            ],
        }
        self.assertEqual(st.attempts_for_phase(data, "A"), 1)

    def test_release_claim_without_reset_flag_unchanged(self) -> None:
        # Regression guard: bare release-claim must not emit EVENT_ATTEMPTS_RESET.
        # Use --force to ensure the release proceeds regardless of heartbeat age.
        self._write(lambda d: _stamp_claim(d, phase="A", heartbeat_age_seconds=30))
        main(self._argv("release-claim", "--force"))
        events = self._read()["events"]
        reset_evts = [e for e in events if e["type"] == st.EVENT_ATTEMPTS_RESET]
        self.assertEqual(reset_evts, [])

    # ---- orphan reap on release (closes #63) ----------------------------------

    def _orphan_reaped_events(self) -> list[dict]:
        return [e for e in self._read()["events"] if e["type"] == st.EVENT_PHASE_ORPHAN_REAPED]

    def test_release_claim_reaps_pid_when_present(self) -> None:
        from unittest.mock import patch

        self._write(
            lambda d: _stamp_claim(
                d,
                phase="A",
                heartbeat_age_seconds=30,
                pid=99999,
            )
        )
        fake_result = st.ReapResult(
            signaled="SIGTERM",
            escalated_kill=False,
            cmdline_mismatch=False,
        )
        with patch("end_of_line.state.reap_orphan_pgroup", return_value=fake_result) as mock:
            rc = main(self._argv("release-claim", "--force"))
        self.assertEqual(rc, 0)
        mock.assert_called_once()
        args, kwargs = mock.call_args
        self.assertEqual(args[0], 99999)
        self.assertEqual(kwargs["cmdline_match"], "test-plan")

    def test_release_claim_no_pid_skips_reap(self) -> None:
        from unittest.mock import patch

        self._write(lambda d: _stamp_claim(d, heartbeat_age_seconds=30))
        with patch("end_of_line.state.reap_orphan_pgroup") as mock:
            rc = main(self._argv("release-claim", "--force"))
        self.assertEqual(rc, 0)
        mock.assert_not_called()
        self.assertEqual(self._orphan_reaped_events(), [])

    def test_release_claim_event_ordering(self) -> None:
        from unittest.mock import patch

        self._write(
            lambda d: _stamp_claim(
                d,
                heartbeat_age_seconds=30,
                pid=99999,
            )
        )
        fake_result = st.ReapResult(
            signaled="SIGTERM",
            escalated_kill=False,
            cmdline_mismatch=False,
        )
        with patch("end_of_line.state.reap_orphan_pgroup", return_value=fake_result):
            main(self._argv("release-claim", "--force"))
        events = self._read()["events"]
        types = [e["type"] for e in events]
        force_idx = types.index(st.EVENT_CLAIM_FORCE_RELEASED)
        reap_idx = types.index(st.EVENT_PHASE_ORPHAN_REAPED)
        self.assertLess(force_idx, reap_idx)

    def test_release_claim_records_signaled_in_event(self) -> None:
        from unittest.mock import patch

        self._write(
            lambda d: _stamp_claim(
                d,
                phase="A",
                heartbeat_age_seconds=30,
                pid=99999,
            )
        )
        fake_result = st.ReapResult(
            signaled="SIGTERM+SIGKILL",
            escalated_kill=True,
            cmdline_mismatch=False,
        )
        with patch("end_of_line.state.reap_orphan_pgroup", return_value=fake_result):
            main(self._argv("release-claim", "--force"))
        evt = self._orphan_reaped_events()[0]
        self.assertEqual(evt["signaled"], "SIGTERM+SIGKILL")
        self.assertEqual(evt["pid"], 99999)
        self.assertEqual(evt["phase"], "A")
        self.assertFalse(evt["cmdline_mismatch"])

    def test_release_claim_cmdline_mismatch_recorded(self) -> None:
        from unittest.mock import patch

        self._write(
            lambda d: _stamp_claim(
                d,
                heartbeat_age_seconds=30,
                pid=99999,
            )
        )
        fake_result = st.ReapResult(
            signaled=None,
            escalated_kill=False,
            cmdline_mismatch=True,
        )
        with patch("end_of_line.state.reap_orphan_pgroup", return_value=fake_result):
            main(self._argv("release-claim", "--force"))
        evt = self._orphan_reaped_events()[0]
        self.assertTrue(evt["cmdline_mismatch"])
        self.assertIsNone(evt["signaled"])


if __name__ == "__main__":
    unittest.main()
