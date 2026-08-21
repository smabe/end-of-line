"""Worker-declared quiet spans: the callback, the clamp, and the expiry.

`false-alarms` p3. p1 and p2 made the idle watchdog's INFERENCE correct; this
removes most of the need for it. A worker about to run a code review or a full
test gate knows it is going to look wedged for the next twenty minutes, and
says so through a token-validated callback instead of leaving the supervisor
to guess from process CPU.

The whole design turns on one refusal: **a span is a lease, not a pair.** Every
close-event mechanism this plan examined has failed in the field — subagent
stop events missing from 42% of traces, and the shipped activity marker's own
close event never firing for a nonzero-exit Bash call. A suppression that waits
for a message to arrive is a suppression that eventually becomes permanent, and
permanent deafness in a wedge detector is strictly worse than the false alarms
this plan set out to remove. So the span carries `expires_at`, stamped from
clu's clock at declaration time and clamped to the project ceiling, and `--end`
can only ever shorten it.

Two edges get their own cases here because this plan has now shipped the same
defect twice — a threshold whose ZERO value silently removed a bound, invisible
because every test exercised the value's use site and never its zero:

* **`worker_quiet_span_ceiling_minutes = 0`** means workers may declare NO
  silence. The safe direction is the one that SHORTENS silence, so zero clamps
  every span to zero length; reading it as "no ceiling" would make the knob
  that limits silence the switch that removes the limit.
* **An EXPIRED span alerts.** Asserted off the emitted event log rather than
  off the predicate, because the predicate returning False proves nothing about
  whether the operator ever hears anything.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from end_of_line import plan_store, supervisor
from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import (
    CONFIG_FILENAME,
    ConfigError,
    DispatchSpec,
    ProjectConfig,
    load_project_config,
)
from end_of_line.supervisor import TickDelta, _emit_stuck_tool, _emit_worker_idle
from tests import CluTestCase, isolate_registry, mutate_state

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""


def _stamp(when: _dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ps_time(seconds: float) -> str:
    """Render seconds the way `ps -o time` does: `[hh:]mm:ss[.cc]`."""
    minutes, secs = divmod(seconds, 60.0)
    return f"{int(minutes)}:{secs:05.2f}"


PS_WEDGED_BUILD = """\
  PID  PPID    ELAPSED        TIME COMMAND
