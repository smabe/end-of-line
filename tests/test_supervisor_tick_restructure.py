"""The tick's I/O shape: snapshot → detect (unlocked) → precondition-guarded apply.

Four things are pinned here, and each one is a property the old shape did NOT
have:

1. **No subprocess runs inside a transaction.** The tick used to hold the state
   lock across `ps`, `lsof` and a reap that polls for seconds. Under one
   project-wide write lock that starves every callback in the project, so a SQL
   trace asserts every seam fires outside the BEGIN..COMMIT window.
2. **A concurrent write discards the tick, and nothing half-lands.** The
   preconditions are re-asserted inside the write transaction; a mismatch is
   `[idle] concurrent_write`, the other writer's work is untouched, and the
   next tick performs the action.
3. **The guard is SELECTIVE, not blunt.** A mid-tick heartbeat must NOT abort a
   lease-expiry apply (it judged the lease) but MUST abort a stalled emit (it
   judged the heartbeat). A guard that fails the first half is a whole-plan
   version counter wearing a costume.
4. **Detection writes nothing the delta does not carry.** Every mutation the
   five detection paths make to the snapshot is diffed against the delta, so a
   helper that still edits the dict in place and forgets the delta fails here
   instead of silently dropping an event in production.
"""

from __future__ import annotations

import copy
import datetime as _dt
import subprocess
import unittest
from contextlib import contextmanager
from typing import Any
from unittest import mock

