"""Guards the scripted demo sequence and the `clu watch` golden it produced.

The golden (`tests/goldens/watch-demo.txt`) is the whole point: p3 and p7 of
the sqlite-migration plan prove the storage swap changed no observable output
by diffing their runs against it. That argument is only worth something if two
things hold, and neither is self-evident — so both are asserted here rather
than assumed:

- the script really is deterministic (a golden recorded from a flaky script
  turns every later comparison into a coin toss), and
- the golden really is the script's output right now, on the JSON backend
  (a golden that has silently drifted would let a genuine regression pass).

The third check is the anti-vacuity one: an empty or truncated golden would
make every later `assertEqual` succeed while proving nothing, so the presence
of the lifecycle's own lines is asserted directly.
"""

from __future__ import annotations

from tests import CluTestCase, demo_script


class ScriptDeterminismTest(CluTestCase):
    def test_two_runs_emit_identical_lines(self) -> None:
        first = demo_script.capture_watch_lines(
            self.tmp_path / "run1", projects_root=self.tmp_path / "proj1"
        )
        second = demo_script.capture_watch_lines(
            self.tmp_path / "run2", projects_root=self.tmp_path / "proj2"
        )
        self.assertEqual(first, second)


class GoldenTest(CluTestCase):
    def test_golden_matches_a_live_run(self) -> None:
        live = demo_script.capture_watch_lines(
            self.tmp_path / "run", projects_root=self.tmp_path / "proj"
        )
        self.assertEqual(
            live,
            demo_script.golden_lines(),
            f"`clu watch` output drifted from {demo_script.GOLDEN_PATH}",
        )

    def test_golden_covers_the_whole_lifecycle(self) -> None:
        # Non-vacuity. The event types behind each line, in order: phase_started,
        # phase_blocked, blocker_answered, blocker_consumed, phase_completed,
        # plan_completed. `clu watch`'s default projection renders them as prose
        # — the type names are not in the text — so the lines are what we can
        # assert on.
        lines = demo_script.golden_lines()
        self.assertTrue(lines, "the golden is empty")
        slug, phase = demo_script.SLUG, demo_script.PHASE
        self.assertIn(f"{slug}/{phase}: started (attempt 1)", lines)  # phase_started
        self.assertIn(f"{slug}/{phase}: completed", lines)  # phase_completed
        self.assertTrue(
            any(line.startswith(f"{slug}/{phase}: BLOCKED ") for line in lines),
            "the golden records no blocker",
        )
        self.assertTrue(
            any("answer received" in line for line in lines),
            "the golden records no blocker answer",
        )
        self.assertIn(f"{slug}: PLAN DONE", lines)  # plan_completed


if __name__ == "__main__":
    import unittest

    unittest.main()
