"""Contract tests for the native write ops — the row-level half of the store.

`mutate_compat` reads a whole plan and writes a whole plan back. These ops do
one thing each, inside one `BEGIN IMMEDIATE`, touching only the rows that
change. What they pin: the token check is the WHERE clause (so a stale worker
loses the race instead of winning it), every op bumps `plans.version` (the hint
dashboards poll on), and a heartbeat never reads or rewrites the event log.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import threading
import unittest
from pathlib import Path

from end_of_line import db, plan_store
from end_of_line import state as st
from tests import CluTestCase


def _seed(orch_dir: Path, slug: str = "p1") -> dict:
    data = st.empty_state(slug, "plans")
    plan_store.create(orch_dir, data)
    return data


def _claim(orch_dir: Path, slug: str = "p1", phase: str = "a") -> str:
    with plan_store.mutate_compat(orch_dir, slug) as data:
        return st.claim_phase(data, phase, lease_minutes=30)


# Distinctly not "now", and distinctly not any value an op will write.
AGED = "2020-01-01T00:00:00Z"


def _age_claim(orch_dir: Path, slug: str = "p1") -> None:
    """Backdate BOTH claim timestamps so a later write is DISTINGUISHABLE.

    `st.utcnow()` is second-resolution: three calls in a row return the same
    string. A test that claims a phase and immediately asserts
    `claim["last_heartbeat_at"] == op(...)` therefore compares a value to
    itself and passes whatever the op wrote — including nothing, and including
    the wrong column. Backdating first is what turns that assertion back into a
    real one, and backdating `started_at` too is what lets a test prove the op
    wrote the intended column rather than its neighbour.
    """
    conn = db.connect(db.project_db_path(orch_dir))
    try:
        with db.write_txn(conn) as cur:
            cur.execute(
                "UPDATE claims SET last_heartbeat_at = ?, started_at = ? WHERE plan_slug = ?",
                (AGED, AGED, slug),
            )
    finally:
        conn.close()


def _version(orch_dir: Path, slug: str = "p1") -> int:
    conn = db.connect(db.project_db_path(orch_dir))
    try:
        row = conn.execute("SELECT version FROM plans WHERE slug = ?", (slug,)).fetchone()
        return int(row[0])
    finally:
        conn.close()


def _count(orch_dir: Path, table: str, slug: str = "p1") -> int:
    conn = db.connect(db.project_db_path(orch_dir))
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE plan_slug = ?", (slug,)).fetchone()
        return int(row[0])
    finally:
        conn.close()


class OpsTestCase(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.orch = self.tmp_path / "proj" / "plans" / ".orchestrator"
        _seed(self.orch)


class HeartbeatOpTests(OpsTestCase):
    def test_stamps_the_claim_and_returns_the_timestamp(self):
        token = _claim(self.orch)
        # Backdate first: claiming and beating inside the same second makes
        # `claim["last_heartbeat_at"] == ts` true before the op runs at all.
        _age_claim(self.orch)
        ts = plan_store.op_heartbeat(self.orch, "p1", token=token, phase="a")
        claim = plan_store.snapshot(self.orch, "p1")["current_claim"]
        self.assertNotEqual(claim["last_heartbeat_at"], AGED, "the heartbeat wrote nothing")
        self.assertEqual(claim["last_heartbeat_at"], ts)
        # And it wrote THAT column: `started_at` is the neighbour a wrong-column
        # UPDATE would land in, and it must be untouched.
        self.assertEqual(claim["started_at"], AGED)


    def test_writes_no_event(self):
        token = _claim(self.orch)
        before = _count(self.orch, "events")
        plan_store.op_heartbeat(self.orch, "p1", token=token, phase="a")
        self.assertEqual(_count(self.orch, "events"), before)

    def test_bumps_the_plan_version(self):
        token = _claim(self.orch)
        before = _version(self.orch)
        plan_store.op_heartbeat(self.orch, "p1", token=token, phase="a")
        self.assertEqual(_version(self.orch), before + 1)

    def test_wrong_token_raises_claim_mismatch(self):
        _claim(self.orch)
        with self.assertRaises(st.ClaimMismatch) as ctx:
            plan_store.op_heartbeat(self.orch, "p1", token="session-nope", phase="a")
        self.assertIn("token mismatch", str(ctx.exception))

    def test_wrong_phase_raises_claim_mismatch(self):
        token = _claim(self.orch)
        with self.assertRaises(st.ClaimMismatch) as ctx:
            plan_store.op_heartbeat(self.orch, "p1", token=token, phase="b")
        self.assertIn("phase mismatch", str(ctx.exception))

    def test_no_claim_raises_claim_mismatch(self):
        with self.assertRaises(st.ClaimMismatch) as ctx:
            plan_store.op_heartbeat(self.orch, "p1", token="session-nope", phase="a")
        self.assertEqual(str(ctx.exception), "no active claim")

    def test_a_rejected_heartbeat_bumps_nothing(self):
        _claim(self.orch)
        before = _version(self.orch)
        with self.assertRaises(st.ClaimMismatch):
            plan_store.op_heartbeat(self.orch, "p1", token="session-nope", phase="a")
        self.assertEqual(_version(self.orch), before)

    def test_missing_plan_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            plan_store.op_heartbeat(self.orch, "absent", token="t", phase="a")

    def test_sql_trace_is_one_transaction_and_never_reads_events(self):
        # The whole point of the phase: a heartbeat used to rewrite the entire
        # event history. Trace every statement the op issues and prove the
        # shape — one BEGIN IMMEDIATE, one claims UPDATE, one version bump,
        # and not a single statement naming `events`.
        token = _claim(self.orch)
        statements: list[str] = []
        real_connect = db.connect

        def tracing_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        db.connect = tracing_connect  # pyright: ignore[reportAttributeAccessIssue]
        try:
            plan_store.op_heartbeat(self.orch, "p1", token=token, phase="a")
        finally:
            db.connect = real_connect  # pyright: ignore[reportAttributeAccessIssue]

        sql = [s.strip().upper() for s in statements]
        self.assertEqual([s for s in sql if s.startswith("BEGIN")], ["BEGIN IMMEDIATE"])
        self.assertEqual(len([s for s in sql if s.startswith("UPDATE CLAIMS")]), 1)
        self.assertEqual(len([s for s in sql if s.startswith("UPDATE PLANS SET VERSION")]), 1)
        self.assertEqual([s for s in sql if "EVENTS" in s], [])
        self.assertEqual(len([s for s in sql if s.startswith("COMMIT")]), 1)


def _heartbeat_worker(orch: str, token: str, count: int, out, gate) -> None:
    """Child process: N heartbeats through the real op, no test seams."""
    from end_of_line import plan_store as ps

    ok = 0
    gate.wait(30)
    for _ in range(count):
        try:
            ps.op_heartbeat(Path(orch), "p1", token=token, phase="a")
            ok += 1
        except BaseException as exc:  # pragma: no cover - reported below
            out.put(f"{type(exc).__name__}: {exc}")
            return
    out.put(f"ok:{ok}")


def _mutate_worker(orch: str, count: int, out, gate) -> None:
    """Child process: N whole-state compat cycles, the facade's shape."""
    from end_of_line import plan_store as ps
    from end_of_line import state as _st

    gate.wait(30)
    try:
        for _ in range(count):
            with ps.mutate_compat(Path(orch), "p1") as data:
                _st.append_event(data, "probe_tick")
        out.put("ok")
    except BaseException as exc:  # pragma: no cover - reported below
        out.put(f"{type(exc).__name__}: {exc}")