from end_of_line import db, inbox, plan_store, quota, supervisor
from end_of_line import state as st
from end_of_line.config import DispatchSpec, ProjectConfig
from end_of_line.supervisor import (
    TickDelta,
    _detect_stalled,
    _emit_stalled_claim_notify,
    _emit_stuck_blocker_repings,
    _emit_stuck_tool,
    _emit_worker_idle,
    tick,
)
from tests import CluTestCase, utcnow_minus

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
| b | `test-plan-b.md` | thing | 1h |
"""

# `ps -eo pid,ppid,etime,time,command` with one long-lived, low-CPU descendant.
PS_WEDGED = (
    "  PID  PPID     ELAPSED        TIME COMMAND\n"
    "78233     1    02:00:00    00:00:10 claude --print\n"
    "81681 78233    00:12:00    00:00:01 xcodebuild -scheme App test\n"
)


def _iso_in(minutes: float) -> str:
    return (_dt.datetime.now(_dt.UTC) + _dt.timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class TickRestructureTestCase(CluTestCase):
    """One plan, one project, seeded straight into the store."""

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

    # -- fixtures --------------------------------------------------------------

    def _read(self) -> dict:
        return plan_store.snapshot(self.orch_dir, "test-plan")

    def _seed_claim(self, **over: Any) -> dict:
        claim = {
            "phase_id": "a",
            "claimed_by": "session-live",
            "lease_expires": _iso_in(30),
            "started_at": utcnow_minus(3600),
            "last_heartbeat_at": utcnow_minus(60),
            "attempts": 1,
        }
        claim.update(over)
        with plan_store.mutate_compat(self.orch_dir, "test-plan") as data:
            data["current_claim"] = claim
        return claim

    # -- 1. no subprocess inside a transaction --------------------------------

    @contextmanager
    def _sql_trace(self):
        """Record every SQL statement and every subprocess seam, in order.

        The seams are marked rather than executed: `walk_worker_tree`, the CPU
        `ps`, the `lsof` probe and the group reap each drop a `SEAM:` line into
        the same list the trace callback writes to, so "did this shell out
        while a transaction was open" becomes a question about one ordered log.
        """
        log: list[str] = []
        real_connect = db.connect

        def _traced(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(lambda sql: log.append(f"SQL:{' '.join(sql.split())}"))
            return conn

        def _seam(name):
            def _fn(*_a, **_kw):
                log.append(f"SEAM:{name}")
                return _SEAM_RETURNS[name]

            return _fn

        _SEAM_RETURNS = {
            "walk_worker_tree": [],
            "reap_orphan_pgroup": st.ReapResult(
                signaled=None, cmdline_mismatch=False, escalated_kill=False
            ),
            "subprocess.run": subprocess.CompletedProcess([], 0, "", ""),
        }
        with (
            mock.patch.object(db, "connect", _traced),
            mock.patch.object(supervisor, "walk_worker_tree", _seam("walk_worker_tree")),
            mock.patch.object(st, "reap_orphan_pgroup", _seam("reap_orphan_pgroup")),
            mock.patch.object(supervisor.subprocess, "run", _seam("subprocess.run")),
        ):
            yield log

    def _seams_inside_transactions(self, log: list[str]) -> list[str]:
        """Every seam that fired while a BEGIN had not yet been closed."""
        depth = 0
        offenders: list[str] = []
        for line in log:
            if line.startswith("SQL:"):
                stmt = line[4:].upper()
                if stmt.startswith("BEGIN"):
                    depth += 1
                elif stmt.startswith("COMMIT") or stmt.startswith("ROLLBACK"):
                    depth = max(depth - 1, 0)
            elif depth:
                offenders.append(line)
        return offenders

    def test_no_subprocess_seam_runs_inside_an_open_transaction(self) -> None:
        # Two fixtures, because the seams are mutually exclusive in one tick:
        # the stuck-tool walk needs an active Bash window, the worker-idle
        # `ps`/`lsof` probes need the absence of one. Both leases are expired,
        # so both ticks also reap AFTER committing.
        cases = [
            (
                "active tool",
                {"active_tool_started_at": utcnow_minus(900)},
                "SEAM:walk_worker_tree",
            ),
            ("idle worker", {}, "SEAM:subprocess.run"),
        ]
        for label, extra, expected_seam in cases:
            with self.subTest(worker=label):
                self.setUp()
                self._seed_claim(
                    lease_expires="2020-01-01T00:00:00Z",
                    pid=78233,
                    pgid=78233,
                    **extra,
                )
                with self._sql_trace() as log:
                    result = tick(self.state_path, self.cfg)
                self.assertEqual(result.action, "lease_expired")
                seams = [line for line in log if line.startswith("SEAM:")]
                # Non-vacuity: a fixture that shelled out nowhere would pass an
                # "in a transaction" check without proving anything.
                self.assertIn(expected_seam, seams)
                self.assertIn("SEAM:reap_orphan_pgroup", seams)
                self.assertEqual(
                    self._seams_inside_transactions(log),
                    [],
                    f"a subprocess ran inside a transaction; trace: {log}",
                )

    def test_the_reap_runs_after_the_commit(self) -> None:
        # Durable state first, best-effort reap last: by the time the group is
        # signaled the release is already readable by any other process.
        self._seed_claim(lease_expires="2020-01-01T00:00:00Z", pid=78233, pgid=78233)
        seen: list[dict | None] = []

        def _reap(pgid, cmdline_match=None):
            seen.append(self._read()["current_claim"])
            return st.ReapResult(signaled=st.SIGNAL_TERM, cmdline_mismatch=False, escalated_kill=False)

        with (
            mock.patch.object(st, "reap_orphan_pgroup", _reap),
            mock.patch.object(supervisor, "walk_worker_tree", lambda *a, **k: []),
        ):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")
        self.assertEqual(seen, [None], "the reap ran before the release committed")
        # And the event it stamps landed, in its own transaction after the reap.
        types = [e["type"] for e in self._read()["events"]]
        self.assertIn(st.EVENT_PHASE_ORPHAN_REAPED, types)
        self.assertLess(
            types.index(st.EVENT_LEASE_EXPIRED),
            types.index(st.EVENT_PHASE_ORPHAN_REAPED),
        )

    # -- 2. a concurrent write discards the tick -------------------------------

    def test_a_callback_between_snapshot_and_apply_discards_the_tick(self) -> None:
        self._seed_claim(pid=999999, pgid=999999)

        def _dead_worker_but_a_callback_lands_first(claim, cmdline_match=None):
            # A `clu complete` commits while the tick is still deciding.
            plan_store.op_release_claim(
                self.orch_dir,
                "test-plan",
                token="session-live",
                phase="a",
                events=[{"ts": st.utcnow(), "type": st.EVENT_PHASE_COMPLETED, "phase": "a"}],
            )
            return False

        with mock.patch.object(st, "claim_worker_alive", _dead_worker_but_a_callback_lands_first):
            result = tick(self.state_path, self.cfg)

        self.assertEqual(str(result), "[idle] concurrent_write")
        self.assertEqual(result.side_notifies, [], "a discarded tick still notified")
        data = self._read()
        # The callback's write is intact and the tick's is nowhere.
        self.assertIsNone(data["current_claim"])
        types = [e["type"] for e in data["events"]]
        self.assertIn(st.EVENT_PHASE_COMPLETED, types)
        self.assertNotIn(st.EVENT_PHASE_WORKER_DEAD, types)

        # And the next tick does the work the discarded one could not.
        follow_up = tick(self.state_path, self.cfg)
        self.assertEqual(follow_up.action, "dispatch")
        self.assertEqual(follow_up.phase_id, "b")

    # -- 3. the guard is selective, not blunt ----------------------------------

    def _land_worker_writes(self) -> None:
        """A heartbeat and an activity stamp, exactly as a live worker sends."""
        plan_store.op_heartbeat(self.orch_dir, "test-plan", token="session-live", phase="a")
        plan_store.op_activity(
            self.orch_dir,
            "test-plan",
            token="session-live",
            phase="a",
            action="start",
        )

    def test_a_mid_tick_heartbeat_spares_a_lease_release_but_kills_a_stalled_emit(
        self,
    ) -> None:
        # Direction one: the lease-expiry decision judged `lease_expires`, which
        # neither a heartbeat nor an activity stamp touches — so it COMMITS.
        self._seed_claim(lease_expires="2020-01-01T00:00:00Z")

        def _classify(_log_path):
            self._land_worker_writes()
            return None

        with mock.patch.object(quota, "classify_log_tail", _classify):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(
            result.action,
            "lease_expired",
            "a heartbeat aborted a decision that never looked at the heartbeat",
        )
        self.assertIsNone(self._read()["current_claim"])

        # Direction two: the stalled emit judged `last_heartbeat_at`, so the
        # same heartbeat MUST abort it.
        self._seed_claim(
            last_heartbeat_at=utcnow_minus(7200),
            started_at=utcnow_minus(9000),
            pid=78233,
        )

        def _alive_but_a_heartbeat_lands(claim, cmdline_match=None):
            self._land_worker_writes()
            return True

        with (
            mock.patch.object(st, "claim_worker_alive", _alive_but_a_heartbeat_lands),
            mock.patch.object(supervisor, "walk_worker_tree", lambda *a, **k: []),
            mock.patch.object(
                supervisor.subprocess,
                "run",
                lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""),
            ),
        ):
            stalled_result = tick(self.state_path, self.cfg)
        self.assertEqual(str(stalled_result), "[idle] concurrent_write")
        types = [e["type"] for e in self._read()["events"]]
        self.assertNotIn(st.EVENT_PHASE_STALLED, types)

    # -- 4. detection writes nothing the delta does not carry ------------------

    def _diff_into_delta(self, before: dict, after: dict, delta: TickDelta) -> None:
        """Every snapshot mutation a detection helper made is in the delta."""
        self.assertEqual(
            after["events"][len(before["events"]) :],
            delta.events,
            "an event reached the snapshot but not the delta",
        )
        old_claim = before.get("current_claim") or {}
        new_claim = after.get("current_claim") or {}
        changed = {
            k for k in set(old_claim) | set(new_claim) if old_claim.get(k) != new_claim.get(k)
        }
        self.assertEqual(
            changed,
            set(delta.claim_updates),
            "a claim field reached the snapshot but not the delta",
        )
        for old_b, new_b in zip(before["blockers"], after["blockers"], strict=True):
            moved = {k for k in set(old_b) | set(new_b) if old_b.get(k) != new_b.get(k)}
            self.assertEqual(
                moved,
                set(delta.blocker_updates.get(new_b["id"], {})),
                "a blocker field reached the snapshot but not the delta",
            )

    def _run_case(self, name: str, data: dict) -> tuple[TickDelta, list[tuple[str, str]]]:
        delta = TickDelta()
        side: list[tuple[str, str]] = []
        before = copy.deepcopy(data)
        if name == "stalled_claim":
            _emit_stalled_claim_notify(data, self.cfg, side, delta=delta)
        elif name == "stuck_blocker":
            _emit_stuck_blocker_repings(data, self.cfg, side, delta=delta)
        elif name == "stuck_tool":
            with mock.patch.object(
                supervisor,
                "walk_worker_tree",
                lambda *a, **k: supervisor._parse_ps_output(PS_WEDGED)[1:],
            ):
                _emit_stuck_tool(data, self.cfg, delta=delta)
        elif name == "worker_idle":
            with mock.patch.object(supervisor, "walk_worker_tree", lambda *a, **k: []):
                _emit_worker_idle(
                    data,
                    self.cfg,
                    side,
                    delta=delta,
                    ps_output="0.2\n",
                    lsof_output="nothing",
                )
        elif name == "detect_stalled":
            _detect_stalled(data, delta=delta)
        else:  # pragma: no cover - guards a typo in the table below
            raise AssertionError(name)
        self._diff_into_delta(before, data, delta)
        return delta, side

    def test_each_detection_path_emits_its_events_and_nothing_escapes_the_delta(
        self,
    ) -> None:
        cases = [
            ("stalled_claim", _state_stalled_claim(), [st.EVENT_STALLED_CLAIM_NOTIFIED], 1),
            ("stuck_blocker", _state_stuck_blocker(), [st.EVENT_STUCK_BLOCKER_REPINGED], 1),
            ("stuck_tool", _state_stuck_tool(), [st.EVENT_TOOL_STUCK], 0),
            ("worker_idle", _state_worker_idle(), [st.EVENT_WORKER_IDLE], 1),
            ("detect_stalled", _state_detect_stalled(), [st.EVENT_PHASE_STALLED], 0),
        ]
        for name, data, expected_events, expected_notifies in cases:
            with self.subTest(path=name):
                delta, side = self._run_case(name, data)
                # Non-vacuity first: a path that silently stopped emitting would
                # otherwise agree with an empty expectation.
                self.assertTrue(delta.events, f"{name} emitted nothing")
                self.assertEqual([e["type"] for e in delta.events], expected_events)
                self.assertEqual(len(side), expected_notifies)

    def test_the_inbox_event_is_raised_not_written_during_detection(self) -> None:
        # The dedup markers commit with the apply, so an inbox note written
        # during detection would re-appear on every retry of a discarded tick.
        data = _state_stalled_claim()
        delta = TickDelta()
        _emit_stalled_claim_notify(data, self.cfg, [], delta=delta)
        self.assertEqual(inbox.read_unprocessed(), [])
        self.assertEqual([e["type"] for e in delta.inbox_events], ["stalled_claim"])

    # -- the dispatch delta -----------------------------------------------------

    def test_the_start_event_carries_no_attempts_key(self) -> None:
        # `clu watch` prints "attempt 1" for every attempt and the golden froze
        # that; a delta built from the claim's own fields must not gain the
        # attempt count the file engine's event never had.
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "dispatch")
        data = self._read()
        started = [e for e in data["events"] if e["type"] == st.EVENT_PHASE_STARTED]
        self.assertEqual(len(started), 1)
        self.assertNotIn("attempts", started[0])
        self.assertEqual(started[0]["claimed_by"], data["current_claim"]["claimed_by"])
        self.assertEqual(result.token, data["current_claim"]["claimed_by"])

    def test_the_claim_records_the_attempt_the_event_log_counts(self) -> None:
        # `claim_phase`'s raw attempt counter, preserved across the restructure:
        # the apply has no history to count, so the tick counts it.
        tick(self.state_path, self.cfg)  # attempt 1 on phase a
        with plan_store.mutate_compat(self.orch_dir, "test-plan") as data:
            data["current_claim"] = None
        tick(self.state_path, self.cfg)  # attempt 2 on phase a
        self.assertEqual(self._read()["current_claim"]["attempts"], 2)

    def test_an_idle_tick_with_nothing_to_write_opens_no_transaction(self) -> None:
        # A plan with a healthy worker is visited every 30s; the visit is a
        # read, so it must not queue behind (or ahead of) that worker's writes.
        self._seed_claim()
        opened: list[str] = []
        real = db.write_txn

        @contextmanager
        def _counting(conn, **kwargs):
            opened.append("write")
            with real(conn, **kwargs) as cur:
                yield cur

        with mock.patch.object(db, "write_txn", _counting):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "idle")
        self.assertEqual(opened, [])

    def test_the_tick_never_nests_a_write_transaction(self) -> None:
        # The premise behind removing `write_txn`'s join: with detection outside
        # the transaction, the quota gate's own write no longer sits inside the
        # tick's, and nothing else in a tick does either.
        from tests import seed_quota_pause

        seed_quota_pause(self.orch_dir, paused_until="2020-01-01T00:00:00Z")
        depth = 0
        nested: list[str] = []
        real = db.write_txn

        @contextmanager
        def _counting(conn, **kwargs):
            nonlocal depth
            if depth:
                nested.append("nested write_txn")
            depth += 1
            try:
                with real(conn, **kwargs) as cur:
                    yield cur
            finally:
                depth -= 1

        with mock.patch.object(db, "write_txn", _counting):
            result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "dispatch")
        self.assertEqual(nested, [])


class ApplyTickDeltaTestCase(CluTestCase):
    """`apply_tick_delta` on its own: all-or-nothing under every precondition."""

    def setUp(self) -> None:
        super().setUp()
        self.orch_dir = self.tmp_path / "orch"
        self.orch_dir.mkdir()
        data = st.empty_state("p", "plans")
        data["events"].append({"ts": st.utcnow(), "type": st.EVENT_QUEUE_POPPED})
        plan_store.create(self.orch_dir, data)

    def _snapshot(self):
        return plan_store.snapshot_with_preconditions(self.orch_dir, "p")

    def test_a_conflict_writes_nothing_at_all(self) -> None:
        data, observed = self._snapshot()
        delta = TickDelta(observed=observed)
        delta.require_event_log()
        delta.status = st.STATUS_HALTED
        delta.events.append({"ts": st.utcnow(), "type": st.EVENT_PHASE_MAX_ATTEMPTS})
        # Somebody else appends first.
        plan_store.op_append_events(
            self.orch_dir, "p", [{"ts": st.utcnow(), "type": st.EVENT_PAUSED}]
        )
        with self.assertRaises(plan_store.TickConflict):
            plan_store.apply_tick_delta(self.orch_dir, "p", delta.pre, delta)
        after = plan_store.snapshot(self.orch_dir, "p")
        self.assertEqual(after["status"], st.STATUS_RUNNING)
        self.assertEqual(
            [e["type"] for e in after["events"]],
            [st.EVENT_QUEUE_POPPED, st.EVENT_PAUSED],
        )

    def test_an_unjudged_fact_is_not_re_asserted(self) -> None:
        # The whole point of preconditions over a version counter: a change to
        # something this decision never read does not abort it.
        data, observed = self._snapshot()
        delta = TickDelta(observed=observed)
        delta.status = st.STATUS_PAUSED
        delta.events.append({"ts": st.utcnow(), "type": st.EVENT_PAUSED})
        plan_store.op_append_events(
            self.orch_dir, "p", [{"ts": st.utcnow(), "type": st.EVENT_RESUMED}]
        )
        plan_store.apply_tick_delta(self.orch_dir, "p", delta.pre, delta)
        self.assertEqual(plan_store.snapshot(self.orch_dir, "p")["status"], st.STATUS_PAUSED)

    def test_the_claim_field_precondition_advances_the_column_it_names(self) -> None:
        # Backdated first, so "the field moved" is a real observation rather
        # than a comparison of a second-resolution timestamp with itself.
        with plan_store.mutate_compat(self.orch_dir, "p") as live:
            live["current_claim"] = {
                "phase_id": "a",
                "claimed_by": "tok",
                "lease_expires": _iso_in(30),
                "started_at": "2020-01-01T00:00:00Z",
                "last_heartbeat_at": "2020-01-01T00:00:00Z",
            }
        _, observed = self._snapshot()
        delta = TickDelta(observed=observed)
        delta.require_claim_field("last_heartbeat_at")
        delta.claim_token = "tok"
        delta.claim_updates["stalled_notified"] = True
        plan_store.apply_tick_delta(self.orch_dir, "p", delta.pre, delta)
        claim = plan_store.snapshot(self.orch_dir, "p")["current_claim"]
        self.assertIs(claim["stalled_notified"], True)
        # The neighbouring column is untouched by the marker write.
        self.assertEqual(claim["last_heartbeat_at"], "2020-01-01T00:00:00Z")

        # Now a heartbeat lands first and the same delta is refused.
        _, observed2 = self._snapshot()
        stamped = plan_store.op_heartbeat(self.orch_dir, "p", token="tok", phase="a")
        self.assertNotEqual(stamped, "2020-01-01T00:00:00Z")
        blocked = TickDelta(observed=observed2)
        blocked.require_claim_field("last_heartbeat_at")
        blocked.events.append({"ts": st.utcnow(), "type": st.EVENT_PHASE_STALLED})
        with self.assertRaises(plan_store.TickConflict):
            plan_store.apply_tick_delta(self.orch_dir, "p", blocked.pre, blocked)
        after = plan_store.snapshot(self.orch_dir, "p")
        self.assertNotIn(st.EVENT_PHASE_STALLED, [e["type"] for e in after["events"]])
        self.assertEqual(after["current_claim"]["last_heartbeat_at"], stamped)

    def test_a_missing_plan_still_raises_file_not_found(self) -> None:
        delta = TickDelta()
        delta.events.append({"ts": st.utcnow(), "type": st.EVENT_PAUSED})
        with self.assertRaises(FileNotFoundError):
            plan_store.apply_tick_delta(self.orch_dir, "gone", delta.pre, delta)


# --- seeded snapshots for the parity table -----------------------------------
#
# Plain dicts rather than store round-trips: these five helpers are called with
# no transaction anywhere near them, which is the point being tested.


def _state_stalled_claim() -> dict:
    data = st.empty_state("test-plan", "plans")
    data["current_claim"] = {
        "phase_id": "a",
        "claimed_by": "session-x",
        "lease_expires": "2020-01-01T00:00:00Z",
        "started_at": utcnow_minus(9000),
        "last_heartbeat_at": utcnow_minus(60),
    }
    return data


def _state_stuck_blocker() -> dict:
    data = st.empty_state("test-plan", "plans")
    data["blockers"] = [
        {
            "id": "q-1",
            "phase_id": "a",
            "type": st.BLOCKER_INPUT,
            "question": "which?",
            "options": ["x", "y"],
            "context": "",
            "asked_at": utcnow_minus(7200),
            "answer": None,
            "answered_at": None,
        }
    ]
    return data


def _state_stuck_tool() -> dict:
    data = st.empty_state("test-plan", "plans")
    data["current_claim"] = {
        "phase_id": "a",
        "claimed_by": "session-x",
        "lease_expires": _iso_in(30),
        "started_at": utcnow_minus(9000),
        "last_heartbeat_at": utcnow_minus(60),
        "pid": 78233,
        "active_tool_started_at": utcnow_minus(900),
    }
    return data


def _state_worker_idle() -> dict:
    data = st.empty_state("test-plan", "plans")
    base = _dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=12)
    data["current_claim"] = {
        "phase_id": "a",
        "claimed_by": "session-x",
        "lease_expires": _iso_in(30),
        "started_at": utcnow_minus(9000),
        "last_heartbeat_at": utcnow_minus(60),
        "pid": 78233,
        "cpu_samples": [
            {"ts": (base + _dt.timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "cpu": 0.3}
            for i in range(6)
        ],
    }
    return data


def _state_detect_stalled() -> dict:
    data = st.empty_state("test-plan", "plans")
    data["current_claim"] = {
        "phase_id": "a",
        "claimed_by": "session-x",
        "lease_expires": _iso_in(30),
        "started_at": utcnow_minus(9000),
        "last_heartbeat_at": utcnow_minus(7200),
    }
    return data


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
