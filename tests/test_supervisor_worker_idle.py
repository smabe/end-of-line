"""Tests for the `_emit_worker_idle` gap-fill in supervisor.py.

Two tiers, and both are load-bearing:

* **Seeded predicate cases** drive `_emit_worker_idle` directly with a
  hand-built sample list. Fast, and they pin one rule at a time.
* **Real-tick drives** run `supervisor.tick` repeatedly against a live store
  with a simulated clock, so the SAMPLES ARE THE ONES THE TICK WROTE. This is
  the tier the seeded cases cannot replace: the defect this suite exists to
  catch (`false-alarms` p1) was a coupling between the tick cadence and the
  retention rule, and a hand-seeded list is exactly the fixture that hides it.
"""

from __future__ import annotations

import datetime as _dt
import unittest
from unittest import mock

from end_of_line import inbox, plan_store, supervisor
from end_of_line import state as st
from end_of_line.config import DispatchSpec, ProjectConfig
from end_of_line.supervisor import TickDelta, _emit_worker_idle
from tests import CluTestCase, mutate_state, utcnow_minus

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _stamp(when: _dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _idle_samples(
    count: int = 21,
    *,
    step_seconds: float = 30.0,
    start_cpu: float = 100.0,
    per_step: float = 0.0,
    newest: _dt.datetime | None = None,
) -> list[dict]:
    """Contiguous CUMULATIVE-CPU samples, newest at ~now.

    `per_step` is the processor time the worker tree accrued between ticks;
    0.0 is a dormant tree. 21 samples 30s apart is 600s of span — one more
    than the retired 20-sample cap could ever hold, which is why the old
    predicate could not be satisfied by continuous sampling.
    """
    end = newest or _now()
    base = end - _dt.timedelta(seconds=(count - 1) * step_seconds)
    return [
        {
            "ts": _stamp(base + _dt.timedelta(seconds=i * step_seconds)),
            "cpu": start_cpu + i * per_step,
        }
        for i in range(count)
    ]


def _ps_time(seconds: float) -> str:
    """Render seconds the way `ps -o time` does: `[hh:]mm:ss[.cc]`."""
    minutes, secs = divmod(seconds, 60.0)
    return f"{int(minutes)}:{secs:05.2f}"


def _tree_snapshot(
    root_pid: int,
    *,
    root_cpu: float = 30.0,
    child: tuple[int, float] | None = None,
    child_elapsed: str = "01:00",
) -> str:
    """A `ps -eo pid,ppid,etime,time,command` snapshot for the idle tree walk.

    Root line plus an optional `(pid, cumulative_cpu_seconds)` child.
    """
    lines = [
        "  PID  PPID    ELAPSED        TIME COMMAND",
        f"{root_pid}     1   13:00   {_ps_time(root_cpu)} "
        f"python3 _pty_spawn_shim.py -- claude --print /clu-phase plan-y my-phase",
    ]
    if child is not None:
        lines.append(
            f"{child[0]} {root_pid}   {child_elapsed}   {_ps_time(child[1])} "
            f"python3 -m unittest discover"
        )
    return "\n".join(lines) + "\n"


def _data_with_idle_claim(
    *,
    with_active_tool: bool = False,
    worker_pid: int = 42000,
    samples: list[dict] | None = None,
) -> dict:
    data = st.empty_state("plan-y", "/tmp/plan-y")
    claim: dict = {
        "phase_id": "my-phase",
        "claimed_by": "session-xyz",
        "pid": worker_pid,
        "lease_expires": "2099-01-01T00:00:00Z",
        "started_at": utcnow_minus(800),
        "last_heartbeat_at": utcnow_minus(60),
        "attempts": 1,
        "cpu_samples": _idle_samples() if samples is None else samples,
    }
    if with_active_tool:
        claim["active_tool_started_at"] = utcnow_minus(120)
    data["current_claim"] = claim
    return data


class EmitWorkerIdleTestCase(CluTestCase):
    def _cfg(self) -> ProjectConfig:
        return ProjectConfig(project_root=self.tmp_path)

    def _emit(self, data: dict, side_notifies: list | None = None, **over) -> None:
        # Default snapshot CONTINUES the seeded history flat: the tree's
        # cumulative total is where the newest sample left it. A test that
        # wants movement seeds it, or passes its own snapshot.
        claim = data.get("current_claim") or {}
        seeded = claim.get("cpu_samples") or []
        kwargs: dict = {
            "tree_ps_output": _tree_snapshot(
                claim.get("pid") or 0,
                root_cpu=seeded[-1]["cpu"] if seeded else 100.0,
            )
        }
        kwargs.update(over)
        _emit_worker_idle(
            data,
            self._cfg(),
            side_notifies if side_notifies is not None else [],
            delta=TickDelta(),
            **kwargs,
        )

    def _idle_events(self, data: dict) -> list[dict]:
        return [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]

    def test_fires_after_threshold_window(self) -> None:
        data = _data_with_idle_claim()
        side_notifies: list = []
        self._emit(data, side_notifies)
        events = self._idle_events(data)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(side_notifies), 1)
        kind, body = side_notifies[0]
        self.assertIn("idle", kind)
        self.assertIn("my-phase", body)

    def test_fires_writes_inbox_event(self) -> None:
        data = _data_with_idle_claim()
        delta = TickDelta()
        _emit_worker_idle(
            data,
            self._cfg(),
            [],
            delta=delta,
            tree_ps_output=_tree_snapshot(42000, root_cpu=100.0),
        )
        # The emitter RAISES the inbox event onto the delta; the tick writes it
        # only after its own apply commits, so a discarded tick leaves no note
        # about a state change it never made. Flushing here is what the tick
        # does, and it also proves the payload is a valid `write_event` call.
        for event in delta.inbox_events:
            inbox.write_event(**event)
        events = inbox.read_unprocessed()
        worker_idle = [e for e in events if e["type"] == "worker_idle"]
        self.assertEqual(len(worker_idle), 1)
        self.assertEqual(worker_idle[0]["details"]["phase_id"], "my-phase")

    def test_idempotent_within_same_claim(self) -> None:
        data = _data_with_idle_claim()
        self._emit(data)
        self._emit(data)
        self.assertEqual(len(self._idle_events(data)), 1)

    def test_suppressed_when_active_tool_present(self) -> None:
        data = _data_with_idle_claim(with_active_tool=True)
        side_notifies: list = []
        self._emit(data, side_notifies)
        self.assertEqual(self._idle_events(data), [])
        self.assertEqual(side_notifies, [])

    def test_suppressed_when_cumulative_cpu_advanced(self) -> None:
        # The measured live-worker rate: ~0.25s of processor time per 30s tick.
        data = _data_with_idle_claim(samples=_idle_samples(per_step=0.25))
        side_notifies: list = []
        self._emit(data, side_notifies)
        self.assertEqual(self._idle_events(data), [])
        self.assertEqual(side_notifies, [])

    def test_suppressed_when_too_few_samples(self) -> None:
        data = _data_with_idle_claim(samples=_idle_samples(2, step_seconds=600.0))
        self._emit(data)
        self.assertEqual(self._idle_events(data), [])

    def test_notified_cleared_on_release_allows_re_fire(self) -> None:
        data = _data_with_idle_claim()
        self._emit(data)
        self.assertTrue(data["current_claim"].get("worker_idle_notified"))

        # Release claim and re-claim the same phase fresh
        data["current_claim"] = {
            "phase_id": "my-phase",
            "claimed_by": "session-xyz2",
            "pid": 42001,
            "lease_expires": "2099-01-01T00:00:00Z",
            "started_at": utcnow_minus(800),
            "last_heartbeat_at": utcnow_minus(60),
            "attempts": 2,
            "cpu_samples": _idle_samples(),
        }
        self._emit(data)
        # Two fires, one per claim
        self.assertEqual(len(self._idle_events(data)), 2)

    def test_no_emit_when_no_claim(self) -> None:
        data = st.empty_state("plan-y", "/tmp/plan-y")
        _emit_worker_idle(data, self._cfg(), [], delta=TickDelta(), tree_ps_output="")
        self.assertEqual(self._idle_events(data), [])

    def test_no_emit_when_no_pid(self) -> None:
        data = _data_with_idle_claim(worker_pid=0)
        data["current_claim"].pop("pid", None)
        _emit_worker_idle(data, self._cfg(), [], delta=TickDelta(), tree_ps_output="")
        self.assertEqual(self._idle_events(data), [])

    # -- what the sample actually measures -------------------------------------

    def test_sample_includes_the_root_pids_own_cpu(self) -> None:
        # `walk_worker_tree` excludes the root by contract. Dropping it would
        # measure the shim's children but not the shim — nearly the whole
        # signal on a worker with no live descendants.
        data = _data_with_idle_claim(samples=[])
        self._emit(data, tree_ps_output=_tree_snapshot(42000, root_cpu=42.5))
        samples = data["current_claim"]["cpu_samples"]
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0]["cpu"], 42.5)

    def test_sample_sums_descendant_cpu_with_the_root(self) -> None:
        data = _data_with_idle_claim(samples=[])
        self._emit(
            data,
            tree_ps_output=_tree_snapshot(42000, root_cpu=1.5, child=(42001, 7.25)),
        )
        self.assertAlmostEqual(data["current_claim"]["cpu_samples"][0]["cpu"], 8.75)

    def test_sample_preserves_fractional_seconds(self) -> None:
        # The separating signal between a waiting worker and a dormant one
        # lives entirely inside the fraction the old parse truncated away.
        data = _data_with_idle_claim(samples=[])
        self._emit(data, tree_ps_output=_tree_snapshot(42000, root_cpu=0.26))
        self.assertAlmostEqual(data["current_claim"]["cpu_samples"][0]["cpu"], 0.26)

    def test_no_sample_when_ps_is_unreadable(self) -> None:
        # A failed `ps` is "cannot judge", not "measured zero" — inventing a
        # sample here would forge the very evidence the window rests on.
        data = _data_with_idle_claim(samples=[])
        self._emit(data, tree_ps_output="")
        self.assertEqual(data["current_claim"]["cpu_samples"], [])
        self.assertEqual(self._idle_events(data), [])

    def test_no_sample_when_the_root_pid_is_absent_from_ps(self) -> None:
        data = _data_with_idle_claim(samples=[])
        self._emit(data, tree_ps_output=_tree_snapshot(99999))
        self.assertEqual(data["current_claim"]["cpu_samples"], [])


