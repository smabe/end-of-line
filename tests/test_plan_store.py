"""Contract tests for the per-plan store — the engine under `st.load`/`st.mutate`.

Written before `plan_store.py` existed. What they pin is the strangler seam's
whole promise: the dict a caller gets back is the dict the JSON engine gave it,
the mutations a caller makes to that dict land as table writes, and the
exception TYPES callers catch by name are unchanged.
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
        with plan_store.mutate_compat(self.orch, "p1") as data:
            st.add_blocker(data, "a", "first?", ["x", "y"])
            st.add_blocker(data, "b", "second?", [])
        ids = [b["id"] for b in plan_store.snapshot(self.orch, "p1")["blockers"]]
        self.assertEqual(ids, ["q-1", "q-2"])

    def test_snapshot_is_consistent_while_a_writer_holds_the_lock(self):
        _seed(self.orch)
        with plan_store.mutate_compat(self.orch, "p1") as data:
            data["status"] = st.STATUS_PAUSED
            # A reader mid-write sees the pre-commit state, never a half-write.
            self.assertEqual(plan_store.snapshot(self.orch, "p1")["status"], st.STATUS_RUNNING)
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["status"], st.STATUS_PAUSED)


class MutateCompatTests(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.orch = self.tmp_path / "proj" / "plans" / ".orchestrator"
        _seed(self.orch)

    def test_claim_round_trip_including_nested_mutation(self):
        with plan_store.mutate_compat(self.orch, "p1") as data:
            token = st.claim_phase(data, "a", 30)
            # The identity contract: callers keep a reference into the yielded
            # dict and mutate THAT, after the store has already seen it.
            claim = data["current_claim"]
            claim["pid"] = 4242
            claim["stuck_tool_emitted_at"] = {"77": "2026-01-01T00:00:00Z"}
        snap = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(snap["current_claim"]["claimed_by"], token)
        self.assertEqual(snap["current_claim"]["pid"], 4242)
        self.assertEqual(snap["current_claim"]["stuck_tool_emitted_at"], {"77": "2026-01-01T00:00:00Z"})

    def test_releasing_the_claim_deletes_the_row(self):
        with plan_store.mutate_compat(self.orch, "p1") as data:
            st.claim_phase(data, "a", 30)
        with plan_store.mutate_compat(self.orch, "p1") as data:
            data["current_claim"] = None
        self.assertIsNone(plan_store.snapshot(self.orch, "p1")["current_claim"])

    def test_expire_then_reclaim_in_one_window(self):
        # `release_if_expired` + `claim_phase` inside one mutate window: the
        # claims row is deleted and re-inserted in the same transaction.
        with plan_store.mutate_compat(self.orch, "p1") as data:
            st.claim_phase(data, "a", 30)
            data["current_claim"]["lease_expires"] = "2000-01-01T00:00:00Z"
        with plan_store.mutate_compat(self.orch, "p1") as data:
            self.assertTrue(st.release_if_expired(data))
            st.claim_phase(data, "a", 30)
        snap = plan_store.snapshot(self.orch, "p1")
        self.assertEqual(snap["current_claim"]["phase_id"], "a")
        self.assertEqual(snap["current_claim"]["attempts"], 2)

    def test_events_append_and_carry_ids(self):
        with plan_store.mutate_compat(self.orch, "p1") as data:
            st.append_event(data, st.EVENT_PAUSED, reason="one")
            st.append_event(data, st.EVENT_RESUMED)
        events = plan_store.snapshot(self.orch, "p1")["events"]
        self.assertEqual([e["type"] for e in events], [st.EVENT_PAUSED, st.EVENT_RESUMED])
        self.assertEqual(events[0]["reason"], "one")
        self.assertEqual([e["id"] for e in events], sorted(e["id"] for e in events))

    def test_event_ids_are_monotonic_across_windows(self):
        with plan_store.mutate_compat(self.orch, "p1") as data:
            st.append_event(data, st.EVENT_PAUSED)
        first = plan_store.snapshot(self.orch, "p1")["events"][-1]["id"]
        with plan_store.mutate_compat(self.orch, "p1") as data:
            st.append_event(data, st.EVENT_RESUMED)
        second = plan_store.snapshot(self.orch, "p1")["events"][-1]["id"]
        self.assertGreater(second, first)

    def test_rewriting_event_history_raises(self):
        with plan_store.mutate_compat(self.orch, "p1") as data:
            st.append_event(data, st.EVENT_PAUSED)
        with self.assertRaises(RuntimeError):
            with plan_store.mutate_compat(self.orch, "p1") as data:
                data["events"] = []

    def test_body_exception_writes_nothing(self):
        with self.assertRaises(ZeroDivisionError):
            with plan_store.mutate_compat(self.orch, "p1") as data:
                data["status"] = st.STATUS_HALTED
                raise ZeroDivisionError
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["status"], st.STATUS_RUNNING)

    def test_missing_plan_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            with plan_store.mutate_compat(self.orch, "absent"):
                pass

    def test_bounded_wait_raises_db_busy(self):
        # The replacement for the flock's LockTimeout: a second writer with a
        # budget gives up rather than freezing the worker's Bash call.
        started = threading.Event()
        release = threading.Event()
        failures: list[BaseException] = []

        def hold() -> None:
            try:
                with plan_store.mutate_compat(self.orch, "p1"):
                    started.set()
                    release.wait(5)
            except BaseException as exc:  # pragma: no cover - surfaced below
                failures.append(exc)

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(started.wait(5))
            with self.assertRaises(db.DbBusy):
                with plan_store.mutate_compat(self.orch, "p1", timeout_s=0.2):
                    pass
        finally:
            release.set()
            holder.join(5)
        self.assertEqual(failures, [])

    def test_worktree_absent_stays_absent(self):
        self.assertNotIn("worktree", plan_store.snapshot(self.orch, "p1"))
        with plan_store.mutate_compat(self.orch, "p1") as data:
            data["worktree"] = {"path": "/wt", "branch": "b", "base_ref": "main"}
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["worktree"]["branch"], "b")
        with plan_store.mutate_compat(self.orch, "p1") as data:
            data["worktree"] = None
        # Cleared, not forgotten — `get_worktree` must see None, not KeyError.
        snap = plan_store.snapshot(self.orch, "p1")
        self.assertIn("worktree", snap)
        self.assertIsNone(snap["worktree"])

    def test_unknown_top_level_fields_survive(self):
        with plan_store.mutate_compat(self.orch, "p1") as data:
            data["in_conflict_with"] = ["other-plan"]
        self.assertEqual(plan_store.snapshot(self.orch, "p1")["in_conflict_with"], ["other-plan"])

    def test_spawned_tasks_round_trip(self):
        with plan_store.mutate_compat(self.orch, "p1") as data:
            data["spawned_tasks"].append(
                {
                    "id": "task-1",
                    "source": "worker",
                    "spawned_by_phase": "a",
                    "title": "T",
                    "description": "D",
                    "depends_on_phases": ["a"],
                    "status": "pending",
                    "spawned_at": st.utcnow(),
                }
            )
        with plan_store.mutate_compat(self.orch, "p1") as data:
            data["spawned_tasks"][0]["status"] = "done"
        task = plan_store.snapshot(self.orch, "p1")["spawned_tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["depends_on_phases"], ["a"])

    def test_version_column_advances_on_every_write(self):
        def version() -> int:
            conn = db.connect(db.project_db_path(self.orch))
            try:
                return int(conn.execute("SELECT version FROM plans WHERE slug='p1'").fetchone()[0])
            finally:
                conn.close()

        before = version()
        with plan_store.mutate_compat(self.orch, "p1") as data:
            data["status"] = st.STATUS_PAUSED
        self.assertEqual(version(), before + 1)


class DumpJsonTests(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.orch = self.tmp_path / "proj" / "plans" / ".orchestrator"
        _seed(self.orch)

    def test_dump_is_parseable_and_carries_the_operator_fields(self):
        with plan_store.mutate_compat(self.orch, "p1") as data:
            st.claim_phase(data, "a", 30)
            st.add_blocker(data, "a", "which?", ["x"])
        parsed = json.loads(plan_store.dump_json(self.orch, "p1"))
        self.assertEqual(parsed["status"], st.STATUS_RUNNING)
        self.assertEqual(parsed["current_claim"]["phase_id"], "a")
        self.assertEqual(parsed["blockers"][0]["question"], "which?")
        self.assertTrue(parsed["events"])


if __name__ == "__main__":
    unittest.main()