78233     1   12:28        0:30.50 claude --print /clu-phase test-plan a
81681 78233   10:00        0:00.50 /usr/bin/xcodebuild test -project HealthDash.xcodeproj
"""


def _tree_snapshot(root_pid: int, *, root_cpu: float = 100.0) -> str:
    """A `ps -eo pid,ppid,etime,time,command` snapshot with no descendants."""
    return (
        "  PID  PPID    ELAPSED        TIME COMMAND\n"
        f"{root_pid}     1   13:00   {_ps_time(root_cpu)} "
        "python3 _pty_spawn_shim.py -- claude --print /clu-phase test-plan a\n"
    )


class _Clock:
    """A pinned wall clock the tick drives advance by hand."""

    def __init__(self, start: _dt.datetime) -> None:
        self.now = start

    def __call__(self) -> _dt.datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += _dt.timedelta(seconds=seconds)


class QuietSpanPredicateTestCase(unittest.TestCase):
    """`state.quiet_span_active` — the read-site bound on a declared span.

    Every shape it cannot date answers False. That is the safe direction: False
    hands the claim back to p1's evidence-based judgment, which this phase sits
    in FRONT of rather than replaces, while True on an undateable record would
    be silence with no end.
    """

    def setUp(self) -> None:
        self.now = _dt.datetime(2026, 8, 21, 12, 0, 0, tzinfo=_dt.UTC)

    def _claim(self, **span) -> dict:
        return {"phase_id": "a", "claimed_by": "session-abc", "quiet_span": span}

    def test_an_unexpired_span_is_active(self) -> None:
        claim = self._claim(
            reason="code-review",
            started_at=_stamp(self.now - _dt.timedelta(minutes=5)),
            expires_at=_stamp(self.now + _dt.timedelta(minutes=15)),
        )
        self.assertTrue(st.quiet_span_active(claim, self.now))

    def test_an_expired_span_is_not_active(self) -> None:
        # The leak this design refuses to build. A worker that declared a span
        # and then died must not keep the watchdog silent for ever.
        claim = self._claim(
            reason="code-review",
            started_at=_stamp(self.now - _dt.timedelta(minutes=90)),
            expires_at=_stamp(self.now - _dt.timedelta(minutes=70)),
        )
        self.assertFalse(st.quiet_span_active(claim, self.now))

    def test_a_span_expiring_exactly_now_is_not_active(self) -> None:
        # The boundary belongs to the SHORTER silence: at `expires_at` the
        # declaration has been honoured in full and the worker is judged again.
        claim = self._claim(
            reason="code-review",
            started_at=_stamp(self.now - _dt.timedelta(minutes=20)),
            expires_at=_stamp(self.now),
        )
        self.assertFalse(st.quiet_span_active(claim, self.now))

    def test_no_span_is_not_active(self) -> None:
        self.assertFalse(
            st.quiet_span_active({"phase_id": "a", "claimed_by": "s"}, self.now)
        )

    def test_a_cleared_span_is_not_active(self) -> None:
        # `--end` writes null rather than deleting the key, because the write
        # goes through the flags catch-all.
        claim = {"phase_id": "a", "claimed_by": "s", "quiet_span": None}
        self.assertFalse(st.quiet_span_active(claim, self.now))

    def test_a_span_with_no_expiry_is_not_active(self) -> None:
        # A record nobody can date is the exact shape of an unbounded
        # suppression, so it must not suppress at all.
        claim = self._claim(reason="code-review", started_at=_stamp(self.now))
        self.assertFalse(st.quiet_span_active(claim, self.now))

    def test_a_corrupt_expiry_is_not_active(self) -> None:
        claim = self._claim(reason="code-review", expires_at="soon-ish")
        self.assertFalse(st.quiet_span_active(claim, self.now))

    def test_a_non_object_span_is_not_active(self) -> None:
        # A hand-edited claim row, or a future writer with the wrong shape.
        for junk in ("code-review", 42, ["code-review"]):
            with self.subTest(value=junk):
                claim = {"phase_id": "a", "claimed_by": "s", "quiet_span": junk}
                self.assertFalse(st.quiet_span_active(claim, self.now))


class QuietSpanClampTestCase(unittest.TestCase):
    """`state.build_quiet_span` — the ceiling is the only bound on the silence."""

    def setUp(self) -> None:
        self.now = _dt.datetime(2026, 8, 21, 12, 0, 0, tzinfo=_dt.UTC)

    def _minutes(self, span: dict) -> float:
        started = st.parse_iso(span["started_at"])
        return (st.parse_iso(span["expires_at"]) - started).total_seconds() / 60.0

    def test_a_declaration_under_the_ceiling_is_honoured(self) -> None:
        span = st.build_quiet_span("code-review", 20, self.now, ceiling_minutes=45)
        self.assertEqual(self._minutes(span), 20.0)
        self.assertEqual(span["reason"], "code-review")
        self.assertEqual(span["started_at"], _stamp(self.now))

    def test_a_declaration_above_the_ceiling_is_clamped(self) -> None:
        # A worker cannot buy unlimited silence by declaring a ten-hour review.
        span = st.build_quiet_span("code-review", 600, self.now, ceiling_minutes=45)
        self.assertEqual(self._minutes(span), 45.0)

    def test_a_zero_ceiling_clamps_every_span_to_nothing(self) -> None:
        # THE zero-value case. `worker_quiet_span_ceiling_minutes = 0` means
        # workers may declare NO silence — the safe reading, because it makes
        # the disabled setting the one that SHORTENS silence. The unsafe
        # reading is "0 means no ceiling", which would turn the knob that
        # limits suppression into the switch that removes it; p2 shipped
        # exactly that inversion on a sibling threshold and review caught it
        # while the whole suite stayed green.
        span = st.build_quiet_span("code-review", 600, self.now, ceiling_minutes=0)
        self.assertEqual(self._minutes(span), 0.0)
        self.assertFalse(
            st.quiet_span_active({"quiet_span": span}, self.now),
            "a zero ceiling must suppress nothing, not suppress forever",
        )

    def test_a_non_positive_declaration_clamps_to_nothing(self) -> None:
        # The callback rejects these before they reach here; the domain
        # function is public and its next caller might not.
        for minutes in (0, -30):
            with self.subTest(minutes=minutes):
                span = st.build_quiet_span("x", minutes, self.now, ceiling_minutes=45)
                self.assertEqual(self._minutes(span), 0.0)


class QuietSpanConfigTestCase(unittest.TestCase):
    """`worker_quiet_span_ceiling_minutes` on the load path.

    Separate from the behaviour above on purpose: the last two phases both
    shipped a threshold whose zero was wrong, and both times the load path was
    where the mismatch entered the diff.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        isolate_registry(self, self.root)

    def _write(self, raw: dict) -> None:
        (self.root / CONFIG_FILENAME).write_text(json.dumps(raw))

    def test_default_is_the_signed_off_value(self) -> None:
        self._write({})
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.worker_quiet_span_ceiling_minutes, 45)
        self.assertEqual(cfg.worker_quiet_span_ceiling_minutes, st.QUIET_SPAN_CEILING_DEFAULT_MINUTES)

    def test_an_override_is_read(self) -> None:
        self._write({"worker_quiet_span_ceiling_minutes": 15})
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.worker_quiet_span_ceiling_minutes, 15)

    def test_zero_loads_and_means_no_declarable_silence(self) -> None:
        # Deliberately NOT rejected like the four idle thresholds, which are
        # detector bounds where zero is a predicate answering without evidence.
        # This is a CAP on suppression, so its zero is meaningful and points
        # the safe way: no worker may declare any silence at all.
        self._write({"worker_quiet_span_ceiling_minutes": 0})
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.worker_quiet_span_ceiling_minutes, 0)

    def test_a_negative_ceiling_is_rejected(self) -> None:
        self._write({"worker_quiet_span_ceiling_minutes": -1})
        with self.assertRaises(ConfigError):
            load_project_config(self.root)

    def test_bools_and_non_ints_are_rejected(self) -> None:
        for bad in (True, 2.5, "45"):
            with self.subTest(value=bad):
                self._write({"worker_quiet_span_ceiling_minutes": bad})
                with self.assertRaises(ConfigError):
                    load_project_config(self.root)