class _Clock:
    """A pinned wall clock the tick drives advance by hand."""

    def __init__(self, start: _dt.datetime) -> None:
        self.now = start

    def __call__(self) -> _dt.datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += _dt.timedelta(seconds=seconds)


class WorkerIdleTickDriveTestCase(CluTestCase):
    """Drive real `tick()` calls and read the EMITTED EVENT LOG.

    No sample here is hand-written: every one is produced by the tick from a
    `ps` snapshot, which is the only way the retention rule and the window
    predicate get exercised against each other.
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
        with mutate_state(self.state_path) as data:
            data["current_claim"] = {
                "phase_id": "a",
                "claimed_by": "session-live",
                "pid": self.pid,
                "pgid": self.pid,
                "lease_expires": _stamp(self.clock.now + _dt.timedelta(hours=4)),
                "started_at": _stamp(self.clock.now - _dt.timedelta(minutes=20)),
                "last_heartbeat_at": _stamp(self.clock.now),
                "attempts": 1,
            }

    def _drive(self, *, per_tick_cpu: float, interrupt: range | None = None) -> None:
        """Run `TICKS` ticks `CADENCE` apart with a tree burning `per_tick_cpu`.

        `interrupt` names tick indices during which a Bash tool is active —
        the worker is demonstrably WORKING, so the tick declines to sample and
        a hole opens in the history. That hole is the historical false alarm.
        """
        cpu = 100.0
        with (
            mock.patch.object(st, "_now_utc", self.clock),
            mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True),
        ):
            for i in range(self.TICKS):
                active = interrupt is not None and i in interrupt
                with mutate_state(self.state_path) as data:
                    claim = data["current_claim"]
                    claim["last_heartbeat_at"] = _stamp(self.clock.now)
                    if active:
                        claim["active_tool_started_at"] = _stamp(self.clock.now)
                    else:
                        claim.pop("active_tool_started_at", None)
                snapshot = _tree_snapshot(self.pid, root_cpu=cpu)
                with mock.patch.object(
                    supervisor, "capture_ps_snapshot", lambda _s=snapshot: _s
                ):
                    supervisor.tick(self.state_path, self.cfg)
                cpu += per_tick_cpu
                self.clock.advance(self.CADENCE)

    def _idle_events(self) -> list[dict]:
        data = plan_store.snapshot(self.orch_dir, "test-plan")
        return [e for e in data["events"] if e["type"] == st.EVENT_WORKER_IDLE]

    def test_busy_worker_never_emits(self) -> None:
        # 0.25s of cumulative processor time per 30s tick — the lightest LIVE
        # process measured on this machine, and lighter than a working one.
        self._drive(per_tick_cpu=0.25)
        self.assertEqual(self._idle_events(), [])

    def test_dormant_worker_emits_exactly_once(self) -> None:
        # The half that proves the detector can produce a TRUE positive at
        # all. Before this phase the window was unsatisfiable under continuous
        # sampling, so this drive emitted nothing.
        self._drive(per_tick_cpu=0.0)
        events = self._idle_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["phase"], "a")
        self.assertEqual(events[0]["pid"], self.pid)

    def test_interrupted_sampling_does_not_emit(self) -> None:
        # The historical false alarm: a Bash call ran, sampling paused, and
        # the resulting HOLE is what made the old window satisfiable — a gap
        # is positive evidence the worker was working. Contiguity rejects it.
        self._drive(per_tick_cpu=0.0, interrupt=range(10, 14))
        self.assertEqual(self._idle_events(), [])

    def test_samples_are_retained_by_age_not_by_count(self) -> None:
        # 25 ticks at 30s is more history than the retired 20-sample cap could
        # hold; what survives is bounded by the retention WINDOW, and it spans
        # at least the 10 minutes the predicate requires.
        self._drive(per_tick_cpu=0.0)
        claim = plan_store.snapshot(self.orch_dir, "test-plan")["current_claim"]
        samples = claim["cpu_samples"]
        stamps = [st.parse_iso(s["ts"]) for s in samples]
        self.assertGreaterEqual((stamps[-1] - stamps[0]).total_seconds(), 600.0)
        self.assertLessEqual(
            (stamps[-1] - stamps[0]).total_seconds(),
            self.cfg.worker_idle_window_minutes * 60
            + self.cfg.worker_idle_max_sample_gap_seconds,
        )


if __name__ == "__main__":
    unittest.main()
