"""Contract tests for the per-plan store — the five tables plan state lives in.

Written before `plan_store.py` existed, and what they pin outlived the
strangler seam that carried the call sites across: the dict a caller reads back
is the dict the JSON engine gave it, a write lands in the column the map says
it lands in, and the exception TYPES callers catch by name are unchanged.
"""

from __future__ import annotations

import json
import sqlite3
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


def _delta(**fields):
    """A `TickDelta`, imported where it is used.

    `supervisor` imports `plan_store`, so the delta type it owns cannot be
    imported at this module's top level without dragging the cycle into the
    test package.
    """
    from end_of_line.supervisor import TickDelta

    return TickDelta(**fields)


class KeyForStatePathTests(unittest.TestCase):
    def test_splits_orchestrator_dir_and_slug(self):
        orch, slug = plan_store.key_for_state_path(
            Path("/proj/plans/.orchestrator/my-plan.state.json")
        )
        self.assertEqual(orch, Path("/proj/plans/.orchestrator"))
        self.assertEqual(slug, "my-plan")

    def test_rejects_a_slug_outside_the_alphabet(self):
        # The path's parent is the caller's orchestrator dir (guarded by
        # `config.state_path`); what this function re-validates is the slug it
        # is about to pass to SQL.
        with self.assertRaises(st.InvalidSlug):
            plan_store.key_for_state_path(Path("/proj/.orchestrator/Evil Plan.state.json"))

    def test_rejects_a_path_that_is_not_a_state_path(self):
        with self.assertRaises(ValueError):
            plan_store.key_for_state_path(Path("/proj/.orchestrator/queue.json"))