class OpsVersusFacadeTests(OpsTestCase):
    """Two PROCESSES, not two threads — the ops must survive a concurrent
    whole-state write from another clu (a tick) without losing a beat."""

    def test_heartbeats_are_not_lost_under_a_concurrent_mutate_compat(self):
        token = _claim(self.orch)
        # Backdate before the run. Without this the final assertion cannot
        # fail: `last_heartbeat_at` is already non-null and already carries
        # this second's timestamp, so a facade write-back that clobbered every
        # beat would leave a value indistinguishable from a surviving one.
        _age_claim(self.orch)
        ctx = mp.get_context("spawn")
        out = ctx.Queue()
        beats = 60
        cycles = 30
        # Both children wait on the barrier, so the two write patterns really
        # overlap instead of the second one starting after the first finished —
        # process startup is fast enough here to hide the contention otherwise.
        gate = ctx.Barrier(2)
        a = ctx.Process(target=_heartbeat_worker, args=(str(self.orch), token, beats, out, gate))
        b = ctx.Process(target=_mutate_worker, args=(str(self.orch), cycles, out, gate))
        a.start()
        b.start()
        a.join(120)
        b.join(120)
        results = [out.get(timeout=10) for _ in range(2)]
        self.assertEqual(sorted(results), sorted([f"ok:{beats}", "ok"]))
        # No heartbeat was lost: the facade's write-back rebuilt the claim from
        # a snapshot taken inside its own transaction, so the last beat stands.
        self.assertEqual(_count(self.orch, "events"), 1 + cycles)
        claim = plan_store.snapshot(self.orch, "p1")["current_claim"]
        self.assertNotEqual(
            claim["last_heartbeat_at"],
            AGED,
            "every beat was clobbered by the concurrent whole-state write-back",
        )


