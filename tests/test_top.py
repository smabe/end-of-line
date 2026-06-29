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
from typing import cast

from end_of_line import registry, top
from end_of_line import state as st
from end_of_line.cli import main as cli_main
from tests import GitProjectTestCase, plan_body


def _now() -> _dt.datetime:
    """The single frozen `now` anchor shared by every time-derived assertion in
    this module — one source so a tz/clock change touches one place, not three."""
    return _dt.datetime(2026, 6, 3, 0, 10, 0, tzinfo=_dt.UTC)


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
        row = top.assemble_row(claim, activity, now=_now())
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
        row = top.assemble_row(claim, activity, now=_now())
        self.assertAlmostEqual(row["last_write_seconds"], 60, delta=1)

    def test_dead_pid_flagged_not_idle(self) -> None:
        claim = {"phase_id": "p", "started_at": "2026-06-03T00:00:00Z", "pid": 999999}
        row = top.assemble_row(claim, {"last_command": None, "last_write": None, "last_text": None,
                                       "last_activity_ts": None, "command_running": False, "tokens": None},
                               now=_now())
        self.assertFalse(row["alive"])


class BaseRowTest(unittest.TestCase):
    """`_base_row` is the single source of the D10 activity-key block shared by
    assemble_row / assemble_blocked_row / (soon) assemble_session_row. These
    lock its key set + that the empty-activity path reproduces the exact
    None/False defaults the blocked row used to inline."""

    _ACTIVITY_KEYS = {
        "last_command", "command_running", "last_write", "last_write_seconds",
        "last_text", "last_activity_seconds", "tokens",
    }

    def test_base_row_has_activity_keys_only(self) -> None:
        activity = {"last_command": "pytest", "command_running": True,
                    "last_write": "/r/a.py", "last_write_ts": "2026-06-03T00:09:00Z",
                    "last_text": "ok", "last_activity_ts": "2026-06-03T00:09:55Z",
                    "tokens": {"input_tokens": 5}}
        base = top._base_row(activity, now=_now())
        self.assertEqual(set(base), self._ACTIVITY_KEYS)
        self.assertEqual(base["last_command"], "pytest")
        self.assertTrue(base["command_running"])
        self.assertAlmostEqual(base["last_write_seconds"], 60, delta=1)
        self.assertAlmostEqual(base["last_activity_seconds"], 5, delta=1)
        self.assertEqual(base["tokens"], {"input_tokens": 5})

    def test_empty_activity_yields_none_defaults(self) -> None:
        # The blocked-row contract: an empty activity dict must collapse to the
        # same None/False defaults the row formerly wrote literally.
        base = top._base_row({}, now=_now())
        self.assertIsNone(base["last_command"])
        self.assertFalse(base["command_running"])
        self.assertIsNone(base["last_write"])
        self.assertIsNone(base["last_write_seconds"])
        self.assertIsNone(base["last_text"])
        self.assertIsNone(base["last_activity_seconds"])
        self.assertIsNone(base["tokens"])

    def test_assemble_row_activity_portion_equals_base(self) -> None:
        activity = {"last_command": "x", "command_running": False, "last_write": None,
                    "last_text": "hi", "last_activity_ts": None, "tokens": None}
        claim = {"phase_id": "p", "started_at": "2026-06-03T00:00:00Z", "pid": os.getpid()}
        row = top.assemble_row(claim, activity, now=_now())
        for k, v in top._base_row(activity, now=_now()).items():
            self.assertEqual(row[k], v)

    def test_assemble_blocked_row_activity_portion_is_base_defaults(self) -> None:
        # The blocked row is the one whose 7 inline activity literals this phase
        # deleted; lock that it now reproduces _base_row({})'s None/False
        # defaults exactly, so a future edit that feeds it non-empty activity (or
        # .update()s over a base key) can't silently ship stale CMD/WROTE cells.
        blocker = {"phase_id": "p", "question": "Which base?", "asked_at": "2026-06-03T00:07:00Z"}
        row = top.assemble_blocked_row(blocker, now=_now())
        for k, v in top._base_row({}, now=_now()).items():
            self.assertEqual(row[k], v)


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


class SessionNameTest(unittest.TestCase):
    """Display-name precedence over a session's tail records:
    customTitle > aiTitle > lastPrompt > `<project>:<short-id>`."""

    def test_custom_title_wins(self) -> None:
        recs = [
            {"type": "ai-title", "aiTitle": "Auto Title"},
            {"type": "last-prompt", "lastPrompt": "hey there"},
            {"type": "custom-title", "customTitle": "My Name"},
        ]
        self.assertEqual(top._session_name(recs, project="p", session_id="abcdef123"), "My Name")

    def test_ai_title_when_no_custom(self) -> None:
        recs = [{"type": "ai-title", "aiTitle": "Auto"}, {"type": "last-prompt", "lastPrompt": "x"}]
        self.assertEqual(top._session_name(recs, project="p", session_id="abcdef123"), "Auto")

    def test_last_prompt_when_no_titles(self) -> None:
        recs = [{"type": "last-prompt", "lastPrompt": "do the thing"}]
        self.assertEqual(top._session_name(recs, project="p", session_id="abcdef123"), "do the thing")

    def test_fallback_project_short_id(self) -> None:
        self.assertEqual(
            top._session_name([], project="myrepo", session_id="abcdef123456"), "myrepo:abcdef12"
        )


class AssembleSessionRowTest(unittest.TestCase):
    def test_session_discriminators_plus_base_activity(self) -> None:
        activity = {"last_command": "ls", "command_running": True, "tokens": {"x": 1},
                    "last_activity_ts": "2026-06-03T00:09:55Z"}
        row = top.assemble_session_row("sess-1", "My Session", activity, now=_now())
        self.assertTrue(row["session"])
        self.assertEqual(row["session_name"], "My Session")
        self.assertEqual(row["session_id"], "sess-1")
        self.assertEqual(row["last_command"], "ls")
        self.assertTrue(row["command_running"])
        # Claim-only keys zero-filled (no claim/phase/lease for a passive session).
        self.assertIsNone(row["phase_id"])
        self.assertIsNone(row["attempts"])
        self.assertIsNone(row["lease_remaining_seconds"])
        # Base activity block reproduced exactly.
        for k, v in top._base_row(activity, now=_now()).items():
            self.assertEqual(row[k], v)


