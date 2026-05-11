"""Inbound iMessage poller tests."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from end_of_line import notify_inbound, registry, state as st
from end_of_line.notify_inbound import OpenBlocker


def _make_chat_db(path: Path, rows: list[tuple[int, int, str | None]]) -> None:
    """Build a chat.db-shaped fixture. rows: [(rowid, is_from_me, text)]."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, "
        "is_from_me INTEGER, text TEXT)"
    )
    conn.executemany(
        "INSERT INTO message (ROWID, is_from_me, text) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _ob(slug: str, *, blocker_id: str = "q-1", options: int = 2,
        root: str | Path = "/p", ts: str = "") -> OpenBlocker:
    """Factory keeps tests readable as OpenBlocker grows fields."""
    return OpenBlocker(
        project_root=Path(root), plan_slug=slug, blocker_id=blocker_id,
        options_count=options, last_notified_at=ts,
    )


class ReplyRouteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _ob("plan-a", blocker_id="q-1", root="/p")
        self.b = _ob("plan-b", blocker_id="q-3", root="/q")

    def test_bare_digit_routes_when_only_one_open(self) -> None:
        self.assertEqual(
            notify_inbound.route_reply("0", [self.a]),
            (self.a, "0"),
        )

    def test_slug_prefix_disambiguates(self) -> None:
        self.assertEqual(
            notify_inbound.route_reply("plan-b 2", [self.a, self.b]),
            (self.b, "2"),
        )

    def test_unknown_slug_rejected(self) -> None:
        self.assertIsNone(notify_inbound.route_reply("plan-z 0", [self.a, self.b]))

    def test_whitespace_tolerated(self) -> None:
        self.assertEqual(
            notify_inbound.route_reply("  plan-a   1  ", [self.a, self.b]),
            (self.a, "1"),
        )

    def test_no_open_blockers(self) -> None:
        self.assertIsNone(notify_inbound.route_reply("0", []))

    def test_casual_chat_rejected(self) -> None:
        # The whole point of the strict regex: noise must not look like an answer.
        for line in ["lol", "no thanks", "10", "yes 0", "", "0 0", "plan-a"]:
            self.assertIsNone(
                notify_inbound.route_reply(line, [self.a]),
                msg=f"line {line!r} unexpectedly routed",
            )


class LastPingedRoutingTestCase(unittest.TestCase):
    """Bare-digit + multiple plans → route to most-recently-pinged plan."""

    def test_bare_digit_picks_most_recent_of_two(self) -> None:
        older = _ob("plan-a", ts="2026-05-10T12:00:00Z")
        newer = _ob("plan-b", ts="2026-05-11T09:00:00Z")
        self.assertEqual(
            notify_inbound.route_reply("0", [older, newer]),
            (newer, "0"),
        )

    def test_bare_digit_picks_most_recent_of_three(self) -> None:
        oldest = _ob("plan-a", ts="2026-05-09T00:00:00Z")
        middle = _ob("plan-b", ts="2026-05-10T00:00:00Z")
        newest = _ob("plan-c", ts="2026-05-11T00:00:00Z")
        self.assertEqual(
            notify_inbound.route_reply("1", [oldest, newest, middle]),
            (newest, "1"),
        )

    def test_slug_prefix_overrides_last_pinged(self) -> None:
        # Explicit beats inferred — even if plan-b was pinged later.
        a = _ob("plan-a", ts="2026-05-10T00:00:00Z")
        b = _ob("plan-b", ts="2026-05-11T00:00:00Z")
        self.assertEqual(
            notify_inbound.route_reply("plan-a 1", [a, b]),
            (a, "1"),
        )

    def test_falls_through_when_top_index_out_of_range(self) -> None:
        # plan-b pinged last but only has 2 options; "5" falls through to plan-a.
        a = _ob("plan-a", options=6, ts="2026-05-10T00:00:00Z")
        b = _ob("plan-b", options=2, ts="2026-05-11T00:00:00Z")
        self.assertEqual(
            notify_inbound.route_reply("5", [a, b]),
            (a, "5"),
        )

    def test_tie_on_last_notified_refuses(self) -> None:
        # Theoretical: two plans pinged at the same ts → ambiguous, refuse.
        same = "2026-05-11T09:00:00Z"
        a = _ob("plan-a", ts=same)
        b = _ob("plan-b", ts=same)
        self.assertIsNone(notify_inbound.route_reply("0", [a, b]))

    def test_no_eligible_plan_returns_none(self) -> None:
        # Digit out of range for every plan → no route.
        a = _ob("plan-a", options=2, ts="2026-05-10T00:00:00Z")
        b = _ob("plan-b", options=3, ts="2026-05-11T00:00:00Z")
        self.assertIsNone(notify_inbound.route_reply("9", [a, b]))


class PollOnceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "chat.db"
        self.dispatched: list[tuple[OpenBlocker, str]] = []
        self.ticks: list[tuple[Path, str]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _dispatch(self, target: OpenBlocker, answer: str) -> None:
        self.dispatched.append((target, answer))

    def _tick(self, project_root: Path, plan_slug: str) -> None:
        self.ticks.append((project_root, plan_slug))

    def _poll(self, conn, last, *, blockers, **kw) -> int:
        kw.setdefault("tick_spawner", self._tick)
        return notify_inbound.poll_once(
            conn, last,
            open_blockers_fn=lambda: blockers,
            dispatcher=kw.pop("dispatcher", self._dispatch),
            **kw,
        )

    def test_dispatches_matched_inbound_only(self) -> None:
        _make_chat_db(self.db_path, [
            (10, 0, "0"),
            (11, 1, "ignore"),
            (12, 0, "lol"),
        ])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 0, blockers=[target])
        self.assertEqual(last, 12)
        self.assertEqual(self.dispatched, [(target, "0")])

    def test_advances_seen_on_no_match(self) -> None:
        # Otherwise a chatty stranger pinning the cursor would let an old
        # unrelated message re-trigger if a blocker later opened.
        _make_chat_db(self.db_path, [(7, 0, "hey")])
        conn = notify_inbound.open_chat_db(self.db_path)
        last = self._poll(conn, 0, blockers=[])
        self.assertEqual(last, 7)
        self.assertEqual(self.dispatched, [])

    def test_skips_already_seen(self) -> None:
        _make_chat_db(self.db_path, [(1, 0, "0"), (2, 0, "0")])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 1, blockers=[target])
        self.assertEqual(last, 2)
        self.assertEqual(len(self.dispatched), 1)

    def test_returns_last_rowid_when_no_new_rows(self) -> None:
        _make_chat_db(self.db_path, [(5, 0, "0")])
        conn = notify_inbound.open_chat_db(self.db_path)
        last = self._poll(conn, 5, blockers=[])
        self.assertEqual(last, 5)

    def test_auto_ticks_after_successful_dispatch(self) -> None:
        # Project root is a fresh tmp dir → no .orchestrator.json → default True.
        proj = self.tmp / "proj"
        proj.mkdir()
        _make_chat_db(self.db_path, [(1, 0, "0")])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root=proj)
        self._poll(conn, 0, blockers=[target])
        self.assertEqual(self.ticks, [(proj, "plan-a")])

    def test_auto_tick_skipped_when_config_opt_out(self) -> None:
        # `.orchestrator.json` with `inbound_auto_tick: false` wins over default.
        proj = self.tmp / "proj"
        proj.mkdir()
        (proj / ".orchestrator.json").write_text(json.dumps({
            "notify": {"inbound_auto_tick": False},
        }))
        _make_chat_db(self.db_path, [(1, 0, "0")])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root=proj)
        self._poll(conn, 0, blockers=[target])
        self.assertEqual(self.dispatched, [(target, "0")])
        self.assertEqual(self.ticks, [])

    def test_no_tick_when_dispatcher_raises(self) -> None:
        # rc!=0 from `clu answer` → raise → no auto-tick (stale state guard).
        proj = self.tmp / "proj"
        proj.mkdir()
        _make_chat_db(self.db_path, [(1, 0, "0"), (2, 0, "hey")])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root=proj)

        def boom(_t, _a):
            raise RuntimeError("clu answer failed")

        last = self._poll(conn, 0, blockers=[target], dispatcher=boom)
        self.assertEqual(last, 2)  # cursor still advances past the bad row
        self.assertEqual(self.ticks, [])

    def test_tick_spawner_failure_does_not_stall_poller(self) -> None:
        # Auto-tick is fire-and-forget; an OSError from Popen must be swallowed.
        proj = self.tmp / "proj"
        proj.mkdir()
        _make_chat_db(self.db_path, [(1, 0, "0"), (2, 0, "0")])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root=proj)

        def angry_tick(_p, _s):
            raise OSError("no clu on PATH")

        last = self._poll(conn, 0, blockers=[target], tick_spawner=angry_tick)
        self.assertEqual(last, 2)
        self.assertEqual(len(self.dispatched), 2)  # both rows still dispatched


class SeenRowidTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_round_trip(self) -> None:
        path = self.tmp / "seen"
        notify_inbound.write_seen(path, 42)
        self.assertEqual(notify_inbound.read_seen(path), 42)

    def test_missing_returns_zero(self) -> None:
        self.assertEqual(notify_inbound.read_seen(self.tmp / "nope"), 0)

    def test_corrupt_returns_zero(self) -> None:
        path = self.tmp / "seen"
        path.write_text("not-a-number")
        self.assertEqual(notify_inbound.read_seen(path), 0)

    def test_write_creates_parent_dir(self) -> None:
        path = self.tmp / "deeper" / "still" / "seen"
        notify_inbound.write_seen(path, 9)
        self.assertEqual(notify_inbound.read_seen(path), 9)


class OpenBlockersForHostTestCase(unittest.TestCase):
    """Registry → state files → first open blocker per plan."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.reg_path = self.tmp / "registry.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, project: Path, slug: str, blockers: list[dict]) -> None:
        sp = project / "plans" / ".orchestrator" / f"{slug}.state.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        with st.locked(sp):
            data = st.empty_state(slug, "plans")
            data["blockers"] = blockers
            st.save_atomic(sp, data)

    def _open_blocker(self, blocker_id: str, *, answer: str | None = None) -> dict:
        return {
            "id": blocker_id, "phase_id": "p", "type": st.BLOCKER_INPUT,
            "question": "?", "options": ["A", "B"], "context": "",
            "asked_at": st.utcnow(), "answer": answer,
            "answered_at": st.utcnow() if answer else None,
        }

    def test_first_open_blocker_per_plan(self) -> None:
        project = self.tmp / "proj"
        project.mkdir()
        self._seed(project, "plan-a", [
            self._open_blocker("q-1", answer="FastAPI"),
            self._open_blocker("q-2"),  # first OPEN — should win
            self._open_blocker("q-3"),
        ])
        registry.register(project, "plan-a", path=self.reg_path)

        out = notify_inbound.open_blockers_for_host(registry.entries(self.reg_path))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].plan_slug, "plan-a")
        self.assertEqual(out[0].blocker_id, "q-2")

    def test_plan_with_no_open_blocker_omitted(self) -> None:
        project = self.tmp / "proj"
        project.mkdir()
        self._seed(project, "plan-a", [self._open_blocker("q-1", answer="x")])
        registry.register(project, "plan-a", path=self.reg_path)
        self.assertEqual(
            notify_inbound.open_blockers_for_host(registry.entries(self.reg_path)),
            [],
        )

    def test_two_plans_each_open(self) -> None:
        # Multi-plan is the steady state — both must surface.
        p1 = self.tmp / "p1"
        p2 = self.tmp / "p2"
        p1.mkdir(); p2.mkdir()
        self._seed(p1, "plan-a", [self._open_blocker("q-1")])
        self._seed(p2, "plan-b", [self._open_blocker("q-7")])
        registry.register(p1, "plan-a", path=self.reg_path)
        registry.register(p2, "plan-b", path=self.reg_path)
        out = notify_inbound.open_blockers_for_host(registry.entries(self.reg_path))
        self.assertEqual(
            sorted((ob.plan_slug, ob.blocker_id) for ob in out),
            [("plan-a", "q-1"), ("plan-b", "q-7")],
        )

    def test_last_notified_at_from_most_recent_phase_blocked(self) -> None:
        # Bare-digit routing leans on this — the ts must come from the
        # plan's own EVENT_PHASE_BLOCKED, not from anywhere else.
        project = self.tmp / "proj"
        project.mkdir()
        sp = project / "plans" / ".orchestrator" / "plan-a.state.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        with st.locked(sp):
            data = st.empty_state("plan-a", "plans")
            data["blockers"] = [self._open_blocker("q-1")]
            data["events"] = [
                {"ts": "2026-05-09T00:00:00Z", "type": st.EVENT_PHASE_BLOCKED,
                 "phase": "p", "blocker_id": "q-0"},
                {"ts": "2026-05-11T12:34:56Z", "type": st.EVENT_PHASE_BLOCKED,
                 "phase": "p", "blocker_id": "q-1"},
            ]
            st.save_atomic(sp, data)
        registry.register(project, "plan-a", path=self.reg_path)
        out = notify_inbound.open_blockers_for_host(registry.entries(self.reg_path))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].last_notified_at, "2026-05-11T12:34:56Z")
        self.assertEqual(out[0].options_count, 2)

    def test_missing_state_file_skipped(self) -> None:
        # Registered but never `clu init`-ed → no state file. Don't crash.
        project = self.tmp / "proj"
        project.mkdir()
        registry.register(project, "plan-a", path=self.reg_path)
        self.assertEqual(
            notify_inbound.open_blockers_for_host(registry.entries(self.reg_path)),
            [],
        )


if __name__ == "__main__":
    unittest.main()