class _CallbackCase(CluTestCase):
    """An initialised project with a claimed phase and a `clu quiet-span` argv
    builder. Holds no tests — the ceiling subclasses below need the same
    fixture at a different `worker_quiet_span_ceiling_minutes`, and inheriting
    the default-ceiling assertions with it would assert the wrong number.
    """

    CEILING: int | None = None

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "proj"
        (self.project / "plans").mkdir(parents=True)
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        raw: dict = {"dispatch": {"command": "echo hi"}}
        if self.CEILING is not None:
            raw["worker_quiet_span_ceiling_minutes"] = self.CEILING
        (self.project / CONFIG_FILENAME).write_text(json.dumps(raw))
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        main(["init", "--project", str(self.project), "--plan", "test-plan"])
        with mutate_state(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def _argv(self, *extra: str, token: str | None = None) -> list[str]:
        return [
            "quiet-span",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--phase",
            "a",
            "--token",
            self.token if token is None else token,
            *extra,
        ]

    def _span(self) -> dict | None:
        return st.load(self.state_path)["current_claim"].get("quiet_span")


class QuietSpanCallbackTestCase(_CallbackCase):
    """`clu quiet-span` — the worker callback and its token boundary."""

    def test_a_declaration_writes_the_span_onto_the_claim(self) -> None:
        rc = main(self._argv("--reason", "code-review", "--expected-minutes", "20"))
        self.assertEqual(rc, ExitCode.OK)
        span = self._span()
        assert span is not None
        self.assertEqual(span["reason"], "code-review")
        started = st.parse_iso(span["started_at"])
        self.assertEqual(
            (st.parse_iso(span["expires_at"]) - started).total_seconds() / 60.0,
            20.0,
        )

    def test_the_declaration_leaves_the_rest_of_the_claim_alone(self) -> None:
        # The span write merges into the flags catch-all; a write of one marker
        # must not erase the neighbours sharing that column.
        with mutate_state(self.state_path) as data:
            data["current_claim"]["worker_idle_notified"] = True
        main(self._argv("--reason", "code-review", "--expected-minutes", "20"))
        claim = st.load(self.state_path)["current_claim"]
        self.assertTrue(claim["worker_idle_notified"])
        self.assertEqual(claim["phase_id"], "a")

    def test_end_clears_the_span(self) -> None:
        main(self._argv("--reason", "code-review", "--expected-minutes", "20"))
        rc = main(self._argv("--end"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertIsNone(self._span())

    def test_end_rejects_declaration_flags(self) -> None:
        for extra in (("--reason", "x"), ("--expected-minutes", "5")):
            with self.subTest(extra=extra):
                rc = main(self._argv("--end", *extra))
                self.assertEqual(rc, ExitCode.INVALID_VALUE)

    def test_a_declaration_needs_a_reason_and_a_duration(self) -> None:
        self.assertEqual(
            main(self._argv("--expected-minutes", "20")), ExitCode.INVALID_VALUE
        )
        self.assertEqual(main(self._argv("--reason", "x")), ExitCode.INVALID_VALUE)
        self.assertIsNone(self._span())

    def test_a_non_positive_duration_is_refused_not_silently_clamped(self) -> None:
        for minutes in ("0", "-5"):
            with self.subTest(minutes=minutes):
                rc = main(self._argv("--reason", "x", "--expected-minutes", minutes))
                self.assertEqual(rc, ExitCode.INVALID_VALUE)
                self.assertIsNone(self._span())

    def test_a_forged_token_is_rejected(self) -> None:
        rc = main(
            self._argv(
                "--reason", "code-review", "--expected-minutes", "20", token="session-forged"
            )
        )
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)
        self.assertIsNone(self._span())

    def test_a_token_from_a_released_claim_is_rejected(self) -> None:
        with mutate_state(self.state_path) as data:
            data["current_claim"] = None
        rc = main(self._argv("--reason", "code-review", "--expected-minutes", "20"))
        self.assertEqual(rc, ExitCode.CLAIM_MISMATCH)

    def test_a_token_for_another_phase_is_rejected(self) -> None:
        argv = self._argv("--reason", "code-review", "--expected-minutes", "20")
        argv[argv.index("--phase") + 1] = "b"
        self.assertEqual(main(argv), ExitCode.CLAIM_MISMATCH)

    def test_an_invalid_phase_slug_is_refused_before_any_write(self) -> None:
        argv = self._argv("--reason", "code-review", "--expected-minutes", "20")
        argv[argv.index("--phase") + 1] = "../escape"
        self.assertEqual(main(argv), ExitCode.INVALID_SLUG)


class QuietSpanCeilingClampTestCase(_CallbackCase):
    """The project ceiling clamps what the worker asked for, asserted directly."""

    CEILING = 10

    def test_a_declaration_above_the_ceiling_is_clamped(self) -> None:
        rc = main(self._argv("--reason", "code-review", "--expected-minutes", "600"))
        self.assertEqual(rc, ExitCode.OK)
        span = self._span()
        assert span is not None
        started = st.parse_iso(span["started_at"])
        self.assertEqual(
            (st.parse_iso(span["expires_at"]) - started).total_seconds() / 60.0,
            10.0,
        )


class QuietSpanZeroCeilingCallbackTestCase(_CallbackCase):
    """A ceiling of 0 accepts the callback and grants no silence.

    The callback still succeeds — an additive contract whose absence must
    degrade to p1's inference cannot start failing phases because an operator
    turned the ceiling down — but the span it writes is already expired.
    """

    CEILING = 0

    def test_the_written_span_is_expired_on_arrival(self) -> None:
        rc = main(self._argv("--reason", "code-review", "--expected-minutes", "600"))
        self.assertEqual(rc, ExitCode.OK)
        span = self._span()
        assert span is not None
        self.assertEqual(span["started_at"], span["expires_at"])
        self.assertFalse(
            st.quiet_span_active({"quiet_span": span}, _dt.datetime.now(_dt.UTC)),
            "ceiling 0 must shorten silence to nothing, never remove the bound",
        )


class _TickDriveCase(CluTestCase):
    """A live plan store with a claimed phase and a hand-driven clock.

    Every sample these subclasses assert on was written by a real `tick()`
    from a real `ps` snapshot, and every `worker_idle` assertion is read off
    the EMITTED EVENT LOG rather than off the predicate — the predicate
    answering correctly proves nothing about whether the operator hears
    anything.
    """

    TICKS = 25
    CADENCE = 30.0
    CEILING = 45

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "proj"
        (self.project / "plans").mkdir(parents=True)
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.cfg = ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command="echo {phase_id}"),
            worker_quiet_span_ceiling_minutes=self.CEILING,
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

    def _declare(self, minutes: int, reason: str = "code-review") -> None:
        """Write a span through the real domain builder on the pinned clock."""
        plan_store.op_stamp_claim_fields(
            self.orch_dir,
            "test-plan",
            token=self.token,
            phase="a",
            fields={
                "quiet_span": st.build_quiet_span(
                    reason,
                    minutes,
                    self.clock.now,
                    ceiling_minutes=self.cfg.worker_quiet_span_ceiling_minutes,
                )
            },
        )

    def _one_tick(self) -> None:
        snapshot = _tree_snapshot(self.pid, root_cpu=100.0)
        with mutate_state(self.state_path) as data:
            data["current_claim"]["last_heartbeat_at"] = _stamp(self.clock.now)
        with mock.patch.object(supervisor, "capture_ps_snapshot", lambda _s=snapshot: _s):
            supervisor.tick(self.state_path, self.cfg)
        self.clock.advance(self.CADENCE)

    def _drive(self) -> None:
        with (
            mock.patch.object(st, "_now_utc", self.clock),
            mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True),
        ):
            for _ in range(self.TICKS):
                self._one_tick()

    def _dump(self) -> dict:
        """What `clu state dump` prints — the operator's own view of the claim."""
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
        return [e for e in self._dump()["events"] if e["type"] == st.EVENT_WORKER_IDLE]