class SessionRowsTest(GitProjectTestCase):
    """gather_rows surfaces fresh non-claim Claude sessions in registered
    projects, deduped against live worker claims."""

    def setUp(self) -> None:
        super().setUp()
        self._pr = TemporaryDirectory()
        self.addCleanup(self._pr.cleanup)
        self.projects_root = Path(self._pr.name)
        self.reg_root = registry.entries()[0].project_root
        self.fresh = _now().timestamp() - 100
        self.stale = _now().timestamp() - (top.SESSION_FRESH_SECONDS + 100)

    def _sess(self, stem, *, records=None, mtime=None, cwd=None, sidechain=False) -> Path:
        cwd = cwd or self.reg_root
        recs = records if records is not None else [
            _asst(text="working", tool="Bash", tool_input={"command": "ls"}, tool_id="b1", cwd=cwd),
        ]
        if sidechain:
            for r in recs:
                r["isSidechain"] = True
        d = self.projects_root / top.encode_project_dir(self.reg_root)
        return _write_jsonl(d / f"{stem}.jsonl", recs,
                            mtime=mtime if mtime is not None else self.fresh)

    def _rows(self) -> list[dict]:
        return top.gather_rows(projects_root=self.projects_root, now=_now())

    def test_fresh_session_becomes_row(self) -> None:
        self._sess("live-sess")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["session"])
        self.assertEqual(rows[0]["session_id"], "live-sess")
        self.assertEqual(rows[0]["last_command"], "ls")
        self.assertEqual(rows[0]["project"], Path(self.reg_root).name)

    def test_stale_session_skipped(self) -> None:
        self._sess("old-sess", mtime=self.stale)
        self.assertEqual(self._rows(), [])

    def test_sidechain_skipped(self) -> None:
        self._sess("sub", sidechain=True)
        self.assertEqual(self._rows(), [])

    def test_cwd_mismatch_skipped(self) -> None:
        self._sess("foreign", cwd="/some/other/proj")
        self.assertEqual(self._rows(), [])

    def test_claimed_session_not_doubled(self) -> None:
        # Worker claim (session_id "wsess") + its transcript, plus an unrelated
        # fresh session. Expect 1 worker row + 1 session row — never a dup.
        self._claim("a")
        with st.mutate(self.state_path) as data:
            data["current_claim"]["session_id"] = "wsess"
        self._sess("wsess")
        self._sess("other")
        rows = self._rows()
        session_ids = sorted(r["session_id"] for r in rows if r.get("session"))
        self.assertEqual(session_ids, ["other"])
        self.assertEqual(sum(1 for r in rows if not r.get("session")), 1)

    def test_claimed_session_deduped_without_session_id(self) -> None:
        # The recommended hardened template omits {session_id}, so a real claim
        # carries none. Dedup must STILL fire — via the worker's resolved
        # transcript path, not the absent sid. The worker's transcript is the
        # newest fresh file in its dir (what locate_transcript resolves to).
        self._claim("a")  # no session_id stamped, as the hardened template yields
        self._sess("wsess", mtime=_now().timestamp() - 50)   # newest -> worker's
        self._sess("other", mtime=_now().timestamp() - 150)  # an unrelated session
        rows = self._rows()
        session_ids = sorted(r["session_id"] for r in rows if r.get("session"))
        self.assertEqual(session_ids, ["other"])  # wsess deduped by path
        self.assertEqual(sum(1 for r in rows if not r.get("session")), 1)

    def test_empty_transcript_no_row_no_crash(self) -> None:
        d = self.projects_root / top.encode_project_dir(self.reg_root)
        _write_jsonl(d / "empty.jsonl", [], mtime=self.fresh)
        self.assertEqual(self._rows(), [])  # no cwd record -> skipped, not raised

    def test_name_from_custom_title(self) -> None:
        self._sess("named", records=[
            {"type": "custom-title", "customTitle": "My Work", "cwd": self.reg_root,
             "isSidechain": False},
            _asst(text="hi", cwd=self.reg_root),
        ])
        self.assertEqual(self._rows()[0]["session_name"], "My Work")

    def test_sessions_sort_after_workers(self) -> None:
        # Worker gets its own (session_id-resolved) transcript so the separate
        # session isn't claimed out; then assert the tier order worker→session.
        self._claim("a")
        with st.mutate(self.state_path) as data:
            data["current_claim"]["session_id"] = "wsess"
        self._sess("wsess")  # the worker's own transcript
        self._sess("zsess")  # a separate non-clu session
        rows = self._rows()
        self.assertFalse(rows[0].get("session"))  # worker first
        self.assertTrue(rows[-1].get("session"))   # session last
        self.assertEqual(rows[-1]["session_id"], "zsess")


class SessionWatchDirsTest(GitProjectTestCase):
    """session_dirs: surface sessions in configured cwds with no registered plan
    (union with registry roots, same project_filter gate + dedup)."""

    def setUp(self) -> None:
        super().setUp()
        self._pr = TemporaryDirectory()
        self.addCleanup(self._pr.cleanup)
        self.projects_root = Path(self._pr.name)
        self.reg_root = registry.entries()[0].project_root
        self.watch = self.tmp_path / "watched-proj"
        self.watch.mkdir()
        self.fresh = _now().timestamp() - 50

    def _sess_in(self, root, stem: str) -> Path:
        d = self.projects_root / top.encode_project_dir(str(root))
        return _write_jsonl(d / f"{stem}.jsonl", [_asst(text="hi", cwd=str(root))], mtime=self.fresh)

    def _rows(self, **over) -> list[dict]:
        return top.gather_rows(projects_root=self.projects_root, now=_now(), **over)

    def test_session_dir_surfaces_unregistered_session(self) -> None:
        self._sess_in(self.watch, "live")
        rows = self._rows(session_dirs=[str(self.watch)])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["session"])
        self.assertEqual(rows[0]["project"], self.watch.name)

    def test_without_session_dirs_unregistered_invisible(self) -> None:
        self._sess_in(self.watch, "live")
        self.assertEqual(self._rows(), [])

    def test_project_filter_excludes_nonmatching_session_dir(self) -> None:
        self._sess_in(self.watch, "live")
        rows = self._rows(session_dirs=[str(self.watch)], project_filter=self.tmp_path / "other")
        self.assertEqual(rows, [])

    def test_session_dir_matching_filter_included(self) -> None:
        self._sess_in(self.watch, "live")
        rows = self._rows(session_dirs=[str(self.watch)], project_filter=self.watch)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["session"])

    def test_session_dir_also_registered_not_doubled(self) -> None:
        self._sess_in(Path(self.reg_root), "live")
        rows = self._rows(session_dirs=[self.reg_root])
        self.assertEqual(sum(1 for r in rows if r.get("session")), 1)


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


class PhaseProgressTest(unittest.TestCase):
    """#86 display layer — phase position / attempts / lease in the curses table
    and detail pane. The data ships in the row dict (gather_rows); this tests the
    render only."""

    def _row(self, **over) -> dict:
        base = {
            "project": "myrepo", "plan": "clu-top-tui", "phase_id": "layout-engine",
            "ran_seconds": 600, "heartbeat_age_seconds": 18, "alive": True,
            "last_command": "pytest", "command_running": False,
            "last_write": None, "last_write_seconds": None,
            "last_text": "wiring", "last_activity_seconds": 2, "tokens": None,
        }
        base.update(over)
        return base

    def test_table_phase_column(self) -> None:
        lines = top.format_rows([self._row(phase_index=3, phase_total=5)], width=160)
        self.assertIn("PHASE", lines[0])
        self.assertIn("3/5", lines[1])

    def test_table_phase_dash_when_unknown(self) -> None:
        # Non-clu / demo workers carry no phase index → "—", never "None".
        body = "\n".join(top.format_rows([self._row()], width=160)[1:])
        self.assertNotIn("None", body)

    def test_detail_phase_attempts_lease(self) -> None:
        text = "\n".join(top.format_detail([self._row(
            phase_index=3, phase_total=5, attempts=1, max_attempts=3,
            lease_remaining_seconds=720)], width=120))
        self.assertIn("PHASE", text)
        self.assertIn("●●◉○○", text)            # 2 done · active · 2 pending
        self.assertIn("3/5", text)
        self.assertIn("layout-engine", text)     # active stage name
        self.assertIn("ATT", text)
        self.assertIn("1/3", text)
        self.assertIn("LEASE", text)
        self.assertIn("12m", text)

    def test_detail_omits_block_when_absent(self) -> None:
        text = "\n".join(top.format_detail([self._row()], width=120))
        self.assertNotIn("PHASE", text)
        self.assertNotIn("ATT", text)
        self.assertNotIn("None", text)

    def test_detail_strip_dropped_past_threshold(self) -> None:
        text = "\n".join(top.format_detail([self._row(phase_index=3, phase_total=12)], width=120))
        self.assertIn("3/12", text)
        self.assertNotIn("●", text)

    def test_phase_strip(self) -> None:
        self.assertEqual(top._phase_strip(3, 5), "●●◉○○")
        self.assertEqual(top._phase_strip(1, 3), "◉○○")
        self.assertEqual(top._phase_strip(3, 12), "")   # past threshold

    def test_human_remaining(self) -> None:
        self.assertEqual(top.human_remaining(None), "—")
        self.assertEqual(top.human_remaining(-5), "exp")
        self.assertEqual(top.human_remaining(90), "1m30s")


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


