"""Tests for operator-side lifecycle commands: pause / resume / retry."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import main
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
| B | `test-plan-b.md` | thing | 1h |
"""


class LifecycleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        rc = main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def _write(self, mut) -> None:
        with st.locked(self.state_path):
            data = st.load(self.state_path)
            mut(data)
            st.save_atomic(self.state_path, data)

    def _argv(self, cmd: str, *extra: str) -> list[str]:
        return [cmd, "--project", str(self.project), "--plan", "test-plan", *extra]

    # ---- pause ------------------------------------------------------------

    def test_pause_flips_status_to_paused(self) -> None:
        rc = main(self._argv("pause"))
        self.assertEqual(rc, 0)
        self.assertEqual(self._read()["status"], st.STATUS_PAUSED)

    def test_pause_records_event_with_reason(self) -> None:
        rc = main(self._argv("pause", "--reason", "investigating slow phase"))
        self.assertEqual(rc, 0)
        evts = [e for e in self._read()["events"] if e["type"] == st.EVENT_PAUSED]
        self.assertEqual(len(evts), 1)
        self.assertEqual(evts[0]["reason"], "investigating slow phase")

    def test_pause_is_idempotent(self) -> None:
        self.assertEqual(main(self._argv("pause")), 0)
        self.assertEqual(main(self._argv("pause")), 0)
        # No second event — re-pausing while already paused should be a no-op.
        evts = [e for e in self._read()["events"] if e["type"] == st.EVENT_PAUSED]
        self.assertEqual(len(evts), 1)

    def test_pause_refused_when_done(self) -> None:
        self._write(lambda d: d.__setitem__("status", st.STATUS_DONE))
        rc = main(self._argv("pause"))
        self.assertNotEqual(rc, 0)
        self.assertEqual(self._read()["status"], st.STATUS_DONE)

    # ---- resume -----------------------------------------------------------

    def test_resume_flips_paused_to_running(self) -> None:
        main(self._argv("pause"))
        rc = main(self._argv("resume"))
        self.assertEqual(rc, 0)
        self.assertEqual(self._read()["status"], st.STATUS_RUNNING)
        evts = [e for e in self._read()["events"] if e["type"] == st.EVENT_RESUMED]
        self.assertEqual(len(evts), 1)

    def test_resume_refused_when_halted(self) -> None:
        self._write(lambda d: d.__setitem__("status", st.STATUS_HALTED))
        rc = main(self._argv("resume"))
        self.assertNotEqual(rc, 0)
        self.assertEqual(self._read()["status"], st.STATUS_HALTED)

    def test_resume_refused_when_done(self) -> None:
        self._write(lambda d: d.__setitem__("status", st.STATUS_DONE))
        rc = main(self._argv("resume"))
        self.assertNotEqual(rc, 0)

    def test_resume_noop_when_already_running(self) -> None:
        rc = main(self._argv("resume"))
        self.assertEqual(rc, 0)
        # No spurious resume event when the plan was already running.
        evts = [e for e in self._read()["events"] if e["type"] == st.EVENT_RESUMED]
        self.assertEqual(evts, [])

    # ---- retry ------------------------------------------------------------

    def _halt_plan_on_phase_a(self) -> None:
        """Simulate the supervisor halting phase 'a' after max attempts."""
        def mut(d: dict) -> None:
            d["status"] = st.STATUS_HALTED
            d["config"]["max_attempts_per_phase"] = 2
            st.append_event(d, st.EVENT_PHASE_STARTED, phase="a", claimed_by="x")
            st.append_event(d, st.EVENT_LEASE_EXPIRED, phase="a")
            st.append_event(d, st.EVENT_PHASE_STARTED, phase="a", claimed_by="y")
            st.append_event(d, st.EVENT_LEASE_EXPIRED, phase="a")
            st.append_event(
                d, st.EVENT_PHASE_MAX_ATTEMPTS, phase="a", attempts=2,
            )
        self._write(mut)

    def test_retry_clears_attempts_and_resumes(self) -> None:
        self._halt_plan_on_phase_a()
        # Sanity: attempts_for_phase counts both before reset.
        self.assertEqual(
            st.attempts_for_phase(self._read(), "a"), 2,
        )
        rc = main(self._argv("retry"))
        self.assertEqual(rc, 0)
        data = self._read()
        self.assertEqual(data["status"], st.STATUS_RUNNING)
        # After retry, attempts_for_phase should not count phase_starteds
        # that happened before the retry-reset point.
        self.assertEqual(st.attempts_for_phase(data, "a"), 0)
        evts = [
            e for e in data["events"] if e["type"] == st.EVENT_RETRY_REQUESTED
        ]
        self.assertEqual(len(evts), 1)
        self.assertEqual(evts[0]["phase"], "a")

    def test_retry_lets_supervisor_redispatch_halted_phase(self) -> None:
        self._halt_plan_on_phase_a()
        main(self._argv("retry"))
        # Next tick should now re-claim phase 'a' instead of halting.
        rc = main(self._argv("tick"))
        self.assertEqual(rc, 0)
        claim = self._read()["current_claim"]
        self.assertIsNotNone(claim)
        self.assertEqual(claim["phase_id"], "a")

    def test_retry_with_explicit_phase(self) -> None:
        self._halt_plan_on_phase_a()
        rc = main(self._argv("retry", "--phase", "a"))
        self.assertEqual(rc, 0)
        self.assertEqual(st.attempts_for_phase(self._read(), "a"), 0)

    def test_retry_refused_when_no_halted_phase(self) -> None:
        # Running plan, no max-attempts event — retry has nothing to clear.
        rc = main(self._argv("retry"))
        self.assertNotEqual(rc, 0)

    def test_retry_rejects_invalid_phase_slug(self) -> None:
        self._halt_plan_on_phase_a()
        rc = main(self._argv("retry", "--phase", "../etc/passwd"))
        self.assertEqual(rc, 2)  # ExitCode.INVALID_SLUG

    def test_retry_explicit_phase_overrides_last_halt(self) -> None:
        # Two halts; explicit --phase points at the older one.
        self._halt_plan_on_phase_a()
        self._write(lambda d: st.append_event(
            d, st.EVENT_PHASE_MAX_ATTEMPTS, phase="b", attempts=2,
        ))
        rc = main(self._argv("retry", "--phase", "a"))
        self.assertEqual(rc, 0)
        evts = [
            e for e in self._read()["events"]
            if e["type"] == st.EVENT_RETRY_REQUESTED
        ]
        self.assertEqual([e["phase"] for e in evts], ["a"])

    # ---- status reason ---------------------------------------------------

    def _status_output(self) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(self._argv("status"))
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_status_shows_pause_reason(self) -> None:
        main(self._argv("pause", "--reason", "investigating slow phase"))
        out = self._status_output()
        self.assertIn("investigating slow phase", out)

    def test_status_shows_pause_without_reason(self) -> None:
        main(self._argv("pause"))
        out = self._status_output()
        # Some marker that the plan is paused manually should appear, not
        # just "Status: paused" with no explanation.
        self.assertIn("Reason:", out)
        self.assertIn("operator pause", out.lower())

    def test_status_shows_sla_pause_blocker(self) -> None:
        # Simulate SLA escalation by writing the event directly.
        def mut(d: dict) -> None:
            d["status"] = st.STATUS_PAUSED
            st.add_blocker(d, "a", "Q?", ["X"], "ctx")
            blocker_id = d["blockers"][-1]["id"]
            st.append_event(
                d, st.EVENT_BLOCKER_SLA_EXCEEDED,
                blocker_id=blocker_id, age_hours=25.3,
            )
        self._write(mut)
        out = self._status_output()
        self.assertIn("Reason:", out)
        self.assertIn("SLA", out)
        self.assertIn("25.3", out)

    def test_status_shows_halt_reason_with_phase_and_attempts(self) -> None:
        self._halt_plan_on_phase_a()
        out = self._status_output()
        self.assertIn("Reason:", out)
        self.assertIn("phase a", out)
        self.assertIn("max attempts", out.lower())

    def test_status_no_reason_when_running(self) -> None:
        out = self._status_output()
        self.assertNotIn("Reason:", out)

    def test_status_no_reason_when_done(self) -> None:
        self._write(lambda d: d.__setitem__("status", st.STATUS_DONE))
        out = self._status_output()
        self.assertNotIn("Reason:", out)

    def test_status_pause_after_sla_shows_operator_reason(self) -> None:
        # Locks down the load-bearing "most-recent wins" tie-breaker: a fresh
        # operator pause that follows an earlier SLA escalation should be the
        # reported reason. Realistic shape: SLA fired in history, blocker was
        # answered, now operator manually pauses again.
        def mut(d: dict) -> None:
            st.add_blocker(d, "a", "Q?", ["X"], "ctx")
            blocker_id = d["blockers"][-1]["id"]
            st.append_event(
                d, st.EVENT_BLOCKER_SLA_EXCEEDED,
                blocker_id=blocker_id, age_hours=25.0,
            )
            # Status stays running so cmd_pause actually emits EVENT_PAUSED
            # rather than short-circuiting on the "already paused" branch.
        self._write(mut)
        rc = main(self._argv("pause", "--reason", "operator override"))
        self.assertEqual(rc, 0)
        out = self._status_output()
        self.assertIn("operator pause", out.lower())
        self.assertIn("operator override", out)
        self.assertNotIn("SLA", out)


if __name__ == "__main__":
    unittest.main()
