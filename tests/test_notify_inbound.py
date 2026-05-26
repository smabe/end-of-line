"""Inbound iMessage poller tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from end_of_line import notify_inbound, registry
from end_of_line.notify_inbound import OpenBlocker
from tests import isolate_registry

DEFAULT_CHAT_ID = "+15551234567"  # operator's self-chat handle for fixtures


def _make_chat_db(path: Path, rows: list[dict]) -> None:
    """Build a chat.db-shaped fixture.

    Each row is a dict:
      {"rowid": N, "is_from_me": 0|1, "text": str|None,
       "chat_id": str (optional, default DEFAULT_CHAT_ID),
       "date_ns": int (optional, default 0 — Apple-epoch ns),
       "attributed_body": bytes|None (optional),
       "assoc_type": int (optional, default 0)}
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, "
        "is_from_me INTEGER, text TEXT, date INTEGER DEFAULT 0, "
        "attributedBody BLOB, associated_message_type INTEGER DEFAULT 0)"
    )
    conn.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT)")
    conn.execute("CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)")
    chat_rowids: dict[str, int] = {}
    for row in rows:
        rowid = row["rowid"]
        is_from_me = row["is_from_me"]
        text = row.get("text")
        chat_id = row.get("chat_id", DEFAULT_CHAT_ID)
        date_ns = row.get("date_ns", 0)
        attributed_body = row.get("attributed_body")
        assoc_type = row.get("assoc_type", 0)
        if chat_id not in chat_rowids:
            chat_rowids[chat_id] = len(chat_rowids) + 1
            conn.execute(
                "INSERT INTO chat (ROWID, chat_identifier) VALUES (?, ?)",
                (chat_rowids[chat_id], chat_id),
            )
        conn.execute(
            "INSERT INTO message (ROWID, is_from_me, text, date, "
            "attributedBody, associated_message_type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rowid, is_from_me, text, date_ns, attributed_body, assoc_type),
        )
        conn.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
            (chat_rowids[chat_id], rowid),
        )
    conn.commit()
    conn.close()


def _encode_typedstream_length(n: int) -> bytes:
    """Signed-byte length encoding used in Apple typedstream.

    Tiers: head 0x00-0x80 literal · 0x81 + u16-LE · 0x82 + u32-LE · 0x83 + u64-LE.
    """
    if n <= 0x80:
        return bytes([n])
    if n < (1 << 16):
        return b"\x81" + n.to_bytes(2, "little")
    if n < (1 << 32):
        return b"\x82" + n.to_bytes(4, "little")
    return b"\x83" + n.to_bytes(8, "little")


def _make_attributed_body(text: str) -> bytes:
    """Emit a minimal NSAttributedString typedstream blob carrying `text`.

    Layout mirrors what chat.db produces — class chain bytes are present so
    fixtures resemble real blobs when eyeballed, even though the decoder
    only scans for HEADER + START_PATTERN + length + UTF-8 + END_PATTERN.
    Class filler avoids `b"\\x01\\x2b"` (the START_PATTERN) by construction.
    """
    HEADER = b"\x04\x0bstreamtyped\x81\xe8\x03"
    CLASS_FILLER = (
        b"\x84\x84\x1aNSMutableAttributedString\x00\x00\x00\x00"
        b"\x84\x84\x12NSAttributedString\x00\x00\x00\x00"
        b"\x84\x84\x08NSObject\x00\x00\x00\x00"
    )
    START = b"\x01\x2b"
    utf8 = text.encode("utf-8")
    return (
        HEADER + CLASS_FILLER + START + _encode_typedstream_length(len(utf8)) + utf8 + b"\x86\x84"
    )