class DeclaredSpanSuppressesTestCase(_TickDriveCase):
    """This phase's gate, both halves, read off the emitted event log.

    The drive is 25 ticks at a 30s cadence — 12 minutes of dormant worker,
    comfortably past the 10-minute idle window — so the SAME drive that goes
    silent under an open span must alert under an expired one. Anything less
    than both halves proves only that a silence switch was installed.
    """

    def test_an_open_span_holds_the_alert(self) -> None:
        self._declare(30)
        self._drive()
        self.assertEqual(self._idle_events(), [])

    def test_an_expired_span_lets_the_watchdog_speak(self) -> None:
        # Five minutes of declared quiet, twelve minutes of actual silence.
        self._declare(5)
        self._drive()
        events = self._idle_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["phase"], "a")

    def test_the_same_drive_with_no_span_alerts(self) -> None:
        # The control: without any declaration this worker is reported, so the
        # first case above is measuring the span and not the fixture.
        self._drive()
        self.assertEqual(len(self._idle_events()), 1)

    def test_a_span_the_worker_never_ended_still_expires(self) -> None:
        # The design's whole point. Nothing calls `--end` here — the worker
        # declared a review and then went silent for good. A pair-shaped
        # suppression would stay open for ever; the lease expires on its own
        # clock and the watchdog reports.
        #
        # The worker's PID stays alive throughout on purpose: a process that
        # actually exits is caught one rule earlier by the dead-PID release, so
        # "alive but wedged inside its own declared span" is the only shape in
        # which this expiry is observable at all.
        self._declare(5)
        self._drive()
        self.assertEqual(len(self._idle_events()), 1)
        # And the span is still on the claim — nothing swept it. It simply
        # stopped being believed, the same shape as the activity marker's bound.
        self.assertIsNotNone(self._claim().get("quiet_span"))

    def test_an_ended_span_stops_suppressing_immediately(self) -> None:
        # `--end` is a courtesy that can only SHORTEN the silence.
        self._declare(30)
        plan_store.op_stamp_claim_fields(
            self.orch_dir,
            "test-plan",
            token=self.token,
            phase="a",
            fields={"quiet_span": None},
        )
        self._drive()
        self.assertEqual(len(self._idle_events()), 1)


