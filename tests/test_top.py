"""Phase 1 of `clu top`: transcript locator + tail reader + field extractor.

These are pure functions over Claude Code session-transcript JSONL files —
no registry/XDG state, so plain `unittest.TestCase` (not CluTestCase).

The locator is the load-bearing piece: the `~/.claude/projects/<enc>` directory
name is a lossy, non-reversible encoding of the worker's cwd, and a single dir
holds many session files (retries) plus separate sidechain files. So we never
trust the dir name or "newest mtime" alone — we confirm each candidate by the
in-file `cwd` field and reject `isSidechain` transcripts.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from end_of_line import registry, top
from end_of_line import state as st

from tests import GitProjectTestCase


def _write_jsonl(path: Path, records: list[dict], *, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _asst(text=None, tool=None, tool_input=None, tool_id=None, usage=None, ts="2026-06-03T00:00:00Z", cwd="/x/a-b"):
    content: list[dict] = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool is not None:
        content.append({"type": "tool_use", "name": tool, "input": tool_input or {}, "id": tool_id or "tu1"})
    msg: dict = {"role": "assistant", "content": content}
    if usage is not None:
        msg["usage"] = usage
    return {"type": "assistant", "timestamp": ts, "cwd": cwd, "isSidechain": False, "message": msg}


def _tool_result(tool_id, ts="2026-06-03T00:00:01Z", cwd="/x/a-b"):
    return {
        "type": "user",
        "timestamp": ts,
        "cwd": cwd,
        "isSidechain": False,
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]},
    }


class EncodeProjectDirTest(unittest.TestCase):
    def test_lossy_mapping_slash_underscore_dot_all_become_dash(self) -> None:
        # Every non-(ascii-alnum-or-dash) char collapses to '-', leading slash too.
        self.assertEqual(top.encode_project_dir(Path("/Users/me/my-project")), "-Users-me-my-project")
        self.assertEqual(top.encode_project_dir(Path("/x/a_b")), "-x-a-b")
        self.assertEqual(top.encode_project_dir(Path("/x/site.com")), "-x-site-com")

    def test_collision_two_cwds_one_dirname(self) -> None:
        # The encoding is non-reversible: '_' and '-' collide. This is exactly
        # why the locator must confirm via the in-file cwd field.
        self.assertEqual(
            top.encode_project_dir(Path("/x/a_b")),
            top.encode_project_dir(Path("/x/a-b")),
        )


class LocateTranscriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_picks_cwd_match_over_newer_decoy_and_sidechain(self) -> None:
        # THE load test. One encoded dir holds three files:
        #   target  — cwd matches, main session, OLDEST
        #   decoy   — collides to same dirname but cwd is a different path, NEWER
        #   side    — cwd matches but isSidechain=True, NEWEST
        # Correct answer is `target`, proving we confirm cwd AND reject sidechains
        # rather than taking newest-mtime.
        target_cwd = "/x/a-b"
        d = self.root / top.encode_project_dir(Path(target_cwd))
        target = _write_jsonl(d / "target.jsonl", [_asst(text="hi", cwd=target_cwd)], mtime=1000)
        _write_jsonl(d / "decoy.jsonl", [_asst(text="other", cwd="/x/a_b")], mtime=2000)
        side = [_asst(text="sub", cwd=target_cwd)]
        side[0]["isSidechain"] = True
        _write_jsonl(d / "side.jsonl", side, mtime=3000)

        self.assertEqual(top.locate_transcript(Path(target_cwd), projects_root=self.root), target)

    def test_picks_newest_among_genuine_cwd_matches(self) -> None:
        cwd = "/x/a-b"
        d = self.root / top.encode_project_dir(Path(cwd))
        _write_jsonl(d / "old.jsonl", [_asst(cwd=cwd)], mtime=1000)
        new = _write_jsonl(d / "new.jsonl", [_asst(cwd=cwd)], mtime=5000)
        self.assertEqual(top.locate_transcript(Path(cwd), projects_root=self.root), new)

    def test_none_when_dir_missing(self) -> None:
        self.assertIsNone(top.locate_transcript(Path("/no/such/cwd"), projects_root=self.root))

    def test_none_when_no_file_confirms_cwd(self) -> None:
        cwd = "/x/a-b"
        d = self.root / top.encode_project_dir(Path(cwd))
        _write_jsonl(d / "decoy.jsonl", [_asst(cwd="/x/a_b")], mtime=2000)
        self.assertIsNone(top.locate_transcript(Path(cwd), projects_root=self.root))

    def test_identity_scan_skips_cwd_less_meta_lines(self) -> None:
        # Real transcripts open with meta records that carry no cwd; the scan
        # must keep reading until it finds a cwd-bearing line.
        cwd = "/x/a-b"
        d = self.root / top.encode_project_dir(Path(cwd))
        recs = [{"type": "file-history-snapshot", "timestamp": "2026-06-03T00:00:00Z"}, _asst(cwd=cwd)]
        f = _write_jsonl(d / "s.jsonl", recs, mtime=1000)
        self.assertEqual(top.locate_transcript(Path(cwd), projects_root=self.root), f)

    def test_session_id_fast_path(self) -> None:
        cwd = "/x/a-b"
        d = self.root / top.encode_project_dir(Path(cwd))
        f = _write_jsonl(d / "abc-123.jsonl", [_asst(cwd=cwd)], mtime=1000)
        self.assertEqual(
            top.locate_transcript(Path(cwd), projects_root=self.root, session_id="abc-123"), f
        )

    def test_session_id_missing_file_returns_none(self) -> None:
        cwd = "/x/a-b"
        (self.root / top.encode_project_dir(Path(cwd))).mkdir(parents=True)
        self.assertIsNone(
            top.locate_transcript(Path(cwd), projects_root=self.root, session_id="nope")
        )

    def test_session_id_missing_file_falls_back_to_cwd_match(self) -> None:
        # A stamped id whose exact file isn't present yet must not blank the
        # worker — fall back to confirming a cwd-matching transcript.
        cwd = "/x/a-b"
        d = self.root / top.encode_project_dir(Path(cwd))
        f = _write_jsonl(d / "actual.jsonl", [_asst(cwd=cwd)], mtime=1000)
        self.assertEqual(
            top.locate_transcript(Path(cwd), projects_root=self.root, session_id="not-written-yet"),
            f,
        )

    def test_session_id_rejects_sidechain_and_cwd_mismatch(self) -> None:
        # The deterministic-id path must still confirm: a misrouted id pointing
        # at a sidechain (or another cwd's session) must not surface.
        cwd = "/x/a-b"
        d = self.root / top.encode_project_dir(Path(cwd))
        side = [_asst(cwd=cwd)]
        side[0]["isSidechain"] = True
        _write_jsonl(d / "sc.jsonl", side, mtime=1000)
        _write_jsonl(d / "other.jsonl", [_asst(cwd="/x/a_b")], mtime=1000)
        self.assertIsNone(top.locate_transcript(Path(cwd), projects_root=self.root, session_id="sc"))
        self.assertIsNone(top.locate_transcript(Path(cwd), projects_root=self.root, session_id="other"))

    def test_explicit_null_cwd_line_is_skipped_not_taken(self) -> None:
        # A meta record with "cwd": null must not short-circuit identity; the
        # later real cwd line is the answer.
        cwd = "/x/a-b"
        d = self.root / top.encode_project_dir(Path(cwd))
        recs = [{"type": "meta", "cwd": None, "timestamp": "2026-06-03T00:00:00Z"}, _asst(cwd=cwd)]
        f = _write_jsonl(d / "s.jsonl", recs, mtime=1000)
        self.assertEqual(top.locate_transcript(Path(cwd), projects_root=self.root), f)


class TailRecordsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_returns_last_n(self) -> None:
        f = _write_jsonl(self.dir / "f.jsonl", [_asst(text=str(i)) for i in range(10)])
        out = top.tail_records(f, want=3)
        self.assertEqual([b["text"] for r in out for b in r["message"]["content"]][-3:], ["7", "8", "9"])

    def test_skips_truncated_final_line(self) -> None:
        # Simulate a writer mid-append: a valid line, then a half-written one
        # with no trailing newline and broken JSON.
        f = self.dir / "f.jsonl"
        f.write_text(json.dumps(_asst(text="good")) + "\n" + '{"type":"assistant","mess')
        out = top.tail_records(f, want=10)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["message"]["content"][0]["text"], "good")

    def test_empty_file(self) -> None:
        f = self.dir / "f.jsonl"
        f.write_text("")
        self.assertEqual(top.tail_records(f), [])


class ExtractActivityTest(unittest.TestCase):
    def test_pulls_last_of_each_kind(self) -> None:
        recs = [
            _asst(text="thinking", tool="Bash", tool_input={"command": "echo old"}, tool_id="b1", ts="2026-06-03T00:00:00Z"),
            _asst(tool="Write", tool_input={"file_path": "/repo/foo.py"}, tool_id="w1", ts="2026-06-03T00:00:05Z"),
            _asst(text="tests pass", tool="Bash", tool_input={"command": "pytest -k routing"}, tool_id="b2",
                  usage={"input_tokens": 10, "output_tokens": 20}, ts="2026-06-03T00:00:09Z"),
        ]
        a = top.extract_activity(recs)
        self.assertEqual(a["last_command"], "pytest -k routing")
        self.assertEqual(a["last_write"], "/repo/foo.py")
        self.assertEqual(a["last_text"], "tests pass")
        self.assertEqual(a["last_activity_ts"], "2026-06-03T00:00:09Z")
        self.assertEqual(a["tokens"], {"input_tokens": 10, "output_tokens": 20})

    def test_running_when_last_bash_has_no_result(self) -> None:
        recs = [_asst(tool="Bash", tool_input={"command": "sleep 30"}, tool_id="b9")]
        self.assertTrue(top.extract_activity(recs)["command_running"])

    def test_not_running_when_result_present(self) -> None:
        recs = [
            _asst(tool="Bash", tool_input={"command": "ls"}, tool_id="b9"),
            _tool_result("b9"),
        ]
        self.assertFalse(top.extract_activity(recs)["command_running"])

    def test_defensive_string_content_and_unknown_types(self) -> None:
        # message.content can be a bare string; unknown top-level types and
        # missing fields must not crash (schema drifts across CC versions).
        recs = [
            {"type": "mode", "timestamp": "2026-06-03T00:00:00Z"},
            {"type": "assistant", "timestamp": "2026-06-03T00:00:01Z",
             "message": {"role": "assistant", "content": "plain string reply"}},
            {"type": "future-unknown-kind", "timestamp": "2026-06-03T00:00:02Z"},
        ]
        a = top.extract_activity(recs)
        self.assertEqual(a["last_text"], "plain string reply")
        self.assertIsNone(a["last_command"])
        self.assertEqual(a["last_activity_ts"], "2026-06-03T00:00:02Z")

    def test_empty_records(self) -> None:
        a = top.extract_activity([])
        self.assertIsNone(a["last_command"])
        self.assertIsNone(a["last_write"])
        self.assertIsNone(a["last_activity_ts"])
        self.assertFalse(a["command_running"])


class AssembleRowTest(unittest.TestCase):
    def _now(self) -> _dt.datetime:
        return _dt.datetime(2026, 6, 3, 0, 10, 0, tzinfo=_dt.UTC)

    def test_ran_seconds_and_alive_pid(self) -> None:
        # started 600s before `now`; pid is this live test process.
        claim = {
            "phase_id": "routing",
            "started_at": "2026-06-03T00:00:00Z",
            "last_heartbeat_at": "2026-06-03T00:09:30Z",
            "pid": os.getpid(),
        }
        activity = {"last_command": "pytest", "last_write": "/r/a.py", "last_text": "ok",
                    "last_activity_ts": "2026-06-03T00:09:55Z", "command_running": False, "tokens": None}
        row = top.assemble_row(claim, activity, now=self._now())
        self.assertEqual(row["phase_id"], "routing")
        self.assertAlmostEqual(row["ran_seconds"], 600, delta=1)
        self.assertAlmostEqual(row["heartbeat_age_seconds"], 30, delta=1)
        self.assertTrue(row["alive"])
        self.assertEqual(row["last_command"], "pytest")

    def test_last_write_age_surfaced(self) -> None:
        claim = {"phase_id": "p", "started_at": "2026-06-03T00:00:00Z", "pid": os.getpid()}
        activity = {"last_command": None, "last_write": "/r/a.py",
                    "last_write_ts": "2026-06-03T00:09:00Z", "last_text": None,
                    "last_activity_ts": "2026-06-03T00:09:00Z", "command_running": False, "tokens": None}
        row = top.assemble_row(claim, activity, now=self._now())
        self.assertAlmostEqual(row["last_write_seconds"], 60, delta=1)

    def test_dead_pid_flagged_not_idle(self) -> None:
        claim = {"phase_id": "p", "started_at": "2026-06-03T00:00:00Z", "pid": 999999}
        row = top.assemble_row(claim, {"last_command": None, "last_write": None, "last_text": None,
                                       "last_activity_ts": None, "command_running": False, "tokens": None},
                               now=self._now())
        self.assertFalse(row["alive"])


class GatherRowsTest(GitProjectTestCase):
    """End-to-end: registered plan + active claim + transcript -> one row."""

    def setUp(self) -> None:
        super().setUp()
        self._pr = TemporaryDirectory()
        self.addCleanup(self._pr.cleanup)
        self.projects_root = Path(self._pr.name)
        # The registered project_root is resolved at register time; build the
        # transcript's dir + cwd field from that exact string so the locator
        # confirms the match (resolve()/symlink drift would otherwise miss).
        self.reg_root = registry.entries()[0].project_root

    def _transcript(self, records: list[dict]) -> Path:
        d = self.projects_root / top.encode_project_dir(self.reg_root)
        return _write_jsonl(d / "sess.jsonl", records, mtime=1000)

    def test_active_claim_with_transcript_becomes_row(self) -> None:
        self._claim("a")
        self._transcript([
            _asst(tool="Bash", tool_input={"command": "pytest -q"}, tool_id="b1", cwd=self.reg_root),
            _asst(tool="Write", tool_input={"file_path": "/r/x.py"}, tool_id="w1", cwd=self.reg_root),
        ])
        rows = top.gather_rows(projects_root=self.projects_root)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["plan"], "test-plan")
        self.assertEqual(r["phase_id"], "a")
        self.assertEqual(r["last_command"], "pytest -q")
        self.assertEqual(r["last_write"], "/r/x.py")
        self.assertTrue(r["alive"])  # claim has no pid -> liveness probe True

    def test_no_active_claim_no_row(self) -> None:
        self.assertEqual(top.gather_rows(projects_root=self.projects_root), [])

    def test_active_claim_without_transcript_still_rows(self) -> None:
        self._claim("a")
        rows = top.gather_rows(projects_root=self.projects_root)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["last_command"])
        self.assertEqual(rows[0]["phase_id"], "a")


class FormatRowsTest(unittest.TestCase):
    def _row(self, **over) -> dict:
        base = {
            "project": "myrepo", "plan": "routing", "phase_id": "impl",
            "ran_seconds": 600, "heartbeat_age_seconds": 18, "alive": True,
            "last_command": "pytest -k routing", "command_running": False,
            "last_write": "/repo/routing.py", "last_write_seconds": 4,
            "last_text": "tests pass, wiring next", "last_activity_seconds": 2, "tokens": None,
        }
        base.update(over)
        return base

    def test_header_and_row_fields_present(self) -> None:
        lines = top.format_rows([self._row()])
        self.assertTrue(any("PLAN" in ln and "RAN" in ln for ln in lines))
        body = "\n".join(lines[1:])
        for token in ("routing", "impl", "pytest -k routing", "routing.py"):
            self.assertIn(token, body)

    def test_running_indicator(self) -> None:
        lines = top.format_rows([self._row(command_running=True)])
        self.assertIn("*", "\n".join(lines[1:]))

    def test_dead_worker_marked(self) -> None:
        lines = top.format_rows([self._row(alive=False)])
        self.assertIn("dead", "\n".join(lines[1:]).lower())

    def test_multiline_text_stays_one_row(self) -> None:
        # A multi-line assistant message or command must not spill a worker
        # across rows / corrupt the grid — newlines collapse to spaces.
        lines = top.format_rows([self._row(last_text="line1\nline2\nline3",
                                           last_command="git commit -m 'a\nb'")], width=200)
        self.assertEqual(len(lines), 2)  # header + exactly one row
        self.assertNotIn("\n", lines[1])
        self.assertIn("line1 line2 line3", lines[1])
        self.assertIn("git commit -m 'a b'", lines[1])

    def test_clamped_to_width(self) -> None:
        lines = top.format_rows([self._row(last_text="x" * 500)], width=60)
        self.assertTrue(all(len(ln) <= 60 for ln in lines))

    def test_wide_terminal_shows_full_name_and_command(self) -> None:
        # The bug report: a wide terminal still truncated name/command because
        # the caps were hardcoded. Columns must expand to fill the width.
        r = self._row(
            plan="workout-logging-bulletproof",
            last_command="cd /Users/smabe/projects/HealthData-workout-logging-bulletproof && pytest",
            last_text="y" * 40,
        )
        body = top.format_rows([r], width=400)[1]
        self.assertNotIn("…", body)
        self.assertIn("workout-logging-bulletproof", body)
        self.assertIn("HealthData-workout-logging-bulletproof && pytest", body)


class FormatDetailTest(unittest.TestCase):
    def _row(self, **over) -> dict:
        base = {
            "project": "myrepo", "plan": "routing", "phase_id": "impl",
            "ran_seconds": 600, "heartbeat_age_seconds": 18, "alive": True,
            "last_command": "pytest", "command_running": True,
            "last_write": "/repo/x.py", "last_write_seconds": 4,
            "last_text": "ok", "last_activity_seconds": 2, "tokens": None,
        }
        base.update(over)
        return base

    def test_long_command_wraps_not_truncated(self) -> None:
        cmd = "cd /a/very/long/path && " + " ".join(f"token{i}" for i in range(40))
        lines = top.format_detail([self._row(last_command=cmd)], width=60)
        self.assertTrue(all(len(ln) <= 60 for ln in lines))
        joined = " ".join(ln.strip() for ln in lines)
        self.assertNotIn("…", joined)
        self.assertIn("token39", joined)  # last token survived via wrapping

    def test_empty(self) -> None:
        self.assertEqual(top.format_detail([]), ["(no active workers)"])

    def test_empty_rows_still_has_header(self) -> None:
        lines = top.format_rows([])
        self.assertTrue(lines and "PLAN" in lines[0])


class RenderOnceTest(GitProjectTestCase):
    def test_writes_snapshot_to_stream(self) -> None:
        self._claim("a")
        out = io.StringIO()
        rc = top.render_once(out, projects_root=Path(self.tmp_path) / "noproj")
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("test-plan", text)
        self.assertIn("PLAN", text)


class HumanAgeTest(unittest.TestCase):
    def test_formats(self) -> None:
        self.assertEqual(top.human_age(0), "0s")
        self.assertEqual(top.human_age(5), "5s")
        self.assertEqual(top.human_age(90), "1m30s")
        self.assertEqual(top.human_age(3700), "1h01m")
        self.assertEqual(top.human_age(None), "—")


# --- Phase 0: the Surface/Rect rendering seam (clu-top-tui) -----------------


def _draw_row(**over) -> dict:
    """A populated render row to stress the draw path through a Surface."""
    base = {
        "project": "myrepo", "plan": "routing", "phase_id": "impl",
        "ran_seconds": 600, "heartbeat_age_seconds": 18, "alive": True,
        "last_command": "pytest -k routing", "command_running": True,
        "last_write": "/repo/routing.py", "last_write_seconds": 4,
        "last_text": "tests pass, wiring next", "last_activity_seconds": 2, "tokens": None,
    }
    base.update(over)
    return base


class RectTest(unittest.TestCase):
    def test_frozen_geometry(self) -> None:
        from end_of_line.top_render import Rect

        r = Rect(1, 2, 30, 10)
        self.assertEqual((r.x, r.y, r.w, r.h), (1, 2, 30, 10))
        with self.assertRaises(Exception):
            r.x = 5  # frozen — assignment must fail

    def test_value_semantics(self) -> None:
        # Frozen → hashable + equal-by-value: the layout engine will key dicts
        # and dedup regions by Rect, so value semantics are part of the contract.
        from end_of_line.top_render import Rect

        self.assertEqual(Rect(1, 2, 3, 4), Rect(1, 2, 3, 4))
        self.assertNotEqual(Rect(1, 2, 3, 4), Rect(1, 2, 3, 5))
        self.assertEqual(len({Rect(0, 0, 1, 1), Rect(0, 0, 1, 1)}), 1)


class BufferSurfaceTest(unittest.TestCase):
    def test_reports_width_and_height(self) -> None:
        from end_of_line.top_render import BufferSurface

        s = BufferSurface(40, 12)
        self.assertEqual(s.width, 40)
        self.assertEqual(s.height, 12)

    def test_records_addstr_calls(self) -> None:
        from end_of_line.top_render import BufferSurface

        s = BufferSurface(40, 12)
        s.addstr(0, 0, "hello")
        s.addstr(1, 2, "world")
        self.assertEqual(s.cells, [(0, 0, "hello"), (1, 2, "world")])

    def test_does_not_truncate_so_overwidth_is_visible(self) -> None:
        # A faithless BufferSurface that clipped to width would make the
        # property test tautological. It must record what the draw code asked
        # to write, so an over-width line is detectable.
        from end_of_line.top_render import BufferSurface

        s = BufferSurface(4, 12)
        s.addstr(0, 0, "way too long")
        self.assertEqual(s.cells[0][2], "way too long")

    def test_clips_rows_outside_height(self) -> None:
        from end_of_line.top_render import BufferSurface

        s = BufferSurface(40, 2)
        s.addstr(5, 0, "off screen")
        self.assertEqual(s.cells, [])


class SurfacePropertyTest(unittest.TestCase):
    """The seam's payoff: drive the real draw path across every geometry via a
    BufferSurface (no terminal) and prove it never raises and never emits a row
    wider than the surface. Impossible before the Surface seam existed."""

    GEOMETRIES = (
        [(w, h) for w in range(6) for h in range(6)]
        + [(200, 5), (30, 120), (1, 1), (80, 24)]
    )

    def test_never_raises_and_rows_fit_width(self) -> None:
        from end_of_line.top_render import BufferSurface

        rows = [_draw_row(), _draw_row(last_text="x" * 500, last_command="y" * 400)]
        for w, h in self.GEOMETRIES:
            for detail in (False, True):
                with self.subTest(w=w, h=h, detail=detail):
                    s = BufferSurface(w, h)
                    top._draw(s, rows, detail=detail, hint="q quit · w detail")
                    for y, x, text in s.cells:
                        self.assertLessEqual(
                            len(text), s.width,
                            f"row {y!r} exceeds width {s.width} ({text!r})",
                        )

    def test_empty_rows_still_safe(self) -> None:
        from end_of_line.top_render import BufferSurface

        for w, h in self.GEOMETRIES:
            s = BufferSurface(w, h)
            top._draw(s, [], detail=False, hint="q quit")
            for _y, _x, text in s.cells:
                self.assertLessEqual(len(text), s.width)


class CursesSurfaceTest(unittest.TestCase):
    def _win(self, maxy: int, maxx: int, raise_on=None):
        import curses

        class _FakeWin:
            def __init__(self) -> None:
                self.calls: list[tuple[int, int, str, int]] = []

            def getmaxyx(self):
                return (maxy, maxx)

            def addnstr(self, y, x, text, n):
                self.calls.append((y, x, text, n))
                if raise_on is not None and (y, x) == raise_on:
                    raise curses.error("addnstr: returned ERR")

        return _FakeWin()

    def test_width_height_reserve_bottom_right(self) -> None:
        # Today's loop reserves the bottom-right cell (addnstr to maxx-1,
        # lines[:maxy-1]); the surface bakes that in so the draw stays clean.
        from end_of_line.top_render import CursesSurface

        s = CursesSurface(self._win(24, 80))
        self.assertEqual(s.width, 79)
        self.assertEqual(s.height, 23)

    def test_bottom_right_curses_error_is_swallowed(self) -> None:
        from end_of_line.top_render import CursesSurface

        win = self._win(2, 6, raise_on=(0, 0))
        s = CursesSurface(win)
        # Must not propagate — real curses raises on the last cell.
        s.addstr(0, 0, "boom")

    def test_truncates_to_width(self) -> None:
        from end_of_line.top_render import CursesSurface

        win = self._win(24, 6)  # width -> 5
        s = CursesSurface(win)
        s.addstr(0, 0, "abcdefghij")
        y, x, text, n = win.calls[0]
        self.assertLessEqual(len(text), 5)
        self.assertLessEqual(n, 5)


# --- Phase 1: the Metric/Pane registry + gather_rows wire contract ----------

# The 13 keys clu serve's JS reads off /api/workers (web/index.html:235 toView).
# Hardcoded on purpose — a constant edited in lockstep with assemble_row would
# defeat the guard. If you rename/drop a key in assemble_row/gather_rows, this
# list is the thing that must scream first. (D10 in plans/clu-top-tui-master.md)
_WIRE_CONTRACT_KEYS = frozenset({
    "project", "plan", "phase_id", "alive", "ran_seconds",
    "last_activity_seconds", "heartbeat_age_seconds", "last_command",
    "command_running", "last_write", "last_write_seconds", "last_text", "tokens",
})


class GatherRowsWireContractTest(GitProjectTestCase):
    """The frozen seam between the TUI and `clu serve` — gather_rows' row dict.

    No curses test catches a key rename; only this does. Keep it asserting
    every one of the 13 keys, by exact name."""

    def setUp(self) -> None:
        super().setUp()
        self._pr = TemporaryDirectory()
        self.addCleanup(self._pr.cleanup)
        self.projects_root = Path(self._pr.name)

    def test_row_carries_all_thirteen_keys_unrenamed(self) -> None:
        self._claim("a")
        rows = top.gather_rows(projects_root=self.projects_root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), _WIRE_CONTRACT_KEYS)


class MetricRegistryTest(unittest.TestCase):
    """Each built-in metric is a pure (compute, render) pair — no curses."""

    def setUp(self) -> None:
        from end_of_line import top_registry

        self.reg = top_registry
        self.snap = top_registry.Snapshot([_draw_row()])

    def test_eight_columns_registered(self) -> None:
        self.assertEqual(
            tuple(self.reg.DEFAULT_COLS),
            ("name", "ran", "act", "hb", "pid", "cmd", "wrote", "saying"),
        )
        for key in self.reg.DEFAULT_COLS:
            self.assertIn(key, self.reg.METRICS)

    def test_name_metric_compute_and_render(self) -> None:
        m = self.reg.METRICS["name"]
        v = m.compute(self.snap, _draw_row(project="myrepo", plan="routing", phase_id="impl"))
        self.assertEqual(v, "myrepo/routing·impl")
        cell = m.render(v, 24)
        self.assertEqual(len(cell), 24)  # left-padded to width
        self.assertTrue(cell.startswith("myrepo/routing·impl"))

    def test_ran_metric_renders_human_age_right_aligned(self) -> None:
        m = self.reg.METRICS["ran"]
        self.assertEqual(m.compute(self.snap, _draw_row(ran_seconds=90)), 90)
        self.assertEqual(m.render(90, 7), "  1m30s")  # right-aligned in 7

    def test_pid_metric_ok_dead(self) -> None:
        m = self.reg.METRICS["pid"]
        self.assertEqual(m.render(True, 4), "  ok")
        self.assertEqual(m.render(False, 4), "dead")

    def test_cmd_metric_running_star_and_clean(self) -> None:
        m = self.reg.METRICS["cmd"]
        v = m.compute(self.snap, _draw_row(command_running=True, last_command="git\nlog"))
        self.assertEqual(v, "*git log")  # running star + newline collapsed

    def test_saying_metric_dash_when_empty(self) -> None:
        m = self.reg.METRICS["saying"]
        self.assertEqual(m.compute(self.snap, _draw_row(last_text=None)), "—")


class TablePaneTest(unittest.TestCase):
    def setUp(self) -> None:
        from end_of_line import top_registry

        self.reg = top_registry
        self.rows = [_draw_row(), _draw_row(phase_id="two", last_text="other")]
        self.snap = top_registry.Snapshot(self.rows)
        self.pane = top_registry.PANES["table"]

    def test_default_is_byte_identical_to_format_rows(self) -> None:
        for width in (40, 80, 120, 200):
            with self.subTest(width=width):
                got = self.pane.render(self.snap, width=width)
                self.assertEqual(got, top.format_rows(self.rows, width=width))

    def test_cols_subset_shows_only_selected_metrics(self) -> None:
        lines = self.pane.render(self.snap, width=120, cols=("saying", "cmd"))
        header = lines[0]
        self.assertIn("SAYING", header)
        self.assertIn("COMMAND", header)
        # The numeric identity columns are gone in a saying/cmd-only view.
        self.assertNotIn("RAN", header)
        self.assertNotIn("PID", header)
        body = "\n".join(lines[1:])
        self.assertIn("pytest -k routing", body)

    def test_cols_subset_clamped_to_width(self) -> None:
        lines = self.pane.render(
            self.snap, width=50, cols=("name", "saying")
        )
        self.assertTrue(all(len(ln) <= 50 for ln in lines))


class PaneErrorBoundaryTest(unittest.TestCase):
    """A pane that raises in render is contained: an inline error band, and
    every other pane still draws."""

    def setUp(self) -> None:
        from end_of_line import top_registry

        self.reg = top_registry
        self.snap = top_registry.Snapshot([_draw_row()])

    def test_raising_pane_yields_error_band(self) -> None:
        def _boom(snapshot, *, width, cols=None):
            raise RuntimeError("kaboom")

        boom = self.reg.Pane(kind="boom", metric_keys=(), render=_boom)
        band = self.reg.safe_render(boom, self.snap, width=80)
        self.assertEqual(len(band), 1)
        self.assertIn("boom", band[0])
        self.assertIn("error", band[0].lower())
        self.assertLessEqual(len(band[0]), 80)

    def test_sibling_pane_unaffected(self) -> None:
        table = self.reg.PANES["table"]
        out = self.reg.safe_render(table, self.snap, width=120)
        self.assertTrue(any("SAYING" in ln for ln in out))


class ParseColsTest(unittest.TestCase):
    def setUp(self) -> None:
        from end_of_line import top_registry

        self.reg = top_registry

    def test_valid_keys_accepted(self) -> None:
        self.assertEqual(self.reg.parse_cols("saying,cmd"), ("saying", "cmd"))
        self.assertEqual(self.reg.parse_cols(" name , ran "), ("name", "ran"))

    def test_unknown_key_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.reg.parse_cols("saying,bogus")
        self.assertIn("bogus", str(ctx.exception))

    def test_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.reg.parse_cols("")
        with self.assertRaises(ValueError):
            self.reg.parse_cols(" , ,")


class ColsCliTest(unittest.TestCase):
    def test_unknown_col_is_a_clean_usage_error(self) -> None:
        from end_of_line.cli import main

        # argparse validates the `type=` before cmd_top runs -> SystemExit(2),
        # not a traceback or a half-built dashboard.
        with self.assertRaises(SystemExit) as ctx:
            main(["top", "--cols", "bogus"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
