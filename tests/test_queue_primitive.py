"""The queue store: entry round-trip, ordering, pop outcomes, pop race.

The queue is two tables in the project database now, so what these tests pin
changed shape. Gone: the JSON file's schema constant, the lock's serialization
of two threads, the tmp-artifact check. Here instead: that an entry dict comes
back the way it went in (columns plus a JSON tail), that `--front` inserts
below the head without renumbering anything, and — the one that matters — that
two SEPARATE PROCESSES racing the same pop consume the head exactly once.

That last test is the replacement for the head re-check the file-backed pop did
by hand after taking its lock. It is a real multi-process race rather than a
mocked one for the same reason `test_db.py`'s contention gate is: an atomicity
claim proved with threads in one interpreter is not the claim being made.
"""

from __future__ import annotations

import multiprocessing as mp
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from end_of_line import db, queue
from end_of_line import state as st
from end_of_line.config import ORCHESTRATOR_DIR, ProjectConfig

CHILD_JOIN_S = 120.0


def _entry(slug: str, **extra) -> dict:
    return {
        "slug": slug,
        "added_at": st.utcnow(),
        "added_by": "operator",
        "position_at_add": "tail",
        **extra,
    }


def _pop_worker(orch_dir: str, slug: str, result_path: str) -> None:
    """Child process: try to pop `slug`; write "won"/"lost" to result_path."""
    won = queue.pop_head_if(Path(orch_dir), slug, queue.OUTCOME_POPPED)
    Path(result_path).write_text("won" if won else "lost")


class QueueRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.orch = Path(self._tmp.name) / ORCHESTRATOR_DIR
        self.orch.mkdir(parents=True)

    def test_pending_on_absent_database_is_empty(self) -> None:
        # Absent database == empty queue, the heir of "no queue.json".
        self.assertEqual(queue.pending(self.orch), [])
        self.assertEqual(queue.history(self.orch), [])
        self.assertFalse(db.project_db_path(self.orch).exists())

    def test_entry_fields_round_trip(self) -> None:
        entry = _entry(
            "next-plan",
            added_by="worker",
            source_plan="current-plan",
            source_phase="impl",
            source_token_fp="ab12cd34",
            reason="chain follow-up",
            batch_id="b1",
        )
        queue.add(self.orch, entry)

        loaded = queue.pending(self.orch)[0]
        self.assertEqual(loaded["slug"], "next-plan")
        self.assertEqual(loaded["added_by"], "worker")
        self.assertEqual(loaded["batch_id"], "b1")
        self.assertEqual(loaded["source_plan"], "current-plan")
        self.assertEqual(loaded["source_phase"], "impl")
        self.assertEqual(loaded["source_token_fp"], "ab12cd34")
        self.assertEqual(loaded["reason"], "chain follow-up")

    def test_absent_optional_fields_read_as_none(self) -> None:
        queue.add(self.orch, _entry("old-plan"))
        loaded = queue.pending(self.orch)[0]
        self.assertIsNone(loaded.get("source_plan"))
        self.assertIsNone(loaded.get("source_phase"))
        self.assertIsNone(loaded.get("batch_id"))

    def test_tail_adds_keep_insertion_order(self) -> None:
        queue.add(self.orch, _entry("first"))
        queue.add(self.orch, _entry("second"))
        self.assertEqual([e["slug"] for e in queue.pending(self.orch)], ["first", "second"])

    def test_front_insert_goes_to_head_without_reordering_the_rest(self) -> None:
        queue.add_many(self.orch, [_entry("a"), _entry("b")])
        positions = queue.add_many(self.orch, [_entry("x"), _entry("y")], front=True)
        self.assertEqual(positions, [1, 2])
        self.assertEqual(
            [e["slug"] for e in queue.pending(self.orch)],
            ["x", "y", "a", "b"],
        )

    def test_duplicate_add_raises_with_position(self) -> None:
        queue.add_many(self.orch, [_entry("a"), _entry("b")])
        with self.assertRaises(queue.AlreadyQueued) as ctx:
            queue.add(self.orch, _entry("b"))
        self.assertEqual(ctx.exception.slug, "b")
        self.assertEqual(ctx.exception.position, 2)

    def test_batch_add_is_all_or_nothing(self) -> None:
        queue.add(self.orch, _entry("a"))
        with self.assertRaises(queue.AlreadyQueued):
            queue.add_many(self.orch, [_entry("new-one"), _entry("a")])
        # The transaction rolled back — the good half of the batch is not there.
        self.assertEqual([e["slug"] for e in queue.pending(self.orch)], ["a"])

    def test_add_rejects_a_path_traversal_slug(self) -> None:
        with self.assertRaises(st.InvalidSlug):
            queue.add(self.orch, _entry("../escape"))

    def test_remove_moves_the_entry_to_history(self) -> None:
        queue.add_many(self.orch, [_entry("a"), _entry("b")])
        self.assertTrue(queue.remove(self.orch, "a"))
        self.assertEqual([e["slug"] for e in queue.pending(self.orch)], ["b"])
        hist = queue.history(self.orch)
        self.assertEqual([e["slug"] for e in hist], ["a"])
        self.assertEqual(hist[0]["outcome"], queue.OUTCOME_REMOVED)
        self.assertTrue(hist[0]["ended_at"])
        # Provenance survives the move.
        self.assertEqual(hist[0]["position_at_add"], "tail")

    def test_remove_of_an_absent_slug_is_false(self) -> None:
        self.assertFalse(queue.remove(self.orch, "nope"))

    def test_pop_stamps_a_fresh_ended_at_and_leaves_added_at_alone(self) -> None:
        # The entry is seeded with a distinctly OLD added_at first, on purpose:
        # `st.utcnow()` is second-resolution, so an entry added and popped in
        # the same second would make "ended_at is now" true before the pop ran,
        # and the assertion would pass against the wrong column — or against
        # nothing being written at all.
        old = "2020-01-01T00:00:00Z"
        queue.add(self.orch, {"slug": "a", "added_at": old, "added_by": "operator"})
        queue.pop_head_if(self.orch, "a", queue.OUTCOME_POPPED)

        row = queue.history(self.orch)[0]
        self.assertEqual(row["added_at"], old, "the pop rewrote added_at")
        self.assertGreater(row["ended_at"], old, "ended_at did not advance")

    def test_pop_head_if_records_the_outcome(self) -> None:
        queue.add_many(self.orch, [_entry("a"), _entry("b")])
        self.assertTrue(queue.pop_head_if(self.orch, "a", queue.OUTCOME_ABSORBED))
        self.assertEqual([e["slug"] for e in queue.pending(self.orch)], ["b"])
        self.assertEqual(queue.history(self.orch)[0]["outcome"], queue.OUTCOME_ABSORBED)

    def test_pop_head_if_refuses_when_the_head_moved(self) -> None:
        queue.add_many(self.orch, [_entry("a"), _entry("b")])
        self.assertFalse(queue.pop_head_if(self.orch, "b", queue.OUTCOME_POPPED))
        # Nothing consumed, nothing recorded.
        self.assertEqual([e["slug"] for e in queue.pending(self.orch)], ["a", "b"])
        self.assertEqual(queue.history(self.orch), [])