class ActivityOpTests(OpsTestCase):
    def test_start_stamps_and_end_clears(self):
        token = _claim(self.orch)
        self.assertTrue(
            plan_store.op_activity(self.orch, "p1", token=token, phase="a", action="start")
        )
        claim = plan_store.snapshot(self.orch, "p1")["current_claim"]
        self.assertIn("active_tool_started_at", claim)
        self.assertTrue(
            plan_store.op_activity(self.orch, "p1", token=token, phase="a", action="end")
        )
        claim = plan_store.snapshot(self.orch, "p1")["current_claim"]
        self.assertNotIn("active_tool_started_at", claim)

    def test_rejects_an_unknown_action(self):
        token = _claim(self.orch)
        with self.assertRaises(ValueError):
            plan_store.op_activity(self.orch, "p1", token=token, phase="a", action="sideways")

    def test_drops_the_stamp_on_contention_rather_than_waiting(self):
        token = _claim(self.orch)
        started = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with plan_store.mutate_compat(self.orch, "p1"):
                started.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(started.wait(5))
            self.assertFalse(
                plan_store.op_activity(
                    self.orch, "p1", token=token, phase="a", action="start", timeout_s=0.2
                )
            )
        finally:
            release.set()
            holder.join(5)
        claim = plan_store.snapshot(self.orch, "p1")["current_claim"]
        self.assertNotIn("active_tool_started_at", claim)

    def test_a_stale_token_still_raises(self):
        _claim(self.orch)
        with self.assertRaises(st.ClaimMismatch):
            plan_store.op_activity(self.orch, "p1", token="session-nope", phase="a", action="start")


class ClaimFieldOpTests(OpsTestCase):
    def test_stamps_columns_and_flags_together(self):
        token = _claim(self.orch)
        plan_store.op_stamp_claim_fields(
            self.orch,
            "p1",
            token=token,
            fields={"pid": 4242, "pgid": 4242, "log_path": "/tmp/a.log", "head_sha_at_claim": "ab"},
        )
        claim = plan_store.snapshot(self.orch, "p1")["current_claim"]
        self.assertEqual(claim["pid"], 4242)
        self.assertEqual(claim["log_path"], "/tmp/a.log")
        # No column of its own — round-trips through the flags catch-all.
        self.assertEqual(claim["head_sha_at_claim"], "ab")

    def test_a_stale_token_raises_and_writes_nothing(self):
        _claim(self.orch)
        with self.assertRaises(st.ClaimMismatch):
            plan_store.op_stamp_claim_fields(
                self.orch, "p1", token="session-nope", fields={"pid": 1}
            )
        self.assertNotIn("pid", plan_store.snapshot(self.orch, "p1")["current_claim"])

    def test_if_unset_guard_skips_the_second_call(self):
        token = _claim(self.orch)
        first = plan_store.op_stamp_claim_fields(
            self.orch,
            "p1",
            token=token,
            phase="a",
            fields={"heartbeat_loop_failing_notified": True},
            if_unset="heartbeat_loop_failing_notified",
            events=[{"ts": st.utcnow(), "type": st.EVENT_HEARTBEAT_LOOP_FAILING, "phase": "a"}],
        )
        second = plan_store.op_stamp_claim_fields(
            self.orch,
            "p1",
            token=token,
            phase="a",
            fields={"heartbeat_loop_failing_notified": True},
            if_unset="heartbeat_loop_failing_notified",
            events=[{"ts": st.utcnow(), "type": st.EVENT_HEARTBEAT_LOOP_FAILING, "phase": "a"}],
        )
        self.assertTrue(first)
        self.assertFalse(second)
        types = [e["type"] for e in plan_store.snapshot(self.orch, "p1")["events"]]
        self.assertEqual(types.count(st.EVENT_HEARTBEAT_LOOP_FAILING), 1)