class CreateAndExistsTests(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.orch = self.tmp_path / "proj" / "plans" / ".orchestrator"

    def test_exists_is_false_before_any_database(self):
        self.assertFalse(plan_store.exists(self.orch, "p1"))

    def test_create_then_exists(self):
        _seed(self.orch)
        self.assertTrue(plan_store.exists(self.orch, "p1"))
        self.assertTrue(db.project_db_path(self.orch).exists())

    def test_create_twice_raises_file_exists(self):
        _seed(self.orch)
        with self.assertRaises(FileExistsError):
            _seed(self.orch)

    def test_exists_is_false_for_another_slug(self):
        _seed(self.orch)
        self.assertFalse(plan_store.exists(self.orch, "other"))

    def test_plan_slugs_enumerates_the_database_not_the_directory(self):
        _seed(self.orch, "alpha")
        _seed(self.orch, "beta")
        # A legacy JSON file in the same directory is invisible to the store.
        (self.orch / "legacy.state.json").write_text("{}")
        self.assertEqual(plan_store.plan_slugs(self.orch), ["alpha", "beta"])


class SnapshotTests(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.orch = self.tmp_path / "proj" / "plans" / ".orchestrator"

    def test_snapshot_round_trips_the_empty_state_dict(self):
        seeded = _seed(self.orch)
        snap = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(snap, seeded)

    def test_missing_plan_raises_file_not_found(self):
        _seed(self.orch)
        with self.assertRaises(FileNotFoundError):
            plan_store.snapshot(self.orch, "absent")

    def test_missing_database_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            plan_store.snapshot(self.orch, "p1")

    def test_too_new_schema_raises_schema_version_mismatch(self):
        _seed(self.orch)
        conn = sqlite3.connect(str(db.project_db_path(self.orch)))
        conn.execute(f"PRAGMA user_version = {db.PROJECT_SCHEMA_VERSION + 1}")
        conn.close()
        with self.assertRaises(st.SchemaVersionMismatch):
            plan_store.snapshot(self.orch, "p1")

    def test_blockers_come_back_in_insertion_order(self):
        _seed(self.orch)
        plan_store.op_add_blocker(
            self.orch, "p1", phase_id="a", question="first?", options=["x", "y"]
        )
        plan_store.op_add_blocker(self.orch, "p1", phase_id="b", question="second?", options=[])
        ids = [b["id"] for b in plan_store.snapshot(self.orch, "p1")["blockers"]]
        self.assertEqual(ids, ["q-1", "q-2"])

    def test_snapshot_is_consistent_while_a_writer_holds_the_lock(self):
        _seed(self.orch)
        conn = db.connect(db.project_db_path(self.orch))
        try:
            with db.write_txn(conn) as cur:
                cur.execute("UPDATE plans SET status = ? WHERE slug = 'p1'", (st.STATUS_PAUSED,))
                # A reader mid-write sees the pre-commit state, never a half-write.
                self.assertEqual(
                    plan_store.snapshot(self.orch, "p1")["status"], st.STATUS_RUNNING
                )
        finally:
            conn.close()
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["status"], st.STATUS_PAUSED)


class DocumentFidelityTests(CluTestCase):
    """The dict a caller writes is the dict a caller reads back.

    Plan state is five tables now, and every write goes through an op that
    names the rows it touches — so the round trip these assert is the column
    map in both directions: scalars, JSON sub-objects, and the catch-alls that
    keep a field nobody gave a column to from vanishing on the next read.
    """

    def setUp(self) -> None:
        super().setUp()
        self.orch = self.tmp_path / "proj" / "plans" / ".orchestrator"
        _seed(self.orch)

    def _claim(self, phase: str = "a") -> str:
        _, observed = plan_store.snapshot_with_preconditions(self.orch, "p1")
        token = plan_store.apply_tick_delta(
            self.orch,
            "p1",
            plan_store.TickPreconditions(),
            _delta(claim_phase=phase, lease_minutes=30, claim_attempts=1),
        )
        assert token is not None
        return token

    def test_claim_round_trip_including_nested_fields(self):
        token = self._claim()
        # The claim carries fields with columns of their own AND fields that
        # only exist in the `flags` catch-all; both have to survive the trip.
        plan_store.op_stamp_claim_fields(
            self.orch,
            "p1",
            token=token,
            fields={"pid": 4242, "stuck_tool_emitted_at": {"77": "2026-01-01T00:00:00Z"}},
        )
        snap = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(snap["current_claim"]["claimed_by"], token)
        self.assertEqual(snap["current_claim"]["pid"], 4242)
        self.assertEqual(
            snap["current_claim"]["stuck_tool_emitted_at"], {"77": "2026-01-01T00:00:00Z"}
        )

    def test_releasing_the_claim_deletes_the_row(self):
        self._claim()
        plan_store.op_release_claim(self.orch, "p1")
        self.assertIsNone(plan_store.snapshot(self.orch, "p1")["current_claim"])

    def test_expire_then_reclaim_in_one_transaction(self):
        # The lease-expiry shape: the claims row is deleted and re-inserted in
        # the same transaction, which is what would trip the primary key if the
        # apply did it in two statements the wrong way round.
        self._claim()
        _, observed = plan_store.snapshot_with_preconditions(self.orch, "p1")
        plan_store.apply_tick_delta(
            self.orch,
            "p1",
            plan_store.TickPreconditions(expect_claim=observed.expect_claim),
            _delta(release_claim=True, claim_phase="a", lease_minutes=30, claim_attempts=2),
        )
        snap = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(snap["current_claim"]["phase_id"], "a")
        self.assertEqual(snap["current_claim"]["attempts"], 2)

    def test_events_append_and_carry_ids(self):
        plan_store.op_append_events(
            self.orch,
            "p1",
            [
                {"ts": st.utcnow(), "type": st.EVENT_PAUSED, "reason": "one"},
                {"ts": st.utcnow(), "type": st.EVENT_RESUMED},
            ],
        )
        events = plan_store.snapshot(self.orch, "p1")["events"]
        self.assertEqual([e["type"] for e in events], [st.EVENT_PAUSED, st.EVENT_RESUMED])
        self.assertEqual(events[0]["reason"], "one")
        self.assertEqual([e["id"] for e in events], sorted(e["id"] for e in events))

    def test_event_ids_are_monotonic_across_transactions(self):
        plan_store.op_append_events(self.orch, "p1", [{"ts": st.utcnow(), "type": st.EVENT_PAUSED}])
        first = plan_store.snapshot(self.orch, "p1")["events"][-1]["id"]
        plan_store.op_append_events(
            self.orch, "p1", [{"ts": st.utcnow(), "type": st.EVENT_RESUMED}]
        )
        second = plan_store.snapshot(self.orch, "p1")["events"][-1]["id"]
        self.assertGreater(second, first)

    def test_a_failed_op_writes_nothing(self):
        # One op, one transaction: a refusal partway through must not leave the
        # fields it had already assembled behind.
        with self.assertRaises(ValueError):
            plan_store.op_set_fields(
                self.orch,
                "p1",
                {"status": st.STATUS_HALTED, "events": []},
            )
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["status"], st.STATUS_RUNNING)

    def test_worktree_absent_stays_absent(self):
        self.assertNotIn("worktree", plan_store.snapshot(self.orch, "p1"))
        plan_store.op_set_worktree(
            self.orch, "p1", {"path": "/wt", "branch": "b", "base_ref": "main"}
        )
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["worktree"]["branch"], "b")
        plan_store.op_set_worktree(self.orch, "p1", None)
        # Cleared, not forgotten — `get_worktree` must see None, not KeyError.
        snap = plan_store.snapshot(self.orch, "p1")
        self.assertIn("worktree", snap)
        self.assertIsNone(snap["worktree"])

    def test_unknown_top_level_fields_survive(self):
        plan_store.op_set_fields(self.orch, "p1", {"in_conflict_with": ["other-plan"]})
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["in_conflict_with"], ["other-plan"])

    def test_spawned_tasks_round_trip(self):
        task_id = plan_store.op_spawn_task(
            self.orch,
            "p1",
            task={
                "source": "worker",
                "spawned_by_phase": "a",
                "title": "T",
                "description": "D",
                "depends_on_phases": ["a"],
                "spawned_at": st.utcnow(),
            },
            status="pending",
        )
        plan_store.op_complete_task(self.orch, "p1", task=task_id)
        task = plan_store.snapshot(self.orch, "p1")["spawned_tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["depends_on_phases"], ["a"])

    def test_bounded_wait_raises_db_busy(self):
        # The replacement for the flock's timeout: a second writer with a
        # budget gives up rather than freezing the worker's Bash call.
        started = threading.Event()
        release = threading.Event()
        failures: list[BaseException] = []

        def hold() -> None:
            conn = db.connect(db.project_db_path(self.orch))
            try:
                with db.write_txn(conn) as cur:
                    cur.execute("UPDATE plans SET version = version + 1 WHERE slug = 'p1'")
                    started.set()
                    release.wait(5)
            except BaseException as exc:  # pragma: no cover - surfaced below
                failures.append(exc)
            finally:
                conn.close()

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(started.wait(5))
            with self.assertRaises(db.DbBusy):
                plan_store.op_append_events(
                    self.orch,
                    "p1",
                    [{"ts": st.utcnow(), "type": st.EVENT_PAUSED}],
                    timeout_s=0.2,
                )
        finally:
            release.set()
            holder.join(5)
        self.assertEqual(failures, [])


class DumpJsonTests(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.orch = self.tmp_path / "proj" / "plans" / ".orchestrator"
        _seed(self.orch)

    def test_dump_is_parseable_and_carries_the_operator_fields(self):
        _, observed = plan_store.snapshot_with_preconditions(self.orch, "p1")
        plan_store.apply_tick_delta(
            self.orch,
            "p1",
            plan_store.TickPreconditions(),
            _delta(claim_phase="a", lease_minutes=30, claim_attempts=1),
        )
        plan_store.op_add_blocker(self.orch, "p1", phase_id="a", question="which?", options=["x"])
        parsed = json.loads(plan_store.dump_json(self.orch, "p1"))
        self.assertEqual(parsed["status"], st.STATUS_RUNNING)
        self.assertEqual(parsed["current_claim"]["phase_id"], "a")
        self.assertEqual(parsed["blockers"][0]["question"], "which?")
        self.assertTrue(parsed["events"])


if __name__ == "__main__":
    unittest.main()
