"""The active-tool marker: bounded suppression, window invalidation, dropped stamps.

Three mechanisms, one file (`false-alarms` p2), and each exists because the
marker's old unbounded form could silence the idle watchdog forever:

* **Bounded suppression.** A Bash command that exits NONZERO fires no closing
  hook event (probed: `PreToolUse` only, for `exit 3`, for a failed command,
  and `PostToolUseFailure` never fires at all), so the marker is left stamped
  until the next Bash call re-stamps it — permanently, when the failing call
  was the phase's last. Nothing can be wired to clear it, so it expires
  instead, at the same age the sibling stuck-tool detector uses.
* **Window invalidation.** A tool START is positive proof of activity, so the
  idle window accumulated before it is void. This is NOT redundant with p1's
  contiguity rule: contiguity voids a window whose sampling hole exceeds
  `worker_idle_max_sample_gap_seconds`, which covers a LONG tool call; a call
  shorter than that leaves a hole contiguity accepts.
* **Dropped stamps.** `stamp_activity_marker` returns False when a contended
  store made it drop the write rather than freeze the worker's Bash call.
  Silently discarding that return leaves no trace of a marker that never landed.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from end_of_line import activity_hook, plan_store, supervisor
from end_of_line import state as st
from end_of_line.cli import main
from end_of_line.config import DispatchSpec, ProjectConfig
from end_of_line.supervisor import TickDelta, _emit_stuck_tool, _emit_worker_idle
from tests import CluTestCase, mutate_state, utcnow_minus

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""

PS_WEDGED_BUILD = """\
  PID  PPID    ELAPSED        TIME COMMAND