class ReleaseOpTests(OpsTestCase):
    def test_returns_the_released_claim_and_deletes_the_row(self):
        token = _claim(self.orch)
        released = plan_store.op_release_claim(self.orch, "p1", token=token, phase="a")
        self.assertIsNotNone(released)
        assert released is not None
        self.assertEqual(released["claimed_by"], token)
        self.assertIsNone(plan_store.snapshot(self.orch, "p1")["current_claim"])

    def test_unvalidated_release_clears_whatever_is_there(self):
        _claim(self.orch)
        released = plan_store.op_release_claim(self.orch, "p1")
        self.assertIsNotNone(released)
        self.assertIsNone(plan_store.snapshot(self.orch, "p1")["current_claim"])

    def test_one_half_of_the_pair_is_a_programming_error(self):
        _claim(self.orch)
        with self.assertRaises(ValueError):
            plan_store.op_release_claim(self.orch, "p1", token="t")

    def test_stale_token_raises_and_keeps_the_claim(self):
        _claim(self.orch)
        with self.assertRaises(st.ClaimMismatch):
            plan_store.op_release_claim(self.orch, "p1", token="session-nope", phase="a")
        self.assertIsNotNone(plan_store.snapshot(self.orch, "p1")["current_claim"])

    def test_events_land_in_the_same_transaction(self):
        token = _claim(self.orch)
        plan_store.op_release_claim(
            self.orch,
            "p1",
            token=token,
            phase="a",
            events=[{"ts": st.utcnow(), "type": st.EVENT_PHASE_COMPLETED, "phase": "a"}],
        )
        data = plan_store.snapshot(self.orch, "p1")
        self.assertIsNone(data["current_claim"])
        self.assertEqual(data["events"][-1]["type"], st.EVENT_PHASE_COMPLETED)

    def test_a_rejected_release_writes_neither_event_nor_release(self):
        _claim(self.orch)
        before = len(plan_store.snapshot(self.orch, "p1")["events"])
        with self.assertRaises(st.ClaimMismatch):
            plan_store.op_release_claim(
                self.orch,
                "p1",
                token="session-nope",
                phase="a",
                events=[{"ts": st.utcnow(), "type": st.EVENT_PHASE_COMPLETED, "phase": "a"}],
            )
        self.assertEqual(len(plan_store.snapshot(self.orch, "p1")["events"]), before)


