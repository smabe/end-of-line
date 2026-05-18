"""Inbound iMessage poller tests."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from end_of_line import notify_inbound, registry, state as st
from end_of_line.notify_inbound import OpenBlocker


DEFAULT_CHAT_ID = "+15551234567"  # operator's self-chat handle for fixtures


def _make_chat_db(path: Path, rows: list[tuple]) -> None:
    """Build a chat.db-shaped fixture.

    rows: (rowid, is_from_me, text) — uses DEFAULT_CHAT_ID for the chat scope,
          OR (rowid, is_from_me, text, chat_identifier) for explicit scoping.
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, "
        "is_from_me INTEGER, text TEXT)"
    )
    conn.execute(
        "CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT)"
    )
    conn.execute(
        "CREATE TABLE chat_message_join "
        "(chat_id INTEGER, message_id INTEGER)"
    )
    chat_rowids: dict[str, int] = {}
    for row in rows:
        if len(row) == 3:
            rowid, is_from_me, text = row
            chat_id = DEFAULT_CHAT_ID
        else:
            rowid, is_from_me, text, chat_id = row
        if chat_id not in chat_rowids:
            chat_rowids[chat_id] = len(chat_rowids) + 1
            conn.execute(
                "INSERT INTO chat (ROWID, chat_identifier) VALUES (?, ?)",
                (chat_rowids[chat_id], chat_id),
            )
        conn.execute(
            "INSERT INTO message (ROWID, is_from_me, text) VALUES (?, ?, ?)",
            (rowid, is_from_me, text),
        )
        conn.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
            (chat_rowids[chat_id], rowid),
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
        self.dispatched: list[tuple[Path, str, int]] = []  # (state_path, blocker_id, answer_index)
        self.ticks: list[tuple[Path, str]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _shell_answer(self, state_path: Path, blocker_id: str, answer_index: int) -> None:
        self.dispatched.append((state_path, blocker_id, answer_index))

    def _tick(self, project_root: Path, plan_slug: str) -> None:
        self.ticks.append((project_root, plan_slug))

    def _mock_locator(self, blockers):
        """Build a locator_fn from pre-built OpenBlockers (avoids real state files)."""
        from end_of_line.notify_base import route_reply
        from end_of_line.state_locator import LocatorResult

        def locator(entries, text):
            match = route_reply(text, blockers)
            if match is None:
                return LocatorResult(variant="NOT_FOUND")
            target, answer = match
            state_path = (
                Path(target.project_root) / "plans" / ".orchestrator"
                / f"{target.plan_slug}.state.json"
            )
            return LocatorResult(
                variant="FOUND",
                state_path=state_path,
                blocker_id=target.blocker_id,
                answer_index=int(answer),
                project_root=Path(target.project_root),
            )
        return locator

    def _poll(self, conn, last, *, blockers, shell_answer_fn=None, **kw) -> int:
        kw.setdefault("tick_spawner", self._tick)
        kw.setdefault("self_chat_id", DEFAULT_CHAT_ID)
        return notify_inbound.poll_once(
            conn, last,
            _locator_fn=kw.pop("_locator_fn", self._mock_locator(blockers)),
            shell_answer_fn=shell_answer_fn or self._shell_answer,
            **kw,
        )

    def _state_path(self, target: OpenBlocker) -> Path:
        return (
            Path(target.project_root) / "plans" / ".orchestrator"
            / f"{target.plan_slug}.state.json"
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
        self.assertEqual(self.dispatched, [(self._state_path(target), "q-1", 0)])

    def test_self_chat_is_from_me_1_routes(self) -> None:
        # In self-chat the operator IS the sender, so the reply row has
        # is_from_me=1. The chat-scoped SQL must accept it.
        _make_chat_db(self.db_path, [(1, 1, "0")])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 0, blockers=[target])
        self.assertEqual(last, 1)
        self.assertEqual(self.dispatched, [(self._state_path(target), "q-1", 0)])

    def test_other_chat_dropped_by_scope(self) -> None:
        _make_chat_db(self.db_path, [(1, 0, "0", "+15559999999")])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 0, blockers=[target])
        # Cursor must NOT advance — out-of-scope rows aren't read at all.
        self.assertEqual(last, 0)
        self.assertEqual(self.dispatched, [])

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
        self.assertEqual(self.dispatched, [(self._state_path(target), "q-1", 0)])
        self.assertEqual(self.ticks, [])

    def test_no_tick_when_dispatcher_raises(self) -> None:
        # answer write failed → raise → no auto-tick (stale state guard).
        proj = self.tmp / "proj"
        proj.mkdir()
        _make_chat_db(self.db_path, [(1, 0, "0"), (2, 0, "hey")])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root=proj)

        def boom(sp, bid, ai):
            raise RuntimeError("clu answer failed")

        last = self._poll(conn, 0, blockers=[target], shell_answer_fn=boom)
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