def _session_render_row(**over) -> dict:
    base = top.assemble_session_row(
        "sess-abc12345", "My Session",
        {"last_command": "ls -la", "last_text": "looking around",
         "command_running": False, "last_activity_ts": "2026-06-03T00:09:55Z"},
        now=_now(),
    )
    base["project"] = "myrepo"
    base["plan"] = None
    base.update(over)
    return base


class SessionRenderTest(unittest.TestCase):
    """Curses render routing for a non-clu session row (top.py)."""

    def test_liveness_cell_is_sess(self) -> None:
        self.assertEqual(top._liveness_cell(_session_render_row()), "sess")

    def test_phase_cell_is_dash(self) -> None:
        self.assertEqual(top._phase_cell(_session_render_row()), "—")

    def test_display_name_uses_session_name(self) -> None:
        self.assertEqual(top.row_display_name(_session_render_row()), "myrepo · My Session")

    def test_display_name_worker_unchanged(self) -> None:
        self.assertEqual(
            top.row_display_name({"project": "myrepo", "plan": "routing", "phase_id": "impl"}),
            "myrepo/routing·impl",
        )

    def test_format_rows_shows_name_and_sess(self) -> None:
        body = top.format_rows([_session_render_row()], width=200)[1]
        self.assertIn("My Session", body)
        self.assertIn("sess", body)
        self.assertNotIn("dead", body)

    def test_format_detail_shows_name_and_sess(self) -> None:
        out = "\n".join(top.format_detail([_session_render_row()]))
        self.assertIn("My Session", out)
        self.assertIn("sess", out)


class SessionRegistryRenderTest(unittest.TestCase):
    """Registry metric routing for a session row (top_registry)."""

    def setUp(self) -> None:
        from end_of_line import top_registry

        self.reg = top_registry
        self.snap = top_registry.Snapshot([_session_render_row()])

    def test_health_metric_returns_session(self) -> None:
        v = self.reg.METRICS["health"].compute(self.snap, _session_render_row())
        self.assertEqual(v, "session")

    def test_health_glyph_has_session(self) -> None:
        self.assertIn("session", self.reg._HEALTH_GLYPH)

    def test_name_metric_uses_session_name(self) -> None:
        v = self.reg.METRICS["name"].compute(self.snap, _session_render_row())
        self.assertEqual(v, "myrepo · My Session")

    def test_pid_metric_is_sess(self) -> None:
        v = self.reg.METRICS["pid"].compute(self.snap, _session_render_row())
        self.assertEqual(v, "sess")

    def test_fleet_summary_counts_sessions_not_dead(self) -> None:
        rows = [
            _session_render_row(),
            _draw_row(alive=True),
            _draw_row(alive=False, blocked=True, blocker_question="?"),
        ]
        s = self.reg.fleet_summary(rows, 200)
        self.assertIn("1 running", s)
        self.assertIn("1 blocked", s)
        self.assertIn("0 dead", s)     # the session must NOT inflate the dead count
        self.assertIn("1 session", s)  # surfaced as its own tier


class RectTest(unittest.TestCase):
    def test_frozen_geometry(self) -> None:
        from end_of_line.top_render import Rect

        r = Rect(1, 2, 30, 10)
        self.assertEqual((r.x, r.y, r.w, r.h), (1, 2, 30, 10))
        with self.assertRaises(Exception):
            # The checker rightly rejects this assignment — provoking that
            # rejection at runtime is the point of the test.
            r.x = 5  # pyright: ignore[reportAttributeAccessIssue]

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
    # Phase 4 (new-metrics) — append-only additions (D10). Each is mirrored into
    # web/index.html's toView so `clu serve` reads the same keys the TUI does.
    "stuck", "attempts", "max_attempts", "lease_remaining_seconds",
    "phase_index", "phase_total",
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

    def test_blocked_row_carries_same_keys_plus_blocked_discriminator(self) -> None:
        # A blocked row is the SAME flat schema plus three append-only keys
        # (D10) — clu serve's toView reads `blocker_question`/`blocked_seconds`
        # by exact name, so a rename here must scream just like a claim-row one.
        with st.mutate(self.state_path) as data:
            st.add_blocker(data, "a", "Pick a base branch?", ["x", "y"])
        rows = top.gather_rows(projects_root=self.projects_root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            set(rows[0]),
            _WIRE_CONTRACT_KEYS | {"blocked", "blocker_question", "blocked_seconds"},
        )

    def test_session_row_carries_same_keys_plus_session_discriminator(self) -> None:
        # A non-clu session row must carry the FULL D10 key set (so a row[...]
        # subscript on /api/workers never KeyErrors) plus the three append-only
        # session discriminators — the guard now covers every row type.
        reg_root = registry.entries()[0].project_root
        d = self.projects_root / top.encode_project_dir(reg_root)
        _write_jsonl(d / "live.jsonl", [_asst(text="hi", cwd=reg_root)],
                     mtime=_now().timestamp() - 10)
        rows = top.gather_rows(projects_root=self.projects_root, now=_now())
        sess = [r for r in rows if r.get("session")]
        self.assertEqual(len(sess), 1)
        self.assertEqual(
            set(sess[0]),
            _WIRE_CONTRACT_KEYS | {"session", "session_name", "session_id"},
        )


