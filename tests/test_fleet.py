"""Fleet view — bare `clu` walks the registry and renders a plan-per-line summary."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from end_of_line import fleet, registry, state as st
from end_of_line.cli import main
from tests import isolate_registry


class SummarizePlanTestCase(unittest.TestCase):
    """summarize_plan(entry) — pure projection from a state file. No I/O on output."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.project = self.tmp / "proj"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, slug: str, mutate=None) -> registry.PlanEntry:
        sp = self.project / "plans" / ".orchestrator" / f"{slug}.state.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        with st.locked(sp):
            data = st.empty_state(slug, "plans")
            if mutate:
                mutate(data)
            st.save_atomic(sp, data)
        return registry.PlanEntry(
            project_root=str(self.project), plan_slug=slug,
            registered_at=st.utcnow(),
        )

    def test_fresh_plan_running_no_phase(self) -> None:
        entry = self._seed("plan-a")
        summary = fleet.summarize_plan(entry)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.plan_slug, "plan-a")
        self.assertEqual(summary.status, st.STATUS_RUNNING)
        self.assertIsNone(summary.current_phase)
        self.assertEqual(summary.open_blocker_count, 0)

    def test_active_claim_surfaces_current_phase(self) -> None:
        def m(data):
            st.claim_phase(data, "phase-1", lease_minutes=30)
        entry = self._seed("plan-a", mutate=m)
        summary = fleet.summarize_plan(entry)
        self.assertEqual(summary.current_phase, "phase-1")

    def test_open_blocker_count(self) -> None:
        def m(data):
            st.add_blocker(data, "p", "?", ["A"])
            st.add_blocker(data, "p", "?", ["B"])
            data["blockers"][0]["answer"] = "A"
        entry = self._seed("plan-a", mutate=m)
        summary = fleet.summarize_plan(entry)
        self.assertEqual(summary.open_blocker_count, 1)

    def test_halted_status_shown_verbatim(self) -> None:
        def m(data):
            data["status"] = st.STATUS_HALTED
        entry = self._seed("plan-a", mutate=m)
        summary = fleet.summarize_plan(entry)
        self.assertEqual(summary.status, st.STATUS_HALTED)

    def test_stalled_claim_overrides_status_label(self) -> None:
        # Stalled is derived, not stored — fleet view has to compute it.
        def m(data):
            st.claim_phase(data, "phase-1", lease_minutes=30)
            data["current_claim"]["last_heartbeat_at"] = "2020-01-01T00:00:00Z"
        entry = self._seed("plan-a", mutate=m)
        summary = fleet.summarize_plan(entry)
        self.assertEqual(summary.status, "stalled")
        self.assertEqual(summary.current_phase, "phase-1")

    def test_last_event_age_from_most_recent_event(self) -> None:
        def m(data):
            st.append_event(data, st.EVENT_PHASE_STARTED, phase="p")
        entry = self._seed("plan-a", mutate=m)
        summary = fleet.summarize_plan(entry)
        self.assertIsNotNone(summary.last_event_age_seconds)
        self.assertLess(summary.last_event_age_seconds, 5)

    def test_missing_state_returns_none(self) -> None:
        # Registered but never `clu init`-ed.
        entry = registry.PlanEntry(
            project_root=str(self.project), plan_slug="not-yet-init",
            registered_at=st.utcnow(),
        )
        self.assertIsNone(fleet.summarize_plan(entry))


class HumanizeAgeTestCase(unittest.TestCase):
    def test_all_buckets(self) -> None:
        cases = [
            (None, "-"),
            (45, "45s"),
            (300, "5m"),
            (7200, "2.0h"),
            (86400 * 3, "3.0d"),
        ]
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(fleet.humanize_age(seconds), expected)


class FleetCommandTestCase(unittest.TestCase):
    """Bare `clu` — argparse dispatches to the fleet view."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        isolate_registry(self, self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, argv: list[str]) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        self.assertEqual(rc, 0, msg=f"non-zero rc; stdout was: {buf.getvalue()!r}")
        return buf.getvalue()

    def _seed_plan(self, slug: str, mutate=None) -> Path:
        project = self.tmp / slug
        project.mkdir()
        (project / "plans").mkdir()
        sp = project / "plans" / ".orchestrator" / f"{slug}.state.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        with st.locked(sp):
            data = st.empty_state(slug, "plans")
            if mutate:
                mutate(data)
            st.save_atomic(sp, data)
        registry.register(project, slug)
        return project

    def test_empty_registry_prints_helpful_message(self) -> None:
        out = self._run([])
        self.assertIn("No plans registered", out)

    def test_single_plan_renders_one_line(self) -> None:
        self._seed_plan("plan-a")
        out = self._run([])
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        self.assertIn("plan-a", lines[1])
        self.assertIn("running", lines[1])

    def test_open_blocker_count_surfaces(self) -> None:
        def m(data):
            st.add_blocker(data, "p", "?", ["A", "B"])
        self._seed_plan("plan-a", mutate=m)
        out = self._run([])
        self.assertIn("plan-a", out)
        self.assertRegex(out, r"plan-a.*\b1\b")  # one open blocker

    def test_stalled_plan_labeled(self) -> None:
        def m(data):
            st.claim_phase(data, "phase-1", lease_minutes=30)
            data["current_claim"]["last_heartbeat_at"] = "2020-01-01T00:00:00Z"
        self._seed_plan("plan-a", mutate=m)
        out = self._run([])
        self.assertIn("stalled", out)

    def test_two_plans_both_rendered(self) -> None:
        self._seed_plan("plan-a")
        self._seed_plan("plan-b")
        out = self._run([])
        self.assertIn("plan-a", out)
        self.assertIn("plan-b", out)

    def test_uninitialized_registry_entry_shown_as_missing(self) -> None:
        # Registered but state never written — must not crash the whole view.
        project = self.tmp / "ghost"
        project.mkdir()
        registry.register(project, "plan-ghost")
        self._seed_plan("plan-a")
        out = self._run([])
        self.assertIn("plan-ghost", out)
        self.assertIn("plan-a", out)
        # Some marker indicating the state is missing — exact text TBD by impl,
        # but it must NOT crash and must NOT skip the line silently.
        self.assertIn("missing", out.lower())


if __name__ == "__main__":
    unittest.main()