class ZeroCeilingGrantsNoSilenceTestCase(_TickDriveCase):
    """`worker_quiet_span_ceiling_minutes = 0`, driven end to end.

    The safe direction for this knob is the one that SHORTENS silence, so an
    operator who sets it to zero must get a watchdog that still speaks. The
    unsafe reading — 0 means "no ceiling" — would produce silence here, and
    the failure would look exactly like the passing case from the predicate's
    side. Two consecutive phases of this plan shipped that inversion, so it is
    asserted where an operator would notice it: the event log.
    """

    CEILING = 0

    def test_a_declared_span_does_not_silence_the_watchdog(self) -> None:
        self._declare(600)
        self._drive()
        self.assertEqual(len(self._idle_events()), 1)


class SpanIsCompareAndSetTestCase(_TickDriveCase):
    """`quiet_span` is re-asserted inside the idle watchdog's apply.

    A declaration landing between the tick's decision and its write means the
    worker just told us it is quiet on purpose, so the emit decided against
    the old value is void. Without the precondition the declaration loses a
    race it exists to win, and the operator gets the false alarm anyway.
    """

    def _seed_samples(self, count: int, *, step_seconds: float = 30.0) -> None:
        """A contiguous flat-CPU history ending ~now, written onto the claim."""
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

    def test_worker_idle_emit_is_discarded_when_a_span_lands_mid_tick(self) -> None:
        self._seed_samples(21)
        with mock.patch.object(st, "claim_worker_alive", lambda *a, **k: True):
            data, observed = plan_store.snapshot_with_preconditions(
                self.orch_dir, "test-plan"
            )
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
            # The declaration the worker made while the tick was thinking.
            self.clock.now = _dt.datetime.now(_dt.UTC)
            self._declare(20)
            with self.assertRaises(plan_store.TickConflict):
                plan_store.apply_tick_delta(self.orch_dir, "test-plan", delta.pre, delta)
        self.assertEqual(self._idle_events(), [])

    def test_stuck_tool_emit_is_also_discarded_by_a_mid_tick_span(self) -> None:
        # `quiet_span` does not SUPPRESS stuck-tool detection — a declared span
        # says the worker expects to be quiet, not that its subprocess may
        # hang. The field joins this watchdog's precondition set anyway,
        # because the two watchdogs share one contract over the fields that
        # suppress either of them, and a set one maintains while the other
        # does not is one nobody can reason about. The cost is bounded: no
        # dedup marker is written, so the next tick re-derives and emits.
        with mutate_state(self.state_path) as data:
            data["current_claim"]["active_tool_started_at"] = _stamp(
                _dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=605)
            )
        data, observed = plan_store.snapshot_with_preconditions(
            self.orch_dir, "test-plan"
        )
        delta = TickDelta(observed=observed)
        _emit_stuck_tool(data, self.cfg, delta=delta, ps_output=PS_WEDGED_BUILD)
        self.assertTrue(
            [e for e in delta.events if e["type"] == st.EVENT_TOOL_STUCK],
            "fixture never reached the emit, so the precondition is untested",
        )
        self.clock.now = _dt.datetime.now(_dt.UTC)
        self._declare(20)
        with self.assertRaises(plan_store.TickConflict):
            plan_store.apply_tick_delta(self.orch_dir, "test-plan", delta.pre, delta)


if __name__ == "__main__":
    unittest.main()