def _ob(
    slug: str, *, blocker_id: str = "q-1", options: int = 2, root: str | Path = "/p", ts: str = ""
) -> OpenBlocker:
    """Factory keeps tests readable as OpenBlocker grows fields."""
    return OpenBlocker(
        project_root=Path(root),
        plan_slug=slug,
        blocker_id=blocker_id,
        options_count=options,
        last_notified_at=ts,
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
                Path(target.project_root)
                / "plans"
                / ".orchestrator"
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
            conn,
            last,
            _locator_fn=kw.pop("_locator_fn", self._mock_locator(blockers)),
            shell_answer_fn=shell_answer_fn or self._shell_answer,
            **kw,
        )

    def _state_path(self, target: OpenBlocker) -> Path:
        return (
            Path(target.project_root) / "plans" / ".orchestrator" / f"{target.plan_slug}.state.json"
        )

    def test_dispatches_matched_inbound_only(self) -> None:
        _make_chat_db(
            self.db_path,
            [
                {"rowid": 10, "is_from_me": 0, "text": "0"},
                {"rowid": 11, "is_from_me": 1, "text": "ignore"},
                {"rowid": 12, "is_from_me": 0, "text": "lol"},
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 0, blockers=[target])
        self.assertEqual(last, 12)
        self.assertEqual(self.dispatched, [(self._state_path(target), "q-1", 0)])

    def test_self_chat_is_from_me_1_routes(self) -> None:
        # In self-chat the operator IS the sender, so the reply row has
        # is_from_me=1. The chat-scoped SQL must accept it.
        _make_chat_db(self.db_path, [{"rowid": 1, "is_from_me": 1, "text": "0"}])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 0, blockers=[target])
        self.assertEqual(last, 1)
        self.assertEqual(self.dispatched, [(self._state_path(target), "q-1", 0)])

    def test_other_chat_dropped_by_scope(self) -> None:
        _make_chat_db(
            self.db_path,
            [
                {"rowid": 1, "is_from_me": 0, "text": "0", "chat_id": "+15559999999"},
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 0, blockers=[target])
        # Cursor must NOT advance — out-of-scope rows aren't read at all.
        self.assertEqual(last, 0)
        self.assertEqual(self.dispatched, [])

    def test_advances_seen_on_no_match(self) -> None:
        # Otherwise a chatty stranger pinning the cursor would let an old
        # unrelated message re-trigger if a blocker later opened.
        _make_chat_db(self.db_path, [{"rowid": 7, "is_from_me": 0, "text": "hey"}])
        conn = notify_inbound.open_chat_db(self.db_path)
        last = self._poll(conn, 0, blockers=[])
        self.assertEqual(last, 7)
        self.assertEqual(self.dispatched, [])

    def test_skips_already_seen(self) -> None:
        _make_chat_db(
            self.db_path,
            [
                {"rowid": 1, "is_from_me": 0, "text": "0"},
                {"rowid": 2, "is_from_me": 0, "text": "0"},
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 1, blockers=[target])
        self.assertEqual(last, 2)
        self.assertEqual(len(self.dispatched), 1)

    def test_returns_last_rowid_when_no_new_rows(self) -> None:
        _make_chat_db(self.db_path, [{"rowid": 5, "is_from_me": 0, "text": "0"}])
        conn = notify_inbound.open_chat_db(self.db_path)
        last = self._poll(conn, 5, blockers=[])
        self.assertEqual(last, 5)

    def test_auto_ticks_after_successful_dispatch(self) -> None:
        # Project root is a fresh tmp dir → no .orchestrator.json → default True.
        proj = self.tmp / "proj"
        proj.mkdir()
        _make_chat_db(self.db_path, [{"rowid": 1, "is_from_me": 0, "text": "0"}])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root=proj)
        self._poll(conn, 0, blockers=[target])
        self.assertEqual(self.ticks, [(proj, "plan-a")])

    def test_auto_tick_skipped_when_config_opt_out(self) -> None:
        # `.orchestrator.json` with `inbound_auto_tick: false` wins over default.
        proj = self.tmp / "proj"
        proj.mkdir()
        (proj / ".orchestrator.json").write_text(
            json.dumps(
                {
                    "notify": {"inbound_auto_tick": False},
                }
            )
        )
        _make_chat_db(self.db_path, [{"rowid": 1, "is_from_me": 0, "text": "0"}])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root=proj)
        self._poll(conn, 0, blockers=[target])
        self.assertEqual(self.dispatched, [(self._state_path(target), "q-1", 0)])
        self.assertEqual(self.ticks, [])

    def test_no_tick_when_dispatcher_raises(self) -> None:
        # answer write failed → raise → no auto-tick (stale state guard).
        proj = self.tmp / "proj"
        proj.mkdir()
        _make_chat_db(
            self.db_path,
            [
                {"rowid": 1, "is_from_me": 0, "text": "0"},
                {"rowid": 2, "is_from_me": 0, "text": "hey"},
            ],
        )
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
        _make_chat_db(
            self.db_path,
            [
                {"rowid": 1, "is_from_me": 0, "text": "0"},
                {"rowid": 2, "is_from_me": 0, "text": "0"},
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root=proj)

        def angry_tick(_p, _s):
            raise OSError("no clu on PATH")

        last = self._poll(conn, 0, blockers=[target], tick_spawner=angry_tick)
        self.assertEqual(last, 2)
        self.assertEqual(len(self.dispatched), 2)  # both rows still dispatched

    def test_attributed_body_routes_when_text_null(self) -> None:
        # The shipped #45 regression: modern macOS rows have text=NULL and
        # body in attributedBody. poll_once must decode and route them.
        _make_chat_db(
            self.db_path,
            [
                {
                    "rowid": 1,
                    "is_from_me": 1,
                    "text": None,
                    "attributed_body": _make_attributed_body("0"),
                },
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 0, blockers=[target])
        self.assertEqual(last, 1)
        self.assertEqual(self.dispatched, [(self._state_path(target), "q-1", 0)])

    def test_text_wins_when_both_populated(self) -> None:
        # Older rows (pre-macOS-10.13) populate `text`; some carry both. If
        # text is set, prefer it over decoding — fast path, no parse risk.
        _make_chat_db(
            self.db_path,
            [
                {
                    "rowid": 1,
                    "is_from_me": 1,
                    "text": "0",
                    "attributed_body": _make_attributed_body("999"),
                },
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 0, blockers=[target])
        self.assertEqual(last, 1)
        # Dispatched with answer_index=0 (from text "0"), not 999 (the blob).
        self.assertEqual(self.dispatched, [(self._state_path(target), "q-1", 0)])

    def test_attachment_only_body_skipped(self) -> None:
        # iMessage stickers / inline images encode as U+FFFC and nothing else.
        # poll_once strips U+FFFC, sees empty body, skips the row — cursor
        # still advances so the row can't re-fire on a future blocker.
        _make_chat_db(
            self.db_path,
            [
                {
                    "rowid": 5,
                    "is_from_me": 1,
                    "text": None,
                    "attributed_body": _make_attributed_body("￼"),
                },
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = self._poll(conn, 0, blockers=[target])
        self.assertEqual(last, 5)
        self.assertEqual(self.dispatched, [])

    def test_tapback_filtered_at_sql_layer(self) -> None:
        # Reactions ("Liked", "Loved", etc.) have associated_message_type
        # 2000-3999 and a decodable attributedBody, but they're rendered
        # placeholders, not operator input. SQL filters them before decode.
        _make_chat_db(
            self.db_path,
            [
                {
                    "rowid": 1,
                    "is_from_me": 1,
                    "text": None,
                    "attributed_body": _make_attributed_body("0"),
                    "assoc_type": 2000,
                },  # "Liked"
                {
                    "rowid": 2,
                    "is_from_me": 1,
                    "text": None,
                    "attributed_body": _make_attributed_body("1"),
                    "assoc_type": 0,
                },  # real reply
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", options=2, root="/p")
        last = self._poll(conn, 0, blockers=[target])
        self.assertEqual(last, 2)
        # Only the real reply (rowid=2, "1") dispatched; the tapback never
        # entered the result set.
        self.assertEqual(self.dispatched, [(self._state_path(target), "q-1", 1)])


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
    conn.execute("CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER)")
    handle_ids: dict[str, int] = {}
    for i, spec in enumerate(chats, start=1):
        conn.execute(
            "INSERT INTO chat (ROWID, chat_identifier, service_name, "
            "room_name, is_archived) VALUES (?, ?, ?, ?, ?)",
            (
                i,
                spec["chat_identifier"],
                spec.get("service_name", "iMessage"),
                spec.get("room_name"),
                int(spec.get("is_archived", 0)),
            ),
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
            conn,
            operator_handle="+15551234567",
            override="explicit-id",
        )
        self.assertEqual(resolved, "explicit-id")

    def test_single_self_chat_candidate(self) -> None:
        _make_resolver_db(
            self.db_path,
            [
                {"chat_identifier": "+15551234567", "participants": ["+15551234567"]},
            ],
        )
        resolved = notify_inbound._resolve_self_chat_id(
            self._open(),
            operator_handle="+15551234567",
        )
        self.assertEqual(resolved, "+15551234567")

    def test_no_candidate_raises(self) -> None:
        _make_resolver_db(
            self.db_path,
            [
                {
                    "chat_identifier": "chat-group",
                    "participants": ["+15551234567", "+15559999999"],
                    "room_name": "group",
                },  # group chat — excluded
            ],
        )
        with self.assertRaises(notify_inbound.SelfChatLookupError) as ctx:
            notify_inbound._resolve_self_chat_id(
                self._open(),
                operator_handle="+15551234567",
            )
        self.assertIn("self_chat_id", str(ctx.exception))

    def test_multiple_candidates_raises_with_override_hint(self) -> None:
        # Two distinct self-chat rows for the same handle (can happen when
        # iCloud sync resurfaces a stale thread alongside the live one).
        _make_resolver_db(
            self.db_path,
            [
                {"chat_identifier": "+15551234567", "participants": ["+15551234567"]},
                {"chat_identifier": "+15551234567", "participants": ["+15551234567"]},
            ],
        )
        with self.assertRaises(notify_inbound.SelfChatLookupError) as ctx:
            notify_inbound._resolve_self_chat_id(
                self._open(),
                operator_handle="+15551234567",
            )
        self.assertIn("self_chat_id", str(ctx.exception))

    def test_group_chat_excluded(self) -> None:
        _make_resolver_db(
            self.db_path,
            [
                {"chat_identifier": "+15551234567", "participants": ["+15551234567"]},
                {
                    "chat_identifier": "chat-group",
                    "participants": ["+15551234567", "+15559999999"],
                    "room_name": "group",
                },
            ],
        )
        resolved = notify_inbound._resolve_self_chat_id(
            self._open(),
            operator_handle="+15551234567",
        )
        self.assertEqual(resolved, "+15551234567")

    def test_archived_chat_excluded(self) -> None:
        _make_resolver_db(
            self.db_path,
            [
                {"chat_identifier": "+15551234567", "participants": ["+15551234567"]},
                {
                    "chat_identifier": "+15551234567",
                    "participants": ["+15551234567"],
                    "is_archived": 1,
                },
            ],
        )
        resolved = notify_inbound._resolve_self_chat_id(
            self._open(),
            operator_handle="+15551234567",
        )
        self.assertEqual(resolved, "+15551234567")

    def test_sms_service_excluded(self) -> None:
        _make_resolver_db(
            self.db_path,
            [
                {
                    "chat_identifier": "+15551234567",
                    "participants": ["+15551234567"],
                    "service_name": "SMS",
                },
            ],
        )
        with self.assertRaises(notify_inbound.SelfChatLookupError):
            notify_inbound._resolve_self_chat_id(
                self._open(),
                operator_handle="+15551234567",
            )


class InboundStateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state_path = self.tmp / "inbound_state.json"
        self.legacy_path = self.tmp / "seen_msg_rowid"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read(self) -> dict:
        return notify_inbound.read_inbound_state(
            self.state_path,
            legacy_path=self.legacy_path,
        )

    def test_missing_file_returns_empty_defaults(self) -> None:
        data = self._read()
        self.assertEqual(data["schema_version"], notify_inbound.INBOUND_STATE_SCHEMA_VERSION)
        self.assertEqual(data["last_inbound_rowid"], 0)
        self.assertEqual(data["outbound_rowids"], {})

    def test_round_trip(self) -> None:
        data = self._read()
        data["last_inbound_rowid"] = 42
        data["outbound_rowids"]["+15551234567"] = 99
        notify_inbound.write_inbound_state(self.state_path, data)
        reloaded = self._read()
        self.assertEqual(reloaded["last_inbound_rowid"], 42)
        self.assertEqual(reloaded["outbound_rowids"], {"+15551234567": 99})

    def test_legacy_seen_file_unlinked_on_first_read(self) -> None:
        # Operators upgrading from the bare-int format had ~/.clu/seen_msg_rowid.
        # First load of the new JSON state must drop the legacy file; cursor
        # resets to 0 and the poll_once LIMIT bounds the re-scan.
        self.legacy_path.write_text("123")
        self._read()
        self.assertFalse(self.legacy_path.exists())

    def test_corrupt_json_returns_defaults(self) -> None:
        self.state_path.write_text("{not valid json")
        self.assertEqual(self._read()["last_inbound_rowid"], 0)

    def test_schema_mismatch_returns_defaults(self) -> None:
        self.state_path.write_text(json.dumps({"schema_version": 999, "last_inbound_rowid": 50}))
        self.assertEqual(self._read()["last_inbound_rowid"], 0)


class OutboundPendingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "chat.db"
        self.pending_path = self.tmp / "outbound_pending.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_append_then_drain_resolves_floor(self) -> None:
        sent_at = 1_000_000_000.0
        date_ns = notify_inbound.unix_to_chatdb_ns(sent_at + 1)
        _make_chat_db(
            self.db_path,
            [
                {
                    "rowid": 7,
                    "is_from_me": 1,
                    "text": "BLOCKED: pick framework",
                    "date_ns": date_ns,
                },
            ],
        )
        notify_inbound.append_outbound_mark(
            DEFAULT_CHAT_ID,
            sent_at,
            path=self.pending_path,
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        floors = notify_inbound.drain_outbound_marks(
            conn,
            path=self.pending_path,
            now=sent_at + 5,
        )
        self.assertEqual(floors, {DEFAULT_CHAT_ID: 7})
        # Mark should be drained.
        remaining = json.loads(self.pending_path.read_text())["marks"]
        self.assertEqual(remaining, [])

    def test_drain_no_visible_row_keeps_young_mark(self) -> None:
        # osascript fired but chat.db hasn't surfaced our row yet.
        sent_at = 1_000_000_000.0
        _make_chat_db(self.db_path, [])  # no rows yet
        notify_inbound.append_outbound_mark(
            DEFAULT_CHAT_ID,
            sent_at,
            path=self.pending_path,
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        floors = notify_inbound.drain_outbound_marks(
            conn,
            path=self.pending_path,
            now=sent_at + 5,
        )
        self.assertEqual(floors, {})
        remaining = json.loads(self.pending_path.read_text())["marks"]
        self.assertEqual(len(remaining), 1)

    def test_drain_stale_mark_dropped(self) -> None:
        # Silently-failed osascript: no row ever appears and the mark
        # ages past the sanity timeout. Drop it.
        sent_at = 1_000_000_000.0
        _make_chat_db(self.db_path, [])
        notify_inbound.append_outbound_mark(
            DEFAULT_CHAT_ID,
            sent_at,
            path=self.pending_path,
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        notify_inbound.drain_outbound_marks(
            conn,
            path=self.pending_path,
            now=sent_at + notify_inbound.OUTBOUND_MARK_SANITY_TIMEOUT_SECONDS + 1,
        )
        remaining = json.loads(self.pending_path.read_text())["marks"]
        self.assertEqual(remaining, [])

    def test_drain_missing_file_returns_empty(self) -> None:
        _make_chat_db(self.db_path, [])
        conn = notify_inbound.open_chat_db(self.db_path)
        floors = notify_inbound.drain_outbound_marks(
            conn,
            path=self.pending_path,
            now=0,
        )
        self.assertEqual(floors, {})


class PollOnceFloorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "chat.db"
        self.dispatched: list[tuple[Path, str, int]] = []
        self.ticks: list[tuple[Path, str]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _shell_answer(self, sp: Path, bid: str, ai: int) -> None:
        self.dispatched.append((sp, bid, ai))

    def _tick(self, project_root: Path, plan_slug: str) -> None:
        self.ticks.append((project_root, plan_slug))

    def _mock_locator(self, blockers):
        from end_of_line.notify_base import route_reply
        from end_of_line.state_locator import LocatorResult

        def locator(entries, text):
            match = route_reply(text, blockers)
            if match is None:
                return LocatorResult(variant="NOT_FOUND")
            target, answer = match
            sp = (
                Path(target.project_root)
                / "plans"
                / ".orchestrator"
                / f"{target.plan_slug}.state.json"
            )
            return LocatorResult(
                variant="FOUND",
                state_path=sp,
                blocker_id=target.blocker_id,
                answer_index=int(answer),
                project_root=Path(target.project_root),
            )

        return locator

    def test_clu_own_row_skipped_when_below_floor(self) -> None:
        # Row 5: clu's outbound (is_from_me=1, multi-line text). Floor=5.
        # Row 7: operator's reply (is_from_me=1, "1"). Above floor → routes.
        _make_chat_db(
            self.db_path,
            [
                {"rowid": 5, "is_from_me": 1, "text": "BLOCKED:\n[0] FastAPI\n[1] Flask"},
                {"rowid": 7, "is_from_me": 1, "text": "1"},
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = notify_inbound.poll_once(
            conn,
            0,
            self_chat_id=DEFAULT_CHAT_ID,
            outbound_floor=5,
            shell_answer_fn=self._shell_answer,
            tick_spawner=self._tick,
            _locator_fn=self._mock_locator([target]),
        )
        self.assertEqual(last, 7)
        # Only the operator's reply dispatches; clu's row never reached the locator.
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(self.dispatched[0][2], 1)

    def test_floor_does_not_block_is_from_me_0(self) -> None:
        # Defensive: floor only filters is_from_me=1; an is_from_me=0 row
        # below the floor (hypothetical inbound from another sender that
        # somehow shares the chat) still routes.
        _make_chat_db(self.db_path, [{"rowid": 3, "is_from_me": 0, "text": "0"}])
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        notify_inbound.poll_once(
            conn,
            0,
            self_chat_id=DEFAULT_CHAT_ID,
            outbound_floor=10,
            shell_answer_fn=self._shell_answer,
            tick_spawner=self._tick,
            _locator_fn=self._mock_locator([target]),
        )
        self.assertEqual(len(self.dispatched), 1)

    def test_attributed_body_below_floor_skipped(self) -> None:
        # Floor short-circuit MUST run before decode — otherwise every
        # clu-sent attributedBody row burns CPU through the decoder.
        # Row 5: clu's own outbound (is_from_me=1, body via attributedBody).
        # Row 7: operator's reply, also is_from_me=1, above floor → routes.
        _make_chat_db(
            self.db_path,
            [
                {
                    "rowid": 5,
                    "is_from_me": 1,
                    "text": None,
                    "attributed_body": _make_attributed_body("BLOCKED:\n[0] FastAPI\n[1] Flask"),
                },
                {
                    "rowid": 7,
                    "is_from_me": 1,
                    "text": None,
                    "attributed_body": _make_attributed_body("1"),
                },
            ],
        )
        conn = notify_inbound.open_chat_db(self.db_path)
        target = _ob("plan-a", blocker_id="q-1", root="/p")
        last = notify_inbound.poll_once(
            conn,
            0,
            self_chat_id=DEFAULT_CHAT_ID,
            outbound_floor=5,
            shell_answer_fn=self._shell_answer,
            tick_spawner=self._tick,
            _locator_fn=self._mock_locator([target]),
        )
        self.assertEqual(last, 7)
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(self.dispatched[0][2], 1)


class ImessageChannelFromRegistryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        isolate_registry(self, self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_project(self, slug: str, channels: list[dict] | None) -> Path:
        project = self.tmp / slug
        (project / "plans").mkdir(parents=True)
        (project / ".orchestrator.json").write_text(
            json.dumps(
                {
                    "plan_dir": "plans",
                    "dispatch": {"kind": "shell", "command": "echo {phase_id}"},
                    "notify": {"channels": channels} if channels else {},
                }
            )
        )
        return project

    def test_no_registered_projects_returns_none(self) -> None:
        self.assertIsNone(notify_inbound.imessage_channel_from_registry())

    def test_imessage_channel_returns_to_handle(self) -> None:
        project = self._make_project(
            "p",
            [
                {"kind": "imessage", "to": "+15551234567"},
            ],
        )
        registry.register(project, "p")
        result = notify_inbound.imessage_channel_from_registry()
        self.assertEqual(result, ("+15551234567", None))

    def test_self_chat_id_override_returned(self) -> None:
        project = self._make_project(
            "p",
            [
                {"kind": "imessage", "to": "+15551234567", "self_chat_id": "+15551234567"},
            ],
        )
        registry.register(project, "p")
        result = notify_inbound.imessage_channel_from_registry()
        self.assertEqual(result, ("+15551234567", "+15551234567"))

    def test_disabled_channel_skipped(self) -> None:
        project = self._make_project(
            "p",
            [
                {"kind": "imessage", "to": "+1", "enabled": False},
            ],
        )
        registry.register(project, "p")
        self.assertIsNone(notify_inbound.imessage_channel_from_registry())

    def test_no_imessage_channel_returns_none(self) -> None:
        project = self._make_project("p", None)
        registry.register(project, "p")
        self.assertIsNone(notify_inbound.imessage_channel_from_registry())


class DecodeAttributedBodyTests(unittest.TestCase):
    """Unit tests for `_decode_attributed_body` — byte-level format parsing.

    The decoder is the load-bearing piece that lets `poll_once` see real
    typed-from-phone replies on modern macOS, where chat.db stores message
    bodies as NSArchiver typedstream blobs in `attributedBody` and leaves
    `text` NULL. These tests pin the four format-corner-cases that a naive
    scan-and-read decoder gets wrong.
    """

    def test_ascii_body_decodes(self) -> None:
        blob = _make_attributed_body("hello")
        self.assertEqual(notify_inbound._decode_attributed_body(blob), "hello")

    def test_emoji_multibyte_utf8_decodes(self) -> None:
        # Length is in BYTES, not codepoints — "héllo 👋" is 11 UTF-8 bytes.
        text = "héllo 👋"
        blob = _make_attributed_body(text)
        self.assertEqual(notify_inbound._decode_attributed_body(blob), text)

    def test_129_byte_boundary_decodes(self) -> None:
        # Canary for the u16-LE sentinel path. A naive `length = head_byte`
        # reads 0x81 as literal 129 and slides into the sentinel bytes,
        # silently corrupting every body ≥ 129 chars.
        text = "a" * 129
        blob = _make_attributed_body(text)
        # Sanity-check the fixture actually exercises the sentinel path.
        self.assertIn(b"\x01\x2b\x81\x81\x00", blob)
        self.assertEqual(notify_inbound._decode_attributed_body(blob), text)

    def test_truncated_length_returns_none(self) -> None:
        # Header + START_PATTERN + 0x81 sentinel but only one of the two
        # u16-LE length bytes present. Decoder must return None, not raise.
        blob = b"\x04\x0bstreamtyped\x81\xe8\x03\x01\x2b\x81\x05"
        self.assertIsNone(notify_inbound._decode_attributed_body(blob))

    def test_missing_start_pattern_returns_none(self) -> None:
        # All header, no payload marker — nothing to decode.
        blob = b"\x04\x0bstreamtyped\x81\xe8\x03"
        self.assertIsNone(notify_inbound._decode_attributed_body(blob))

    def test_empty_blob_returns_none(self) -> None:
        self.assertIsNone(notify_inbound._decode_attributed_body(b""))
        self.assertIsNone(notify_inbound._decode_attributed_body(None))

    def test_invalid_utf8_returns_none(self) -> None:
        # START_PATTERN, length 3, then bytes that aren't valid UTF-8.
        blob = b"\x04\x0bstreamtyped\x81\xe8\x03\x01\x2b\x03\xff\xfe\xfd"
        self.assertIsNone(notify_inbound._decode_attributed_body(blob))

    def test_attachment_replacement_char_preserved(self) -> None:
        # Decoder returns raw bytes including U+FFFC; `poll_once` is the
        # layer that strips and empty-checks. Single-responsibility split.
        blob = _make_attributed_body("￼")
        self.assertEqual(notify_inbound._decode_attributed_body(blob), "￼")


if __name__ == "__main__":
    unittest.main()