class AttestationOpTests(OpsTestCase):
    def test_stamps_and_emits_in_one_transaction(self):
        token = _claim(self.orch)
        plan_store.op_stamp_attestation(
            self.orch,
            "p1",
            token=token,
            phase="a",
            kind=st.ATTESTATION_VERIFY,
            commit_sha="deadbee",
            event={"ts": st.utcnow(), "type": st.EVENT_VERIFY_STAMPED, "phase": "a"},
        )
        data = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(
            st.attestation_commit_sha(data, st.ATTESTATION_VERIFY),
            "deadbee",
        )
        self.assertEqual(data["events"][-1]["type"], st.EVENT_VERIFY_STAMPED)

    def test_second_kind_does_not_clobber_the_first(self):
        token = _claim(self.orch)
        for kind in (st.ATTESTATION_VERIFY, st.ATTESTATION_SIMPLIFY):
            plan_store.op_stamp_attestation(
                self.orch, "p1", token=token, phase="a", kind=kind, commit_sha=f"sha-{kind}"
            )
        data = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(st.attestation_commit_sha(data, st.ATTESTATION_VERIFY), "sha-verify")
        self.assertEqual(st.attestation_commit_sha(data, st.ATTESTATION_SIMPLIFY), "sha-simplify")

    def test_operator_path_without_a_token_still_stamps(self):
        _claim(self.orch)
        plan_store.op_stamp_attestation(
            self.orch, "p1", token=None, phase="a", kind=st.ATTESTATION_VERIFY, commit_sha="op"
        )
        data = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(st.attestation_commit_sha(data, st.ATTESTATION_VERIFY), "op")

    def test_no_claim_is_a_value_error_on_the_operator_path(self):
        with self.assertRaises(ValueError):
            plan_store.op_stamp_attestation(
                self.orch,
                "p1",
                token=None,
                phase="a",
                kind=st.ATTESTATION_VERIFY,
                commit_sha="x",
            )

    def test_no_claim_is_a_claim_mismatch_when_a_token_was_offered(self):
        # `st.ClaimMismatch` is a RuntimeError, not a ValueError — the two
        # answers are genuinely different, and `clu attest` exits
        # CLAIM_MISMATCH on this one.
        with self.assertRaises(st.ClaimMismatch):
            plan_store.op_stamp_attestation(
                self.orch,
                "p1",
                token="session-nope",
                phase="a",
                kind=st.ATTESTATION_SIMPLIFY,
                commit_sha="x",
            )


class BlockerOpTests(OpsTestCase):
    def test_add_blocker_mints_the_id_and_emits(self):
        blocker_id = plan_store.op_add_blocker(
            self.orch,
            "p1",
            phase_id="a",
            question="Which?",
            options=["x", "y"],
            context="ctx",
            blocker_type=st.BLOCKER_INPUT,
        )
        self.assertEqual(blocker_id, "q-1")
        data = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(data["blockers"][0]["question"], "Which?")
        self.assertEqual(data["blockers"][0]["options"], ["x", "y"])
        self.assertIsNone(data["blockers"][0]["answer"])
        self.assertEqual(data["events"][-1]["type"], st.EVENT_PHASE_BLOCKED)
        second = plan_store.op_add_blocker(
            self.orch,
            "p1",
            phase_id="a",
            question="And?",
            options=[],
            context="",
            blocker_type=st.BLOCKER_INPUT,
        )
        self.assertEqual(second, "q-2")

    def test_add_blocker_releases_the_claim_in_the_same_transaction(self):
        token = _claim(self.orch)
        plan_store.op_add_blocker(
            self.orch,
            "p1",
            phase_id="a",
            question="Which?",
            options=["x"],
            context="",
            blocker_type=st.BLOCKER_INPUT,
            release_token=token,
            release_phase="a",
        )
        data = plan_store.snapshot(self.orch, "p1")
        self.assertIsNone(data["current_claim"])
        self.assertEqual(len(data["blockers"]), 1)

    def test_add_blocker_with_a_stale_token_writes_nothing(self):
        _claim(self.orch)
        with self.assertRaises(st.ClaimMismatch):
            plan_store.op_add_blocker(
                self.orch,
                "p1",
                phase_id="a",
                question="Which?",
                options=["x"],
                context="",
                blocker_type=st.BLOCKER_INPUT,
                release_token="session-nope",
                release_phase="a",
            )
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["blockers"], [])

    def test_answer_resolves_an_option_index_inside_the_transaction(self):
        plan_store.op_add_blocker(
            self.orch,
            "p1",
            phase_id="a",
            question="Which?",
            options=["postgres", "sqlite"],
            context="",
            blocker_type=st.BLOCKER_INPUT,
        )
        resolved = plan_store.op_answer_blocker(self.orch, "p1", blocker_id="q-1", answer="1")
        self.assertEqual(resolved, "sqlite")
        data = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(data["blockers"][0]["answer"], "sqlite")
        self.assertIsNotNone(data["blockers"][0]["answered_at"])
        self.assertEqual(data["events"][-1]["type"], st.EVENT_BLOCKER_ANSWERED)
        self.assertEqual(data["events"][-1]["answer"], "sqlite")

    def test_free_text_answer_passes_through(self):
        plan_store.op_add_blocker(
            self.orch,
            "p1",
            phase_id="a",
            question="Which?",
            options=["x"],
            context="",
            blocker_type=st.BLOCKER_INPUT,
        )
        self.assertEqual(
            plan_store.op_answer_blocker(self.orch, "p1", blocker_id="q-1", answer="neither"),
            "neither",
        )

    def test_answering_twice_raises_key_error(self):
        plan_store.op_add_blocker(
            self.orch,
            "p1",
            phase_id="a",
            question="Which?",
            options=["x"],
            context="",
            blocker_type=st.BLOCKER_INPUT,
        )
        plan_store.op_answer_blocker(self.orch, "p1", blocker_id="q-1", answer="x")
        with self.assertRaises(KeyError):
            plan_store.op_answer_blocker(self.orch, "p1", blocker_id="q-1", answer="x")

    def test_blocker_metadata_merges_per_channel(self):
        plan_store.op_add_blocker(
            self.orch,
            "p1",
            phase_id="a",
            question="Which?",
            options=["x"],
            context="",
            blocker_type=st.BLOCKER_INPUT,
        )
        plan_store.op_stamp_blocker_metadata(
            self.orch, "p1", blocker_id="q-1", channel="discord", metadata={"message_id": "m1"}
        )
        plan_store.op_stamp_blocker_metadata(
            self.orch, "p1", blocker_id="q-1", channel="imessage", metadata={"chat_id": "c1"}
        )
        meta = plan_store.snapshot(self.orch, "p1")["blockers"][0]["notify_metadata"]
        self.assertEqual(meta["discord"]["message_id"], "m1")
        self.assertEqual(meta["imessage"]["chat_id"], "c1")

    def test_blocker_metadata_on_an_unknown_blocker_is_a_no_op(self):
        plan_store.op_stamp_blocker_metadata(
            self.orch, "p1", blocker_id="q-9", channel="discord", metadata={"message_id": "m"}
        )
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["blockers"], [])


