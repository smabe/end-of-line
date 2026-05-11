"""Unit tests for end_of_line.plan_parser."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from end_of_line.plan_parser import parse_sessions_index


WATCH_PLAN = """\
# Watch — Start Workout

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A — Foundation | `watch-start-workout-a-foundation.md` | Phase 0 + Phase 1 | 2-3 hr |
| B — Extract | `watch-start-workout-b-extract.md` | Phase 1.5 | 2-3 hr |
| C — Feature | `watch-start-workout-c-feature.md` | Phase 2 + Phase 3 | 6-8 hr |

## Goal
Stuff.
"""


class TestSessionsIndex(unittest.TestCase):
    def _write(self, body: str, name: str = "watch-start-workout.md") -> Path:
        d = tempfile.mkdtemp()
        p = Path(d) / name
        p.write_text(body)
        return p

    def test_parses_three_phases(self) -> None:
        p = self._write(WATCH_PLAN)
        phases = parse_sessions_index(p)
        self.assertEqual(len(phases), 3)
        ids = [ph.id for ph in phases]
        self.assertEqual(ids, ["a-foundation", "b-extract", "c-feature"])

    def test_extracts_scope_and_effort(self) -> None:
        p = self._write(WATCH_PLAN)
        phases = parse_sessions_index(p)
        self.assertEqual(phases[0].scope, "Phase 0 + Phase 1")
        self.assertEqual(phases[0].effort, "2-3 hr")

    def test_returns_empty_when_no_sessions_index(self) -> None:
        p = self._write("# Plain plan\n\n## Goal\nThing.\n")
        self.assertEqual(parse_sessions_index(p), [])

    def test_handles_no_backticks(self) -> None:
        body = """\
# Plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | foo-a.md | thing | 1h |
"""
        p = self._write(body, name="foo.md")
        phases = parse_sessions_index(p)
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].id, "a")
        self.assertEqual(phases[0].plan_file, "foo-a.md")


if __name__ == "__main__":
    unittest.main()
