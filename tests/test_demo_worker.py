"""Phase 1 of `clu demo`: the synthetic-transcript core of `demo_worker.py`.

The load-bearing test is `DemoWorkerLoadTest` — it writes a synthetic transcript
with the real record builders, then drives it through the REAL `top.gather_rows`
parser and asserts the dashboard fields light up. If that test fails, the
schema research behind the demo worker was wrong (return to EXPLORE, don't tune
the records until it passes by accident).

The unit tests below pin the pure builders: `transcript_path` (reuses
`top.encode_project_dir`), `build_records` (records carry the cwd + non-sidechain
markers the locator demands, and exercise every `extract_activity` branch), and
`append_records` (JSONL append + parent mkdir).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from end_of_line import demo_worker, registry, top
from end_of_line import state as st

from tests import GitProjectTestCase, utcnow_minus


class TranscriptPathTest(unittest.TestCase):
    def test_path_is_encoded_dir_plus_session_file(self) -> None:
        # Mirrors what the locator reconstructs: projects_root / <encoded cwd> /
        # <session_id>.jsonl. The dir name must be top's lossy encoding.
        root = Path("/tmp/projects")
        cwd = "/Users/me/my_repo"
        p = demo_worker.transcript_path(cwd, "sess-9", projects_root=root)
        self.assertEqual(p, root / top.encode_project_dir(cwd) / "sess-9.jsonl")

    def test_default_projects_root_is_tops(self) -> None:
        p = demo_worker.transcript_path("/x/a-b", "s")
        self.assertEqual(p.parent.parent, top.PROJECTS_ROOT)


class BuildRecordsTest(unittest.TestCase):
    CWD = "/x/demo-proj"
    TS = "2026-06-03T00:00:00Z"

    def _activity(self, scenario: str, step: int = 0) -> dict:
        recs = demo_worker.build_records(
            scenario, step, cwd=self.CWD, session_id="s", now=self.TS
        )
        return top.extract_activity(recs)

    def test_every_record_carries_cwd_and_is_not_sidechain(self) -> None:
        # The locator rejects a file whose identifying record lacks the real cwd
        # or is a sidechain — so EVERY synthetic record must carry both.
        for scenario in demo_worker.SCENARIOS:
            recs = demo_worker.build_records(
                scenario, 0, cwd=self.CWD, session_id="s", now=self.TS
            )
            self.assertTrue(recs, f"{scenario} produced no records")
            for rec in recs:
                self.assertEqual(rec["cwd"], self.CWD, scenario)
                self.assertFalse(rec["isSidechain"], scenario)
                self.assertEqual(rec["timestamp"], self.TS, scenario)

    def test_busy_exposes_all_dashboard_fields_with_running_command(self) -> None:
        a = self._activity("busy")
        self.assertTrue(a["last_command"])
        self.assertTrue(a["command_running"])  # busy = mid-command, no result
        self.assertTrue(a["last_write"])
        self.assertTrue(a["last_text"])
        self.assertIsInstance(a["tokens"], dict)
        self.assertEqual(a["last_activity_ts"], self.TS)

    def test_idle_command_is_resolved_not_running(self) -> None:
        # idle still shows a last command, but it has a tool_result so the
        # dashboard doesn't render a live `*` — the idle signal is ACT climbing.
        a = self._activity("idle")
        self.assertTrue(a["last_command"])
        self.assertFalse(a["command_running"])

    def test_scenarios_are_visually_distinct(self) -> None:
        # Demo value depends on the rows looking like different workers.
        cmds = {s: self._activity(s)["last_command"] for s in demo_worker.SCENARIOS}
        self.assertEqual(len(set(cmds.values())), len(demo_worker.SCENARIOS))

    def test_step_advances_write_target(self) -> None:
        w0 = self._activity("busy", 0)["last_write"]
        w1 = self._activity("busy", 1)["last_write"]
        self.assertNotEqual(w0, w1)


class AppendRecordsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_creates_parent_and_appends_jsonl(self) -> None:
        path = self.root / "deep" / "nested" / "t.jsonl"
        demo_worker.append_records(path, [{"a": 1}])
        demo_worker.append_records(path, [{"b": 2}, {"c": 3}])
        lines = path.read_text().splitlines()
        self.assertEqual([json.loads(ln) for ln in lines], [{"a": 1}, {"b": 2}, {"c": 3}])

    def test_empty_records_is_noop_but_safe(self) -> None:
        # A truly empty step touches nothing — no stray file, no mkdir.
        path = self.root / "t.jsonl"
        demo_worker.append_records(path, [])
        self.assertFalse(path.exists())


class DemoWorkerLoadTest(GitProjectTestCase):
    """THE load test: synthetic transcript -> real top.gather_rows -> live row.

    Proves the record builders match the parser contract end-to-end. Mirrors
    `tests.test_top.GatherRowsTest`: a registered plan with an active claim plus
    a transcript under a tmp projects_root yields exactly one rendered row.
    """

    def setUp(self) -> None:
        super().setUp()
        self._pr = TemporaryDirectory()
        self.addCleanup(self._pr.cleanup)
        self.projects_root = Path(self._pr.name)
        # Build the transcript's cwd field from the exact registered path string
        # so the locator confirms the match (resolve()/symlink drift would miss).
        self.reg_root = registry.entries()[0].project_root

    def test_synthetic_transcript_renders_through_real_parser(self) -> None:
        session_id = "demo-load-sess"
        self._claim("a")
        # Stamp the session_id the real demo dispatch stamps (dispatch._stamp_pid)
        # so the locator takes its deterministic <session_id>.jsonl fast path —
        # the exact branch the demo relies on — not the cwd-glob fallback.
        with st.mutate(self.state_path) as data:
            data["current_claim"]["session_id"] = session_id
        ts = utcnow_minus(3)  # 3s ago -> fresh ACT against the real clock
        path = demo_worker.transcript_path(
            self.reg_root, session_id, projects_root=self.projects_root
        )
        recs = demo_worker.build_records(
            "busy", 0, cwd=self.reg_root, session_id=session_id, now=ts
        )
        demo_worker.append_records(path, recs)

        rows = top.gather_rows(projects_root=self.projects_root)

        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["plan"], "test-plan")
        self.assertEqual(r["phase_id"], "a")
        self.assertTrue(r["last_command"])
        self.assertTrue(r["command_running"])
        self.assertTrue(r["last_write"])
        self.assertTrue(r["last_text"])
        self.assertIsInstance(r["tokens"], dict)
        self.assertIsNotNone(r["last_activity_seconds"])
        self.assertLess(r["last_activity_seconds"], 10)
        self.assertGreaterEqual(r["last_activity_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