78233     1   12:28        0:30.50 claude --print /clu-phase test-plan a
81681 78233   10:00        0:00.50 /usr/bin/xcodebuild test -project HealthDash.xcodeproj
"""


def _stamp(when: _dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ps_time(seconds: float) -> str:
    """Render seconds the way `ps -o time` does: `[hh:]mm:ss[.cc]`."""
    minutes, secs = divmod(seconds, 60.0)
    return f"{int(minutes)}:{secs:05.2f}"


def _tree_snapshot(root_pid: int, *, root_cpu: float = 100.0) -> str:
    """A `ps -eo pid,ppid,etime,time,command` snapshot with no descendants."""
    return (
        "  PID  PPID    ELAPSED        TIME COMMAND\n"
        f"{root_pid}     1   13:00   {_ps_time(root_cpu)} "
        "python3 _pty_spawn_shim.py -- claude --print /clu-phase test-plan a\n"
    )


class ActivityMarkerBoundTestCase(unittest.TestCase):
    """`state.activity_marker_suppresses` — the predicate that replaced a bare
    truthiness test on `active_tool_started_at`."""

    def _claim(self, age_seconds: int | None) -> dict:
        claim: dict = {"phase_id": "a", "claimed_by": "session-abc"}
        if age_seconds is not None:
            claim["active_tool_started_at"] = utcnow_minus(age_seconds)
        return claim

    def _now(self) -> _dt.datetime:
        return _dt.datetime.now(_dt.UTC)

    def test_absent_marker_does_not_suppress(self) -> None:
        self.assertFalse(
            st.activity_marker_suppresses(
                self._claim(None), self._now(), max_age_seconds=300
            )
        )

    def test_fresh_marker_suppresses(self) -> None:
        self.assertTrue(
            st.activity_marker_suppresses(
                self._claim(30), self._now(), max_age_seconds=300
            )
        )

    def test_marker_at_the_bound_still_suppresses(self) -> None:
        # The bound is the stuck-tool detector's own window: a tool call that
        # has not yet reached it is one both watchdogs agree is still running.
        self.assertTrue(
            st.activity_marker_suppresses(
                self._claim(299), self._now(), max_age_seconds=300
            )
        )

    def test_marker_past_the_bound_stops_suppressing(self) -> None:
        # The leak: a nonzero-exit Bash call left this stamped and no event
        # will ever clear it.
        self.assertFalse(
            st.activity_marker_suppresses(
                self._claim(601), self._now(), max_age_seconds=300
            )
        )

    def test_corrupt_marker_does_not_suppress(self) -> None:
        # A stamp whose age cannot be computed cannot be shown to be FRESH,
        # and an unbounded silence is the failure this predicate exists to
        # close. `_emit_stuck_tool` already prints the operator warning.
        claim = self._claim(None)
        claim["active_tool_started_at"] = "not-a-timestamp"
        self.assertFalse(
            st.activity_marker_suppresses(claim, self._now(), max_age_seconds=300)
        )

    def test_a_disabled_detector_falls_back_to_a_bound_it_does_not_remove_one(self) -> None:
        # `stuck_tool_threshold_seconds = 0` DISABLES the stuck-tool detector,
        # so there is no sibling window to derive a bound from. Two readings
        # are wrong in opposite directions: a bound of zero seconds makes
        # every live Bash call look idle, and NO bound hands back the silence
        # switch this whole phase exists to remove — one failing test run and
        # the watchdog is deaf for the rest of the phase. So the predicate
        # falls back to its own default instead of doing either.
        fresh = self._claim(10)
        ancient = self._claim(9999)
        self.assertTrue(
            st.activity_marker_suppresses(fresh, self._now(), max_age_seconds=0)
        )
        self.assertFalse(
            st.activity_marker_suppresses(ancient, self._now(), max_age_seconds=0)
        )
        # The fallback equals the config default, so nobody who left the
        # detector alone sees a behaviour change.
        self.assertEqual(st.ACTIVITY_MARKER_FALLBACK_BOUND_SECONDS, 300)

    def test_the_fallback_bound_is_what_a_disabled_detector_uses(self) -> None:
        # Pin the boundary itself, so a future edit to the constant cannot
        # silently widen the window a disabled detector suppresses for.
        bound = st.ACTIVITY_MARKER_FALLBACK_BOUND_SECONDS
        self.assertTrue(
            st.activity_marker_suppresses(
                self._claim(bound - 1), self._now(), max_age_seconds=0
            )
        )
        self.assertFalse(
            st.activity_marker_suppresses(
                self._claim(bound + 1), self._now(), max_age_seconds=0
            )
        )

    def test_a_marker_in_the_future_suppresses(self) -> None:
        # Clock skew between the worker stamping and the supervisor reading;
        # negative age is "just started", never "very old".
        claim = self._claim(None)
        claim["active_tool_started_at"] = _stamp(
            self._now() + _dt.timedelta(seconds=30)
        )
        self.assertTrue(
            st.activity_marker_suppresses(claim, self._now(), max_age_seconds=300)
        )


class _Clock:
    """A pinned wall clock the tick drives advance by hand."""

    def __init__(self, start: _dt.datetime) -> None:
        self.now = start

    def __call__(self) -> _dt.datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += _dt.timedelta(seconds=seconds)


class _TickDriveCase(CluTestCase):
    """A live plan store with a claimed phase and a hand-driven clock.

    Every sample these subclasses assert on was written by a real `tick()`
    from a real `ps` snapshot — a hand-seeded sample list is exactly the
    fixture that hides a coupling between the tick cadence and the window.
    """

    TICKS = 25
    CADENCE = 30.0

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "proj"
        (self.project / "plans").mkdir(parents=True)
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.cfg = ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command="echo {phase_id}"),
        )
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        self.state_path.parent.mkdir(parents=True)
        self.orch_dir = self.state_path.parent
        plan_store.create(self.orch_dir, st.empty_state("test-plan", "plans"))
        self.clock = _Clock(_dt.datetime(2026, 8, 21, 12, 0, 0, tzinfo=_dt.UTC))
        self.pid = 78233
        self.token = "session-live"
        with mutate_state(self.state_path) as data:
            data["current_claim"] = {
                "phase_id": "a",
                "claimed_by": self.token,
                "pid": self.pid,
                "pgid": self.pid,
                "lease_expires": _stamp(self.clock.now + _dt.timedelta(hours=4)),
                "started_at": _stamp(self.clock.now - _dt.timedelta(minutes=20)),
                "last_heartbeat_at": _stamp(self.clock.now),
                "attempts": 1,
            }

    def _one_tick(self) -> None:
        snapshot = _tree_snapshot(self.pid, root_cpu=100.0)
        with mutate_state(self.state_path) as data:
            data["current_claim"]["last_heartbeat_at"] = _stamp(self.clock.now)
        with mock.patch.object(supervisor, "capture_ps_snapshot", lambda _s=snapshot: _s):
            supervisor.tick(self.state_path, self.cfg)
        self.clock.advance(self.CADENCE)

    def _dump(self) -> dict:
        """What `clu state dump` prints — the operator's own view of the claim.

        Deliberately the CLI and not `plan_store.snapshot`: these cases are
        about what an operator can SEE about a marker and a window, so reading
        an internal return value would prove less than the assertion claims.
        """
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(
                ["state", "dump", "--project", str(self.project), "--plan", "test-plan"]
            )
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def _claim(self) -> dict:
        return self._dump()["current_claim"]

    def _idle_events(self) -> list[dict]:
        return [
            e for e in self._dump()["events"] if e["type"] == st.EVENT_WORKER_IDLE
        ]


class StaleMarkerStopsSilencingTestCase(_TickDriveCase):
    """This phase's gate, read off the EMITTED EVENT LOG.

    A claim carrying a marker no event will ever clear is silent forever under
    the old unbounded read; past the bound it alerts like any other dormant
    worker.
    """

    def test_a_marker_past_the_bound_lets_the_watchdog_speak(self) -> None:
        with mutate_state(self.state_path) as data:
            # The shape the probe reproduced: `ls /nonexistent` exited 1, the
            # PreToolUse stamp landed, and no closing event ever fired.
            data["current_claim"]["active_tool_started_at"] = _stamp(
                self.clock.now - _dt.timedelta(seconds=1200)
            )
        with (
            mock.patch.object(st, "_now_utc", self.clock),
            mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True),
        ):
            for _ in range(self.TICKS):
                self._one_tick()
        events = self._idle_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["phase"], "a")
        # And the marker is still stamped — nothing swept it; it simply
        # stopped being believed.
        self.assertIn("active_tool_started_at", self._claim())

    def test_a_marker_stamped_seconds_ago_still_silences(self) -> None:
        # The other half: a genuinely-running tool call must keep suppressing,
        # or this phase trades a deaf watchdog for a lying one.
        with (
            mock.patch.object(st, "_now_utc", self.clock),
            mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True),
        ):
            for _ in range(self.TICKS):
                with mutate_state(self.state_path) as data:
                    data["current_claim"]["active_tool_started_at"] = _stamp(
                        self.clock.now
                    )
                self._one_tick()
        self.assertEqual(self._idle_events(), [])


class ToolStartVoidsTheWindowTestCase(_TickDriveCase):
    """A tool START invalidates whatever idle window had accumulated.

    Driven through `plan_store.op_activity` — the real write site — rather
    than by editing the claim, because the clear IS part of that op.
    """

    def _start(self) -> bool:
        return plan_store.op_activity(
            self.orch_dir, "test-plan", token=self.token, phase="a", action="start"
        )

    def _end(self) -> bool:
        return plan_store.op_activity(
            self.orch_dir, "test-plan", token=self.token, phase="a", action="end"
        )

    def test_start_clears_the_accumulated_samples(self) -> None:
        with (
            mock.patch.object(st, "_now_utc", self.clock),
            mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True),
        ):
            for _ in range(5):
                self._one_tick()
            self.assertTrue(self._claim()["cpu_samples"])
            self._start()
        # Read back from the store, not from a return value.
        self.assertEqual(self._claim().get("cpu_samples"), [])

    def test_start_rearms_the_idle_dedup_marker(self) -> None:
        # A worker that went quiet, was warned about, then did something is
        # eligible to be warned about again — otherwise one alert per claim
        # is all an operator ever gets.
        with mutate_state(self.state_path) as data:
            data["current_claim"]["worker_idle_notified"] = True
            data["current_claim"]["worker_idle_notified_at"] = _stamp(self.clock.now)
        with mock.patch.object(st, "_now_utc", self.clock):
            self._start()
        claim = self._claim()
        self.assertFalse(claim.get("worker_idle_notified"))
        self.assertIsNone(claim.get("worker_idle_notified_at"))

    def test_end_leaves_the_samples_alone(self) -> None:
        # Only a START is proof of work. An END is the tool finishing, and the
        # samples taken after it are the ones the next window is built from.
        with (
            mock.patch.object(st, "_now_utc", self.clock),
            mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True),
        ):
            for _ in range(5):
                self._one_tick()
            before = self._claim()["cpu_samples"]
            self._end()
        self.assertEqual(self._claim()["cpu_samples"], before)

    def test_a_short_tool_call_voids_the_window(self) -> None:
        # THE p1 carry-in. This Bash call starts and ends between two ticks —
        # far shorter than `worker_idle_max_sample_gap_seconds` (60s) — so it
        # leaves NO sampling hole and p1's contiguity rule accepts the history
        # either side of it as one uninterrupted quiet span. The clear is the
        # only thing that voids it.
        self.assertLess(self.CADENCE, self.cfg.worker_idle_max_sample_gap_seconds)
        with (
            mock.patch.object(st, "_now_utc", self.clock),
            mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True),
        ):
            for i in range(self.TICKS):
                if i == 15:
                    self._start()
                    self._end()
                self._one_tick()
        # Sampling was never interrupted — every tick appended one.
        stamps = [st.parse_iso(s["ts"]) for s in self._claim()["cpu_samples"]]
        gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
        self.assertTrue(
            all(g <= self.cfg.worker_idle_max_sample_gap_seconds for g in gaps),
            f"contiguity would have voided this window on its own: {gaps}",
        )
        # …and yet the window is too short to alert on, because the START
        # threw away everything before it.
        self.assertEqual(self._idle_events(), [])

    def test_the_same_drive_without_the_tool_call_does_alert(self) -> None:
        # The control that makes the case above mean something: identical
        # drive, no tool call, and the watchdog fires.
        with (
            mock.patch.object(st, "_now_utc", self.clock),
            mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True),
        ):
            for _ in range(self.TICKS):
                self._one_tick()
        self.assertEqual(len(self._idle_events()), 1)

    def test_start_overwrites_a_still_open_window(self) -> None:
        # Ported from the deleted `mark_active_tool_start` helper's own test:
        # PreToolUse fires before every Bash call, and a nonzero-exit call
        # left the previous one open. The later stamp just slides the window.
        with mock.patch.object(st, "_now_utc", self.clock):
            self._start()
            first = self._claim()["active_tool_started_at"]
            self.clock.advance(300)
            self._start()
        second = self._claim()["active_tool_started_at"]
        self.assertNotEqual(first, second)
        self.assertEqual(second, _stamp(self.clock.now))

    def test_end_without_a_start_is_a_no_op(self) -> None:
        # Ported from the deleted `clear_active_tool` helper's own test: a
        # PostToolUse with no matching Pre must not raise.
        self.assertTrue(self._end())
        self.assertNotIn("active_tool_started_at", self._claim())


class SuppressionFieldIsCompareAndSetTestCase(_TickDriveCase):
    """`active_tool_started_at` is re-asserted inside BOTH watchdogs' applies.

    A Bash call landing between the tick's decision and its write means the
    worker is demonstrably not idle (and the stuck-tool candidate window just
    slid), so the emit that was decided against the old value is void.
    """

    def _observed(self) -> tuple[dict, plan_store.TickPreconditions]:
        return plan_store.snapshot_with_preconditions(self.orch_dir, "test-plan")

    def _apply(self, delta: TickDelta) -> None:
        plan_store.apply_tick_delta(self.orch_dir, "test-plan", delta.pre, delta)

    def _seed_samples(self, count: int, *, step_seconds: float = 30.0) -> None:
        """A contiguous flat-CPU history ending ~now, written onto the claim.

        Hand-seeded on purpose: these cases are about the PRECONDITION, not
        about the window predicate, and driving a real tick to the exact edge
        of its first emit makes the fixture depend on the very arithmetic the
        p1 suite already covers.
        """
        end = _dt.datetime.now(_dt.UTC)
        base = end - _dt.timedelta(seconds=(count - 1) * step_seconds)
        with mutate_state(self.state_path) as data:
            data["current_claim"]["cpu_samples"] = [
                {
                    "ts": _stamp(base + _dt.timedelta(seconds=i * step_seconds)),
                    "cpu": 100.0,
                }
                for i in range(count)
            ]

    def test_worker_idle_emit_is_discarded_when_a_tool_starts_mid_tick(self) -> None:
        self._seed_samples(21)
        with mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True):
            data, observed = self._observed()
            delta = TickDelta(observed=observed)
            _emit_worker_idle(
                data,
                self.cfg,
                [],
                delta=delta,
                tree_ps_output=_tree_snapshot(self.pid, root_cpu=100.0),
            )
            self.assertTrue(
                [e for e in delta.events if e["type"] == st.EVENT_WORKER_IDLE],
                "fixture never reached the emit, so the precondition is untested",
            )
            # The Bash call the worker made while the tick was thinking.
            plan_store.op_activity(
                self.orch_dir, "test-plan", token=self.token, phase="a", action="start"
            )
            with self.assertRaises(plan_store.TickConflict):
                self._apply(delta)
        self.assertEqual(self._idle_events(), [])

    def test_stuck_tool_emit_is_discarded_when_the_window_slides_mid_tick(self) -> None:
        with mutate_state(self.state_path) as data:
            data["current_claim"]["active_tool_started_at"] = utcnow_minus(605)
        data, observed = self._observed()
        delta = TickDelta(observed=observed)
        _emit_stuck_tool(data, self.cfg, delta=delta, ps_output=PS_WEDGED_BUILD)
        self.assertTrue(
            [e for e in delta.events if e["type"] == st.EVENT_TOOL_STUCK],
            "fixture never reached the emit, so the precondition is untested",
        )
        plan_store.op_activity(
            self.orch_dir, "test-plan", token=self.token, phase="a", action="start"
        )
        with self.assertRaises(plan_store.TickConflict):
            self._apply(delta)

    def test_the_sample_write_is_discarded_when_a_tool_voids_it(self) -> None:
        # `op_activity` is a SECOND writer of `cpu_samples` now, so the tick's
        # append can no longer ride along unguarded: without this precondition
        # a tick in flight rewrites the very window the START threw away.
        # Two samples is under `worker_idle_min_samples`, so this tick appends
        # and emits NOTHING — the append is the only write under test.
        self._seed_samples(2)
        with mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True):
            data, observed = self._observed()
            delta = TickDelta(observed=observed)
            _emit_worker_idle(
                data,
                self.cfg,
                [],
                delta=delta,
                tree_ps_output=_tree_snapshot(self.pid, root_cpu=100.0),
            )
            self.assertEqual(delta.events, [])
            self.assertIn("cpu_samples", delta.claim_updates)
            plan_store.op_activity(
                self.orch_dir, "test-plan", token=self.token, phase="a", action="start"
            )
            with self.assertRaises(plan_store.TickConflict):
                self._apply(delta)
        self.assertEqual(self._claim().get("cpu_samples"), [])


class DroppedStampIsLoggedTestCase(CluTestCase):
    """A stamp the store was too busy to take leaves a trace.

    `stamp_activity_marker` returns False rather than raising, because
    freezing a worker's Bash call over a marker update is worse than losing
    one. Both entry points discarded that False, so the loss was invisible.
    """

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        main(["init", "--project", str(self.project), "--plan", "test-plan"])
        with mutate_state(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def _argv(self, *extra: str) -> list[str]:
        return [
            "activity",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            self.token,
            *extra,
        ]

    def test_cli_reports_a_dropped_stamp_and_still_exits_ok(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.object(st, "stamp_activity_marker", return_value=False),
            redirect_stderr(err),
        ):
            rc = main(self._argv("--start-bash"))
        self.assertEqual(rc, 0)
        self.assertIn("dropped", err.getvalue())
        self.assertIn("test-plan", err.getvalue())

    def test_cli_is_silent_when_the_stamp_lands(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.object(st, "stamp_activity_marker", return_value=True),
            redirect_stderr(err),
        ):
            rc = main(self._argv("--start-bash"))
        self.assertEqual(rc, 0)
        self.assertEqual(err.getvalue(), "")

    def test_hook_entry_point_reports_a_dropped_stamp(self) -> None:
        # The hot path the bundled SKILL.md recipe actually wires. Its
        # `2>/dev/null` hides this from the worker's transcript by design; an
        # operator running the module by hand is who it is for.
        err = io.StringIO()
        with (
            mock.patch.object(st, "stamp_activity_marker", return_value=False),
            redirect_stderr(err),
        ):
            rc = activity_hook.main(
                [
                    "--project",
                    str(self.project),
                    "--plan",
                    "test-plan",
                    "--phase",
                    "a",
                    "--token",
                    self.token,
                    "--start-bash",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("dropped", err.getvalue())


if __name__ == "__main__":
    unittest.main()