class TaskOpTests(OpsTestCase):
    def _spawn(self, token: str) -> str:
        return plan_store.op_spawn_task(
            self.orch,
            "p1",
            task={
                "source": "issue",
                "spawned_by_phase": "a",
                "title": "T",
                "description": "D",
                "depends_on_phases": ["a"],
                "spawned_at": st.utcnow(),
            },
            status="pending",
            token=token,
            phase="a",
        )

    def test_spawn_mints_the_id_and_emits(self):
        token = _claim(self.orch)
        task_id = self._spawn(token)
        self.assertEqual(task_id, "task-1")
        data = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(data["spawned_tasks"][0]["title"], "T")
        self.assertEqual(data["spawned_tasks"][0]["status"], "pending")
        self.assertEqual(data["events"][-1]["type"], st.EVENT_TASK_SPAWNED)
        self.assertEqual(data["events"][-1]["task"], "task-1")
        self.assertEqual(self._spawn(token), "task-2")

    def test_spawn_with_a_stale_token_writes_nothing(self):
        _claim(self.orch)
        with self.assertRaises(st.ClaimMismatch):
            self._spawn("session-nope")
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["spawned_tasks"], [])

    def test_complete_flips_status_and_emits(self):
        token = _claim(self.orch)
        task_id = self._spawn(token)
        plan_store.op_complete_task(
            self.orch,
            "p1",
            task=task_id,
            token=token,
            phase="a",
            event={"ts": st.utcnow(), "type": st.EVENT_TASK_COMPLETED, "task": task_id},
        )
        data = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(data["spawned_tasks"][0]["status"], "done")
        self.assertIsNotNone(data["spawned_tasks"][0]["completed_at"])
        self.assertEqual(data["events"][-1]["type"], st.EVENT_TASK_COMPLETED)

    def test_complete_unknown_task_raises_key_error(self):
        with self.assertRaises(KeyError):
            plan_store.op_complete_task(self.orch, "p1", task="task-9")


