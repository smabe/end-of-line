"""Phase `add` tests: `clu queue add` CLI subcommand.

All six exit paths plus --front, history-only re-add, running-slug re-add,
entry shape, and symlink path-resolution. Uses `isolate_queue` (registry +
per-project tmp paths) so the host registry is never touched.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from end_of_line import db, queue, registry
from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import ProjectConfig
from tests import isolate_queue, stamp_future_schema

_PLAN_BODY = "# placeholder plan\n"


def _bootstrap(project: Path, slug: str = "seed-plan") -> None:
    """Register `slug` against `project` and write its plan file.

    `clu queue add` requires the project to be in the host registry (at
    least one row) before it accepts an enqueue. Tests that exercise the
    happy path pre-seed that row.
    """
    (project / "plans").mkdir(exist_ok=True)
    (project / "plans" / f"{slug}.md").write_text(_PLAN_BODY)
    registry.register(project, slug)


def _write_plan(project: Path, slug: str) -> Path:
    plans_dir = project / "plans"
    plans_dir.mkdir(exist_ok=True)
    path = plans_dir / f"{slug}.md"
    path.write_text(_PLAN_BODY)
    return path


class QueueAddTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        isolate_queue(self, self.project)
        self.orch = ProjectConfig(project_root=self.project).orchestrator_dir()
        self.db_path = db.project_db_path(self.orch)

    # --- happy paths ---

    def test_add_success_appends_to_tail(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "new-plan")
        rc = main(["queue", "add", "new-plan", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = [e["slug"] for e in queue.pending(self.orch)]
        self.assertEqual(slugs, ["new-plan"])

    def test_add_front_inserts_at_position_0(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "a")
        _write_plan(self.project, "b")
        _write_plan(self.project, "c")
        main(["queue", "add", "a", "--project", str(self.project)])
        main(["queue", "add", "b", "--project", str(self.project)])
        rc = main(["queue", "add", "c", "--front", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = [e["slug"] for e in queue.pending(self.orch)]
        self.assertEqual(slugs, ["c", "a", "b"])

    def test_add_appends_when_queue_nonempty(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "a")
        _write_plan(self.project, "b")
        main(["queue", "add", "a", "--project", str(self.project)])
        rc = main(["queue", "add", "b", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = [e["slug"] for e in queue.pending(self.orch)]
        self.assertEqual(slugs, ["a", "b"])

    # --- rejection paths (the four documented exit codes besides OK) ---

    def test_add_rejects_invalid_slug(self) -> None:
        _bootstrap(self.project)
        rc = main(["queue", "add", "Bad Slug!", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.INVALID_SLUG)
        self.assertEqual(queue.pending(self.orch), [])

    def test_add_rejects_unknown_project(self) -> None:
        # No bootstrap: registry is empty for this project.
        _write_plan(self.project, "foo")  # plan file exists; bootstrap check fires first.
        rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertEqual(queue.pending(self.orch), [])

    def test_add_rejects_missing_plan_file(self) -> None:
        _bootstrap(self.project)
        rc = main(["queue", "add", "nonexistent", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)
        self.assertEqual(queue.pending(self.orch), [])

    def test_add_rejects_duplicate_pending(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        main(["queue", "add", "foo", "--project", str(self.project)])
        rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        slugs = [e["slug"] for e in queue.pending(self.orch)]
        self.assertEqual(slugs, ["foo"])

    # --- re-add edge cases ---

    def test_add_allows_re_add_of_history_only_slug(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        # Seed a history row (without a pending row) by queueing then removing.
        queue.add(self.orch, {"slug": "foo", "added_at": st.utcnow(), "added_by": "operator"})
        queue.remove(self.orch, "foo")
        rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual([e["slug"] for e in queue.pending(self.orch)], ["foo"])
        hist = queue.history(self.orch)
        self.assertEqual([(e["slug"], e["outcome"]) for e in hist], [("foo", "removed")])

    def test_add_idempotency_on_currently_running_slug(self) -> None:
        # Slug is registered (treated as currently running in production) but
        # not in the pending queue — re-enqueue is allowed.
        _bootstrap(self.project, slug="foo")
        rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = [e["slug"] for e in queue.pending(self.orch)]
        self.assertEqual(slugs, ["foo"])

    # --- shape + path resolution ---

    def test_add_entry_shape(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "shape")
        main(["queue", "add", "shape", "--project", str(self.project)])
        entry = queue.pending(self.orch)[0]
        self.assertEqual(entry["slug"], "shape")
        self.assertEqual(entry["added_by"], "operator")
        self.assertEqual(entry["position_at_add"], "tail")
        self.assertIn("added_at", entry)
        # ISO-Z timestamp shape.
        self.assertTrue(entry["added_at"].endswith("Z"))

    def test_add_front_records_position_at_add(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "head")
        main(["queue", "add", "head", "--front", "--project", str(self.project)])
        entry = queue.pending(self.orch)[0]
        self.assertEqual(entry["position_at_add"], "front")

    def test_add_refuses_on_a_database_from_a_newer_clu(self) -> None:
        # The corrupt-file case it replaces cannot happen any more — a
        # transaction never leaves a half-written queue. What CAN arrive is a
        # database this clu is too old to read, and the operator-at-keyboard
        # contract is the same: refuse, name it, change nothing.
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        stamp_future_schema(self.db_path)
        import io
        from contextlib import redirect_stderr

        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("queue unreadable", err.getvalue())
        self.assertIn(str(self.db_path), err.getvalue())

    def test_add_uses_resolved_path_for_bootstrap(self) -> None:
        # Symlinked project root: `clu queue add` should accept the symlink as
        # --project and still find the registry row written under the real
        # path. registry.register canonicalises via Path.resolve(); the
        # bootstrap check must mirror that.
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        link = Path(self._tmp.name).parent / f"link-{self.project.name}"
        os.symlink(self.project, link)
        self.addCleanup(link.unlink)
        rc = main(["queue", "add", "foo", "--project", str(link)])
        self.assertEqual(rc, ExitCode.OK)

    # --- multi-arg add ---

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        import io
        from contextlib import redirect_stderr, redirect_stdout

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_add_multiple_slugs_appends_in_order(self) -> None:
        _bootstrap(self.project)
        for s in ("a", "b", "c"):
            _write_plan(self.project, s)
        rc, out, _ = self._run(["queue", "add", "a", "b", "c", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        slugs = [e["slug"] for e in queue.pending(self.orch)]
        self.assertEqual(slugs, ["a", "b", "c"])
        self.assertIn("queued at position 1", out)
        self.assertIn("queued at position 2", out)
        self.assertIn("queued at position 3", out)
        self.assertIn("queued 3 plans", out)

    def test_add_single_slug_unchanged_output(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        rc, out, _ = self._run(["queue", "add", "foo", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        # Single arg: exactly one position line, NO batch total line.
        self.assertIn("queued at position 1", out)
        self.assertNotIn("queued 1 plans", out)
        self.assertNotIn("queued 1 plan", out)

    def test_add_multiple_atomic_on_invalid_slug(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "a")
        _write_plan(self.project, "c")
        rc, _, err = self._run(
            ["queue", "add", "a", "INVALID-SLUG", "c", "--project", str(self.project)]
        )
        self.assertEqual(rc, ExitCode.INVALID_SLUG)
        self.assertIn("INVALID-SLUG", err)
        # Queue file may not even exist; if it does, must be empty.
        self.assertEqual(queue.pending(self.orch), [])

    def test_add_multiple_atomic_on_missing_plan_file(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "a")
        _write_plan(self.project, "c")
        rc, _, err = self._run(
            ["queue", "add", "a", "missing", "c", "--project", str(self.project)]
        )
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)
        self.assertIn("missing", err)
        self.assertEqual(queue.pending(self.orch), [])

    def test_add_multiple_atomic_on_within_batch_dupe(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "a")
        _write_plan(self.project, "b")
        rc, _, err = self._run(["queue", "add", "a", "b", "a", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("duplicate slug 'a' in batch", err)
        self.assertEqual(queue.pending(self.orch), [])

    def test_add_multiple_atomic_on_pre_existing_dupe(self) -> None:
        _bootstrap(self.project)
        for s in ("foo", "bar", "baz"):
            _write_plan(self.project, s)
        main(["queue", "add", "foo", "--project", str(self.project)])
        rc, _, err = self._run(
            ["queue", "add", "bar", "foo", "baz", "--project", str(self.project)]
        )
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("'foo'", err)
        self.assertIn("already queued at position 1", err)
        slugs = [e["slug"] for e in queue.pending(self.orch)]
        self.assertEqual(slugs, ["foo"])

    def test_add_multiple_front_preserves_arg_order(self) -> None:
        _bootstrap(self.project)
        for s in ("x", "y", "a", "b", "c"):
            _write_plan(self.project, s)
        main(["queue", "add", "x", "--project", str(self.project)])
        main(["queue", "add", "y", "--project", str(self.project)])
        rc, _, _ = self._run(
            ["queue", "add", "a", "b", "c", "--front", "--project", str(self.project)]
        )
        self.assertEqual(rc, ExitCode.OK)
        slugs = [e["slug"] for e in queue.pending(self.orch)]
        self.assertEqual(slugs, ["a", "b", "c", "x", "y"])

    def test_add_multiple_dispatched_in_one_transaction(self) -> None:
        from unittest import mock

        _bootstrap(self.project)
        for s in ("a", "b", "c"):
            _write_plan(self.project, s)
        real = queue.add_many
        with mock.patch.object(queue, "add_many", wraps=real) as spy:
            rc = main(["queue", "add", "a", "b", "c", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        # One insert transaction covers the whole batch.
        self.assertEqual(spy.call_count, 1)


if __name__ == "__main__":
    unittest.main()
