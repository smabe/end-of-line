"""Phase `primitive` tests: queue module + ProjectConfig.queue_path.

The state.locked_json extraction has its own tests in test_state.py. These
tests focus on the new queue.py module and the per-project queue_path()
helper.
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from end_of_line import queue
from end_of_line import state as st
from end_of_line.config import ORCHESTRATOR_DIR, ProjectConfig


class QueueModuleConstants(unittest.TestCase):
    def test_schema_version_is_one(self) -> None:
        self.assertEqual(queue.SCHEMA_VERSION, 1)

    def test_empty_shape(self) -> None:
        empty = queue._empty()
        self.assertEqual(empty["schema_version"], 1)
        self.assertEqual(empty["queue"], [])
        self.assertEqual(empty["history"], [])


class QueueLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "queue.json"

    def test_load_missing_raises(self) -> None:
        # `load` mirrors `state.load`: raises FileNotFoundError on missing.
        # The cron path uses a try/except wrapper for missing-tolerance.
        with self.assertRaises(FileNotFoundError):
            queue.load(self.path)

    def test_load_raises_on_schema_mismatch(self) -> None:
        self.path.write_text(json.dumps({"schema_version": 99, "queue": [], "history": []}))
        with self.assertRaises(st.SchemaVersionMismatch):
            queue.load(self.path)

    def test_load_returns_current_version(self) -> None:
        self.path.write_text(json.dumps(queue._empty()))
        data = queue.load(self.path)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["queue"], [])


class QueueMutateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sub" / "queue.json"

    def test_mutate_creates_missing_file_with_empty_factory(self) -> None:
        with queue.mutate(self.path) as data:
            self.assertEqual(data, queue._empty())
            data["queue"].append({"slug": "alpha"})
        # File now exists with appended entry.
        reloaded = queue.load(self.path)
        self.assertEqual(reloaded["queue"], [{"slug": "alpha"}])

    def test_mutate_round_trip(self) -> None:
        with queue.mutate(self.path) as data:
            data["queue"].append({"slug": "first"})
        with queue.mutate(self.path) as data:
            data["queue"].append({"slug": "second"})
        reloaded = queue.load(self.path)
        slugs = [row["slug"] for row in reloaded["queue"]]
        self.assertEqual(slugs, ["first", "second"])

    def test_mutate_leaves_no_tmp_artifact(self) -> None:
        with queue.mutate(self.path) as data:
            data["queue"].append({"slug": "x"})
        leftover = list(self.path.parent.glob("queue.json.*.tmp"))
        self.assertEqual(leftover, [])

    def test_mutate_serializes_concurrent_writers(self) -> None:
        # Two threads each append one entry; the lock must serialize so both
        # writes survive. Without locking, the second writer would re-load
        # the pre-first state and clobber it.
        with queue.mutate(self.path) as data:
            data["queue"].append({"slug": "seed"})

        ready = threading.Event()
        done = threading.Event()

        def slow_writer() -> None:
            with queue.mutate(self.path) as data:
                ready.set()
                # Hold the lock briefly so the other thread is forced to wait.
                done.wait(timeout=2)
                data["queue"].append({"slug": "slow"})

        def fast_writer() -> None:
            ready.wait(timeout=2)
            with queue.mutate(self.path) as data:
                data["queue"].append({"slug": "fast"})

        t_slow = threading.Thread(target=slow_writer)
        t_fast = threading.Thread(target=fast_writer)
        t_slow.start()
        ready.wait(timeout=2)
        t_fast.start()
        # Hold for a moment so fast_writer is blocked on the lock, then
        # release slow.
        done.set()
        t_slow.join(timeout=5)
        t_fast.join(timeout=5)

        slugs = [row["slug"] for row in queue.load(self.path)["queue"]]
        self.assertEqual(sorted(slugs), ["fast", "seed", "slow"])


class ProjectConfigQueuePath(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def test_queue_path_default_plan_dir(self) -> None:
        cfg = ProjectConfig(project_root=self.root)
        self.assertEqual(
            cfg.queue_path(),
            self.root / "plans" / ORCHESTRATOR_DIR / "queue.json",
        )

    def test_queue_path_custom_plan_dir(self) -> None:
        cfg = ProjectConfig(project_root=self.root, plan_dir="ops/plans")
        self.assertEqual(
            cfg.queue_path(),
            self.root / "ops" / "plans" / ORCHESTRATOR_DIR / "queue.json",
        )

    def test_queue_path_siblings_state_path(self) -> None:
        # queue.json lives in the same .orchestrator/ dir as state files.
        cfg = ProjectConfig(project_root=self.root)
        sp = cfg.state_path("plan-a")
        qp = cfg.queue_path()
        self.assertEqual(sp.parent, qp.parent)


if __name__ == "__main__":
    unittest.main()
