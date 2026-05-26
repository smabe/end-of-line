"""Phase `list` tests: `clu queue list` + bare `clu queue` alias.

Covers the empty path, status projection from the host registry, the
head-freeze marker, missing-plan-file rendering, the optional failure
history section, and the dispatch shape for the no-subcommand form.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from end_of_line import queue, registry, state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import ProjectConfig
from tests import isolate_queue

_PLAN_BODY = "# placeholder plan\n"


def _write_plan(project: Path, slug: str) -> Path:
    plans_dir = project / "plans"
    plans_dir.mkdir(exist_ok=True)
    path = plans_dir / f"{slug}.md"
    path.write_text(_PLAN_BODY)
    return path


def _bootstrap(project: Path, slug: str = "seed-plan") -> None:
    _write_plan(project, slug)
    registry.register(project, slug)


def _add(project: Path, slug: str) -> None:
    main(["queue", "add", slug, "--project", str(project)])


class QueueListTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        isolate_queue(self, self.project)
        self.queue_path = ProjectConfig(project_root=self.project).queue_path()

    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_list_empty_queue(self) -> None:
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("(queue is empty)", out)

    def test_list_one_pending(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        _add(self.project, "foo")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("POS", out)
        self.assertIn("SLUG", out)
        self.assertIn("STATUS", out)
        self.assertIn("NOTE", out)
        self.assertIn("foo", out)
        self.assertIn("queued", out)

    def test_list_multiple_pending_preserves_order(self) -> None:
        _bootstrap(self.project)
        for slug in ("a", "b", "c"):
            _write_plan(self.project, slug)
            _add(self.project, slug)
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        pos_a = out.find(" a ")
        pos_b = out.find(" b ")
        pos_c = out.find(" c ")
        self.assertGreater(pos_a, -1)
        self.assertLess(pos_a, pos_b)
        self.assertLess(pos_b, pos_c)

    def test_list_renders_running_status_from_registry(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        # Init writes state.json with status=running + registers foo.
        main(["init", "--project", str(self.project), "--plan", "foo"])
        _add(self.project, "foo")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("running", out)

    def test_list_renders_halted_freeze_marker(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        main(["init", "--project", str(self.project), "--plan", "foo"])
        cfg = ProjectConfig(project_root=self.project)
        with st.mutate(cfg.state_path("foo")) as data:
            data["status"] = st.STATUS_HALTED
        _add(self.project, "foo")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("chain frozen at head", out)

    def test_list_renders_paused_freeze_marker(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        main(["init", "--project", str(self.project), "--plan", "foo"])
        cfg = ProjectConfig(project_root=self.project)
        with st.mutate(cfg.state_path("foo")) as data:
            data["status"] = st.STATUS_PAUSED
        _add(self.project, "foo")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("chain frozen at head", out)

    def test_list_freeze_marker_only_when_head_halted(self) -> None:
        # Halted plan elsewhere (not at head) must NOT trigger the marker.
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        _write_plan(self.project, "bar")
        main(["init", "--project", str(self.project), "--plan", "bar"])
        cfg = ProjectConfig(project_root=self.project)
        with st.mutate(cfg.state_path("bar")) as data:
            data["status"] = st.STATUS_HALTED
        # Order: foo (head, unregistered, queued), then bar (halted).
        _add(self.project, "foo")
        _add(self.project, "bar")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertNotIn("chain frozen at head", out)

    def test_list_renders_missing_plan_file(self) -> None:
        _bootstrap(self.project)
        plan = _write_plan(self.project, "foo")
        _add(self.project, "foo")
        plan.unlink()
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("plan file missing", out)

    def test_list_renders_failure_history_when_present(self) -> None:
        _bootstrap(self.project)
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        with queue.mutate(self.queue_path) as data:
            data["history"].append(
                {
                    "slug": "alpha",
                    "outcome": "abandoned",
                    "ended_at": st.utcnow(),
                }
            )
            data["history"].append(
                {
                    "slug": "beta",
                    "outcome": "removed",
                    "ended_at": st.utcnow(),
                }
            )
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("Recent failures:", out)
        self.assertIn("alpha", out)
        self.assertIn("abandoned", out)
        self.assertIn("beta", out)
        self.assertIn("removed", out)

    def test_list_omits_failure_section_when_history_empty(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "foo")
        _add(self.project, "foo")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertNotIn("Recent failures:", out)

    def test_list_bare_clu_queue_defaults_to_list(self) -> None:
        old_cwd = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, str(old_cwd))
        rc, out = self._run(["queue"])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("(queue is empty)", out)

    def test_list_unregistered_project(self) -> None:
        # No bootstrap → registry has no row for this project. List must not
        # error out (bootstrap is only enforced on `add`).
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("(queue is empty)", out)

    def test_list_refuses_on_corrupt_queue(self) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text("{not valid json")
        from contextlib import redirect_stderr

        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.GENERIC)
        diagnosis = err.getvalue()
        self.assertIn("queue.json corrupt", diagnosis)
        self.assertIn(str(self.queue_path), diagnosis)
        self.assertIn("Open Claude in this project to repair", diagnosis)

    def test_list_diagnosis_mentions_backup_paths(self) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text("garbage")
        backup = self.queue_path.with_name(self.queue_path.name + ".corrupt-20260101T000000Z")
        backup.write_text("{}")
        from contextlib import redirect_stderr

        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn(backup.name, err.getvalue())

    def test_list_diagnosis_when_no_backup_present(self) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text("garbage")
        from contextlib import redirect_stderr

        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("No backup files found", err.getvalue())

    def test_list_handles_missing_queue_file(self) -> None:
        self.assertFalse(self.queue_path.exists())
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("(queue is empty)", out)
        # List must NOT create the queue file as a side effect.
        self.assertFalse(self.queue_path.exists())

    # --- in-flight footer ---

    def _claim_plan(
        self,
        slug: str,
        *,
        started_at: str = "2026-05-12T10:00:00Z",
        lease_expires: str = "2026-05-12T10:30:00Z",
        last_heartbeat_at: str | None = None,
    ) -> None:
        """Register `slug` and write a state.json with an active claim."""
        _write_plan(self.project, slug)
        main(["init", "--project", str(self.project), "--plan", slug])
        cfg = ProjectConfig(project_root=self.project)
        with st.mutate(cfg.state_path(slug)) as data:
            data["current_claim"] = {
                "phase_id": "x",
                "claimed_by": f"session-{slug}-00000000",
                "lease_expires": lease_expires,
                "started_at": started_at,
                "last_heartbeat_at": last_heartbeat_at or started_at,
                "attempts": 1,
            }

    def test_list_in_flight_footer_when_plan_dispatched(self) -> None:
        # Plan was popped from queue and is now in flight (pending is empty).
        self._claim_plan("foo")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("(queue is empty)", out)
        self.assertIn("In flight: foo", out)
        self.assertIn("dispatched 10:00:00 UTC", out)
        self.assertIn("lease until 10:30:00 UTC", out)

    def test_list_in_flight_footer_with_pending(self) -> None:
        self._claim_plan("foo")
        _write_plan(self.project, "bar")
        _add(self.project, "bar")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("bar", out)
        self.assertIn("In flight: foo", out)

    def test_list_no_in_flight_footer_when_empty(self) -> None:
        _bootstrap(self.project)
        _write_plan(self.project, "bar")
        _add(self.project, "bar")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertNotIn("In flight:", out)

    def test_list_in_flight_sorts_by_started_at(self) -> None:
        # Two in-flight plans — sort by started_at ascending.
        self._claim_plan(
            "later",
            started_at="2026-05-12T11:00:00Z",
            lease_expires="2026-05-12T11:30:00Z",
        )
        self._claim_plan(
            "earlier",
            started_at="2026-05-12T09:00:00Z",
            lease_expires="2026-05-12T09:30:00Z",
        )
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        i_earlier = out.find("In flight: earlier")
        i_later = out.find("In flight: later")
        self.assertGreater(i_earlier, -1)
        self.assertGreater(i_later, -1)
        self.assertLess(i_earlier, i_later)

    def test_list_in_flight_includes_stalled(self) -> None:
        # Past-lease claim — still surface in the footer.
        self._claim_plan(
            "foo",
            started_at="2026-05-11T10:00:00Z",
            lease_expires="2026-05-11T10:30:00Z",
            last_heartbeat_at="2026-05-11T10:00:00Z",
        )
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("In flight: foo", out)

    def test_list_in_flight_omits_slug_also_in_pending(self) -> None:
        # Defensive: if the same slug appears in pending AND has a claim,
        # don't double-report it in the footer.
        self._claim_plan("foo")
        _add(self.project, "foo")
        rc, out = self._run(["queue", "list", "--project", str(self.project)])
        self.assertEqual(rc, ExitCode.OK)
        self.assertNotIn("In flight: foo", out)


if __name__ == "__main__":
    unittest.main()