class StatusOpTests(OpsTestCase):
    def test_sets_status_with_its_event(self):
        plan_store.op_set_status(
            self.orch,
            "p1",
            status=st.STATUS_PAUSED,
            event={"ts": st.utcnow(), "type": st.EVENT_SYSTEMIC_FAILURE, "phase": "a"},
        )
        data = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(data["status"], st.STATUS_PAUSED)
        self.assertEqual(data["events"][-1]["type"], st.EVENT_SYSTEMIC_FAILURE)

    def test_releases_the_claim_when_asked(self):
        token = _claim(self.orch)
        plan_store.op_set_status(
            self.orch,
            "p1",
            status=st.STATUS_PAUSED,
            release_token=token,
            release_phase="a",
        )
        data = plan_store.snapshot(self.orch, "p1")
        self.assertIsNone(data["current_claim"])
        self.assertEqual(data["status"], st.STATUS_PAUSED)


class AppendEventsOpTests(OpsTestCase):
    def test_events_carry_ids_back_to_the_caller(self):
        events = [
            {"ts": st.utcnow(), "type": st.EVENT_PAUSED, "reason": "r"},
            {"ts": st.utcnow(), "type": st.EVENT_RESUMED},
        ]
        plan_store.op_append_events(self.orch, "p1", events)
        self.assertEqual([e["id"] for e in events], [1, 2])
        self.assertEqual(len(plan_store.snapshot(self.orch, "p1")["events"]), 2)


class ArchiveEventsOpTests(OpsTestCase):
    def _history(self, n: int) -> None:
        plan_store.op_append_events(
            self.orch,
            "p1",
            [{"ts": st.utcnow(), "type": st.EVENT_PAUSED, "n": i} for i in range(n)],
        )

    def test_moves_the_hot_rows_into_the_archive(self):
        self._history(6)
        before = _count(self.orch, "events")
        self.assertGreaterEqual(before, 5)
        moved = plan_store.op_archive_events(self.orch, "p1")
        self.assertEqual(moved, before)
        self.assertEqual(_count(self.orch, "events"), 0)
        self.assertEqual(_count(self.orch, "events_archive"), before)

    def test_snapshot_reads_the_hot_table_only(self):
        self._history(6)
        plan_store.op_archive_events(self.orch, "p1")
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["events"], [])

    def test_dump_still_renders_the_full_history(self):
        self._history(6)
        before = len(plan_store.snapshot(self.orch, "p1")["events"])
        plan_store.op_archive_events(self.orch, "p1")
        plan_store.op_append_events(
            self.orch, "p1", [{"ts": st.utcnow(), "type": st.EVENT_RESUMED}]
        )
        dumped = json.loads(plan_store.dump_json(self.orch, "p1"))
        self.assertEqual(len(dumped["events"]), before + 1)
        self.assertEqual(dumped["events"][-1]["type"], st.EVENT_RESUMED)
        self.assertIn("archived_at", dumped["events"][0])
        # Ids stay monotonic across the archive boundary.
        ids = [e["id"] for e in dumped["events"]]
        self.assertEqual(ids, sorted(ids))

    def test_archiving_a_plan_with_no_events_moves_nothing(self):
        self.assertEqual(plan_store.op_archive_events(self.orch, "p1"), 0)

    def test_only_this_plans_rows_move(self):
        _seed(self.orch, "p2")
        self._history(3)
        plan_store.op_append_events(
            self.orch, "p2", [{"ts": st.utcnow(), "type": st.EVENT_PAUSED}]
        )
        plan_store.op_archive_events(self.orch, "p1")
        self.assertEqual(_count(self.orch, "events", "p2"), 1)
        self.assertEqual(_count(self.orch, "events_archive", "p2"), 0)