class MetricRegistryTest(unittest.TestCase):
    """Each built-in metric is a pure (compute, render) pair — no curses."""

    def setUp(self) -> None:
        from end_of_line import top_registry

        self.reg = top_registry
        self.snap = top_registry.Snapshot([_draw_row()])

    def test_default_columns_registered(self) -> None:
        # `progress` (PHASE) joins the default table for #86 — between pid and cmd.
        self.assertEqual(
            tuple(self.reg.DEFAULT_COLS),
            ("name", "ran", "act", "hb", "pid", "progress", "cmd", "wrote", "saying"),
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

    def test_pid_metric_ok_dead_blocked(self) -> None:
        m = self.reg.METRICS["pid"]
        # render right-aligns the liveness label.
        self.assertEqual(m.render("ok", 4), "  ok")
        self.assertEqual(m.render("dead", 4), "dead")
        self.assertEqual(m.render("blk", 4), " blk")
        # compute mirrors the compact table — blocked is checked before dead, so
        # a `--cols pid` view never mislabels a blocked plan (alive=False) as dead.
        self.assertEqual(m.compute(self.snap, _draw_row(alive=True)), "ok")
        self.assertEqual(m.compute(self.snap, _draw_row(alive=False)), "dead")
        self.assertEqual(m.compute(self.snap, _draw_row(alive=False, blocked=True)), "blk")

    def test_cmd_metric_running_star_and_clean(self) -> None:
        m = self.reg.METRICS["cmd"]
        v = m.compute(self.snap, _draw_row(command_running=True, last_command="git\nlog"))
        self.assertEqual(v, "*git log")  # running star + newline collapsed

    def test_saying_metric_dash_when_empty(self) -> None:
        m = self.reg.METRICS["saying"]
        self.assertEqual(m.compute(self.snap, _draw_row(last_text=None)), "—")

    def test_saying_metric_shows_blocker_question_when_blocked(self) -> None:
        # A `--cols saying` view of a blocked plan must surface the question, not
        # the (absent) last_text — same as the default table.
        m = self.reg.METRICS["saying"]
        row = _draw_row(last_text=None, blocked=True, blocker_question="Which base?")
        self.assertEqual(m.compute(self.snap, row), "Which base?")


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


# --- Phase 2: the layout engine + fleet header (clu-top-tui) ----------------


class ChoosePresetTest(unittest.TestCase):
    """The verified width ladder (D2): width-primary, with a rows floor that
    forces the wide-short strip and a tiny-terminal fallback."""

    def _preset(self, w: int, h: int) -> str:
        from end_of_line.top_layout import choose_preset

        return choose_preset(w, h)

    def test_width_ladder_at_tall_height(self) -> None:
        self.assertEqual(self._preset(80, 24), "split")
        self.assertEqual(self._preset(120, 40), "split")
        self.assertEqual(self._preset(79, 24), "stacked")  # just under the split rung
        self.assertEqual(self._preset(50, 24), "stacked")
        self.assertEqual(self._preset(49, 24), "master")   # just under the stacked rung
        self.assertEqual(self._preset(30, 120), "master")  # phone: narrow but tall

    def test_rows_floor_forces_strip_even_when_wide(self) -> None:
        # The wide-short dock under coolant: plenty of columns, few rows.
        self.assertEqual(self._preset(200, 5), "strip")
        self.assertEqual(self._preset(100, 11), "strip")   # 11 < 12 floor
        self.assertEqual(self._preset(100, 12), "split")   # 12 is not < 12

    def test_tiny_terminal_falls_back_to_one_line(self) -> None:
        self.assertEqual(self._preset(1, 1), "fallback")
        self.assertEqual(self._preset(33, 1), "fallback")
        self.assertEqual(self._preset(34, 1), "strip")     # width floor cleared


class LayoutEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        from end_of_line.top_layout import LayoutEngine

        self.engine = LayoutEngine()

    def test_split_places_list_left_detail_right_no_overlap(self) -> None:
        from end_of_line.top_render import Rect

        lay = self.engine.layout(80, 24)
        self.assertEqual(lay.preset, "split")
        self.assertEqual(lay.rects["header"], Rect(0, 0, 80, 1))
        lst, det = lay.rects["list"], lay.rects["detail"]
        self.assertEqual(lst.x, 0)
        self.assertEqual(lst.y, 1)               # under the header
        self.assertEqual(det.x, lst.x + lst.w + 1)  # 1-col divider channel, no overlap
        self.assertEqual(lst.w + det.w + 1, 80)     # halves + divider fill the width
        self.assertEqual(lst.y, det.y)
        self.assertEqual(lst.h, det.h)
        self.assertEqual(lay.rects["hint"], Rect(0, 23, 80, 1))

    def test_stacked_places_list_top_detail_bottom(self) -> None:
        lay = self.engine.layout(60, 24)
        self.assertEqual(lay.preset, "stacked")
        lst, det = lay.rects["list"], lay.rects["detail"]
        self.assertEqual(lst.x, 0)
        self.assertEqual(det.x, 0)
        self.assertEqual(lst.w, 60)
        self.assertEqual(det.w, 60)
        self.assertEqual(det.y, lst.y + lst.h + 1)  # 1-row divider beneath the list
        self.assertEqual(lst.h + det.h + 1, 22)     # halves + divider = 24 - header - hint

    def test_master_is_list_only_no_detail(self) -> None:
        lay = self.engine.layout(40, 24)
        self.assertEqual(lay.preset, "master")
        self.assertNotIn("detail", lay.rects)
        self.assertEqual(lay.rects["list"].w, 40)

    def test_strip_is_list_only_with_header(self) -> None:
        lay = self.engine.layout(200, 5)
        self.assertEqual(lay.preset, "strip")
        self.assertNotIn("detail", lay.rects)
        self.assertIn("header", lay.rects)
        self.assertEqual(lay.rects["list"].w, 200)

    def test_resize_changes_layout(self) -> None:
        wide = self.engine.layout(80, 24)
        narrow = self.engine.layout(40, 24)
        self.assertNotEqual(wide.preset, narrow.preset)
        self.assertIn("detail", wide.rects)
        self.assertNotIn("detail", narrow.rects)

    def test_override_forces_preset(self) -> None:
        lay = self.engine.layout(40, 24, override="split")
        self.assertEqual(lay.preset, "split")
        self.assertIn("detail", lay.rects)        # forced split even though w<80

    def test_degenerate_geometry_is_safe(self) -> None:
        for w, h in ((0, 0), (0, 10), (10, 0)):
            lay = self.engine.layout(w, h)
            self.assertIsInstance(lay.rects, dict)  # never raises


class NextPresetTest(unittest.TestCase):
    def test_w_cycles_auto_then_each_preset(self) -> None:
        from end_of_line.top_layout import next_preset

        seen: list[str | None] = [None]
        cur = None
        for _ in range(5):
            cur = next_preset(cur)
            seen.append(cur)
        # Cycles through the meaningful presets and returns to auto (None).
        self.assertEqual(seen[1:5], ["split", "stacked", "master", "strip"])
        self.assertIsNone(next_preset("strip"))


class FleetSummaryTest(unittest.TestCase):
    def _rows(self) -> list[dict]:
        return [
            _draw_row(alive=True, last_activity_seconds=2),
            _draw_row(alive=True, last_activity_seconds=300),
            _draw_row(alive=False, last_activity_seconds=None),
        ]

    def test_counts_running_dead_and_oldest_act(self) -> None:
        from end_of_line.top_registry import fleet_summary

        line = fleet_summary(self._rows(), 80)
        self.assertIn("2 running", line)
        self.assertIn("1 dead", line)
        self.assertIn("oldest-ACT", line)
        self.assertIn("5m00s", line)  # human_age(max(2, 300)) over the alive rows

    def test_empty_says_no_active_workers(self) -> None:
        from end_of_line.top_registry import fleet_summary

        self.assertIn("no active workers", fleet_summary([], 80))

    def test_clamped_to_width(self) -> None:
        from end_of_line.top_registry import fleet_summary

        self.assertLessEqual(len(fleet_summary(self._rows(), 12)), 12)

    def test_registered_as_header_pane(self) -> None:
        from end_of_line.top_registry import PANES, Snapshot, safe_render

        out = safe_render(PANES["header"], Snapshot(self._rows()), width=80)
        self.assertEqual(len(out), 1)
        self.assertIn("running", out[0])


class DetailPaneTest(unittest.TestCase):
    def test_mirrors_format_detail_for_the_fleet(self) -> None:
        from end_of_line.top_registry import PANES, Snapshot

        rows = [_draw_row(), _draw_row(phase_id="two")]
        got = PANES["detail"].render(Snapshot(rows), width=120)
        self.assertEqual(got, top.format_detail(rows, width=120))


class LayoutDrawPropertyTest(unittest.TestCase):
    """Phase 0's property, now over the real Phase 2 draw path: drive the layout
    engine + pane rendering across every geometry (and every forced preset) via a
    BufferSurface — assert it never raises and never emits a row wider than the
    surface. This is the regression guard the curses loop is otherwise untestable
    for; it supersedes the Phase-0 `_draw` property test 1:1."""

    GEOMETRIES = (
        [(w, h) for w in range(6) for h in range(6)]
        + [(200, 5), (30, 120), (1, 1), (80, 24), (60, 24), (40, 50)]
    )

    def test_never_raises_and_rows_fit_width(self) -> None:
        from end_of_line.top_layout import LayoutEngine
        from end_of_line.top_registry import Snapshot
        from end_of_line.top_render import BufferSurface

        engine = LayoutEngine()
        rows = [_draw_row(), _draw_row(last_text="x" * 500, last_command="y" * 400)]
        snap = Snapshot(rows)
        for w, h in self.GEOMETRIES:
            for override in (None, "split", "stacked", "master", "strip"):
                with self.subTest(w=w, h=h, override=override):
                    s = BufferSurface(w, h)
                    layout = engine.layout(w, h, override=override)
                    top._draw_panes(s, snap, layout, hint="q quit · w layout")
                    for y, _x, text in s.cells:
                        self.assertLessEqual(
                            len(text), s.width,
                            f"row {y!r} exceeds width {s.width} ({text!r})",
                        )

    def test_empty_rows_still_safe(self) -> None:
        from end_of_line.top_layout import LayoutEngine
        from end_of_line.top_registry import Snapshot
        from end_of_line.top_render import BufferSurface

        engine = LayoutEngine()
        snap = Snapshot([])
        for w, h in self.GEOMETRIES:
            s = BufferSurface(w, h)
            top._draw_panes(s, snap, engine.layout(w, h), hint="q quit")
            for _y, _x, text in s.cells:
                self.assertLessEqual(len(text), s.width)


# --- Phase 3: sticky-by-identity selection + detail pane (clu-top-tui) -------


class SelectionModelTest(unittest.TestCase):
    """Selection is sticky by `(project, plan, phase_id)` identity, re-resolved
    every tick — never by raw list index (the #1 QA risk). Mirrors the web's
    `wkey` + `findIndex` re-resolution (web/index.html:376-385)."""

    def _rows(self, *phases: str) -> list[dict]:
        return [_draw_row(phase_id=p) for p in phases]

    def test_cursor_follows_worker_when_it_moves_position(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        app.sync_selection(self._rows("a", "b", "c"))
        app.move(1, self._rows("a", "b", "c"))  # select b (index 1)
        self.assertEqual(app.selected_index, 1)
        self.assertEqual(app.selected_key, ("myrepo", "routing", "b"))
        # b slides to the bottom; the cursor tracks the worker, not the index.
        app.sync_selection(self._rows("a", "c", "b"))
        self.assertEqual(app.selected_index, 2)
        self.assertEqual(app.selected_key, ("myrepo", "routing", "b"))

    def test_dropout_clamps_gracefully_no_crash(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        rows = self._rows("a", "b", "c")
        app.sync_selection(rows)
        app.move_to(2, rows)  # select c (index 2)
        # c drops out; the cursor clamps to the old index within the new length.
        app.sync_selection(self._rows("a", "b"))
        self.assertEqual(app.selected_index, 1)
        self.assertEqual(app.selected_key, ("myrepo", "routing", "b"))

    def test_empty_list_clears_selection_no_crash(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        app.sync_selection(self._rows("a", "b"))
        app.sync_selection([])
        self.assertIsNone(app.selected_key)
        self.assertEqual(app.selected_index, 0)

    def test_move_clamps_no_wrap(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        rows = self._rows("a", "b")
        app.sync_selection(rows)
        app.move(-1, rows)  # already at top — clamp, don't wrap to bottom
        self.assertEqual(app.selected_index, 0)
        app.move(5, rows)   # past bottom — clamp to last
        self.assertEqual(app.selected_index, 1)

    def test_move_resets_detail_scroll_when_selection_changes(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        rows = self._rows("a", "b")
        app.sync_selection(rows)
        app.scroll = 5
        app.move(1, rows)  # selection a -> b
        self.assertEqual(app.scroll, 0)

    def test_scroll_by_clamps_at_zero(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        app.scroll_by(-3)
        self.assertEqual(app.scroll, 0)
        app.scroll_by(4)
        self.assertEqual(app.scroll, 4)

    def test_tab_toggles_focus_and_esc_returns(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        self.assertEqual(app.focus, "list")
        app.toggle_focus()
        self.assertEqual(app.focus, "detail")
        app.toggle_focus()
        self.assertEqual(app.focus, "list")

    def test_drill_in_and_out(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        app.drill_in()
        self.assertTrue(app.drill)
        app.drill_out()  # Esc leaves drill first
        self.assertFalse(app.drill)
        app.focus = "detail"
        app.drill_out()  # Esc then drops detail focus
        self.assertEqual(app.focus, "list")


class DrillLayoutTest(unittest.TestCase):
    """Fullscreen drill on the narrow (master) preset: Enter replaces the
    list-only body with a fullscreen detail; Esc returns."""

    def test_drill_makes_detail_fullscreen_on_master(self) -> None:
        from end_of_line.top_layout import LayoutEngine

        lay = LayoutEngine().layout(40, 24, drill=True)
        self.assertEqual(lay.preset, "master")
        self.assertIn("detail", lay.rects)
        self.assertNotIn("list", lay.rects)

    def test_drill_ignored_when_split_already_shows_detail(self) -> None:
        from end_of_line.top_layout import LayoutEngine

        lay = LayoutEngine().layout(120, 40, drill=True)
        self.assertEqual(lay.preset, "split")
        self.assertIn("list", lay.rects)
        self.assertIn("detail", lay.rects)


class SelectionAwareDetailTest(unittest.TestCase):
    """The detail pane tracks the cursor: it renders the SELECTED worker's full,
    untruncated SAYING — and only that worker's, not the fleet's."""

    def _draw(self, rows, app, w, h, *, drill=False):
        from end_of_line.top_layout import LayoutEngine
        from end_of_line.top_registry import Snapshot
        from end_of_line.top_render import BufferSurface

        s = BufferSurface(w, h)
        layout = LayoutEngine().layout(w, h, drill=drill)
        top._draw_panes(s, Snapshot(rows), layout, app=app)
        return s, layout

    def test_detail_shows_selected_worker_full_untruncated_saying(self) -> None:
        from end_of_line.top_layout import AppState

        say = "HEAD_TOKEN " + "filler " * 60 + "TAIL_TOKEN"
        rows = [
            _draw_row(phase_id="a", last_text="alpha-only-text"),
            _draw_row(phase_id="b", last_text=say),
        ]
        app = AppState()
        app.sync_selection(rows)
        app.move(1, rows)  # select b
        s, layout = self._draw(rows, app, 120, 40)
        det = layout.rects["detail"]
        detail_text = " ".join(t for (_y, x, t) in s.cells if x >= det.x)
        # full SAYING reproduced end-to-end (word-wrapped, never ellipsized)…
        self.assertIn("HEAD_TOKEN", detail_text)
        self.assertIn("TAIL_TOKEN", detail_text)
        # …and the non-selected worker's text is absent from the detail region.
        self.assertNotIn("alpha-only-text", detail_text)

    def test_list_marks_the_selected_row(self) -> None:
        from end_of_line.top_layout import AppState

        rows = [_draw_row(phase_id="a"), _draw_row(phase_id="b")]
        app = AppState()
        app.sync_selection(rows)
        app.move(1, rows)  # select b
        s, _layout = self._draw(rows, app, 120, 40)
        cursor_cells = [c for c in s.cells if "▸" in c[2]]
        self.assertEqual(len(cursor_cells), 1)  # exactly one row carries the cursor

    def test_drilled_detail_renders_selected_worker(self) -> None:
        from end_of_line.top_layout import AppState

        rows = [_draw_row(phase_id="a", last_text="DRILL_SAYING")]
        app = AppState()
        app.sync_selection(rows)
        app.drill_in()
        s, layout = self._draw(rows, app, 40, 24, drill=True)
        self.assertNotIn("list", layout.rects)
        txt = " ".join(t for (_y, _x, t) in s.cells)
        self.assertIn("DRILL_SAYING", txt)

    def test_empty_fleet_detail_is_safe(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        app.sync_selection([])
        s, _layout = self._draw([], app, 120, 40)  # must not raise
        self.assertIsInstance(s.cells, list)


class HandleKeyTest(unittest.TestCase):
    """The keypress dispatcher: read-only by invariant (D7), with the
    list-vs-detail scroll split that mirrors the web."""

    def setUp(self) -> None:
        import curses

        from end_of_line.top_layout import AppState, LayoutEngine

        self.curses = curses
        self.app = AppState()
        self.engine = LayoutEngine()
        self.rows = [_draw_row(phase_id=p) for p in ("a", "b", "c")]
        self.app.sync_selection(self.rows)

    def _key(self, ch, *, w=120, h=40, drill=False):
        layout = self.engine.layout(w, h, drill=drill)
        return top._handle_key(ch, self.app, self.rows, layout, self.curses)

    def test_q_quits(self) -> None:
        self.assertTrue(self._key(ord("q")))
        self.assertFalse(self._key(self.curses.KEY_DOWN))

    def test_arrows_move_selection_in_list_focus(self) -> None:
        self._key(self.curses.KEY_DOWN)
        self.assertEqual(self.app.selected_index, 1)
        self._key(ord("k"))  # up
        self.assertEqual(self.app.selected_index, 0)

    def test_g_and_G_jump_to_ends(self) -> None:
        self._key(ord("G"))
        self.assertEqual(self.app.selected_index, 2)
        self._key(ord("g"))
        self.assertEqual(self.app.selected_index, 0)

    def test_detail_focus_scrolls_instead_of_moving(self) -> None:
        self.app.focus = "detail"  # split has a detail pane
        self._key(self.curses.KEY_DOWN)
        self.assertEqual(self.app.selected_index, 0)  # selection unchanged
        self.assertEqual(self.app.scroll, 1)          # detail scrolled instead

    def test_enter_drills_only_on_master_preset(self) -> None:
        self._key(self.curses.KEY_ENTER, w=120, h=40)  # split → no drill
        self.assertFalse(self.app.drill)
        self._key(self.curses.KEY_ENTER, w=40, h=24)   # master → drills
        self.assertTrue(self.app.drill)

    def test_esc_leaves_drill(self) -> None:
        self.app.drill = True
        self._key(27)  # Esc
        self.assertFalse(self.app.drill)

    def test_w_cycles_layout_preset(self) -> None:
        self.assertIsNone(self.app.layout_preset)
        self._key(ord("w"))
        self.assertEqual(self.app.layout_preset, "split")

    def test_pagedown_in_drill_scrolls_by_detail_height_not_one(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        rows = [_draw_row(phase_id="a", last_text="word " * 400)]
        app.sync_selection(rows)
        app.drill_in()
        layout = self.engine.layout(40, 24, drill=True)
        det = layout.rects["detail"]
        top._handle_key(self.curses.KEY_NPAGE, app, rows, layout, self.curses)
        # A page is the scrolled pane's height — not the absent list rect's 1.
        self.assertEqual(app.scroll, det.h - 1)
        self.assertGreater(app.scroll, 1)

    def test_focus_normalizes_to_list_when_no_detail_pane(self) -> None:
        from end_of_line.top_layout import AppState

        app = AppState()
        rows = self.rows
        app.sync_selection(rows)
        app.focus = "detail"  # stale focus carried in from a wider geometry
        layout = self.engine.layout(40, 24)  # master — list only, no detail
        top._handle_key(self.curses.KEY_DOWN, app, rows, layout, self.curses)
        self.assertEqual(app.focus, "list")
        self.assertEqual(app.selected_index, 1)  # arrow moved selection, didn't scroll

    def test_no_destructive_keybind(self) -> None:
        # Every printable key that isn't bound is a no-op — the UI is read-only,
        # so a stray keystroke must never mutate worker state or quit.
        for ch in (ord("d"), ord("x"), ord("r"), ord("!"), ord(" ")):
            self.assertFalse(self._key(ch))


# --- Phase 4: new metrics (fused health glyph, tokens, attempts, lease, ------
#     phase progress) — each registered in top_registry.py alone, with the
#     health + token math pinned to web/index.html so the two dashboards agree.


class AssembleRowNewKeysTest(unittest.TestCase):
    """assemble_row exposes the claim-derived keys the new metrics read."""

    def _activity(self) -> dict:
        return {"last_command": None, "last_write": None, "last_text": None,
                "last_activity_ts": None, "command_running": False, "tokens": None}

    def test_attempts_and_lease_remaining_and_stuck(self) -> None:
        claim = {
            "phase_id": "p", "started_at": "2026-06-03T00:00:00Z", "pid": os.getpid(),
            "attempts": 2, "lease_expires": "2026-06-03T00:25:00Z",
            "stuck_tool_emitted_at": "2026-06-03T00:08:00Z",
        }
        row = top.assemble_row(claim, self._activity(), now=_now())
        self.assertEqual(row["attempts"], 2)
        self.assertAlmostEqual(row["lease_remaining_seconds"], 15 * 60, delta=1)
        self.assertTrue(row["stuck"])

    def test_no_stuck_marker_is_false(self) -> None:
        claim = {"phase_id": "p", "started_at": "2026-06-03T00:00:00Z", "pid": os.getpid()}
        row = top.assemble_row(claim, self._activity(), now=_now())
        self.assertFalse(row["stuck"])
        self.assertIsNone(row["attempts"])
        self.assertIsNone(row["lease_remaining_seconds"])

    def test_expired_lease_is_negative(self) -> None:
        claim = {"phase_id": "p", "started_at": "2026-06-03T00:00:00Z", "pid": os.getpid(),
                 "lease_expires": "2026-06-03T00:05:00Z"}  # 5 min before `now`
        row = top.assemble_row(claim, self._activity(), now=_now())
        self.assertLess(row["lease_remaining_seconds"], 0)


class GatherRowsNewKeysTest(GitProjectTestCase):
    """gather_rows enriches the row with the plan-config-derived keys."""

    def setUp(self) -> None:
        super().setUp()
        self._pr = TemporaryDirectory()
        self.addCleanup(self._pr.cleanup)
        self.projects_root = Path(self._pr.name)

    def test_max_attempts_and_phase_progress(self) -> None:
        self._claim("a")
        rows = top.gather_rows(projects_root=self.projects_root)
        self.assertEqual(len(rows), 1)
        # Default config max attempts surfaces for the attempts X/max metric.
        self.assertEqual(rows[0]["max_attempts"], st.DEFAULT_MAX_ATTEMPTS)
        # A single-phase test plan has no sessions index → progress unknown.
        self.assertIn("phase_index", rows[0])
        self.assertIn("phase_total", rows[0])


class BlockedRowGatherTest(GitProjectTestCase):
    """A plan with an OPEN blocker but no claim surfaces as a claimless
    'blocked' row (clu-dashboard-blocked). `clu block` releases the claim, so
    the blocker persists in `data['blockers']` while `current_claim` is gone —
    gather_rows reads it back instead of skipping the plan."""

    def setUp(self) -> None:
        super().setUp()
        self._pr = TemporaryDirectory()
        self.addCleanup(self._pr.cleanup)
        self.projects_root = Path(self._pr.name)

    def _state_path(self, slug: str) -> Path:
        return self.project / "plans" / ".orchestrator" / f"{slug}.state.json"

    def _block(self, slug: str, phase: str, question: str) -> str:
        with st.mutate(self._state_path(slug)) as data:
            return st.add_blocker(data, phase, question, ["yes", "no"])

    def _register_plan(self, slug: str, *sessions: str) -> None:
        (self.project / "plans" / f"{slug}.md").write_text(plan_body(*sessions))
        self.assertEqual(cli_main(["init", "--project", str(self.project), "--plan", slug]), 0)

    def test_open_blocker_no_claim_becomes_blocked_row(self) -> None:
        self._block("test-plan", "a", "Pick a branch name?")
        rows = top.gather_rows(projects_root=self.projects_root)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertTrue(r["blocked"])
        self.assertEqual(r["blocker_question"], "Pick a branch name?")
        self.assertEqual(r["phase_id"], "a")
        self.assertFalse(r["alive"])
        # Still shows WHERE in the plan it's stuck (phase x-of-N like a claim).
        self.assertEqual(r["phase_index"], 1)
        self.assertEqual(r["phase_total"], 2)
        self.assertIsNotNone(r["blocked_seconds"])
        # One flat schema (D10): claim-only fields present but None.
        self.assertIsNone(r["last_command"])
        self.assertIsNone(r["lease_remaining_seconds"])

    def test_answered_blocker_produces_no_row(self) -> None:
        bid = self._block("test-plan", "a", "q?")
        with st.mutate(self._state_path("test-plan")) as data:
            for b in data["blockers"]:
                if b["id"] == bid:
                    b["answer"] = "yes"
        self.assertEqual(top.gather_rows(projects_root=self.projects_root), [])

    def test_blocked_sorts_before_running(self) -> None:
        # test-plan (registered first in setUp) is running; a later-registered
        # plan is blocked. Registry order is [running, blocked]; the blocked row
        # must still float to the top of the fleet (stable blocked-first sort).
        self._claim("a")
        self._register_plan("zzz-blocked", "x")
        self._block("zzz-blocked", "x", "need input")
        rows = top.gather_rows(projects_root=self.projects_root)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["blocked"])
        self.assertEqual(rows[0]["plan"], "zzz-blocked")
        self.assertFalse(rows[1].get("blocked"))
        self.assertEqual(rows[1]["plan"], "test-plan")


class BlockedHealthAndRenderTest(unittest.TestCase):
    """The 'blocked' health state (amber `!`, sorts to top) + the curses render
    of a claimless blocked row (`blk` PID cell, blocker question, detail block,
    fleet count). Blocked is checked BEFORE the dead path everywhere — a blocked
    plan has `alive=False` and must read as needs-you, never as work-died."""

    def _row(self, **over) -> dict:
        base = {
            "project": "myrepo", "plan": "routing", "phase_id": "impl",
            "ran_seconds": None, "heartbeat_age_seconds": None, "alive": False,
            "last_command": None, "command_running": False,
            "last_write": None, "last_write_seconds": None,
            "last_text": None, "last_activity_seconds": None, "tokens": None,
            "attempts": None, "max_attempts": 3, "lease_remaining_seconds": None,
            "stuck": False, "phase_index": 1, "phase_total": 3,
            "blocked": True, "blocker_question": "Which API base?",
            "blocked_seconds": 180,
        }
        base.update(over)
        return base

    def test_m_health_returns_blocked_first(self) -> None:
        from end_of_line import top_registry

        snap = top_registry.Snapshot([self._row()])
        self.assertEqual(top_registry.METRICS["health"].compute(snap, self._row()), "blocked")

    def test_four_distinct_health_glyphs(self) -> None:
        from end_of_line import top_registry

        m = top_registry.METRICS["health"]
        glyphs = {m.render(s, 1) for s in ("ok", "warn", "dead", "blocked")}
        self.assertEqual(len(glyphs), 4)

    def test_blocked_sorts_above_dead_in_health_metric(self) -> None:
        from end_of_line import top_registry

        m = top_registry.METRICS["health"]
        # Metric.sort_key is registry-typed `(object) -> object`; the health
        # metric's keys are ints.
        self.assertLess(cast(int, m.sort_key("blocked")), cast(int, m.sort_key("dead")))

    def test_format_rows_shows_blk_and_question_not_dead(self) -> None:
        body = "\n".join(top.format_rows([self._row()], width=200)[1:])
        self.assertIn("blk", body)
        self.assertIn("Which API base?", body)
        self.assertNotIn("dead", body)

    def test_format_detail_shows_blocked_block(self) -> None:
        out = "\n".join(top.format_detail([self._row()], width=120))
        self.assertIn("BLOCKED", out)
        self.assertIn("Which API base?", out)
        self.assertIn("blk", out)
        self.assertNotIn("dead", out)

    def test_fleet_summary_counts_blocked_not_dead(self) -> None:
        from end_of_line.top_registry import fleet_summary

        rows = [self._row(), _draw_row(alive=True, last_activity_seconds=2)]
        line = fleet_summary(rows, 120)
        self.assertIn("1 blocked", line)
        self.assertIn("1 running", line)
        # The blocked row (alive=False) must NOT inflate the dead count.
        self.assertIn("0 dead", line)


class WorkerHealthTest(unittest.TestCase):
    """The fused glyph's classifier (D8) — one signal from PID + ACT + HB +
    stuck. The act>60 threshold is pinned to web/index.html:238."""

    def setUp(self) -> None:
        from end_of_line import top_registry

        self.health = top_registry.worker_health

    def test_dead_pid_is_red(self) -> None:
        self.assertEqual(self.health(alive=False, act=2, hb=2, stuck=False), "dead")

    def test_fresh_worker_is_green(self) -> None:
        self.assertEqual(self.health(alive=True, act=10, hb=20, stuck=False), "ok")

    def test_act_threshold_parity_with_web(self) -> None:
        # index.html:238 → `act > 60` is warn; 60 is still ok, 61 tips to warn.
        self.assertEqual(self.health(alive=True, act=60, hb=2, stuck=False), "ok")
        self.assertEqual(self.health(alive=True, act=61, hb=2, stuck=False), "warn")

    def test_act_none_is_warn(self) -> None:
        # Matches the web's `act == null || act > 60` → warn.
        self.assertEqual(self.health(alive=True, act=None, hb=2, stuck=False), "warn")

    def test_pid_alive_but_act_stale_is_not_green(self) -> None:
        # The silent-wedge D8 exists to catch: PID ok, transcript gone quiet.
        self.assertEqual(self.health(alive=True, act=300, hb=5, stuck=False), "warn")

    def test_stuck_command_escalates_even_when_act_fresh(self) -> None:
        self.assertEqual(self.health(alive=True, act=3, hb=3, stuck=True), "warn")

    def test_dead_heartbeat_loop_is_warn(self) -> None:
        # hb well past the 25-min ceiling = the heartbeat loop itself died.
        self.assertEqual(self.health(alive=True, act=3, hb=2000, stuck=False), "warn")
        # A normal 2-min heartbeat age never false-positives.
        self.assertEqual(self.health(alive=True, act=3, hb=120, stuck=False), "ok")


class TokenTotalParityTest(unittest.TestCase):
    """The token sum must match web/index.html:218 tokenTotal exactly."""

    def setUp(self) -> None:
        from end_of_line import top_registry

        self.total = top_registry.token_total
        self.human = top_registry.token_human

    def test_sums_flat_numeric_values_like_js(self) -> None:
        usage = {
            "input_tokens": 1200, "output_tokens": 300,
            "cache_read_input_tokens": 40000, "cache_creation_input_tokens": 500,
        }
        self.assertEqual(self.total(usage), 1200 + 300 + 40000 + 500)

    def test_nested_dicts_are_skipped_like_js(self) -> None:
        # JS sums only `typeof v === "number"`; a nested cache_creation object
        # is skipped. Python must skip dict values the same way or the two
        # dashboards report different token totals.
        usage = {"input_tokens": 100, "cache_creation": {"ephemeral_5m": 9999}}
        self.assertEqual(self.total(usage), 100)

    def test_none_and_empty_become_none(self) -> None:
        self.assertIsNone(self.total(None))
        self.assertIsNone(self.total({}))
        self.assertIsNone(self.total("nonsense"))

    def test_scalar_passthrough(self) -> None:
        self.assertEqual(self.total(42), 42)

    def test_human_compact_format(self) -> None:
        self.assertEqual(self.human(None), "—")
        self.assertEqual(self.human(950), "950")
        self.assertEqual(self.human(45000), "45K")
        self.assertEqual(self.human(1_250_000), "1.25M")


class NewMetricsTest(unittest.TestCase):
    """Each new metric is a pure (compute, render) pair, registered alone in
    top_registry.py and reachable through --cols."""

    def setUp(self) -> None:
        from end_of_line import top_registry

        self.reg = top_registry
        self.snap = top_registry.Snapshot([_draw_row()])

    def _row(self, **over) -> dict:
        base = _draw_row(
            tokens={"input_tokens": 1000, "output_tokens": 250000},
            attempts=1, max_attempts=3, lease_remaining_seconds=720,
            phase_index=2, phase_total=5, stuck=False,
        )
        base.update(over)
        return base

    def test_new_metrics_registered_and_cols_selectable(self) -> None:
        for key in ("health", "tokens", "attempts", "lease", "progress"):
            self.assertIn(key, self.reg.METRICS)
            self.assertIn(key, self.reg.metric_keys())
        # --cols accepts them without an "unknown column" error.
        self.assertEqual(
            self.reg.parse_cols("health,tokens,attempts"),
            ("health", "tokens", "attempts"),
        )

    def test_health_metric_renders_glyph_by_state(self) -> None:
        m = self.reg.METRICS["health"]
        self.assertEqual(m.compute(self.snap, self._row(alive=True, last_activity_seconds=2)), "ok")
        self.assertEqual(m.compute(self.snap, self._row(alive=False)), "dead")
        # render maps each state to its own glyph; the three are distinct.
        glyphs = {m.render(s, 1) for s in ("ok", "warn", "dead")}
        self.assertEqual(len(glyphs), 3)

    def test_tokens_metric_matches_web_sum(self) -> None:
        m = self.reg.METRICS["tokens"]
        v = m.compute(self.snap, self._row())
        self.assertEqual(v, 1000 + 250000)
        self.assertEqual(m.render(v, 8).strip(), "251K")

    def test_attempts_metric_x_of_max(self) -> None:
        m = self.reg.METRICS["attempts"]
        self.assertEqual(m.render(m.compute(self.snap, self._row(attempts=2, max_attempts=3)), 5).strip(), "2/3")
        self.assertEqual(m.render(m.compute(self.snap, self._row(attempts=None)), 5).strip(), "—")

    def test_lease_metric_countdown_and_expired(self) -> None:
        m = self.reg.METRICS["lease"]
        self.assertEqual(m.render(m.compute(self.snap, self._row(lease_remaining_seconds=720)), 6).strip(), "12m00s")
        self.assertEqual(m.render(m.compute(self.snap, self._row(lease_remaining_seconds=-5)), 6).strip(), "exp")
        self.assertEqual(m.render(m.compute(self.snap, self._row(lease_remaining_seconds=None)), 6).strip(), "—")

    def test_progress_metric_x_of_n(self) -> None:
        m = self.reg.METRICS["progress"]
        self.assertEqual(m.render(m.compute(self.snap, self._row(phase_index=2, phase_total=5)), 5).strip(), "2/5")
        self.assertEqual(m.render(m.compute(self.snap, self._row(phase_index=None, phase_total=None)), 5).strip(), "—")

    def test_table_pane_can_render_new_cols_no_engine_edit(self) -> None:
        # The proof: a pane built from the new metric keys renders through the
        # existing table pane with no layout/render-loop change.
        pane = self.reg.PANES["table"]
        snap = self.reg.Snapshot([self._row()])
        lines = pane.render(snap, width=60, cols=("name", "health", "tokens", "attempts"))
        self.assertTrue(all(len(ln) <= 60 for ln in lines))
        self.assertIn("TOKENS", lines[0])


if __name__ == "__main__":
    unittest.main()