class QueuePopRaceTest(unittest.TestCase):
    """Two processes pop the same head; exactly one wins and nothing is lost."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.orch = Path(self._tmp.name) / ORCHESTRATOR_DIR
        self.orch.mkdir(parents=True)

    def test_two_poppers_consume_the_head_exactly_once(self) -> None:
        # Arrange — one head, one follower, and the database on disk before
        # either child opens it (spawn, never fork: an inherited SQLite
        # connection is not safe across a fork).
        queue.add_many(self.orch, [_entry("head-plan"), _entry("next-plan")])
        ctx = mp.get_context("spawn")
        results = [Path(self._tmp.name) / f"race-{i}.txt" for i in range(2)]

        # Act — both children race the same pop.
        procs = [
            ctx.Process(target=_pop_worker, args=(str(self.orch), "head-plan", str(res)))
            for res in results
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(CHILD_JOIN_S)

        # Assert — both exited clean, exactly one claims the pop, the head left
        # the queue once, and the follower is untouched.
        for i, p in enumerate(procs):
            self.assertEqual(p.exitcode, 0, f"popper {i} exited {p.exitcode} (expected 0)")
        outcomes = sorted(r.read_text() for r in results)
        self.assertEqual(outcomes, ["lost", "won"], f"both poppers reported {outcomes}")
        self.assertEqual([e["slug"] for e in queue.pending(self.orch)], ["next-plan"])
        hist = queue.history(self.orch)
        self.assertEqual([e["slug"] for e in hist], ["head-plan"])


class ProjectConfigOrchestratorDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def test_orchestrator_dir_default_plan_dir(self) -> None:
        cfg = ProjectConfig(project_root=self.root)
        self.assertEqual(cfg.orchestrator_dir(), self.root / "plans" / ORCHESTRATOR_DIR)

    def test_orchestrator_dir_custom_plan_dir(self) -> None:
        cfg = ProjectConfig(project_root=self.root, plan_dir="ops/plans")
        self.assertEqual(
            cfg.orchestrator_dir(),
            self.root / "ops" / "plans" / ORCHESTRATOR_DIR,
        )

    def test_orchestrator_dir_holds_the_state_key(self) -> None:
        cfg = ProjectConfig(project_root=self.root)
        self.assertEqual(cfg.state_path("plan-a").parent, cfg.orchestrator_dir())


if __name__ == "__main__":
    unittest.main()