class VersionBumpTests(OpsTestCase):
    """Every op is a change hint. An op that forgets makes a live plan look
    idle in `clu top` — so the bump is structural, and this test walks the
    whole surface rather than trusting each op to remember."""

    def test_every_op_bumps_the_plan_version(self):
        token = _claim(self.orch)
        blocker_id = plan_store.op_add_blocker(
            self.orch,
            "p1",
            phase_id="a",
            question="Q?",
            options=["x"],
            context="",
            blocker_type=st.BLOCKER_INPUT,
        )
        calls = [
            lambda: plan_store.op_heartbeat(self.orch, "p1", token=token, phase="a"),
            lambda: plan_store.op_activity(
                self.orch, "p1", token=token, phase="a", action="start"
            ),
            lambda: plan_store.op_stamp_claim_fields(
                self.orch, "p1", token=token, fields={"pid": 7}
            ),
            lambda: plan_store.op_append_events(
                self.orch, "p1", [{"ts": st.utcnow(), "type": st.EVENT_PAUSED}]
            ),
            lambda: plan_store.op_stamp_attestation(
                self.orch,
                "p1",
                token=token,
                phase="a",
                kind=st.ATTESTATION_VERIFY,
                commit_sha="s",
            ),
            lambda: plan_store.op_answer_blocker(
                self.orch, "p1", blocker_id=blocker_id, answer="x"
            ),
            lambda: plan_store.op_stamp_blocker_metadata(
                self.orch, "p1", blocker_id=blocker_id, channel="discord", metadata={"m": "1"}
            ),
            lambda: plan_store.op_spawn_task(
                self.orch,
                "p1",
                task={"source": "issue", "spawned_by_phase": "a", "title": "T"},
                status="pending",
                token=token,
                phase="a",
            ),
            lambda: plan_store.op_complete_task(self.orch, "p1", task="task-1"),
            lambda: plan_store.op_set_status(self.orch, "p1", status=st.STATUS_PAUSED),
            lambda: plan_store.op_archive_events(self.orch, "p1"),
            lambda: plan_store.op_release_claim(self.orch, "p1", token=token, phase="a"),
        ]
        for call in calls:
            before = _version(self.orch)
            call()
            self.assertEqual(_version(self.orch), before + 1, f"{call} did not bump version")


class PhaseStartedShapeTests(OpsTestCase):
    """`phase_started` carries no `attempts` key, and this phase must not add one.

    `clu watch` prints "attempt 1" for every attempt because the claim computes
    the count onto itself and never puts it in the event, while the formatter
    falls back to 1. That is a real bug — and fixing it here would change
    observable output inside a migration whose whole premise is that nothing
    observable changes, contaminating the golden three phases diff against. The
    ops write exactly these paths, so the omission is pinned rather than left
    to good intentions.
    """

    def test_the_claim_event_carries_no_attempts_key(self):
        token = _claim(self.orch)
        plan_store.op_heartbeat(self.orch, "p1", token=token, phase="a")
        started = [
            e
            for e in plan_store.snapshot(self.orch, "p1")["events"]
            if e["type"] == st.EVENT_PHASE_STARTED
        ]
        self.assertEqual(len(started), 1)
        self.assertNotIn("attempts", started[0])


class SchemaGuardTests(OpsTestCase):
    def test_a_newer_database_is_refused_by_the_ops_too(self):
        conn = db.connect(db.project_db_path(self.orch))
        try:
            conn.execute(f"PRAGMA user_version = {db.PROJECT_SCHEMA_VERSION + 1}")
        finally:
            conn.close()
        with self.assertRaises(st.SchemaVersionMismatch):
            plan_store.op_append_events(
                self.orch, "p1", [{"ts": st.utcnow(), "type": st.EVENT_PAUSED}]
            )

    def test_an_unreadable_database_surfaces_as_sqlite_error(self):
        # The ops are WRITE paths: a broken store is a real failure, not a
        # tolerant read. What matters is that it stays inside the exception
        # family the callers' guards name (`db.DEGRADABLE_ERRORS`).
        path = db.project_db_path(self.orch)
        path.write_bytes(b"not a database at all")
        with self.assertRaises(db.DEGRADABLE_ERRORS):
            plan_store.op_append_events(
                self.orch, "p1", [{"ts": st.utcnow(), "type": st.EVENT_PAUSED}]
            )


class InvalidSlugTests(OpsTestCase):
    def test_every_op_validates_the_slug_before_it_reaches_sql(self):
        with self.assertRaises(st.InvalidSlug):
            plan_store.op_heartbeat(self.orch, "../escape", token="t", phase="a")
        with self.assertRaises(st.InvalidSlug):
            plan_store.op_append_events(self.orch, "../escape", [])


if __name__ == "__main__":
    unittest.main()