def _make_resolver_db(path: Path, chats: list[dict]) -> None:
    """Build chat.db tables for `_resolve_self_chat_id` tests.

    Each chat dict: chat_identifier (str), participants (list[str] of handle ids),
    service_name (default 'iMessage'), room_name (default None), is_archived (0/1).
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, "
        "service_name TEXT, room_name TEXT, is_archived INTEGER)"
    )
    conn.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)")
    conn.execute(
        "CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER)"
    )
    handle_ids: dict[str, int] = {}
    for i, spec in enumerate(chats, start=1):
        conn.execute(
            "INSERT INTO chat (ROWID, chat_identifier, service_name, "
            "room_name, is_archived) VALUES (?, ?, ?, ?, ?)",
            (i, spec["chat_identifier"], spec.get("service_name", "iMessage"),
             spec.get("room_name"), int(spec.get("is_archived", 0))),
        )
        for handle in spec["participants"]:
            if handle not in handle_ids:
                handle_ids[handle] = len(handle_ids) + 1
                conn.execute(
                    "INSERT INTO handle (ROWID, id) VALUES (?, ?)",
                    (handle_ids[handle], handle),
                )
            conn.execute(
                "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
                (i, handle_ids[handle]),
            )
    conn.commit()
    conn.close()


class ResolveSelfChatIdTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "chat.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _open(self) -> sqlite3.Connection:
        return notify_inbound.open_chat_db(self.db_path)

    def test_override_short_circuits_lookup(self) -> None:
        # No tables built — override must not touch the DB at all.
        _make_resolver_db(self.db_path, [])
        conn = self._open()
        resolved = notify_inbound._resolve_self_chat_id(
            conn, operator_handle="+15551234567", override="explicit-id",
        )
        self.assertEqual(resolved, "explicit-id")

    def test_single_self_chat_candidate(self) -> None:
        _make_resolver_db(self.db_path, [
            {"chat_identifier": "+15551234567",
             "participants": ["+15551234567"]},
        ])
        resolved = notify_inbound._resolve_self_chat_id(
            self._open(), operator_handle="+15551234567",
        )
        self.assertEqual(resolved, "+15551234567")

    def test_no_candidate_raises(self) -> None:
        _make_resolver_db(self.db_path, [
            {"chat_identifier": "chat-group",
             "participants": ["+15551234567", "+15559999999"],
             "room_name": "group"},  # group chat — excluded
        ])
        with self.assertRaises(notify_inbound.SelfChatLookupError) as ctx:
            notify_inbound._resolve_self_chat_id(
                self._open(), operator_handle="+15551234567",
            )
        self.assertIn("self_chat_id", str(ctx.exception))

    def test_multiple_candidates_raises_with_override_hint(self) -> None:
        # Two distinct self-chat rows for the same handle (can happen when
        # iCloud sync resurfaces a stale thread alongside the live one).
        _make_resolver_db(self.db_path, [
            {"chat_identifier": "+15551234567",
             "participants": ["+15551234567"]},
            {"chat_identifier": "+15551234567",
             "participants": ["+15551234567"]},
        ])
        with self.assertRaises(notify_inbound.SelfChatLookupError) as ctx:
            notify_inbound._resolve_self_chat_id(
                self._open(), operator_handle="+15551234567",
            )
        self.assertIn("self_chat_id", str(ctx.exception))

    def test_group_chat_excluded(self) -> None:
        _make_resolver_db(self.db_path, [
            {"chat_identifier": "+15551234567",
             "participants": ["+15551234567"]},
            {"chat_identifier": "chat-group",
             "participants": ["+15551234567", "+15559999999"],
             "room_name": "group"},
        ])
        resolved = notify_inbound._resolve_self_chat_id(
            self._open(), operator_handle="+15551234567",
        )
        self.assertEqual(resolved, "+15551234567")

    def test_archived_chat_excluded(self) -> None:
        _make_resolver_db(self.db_path, [
            {"chat_identifier": "+15551234567",
             "participants": ["+15551234567"]},
            {"chat_identifier": "+15551234567",
             "participants": ["+15551234567"], "is_archived": 1},
        ])
        resolved = notify_inbound._resolve_self_chat_id(
            self._open(), operator_handle="+15551234567",
        )
        self.assertEqual(resolved, "+15551234567")

    def test_sms_service_excluded(self) -> None:
        _make_resolver_db(self.db_path, [
            {"chat_identifier": "+15551234567",
             "participants": ["+15551234567"], "service_name": "SMS"},
        ])
        with self.assertRaises(notify_inbound.SelfChatLookupError):
            notify_inbound._resolve_self_chat_id(
                self._open(), operator_handle="+15551234567",
            )


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


if __name__ == "__main__":
    unittest.main()
