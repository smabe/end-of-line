"""iMessage inbound poller — IMessageInboundPoller + chat.db helpers.

All iMessage-specific inbound logic lives here. notify_inbound.py is a thin
shim that re-exports this module's surface and provides the __main__ entry.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import db, registry, state_locator
from . import state as st
from .config import load_project_config
from .notify_base import OpenBlocker, Reply

DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
LEGACY_SEEN_PATH = Path.home() / ".clu" / "seen_msg_rowid"
DEFAULT_POLL_SECONDS = 4
POLL_BATCH_LIMIT = 500
INBOUND_STATE_SCHEMA_VERSION = 1
# The schema version of the `outbound_pending.json` file the marks lived in
# before they became rows. Nothing reads or writes that file any more; the
# constant is kept only so the quarantine sweep has a name to point at.
OUTBOUND_PENDING_SCHEMA_VERSION = 1
OUTBOUND_MARK_SANITY_TIMEOUT_SECONDS = 60.0
APPLE_EPOCH_OFFSET_SECONDS = 978_307_200  # Unix → Apple-epoch (Jan 1 2001).


def unix_to_chatdb_ns(unix_seconds: float) -> int:
    """Convert Unix epoch seconds to chat.db's Apple-epoch nanoseconds.

    macOS High Sierra+ stores `message.date` as nanoseconds offset from
    2001-01-01 UTC. Bindings against this column MUST go through this
    helper — passing raw `time.time()` lands rows 31 years in the past.
    """
    return int((unix_seconds - APPLE_EPOCH_OFFSET_SECONDS) * 1_000_000_000)


Dispatcher = Callable[[OpenBlocker, str], None]
OpenBlockersFn = Callable[[], list[OpenBlocker]]
TickSpawner = Callable[[Path, str], None]
ShellAnswerFn = Callable[[Path, str, int], None]

log = logging.getLogger(__name__)


class SelfChatLookupError(ValueError):
    """Auto-resolver couldn't pin down the operator's self-chat."""


def _resolve_self_chat_id(
    conn: sqlite3.Connection,
    *,
    operator_handle: str,
    override: str | None = None,
) -> str:
    """Resolve the operator's self-chat `chat_identifier`.

    Honors `override` if provided. Otherwise queries chat.db for the unique
    iMessage chat where the operator's handle is the sole participant and
    the chat_identifier matches that handle (single-participant self-chat).
    Group chats (`room_name` set) and archived chats are excluded.

    Raises SelfChatLookupError on 0 or >1 candidates with a hint to set
    `self_chat_id` explicitly on the iMessage channel.
    """
    if override is not None:
        return override
    rows = conn.execute(
        "SELECT c.chat_identifier FROM chat c "
        "JOIN chat_handle_join chj ON chj.chat_id = c.ROWID "
        "JOIN handle h ON h.ROWID = chj.handle_id "
        "WHERE c.service_name = 'iMessage' "
        "AND c.room_name IS NULL AND c.is_archived = 0 "
        "AND c.chat_identifier = h.id AND h.id = ? "
        "GROUP BY c.ROWID HAVING COUNT(chj.handle_id) = 1",
        (operator_handle,),
    ).fetchall()
    if not rows:
        raise SelfChatLookupError(
            f"no self-chat found for handle {operator_handle!r}; "
            "set self_chat_id on the iMessage channel"
        )
    if len(rows) > 1:
        candidates = ", ".join(repr(r[0]) for r in rows)
        raise SelfChatLookupError(
            f"multiple self-chat candidates for {operator_handle!r} "
            f"({candidates}); set self_chat_id on the iMessage channel"
        )
    return rows[0][0]


def _cli_dispatch(target: OpenBlocker, answer: str) -> None:
    """Fire `clu answer` as a subprocess. Raises CalledProcessError on rc!=0
    so callers know whether to auto-tick; the outer poll loop catches it so a
    bad `clu answer` can't tank the poller."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "end_of_line.cli",
            "answer",
            "--project",
            str(target.project_root),
            "--plan",
            target.plan_slug,
            answer,
        ],
        check=True,
    )


def _spawn_tick(project_root: Path, plan_slug: str) -> None:
    """Fire-and-forget `clu tick` for the plan whose blocker was just
    answered, so the next phase dispatches immediately instead of waiting for
    the next cron firing. Mirrors `dispatch.py`'s Popen pattern."""
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "end_of_line.cli",
            "tick",
            "--project",
            str(project_root),
            "--plan",
            plan_slug,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _shell_clu_answer(state_path: Path, blocker_id: str, answer_index: int) -> None:
    """Directly write the blocker answer into the state file.

    Replaces the old subprocess-based _cli_dispatch for the iMessage path;
    avoids the overhead of spawning a new process when the locator has already
    resolved state_path and answer_index.
    """
    with st.mutate(state_path) as data:
        resolved = st.resolve_blocker_answer(data, blocker_id, str(answer_index))
        st.answer_blocker(data, blocker_id, resolved)


# Apple NSAttributedString typedstream decoder. chat.db stores message
# bodies as NSArchiver typedstream blobs in `attributedBody` and leaves the
# plain `text` column NULL on modern macOS — we need this to read replies.
# Format references:
#   https://chrissardegna.com/blog/reverse-engineering-apples-typedstream-format/
#   ReagentX/imessage-exporter `imessage-database/src/util/streamtyped.rs`
# We diverge from the Rust parser by using strict UTF-8 + return-None instead
# of lossy + U+FFFD: routing garbage is worse than silently skipping a row.
_ATTR_BODY_START = b"\x01\x2b"
_ATTR_BODY_MAX_SIZE = 65536
_NS_ATTACHMENT_CHAR = "￼"  # NSAttachmentCharacter — stickers / inline images


def _decode_attributed_body(blob: bytes | None) -> str | None:
    """Extract the leading NSString UTF-8 payload from an NSAttributedString
    typedstream blob. Returns the raw decoded string (including any `U+FFFC`
    NSAttachmentCharacter chars — callers strip + empty-check). Returns None
    on missing pattern, truncated length, oversized blob, or invalid UTF-8;
    never raises.

    Length encoding (head byte after START_PATTERN):
      0x00–0x80 → literal length
      0x81      → next 2 bytes are u16-LE length
    Wider sentinels (0x82 = u32-LE, 0x83 = u64-LE) exist in the format but
    can never fire inside our 64KB-capped blob — they fall through to None.

    Assumes the user-text NSString is the FIRST occurrence of START_PATTERN
    in the blob — true for chat.db message bodies, would NOT generalize to
    edited-message history where class back-references can shift positions.
    """
    if not blob or len(blob) > _ATTR_BODY_MAX_SIZE:
        return None
    idx = blob.find(_ATTR_BODY_START)
    if idx < 0:
        return None
    cur = idx + len(_ATTR_BODY_START)
    if cur >= len(blob):
        return None
    head = blob[cur]
    cur += 1
    if head <= 0x80:
        length = head
    elif head == 0x81:
        if cur + 2 > len(blob):
            return None
        length = int.from_bytes(blob[cur : cur + 2], "little")
        cur += 2
    else:
        return None
    if cur + length > len(blob):
        return None
    try:
        return blob[cur : cur + length].decode("utf-8")
    except UnicodeDecodeError:
        return None


def poll_once(
    conn: sqlite3.Connection,
    last_rowid: int,
    *,
    self_chat_id: str,
    outbound_floor: int = 0,
    entries_fn: Callable[[], list[registry.PlanEntry]] = registry.entries,
    shell_answer_fn: ShellAnswerFn = _shell_clu_answer,
    tick_spawner: TickSpawner = _spawn_tick,
    _locator_fn=None,  # (list[PlanEntry], str) -> LocatorResult; None = use state_locator
) -> int:
    """Scan chat.db for inbound rows after `last_rowid` in the operator's
    self-chat. Returns new high-water.

    Scoped to `self_chat_id` so the poller only sees one chat. No
    `is_from_me` filter: self-chat replies have `is_from_me = 1` because
    the operator IS the sender. Clu's own outbound rows are filtered by
    `outbound_floor` — any is_from_me=1 row at-or-below the floor is
    skipped (matches send-side `append_outbound_mark` resolved by
    `drain_outbound_marks`).

    Modern macOS stores message bodies as NSArchiver typedstream blobs in
    `attributedBody` and leaves `text` NULL. We accept either column and
    decode the blob via `_decode_attributed_body` when text is missing.
    Tapbacks (`associated_message_type != 0`) are filtered at the SQL
    layer — they have decodable bodies but they're rendered placeholders,
    not operator input.

    Always advances the high-water past every row we read, matched or
    not — otherwise a chatty stranger could keep the cursor stuck on an
    old digit-shaped message and resurrect it once a future blocker opens.
    """
    # LIMIT caps first-tick blowup (e.g. seen_rowid=0 against a chat.db with
    # a year of history); next tick advances the cursor and picks up the rest.
    rows = conn.execute(
        "SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me FROM message m "
        "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
        "JOIN chat c ON c.ROWID = cmj.chat_id "
        "WHERE m.ROWID > ? "
        "AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL) "
        "AND m.associated_message_type = 0 "
        "AND c.chat_identifier = ? "
        "ORDER BY m.ROWID ASC LIMIT ?",
        (last_rowid, self_chat_id, POLL_BATCH_LIMIT),
    ).fetchall()
    if not rows:
        return last_rowid
    entries = entries_fn()
    _find = _locator_fn or state_locator.find_blocker_for_reply
    for rowid, text, attributed_body, is_from_me in rows:
        if is_from_me == 1 and rowid <= outbound_floor:
            continue
        if text is not None:
            body = text
        else:
            decoded = _decode_attributed_body(attributed_body)
            if decoded is None:
                continue
            # Strip attachment placeholders — attachment-only rows decode
            # to just these and aren't operator input.
            body = decoded.replace(_NS_ATTACHMENT_CHAR, "")
        if not body:
            continue
        result = _find(entries, body)
        if result.variant != "FOUND":
            log.info("imessage inbound: dropping %r — %s", body, result.variant)
            continue
        state_path, blocker_id = result.state_path, result.blocker_id
        answer_index, project_root = result.answer_index, result.project_root
        assert (
            state_path is not None
            and blocker_id is not None
            and answer_index is not None
            and project_root is not None
        )  # FOUND sets all (state_locator)
        try:
            shell_answer_fn(state_path, blocker_id, answer_index)
        except Exception as exc:
            # answer write failed — don't auto-tick on stale state; cursor
            # still advances so a wedged reply can't re-fire forever.
            print(f"notify_inbound: dispatch failed: {exc}", file=sys.stderr)
            continue
        if not _auto_tick_enabled(project_root):
            continue
        plan_slug = state_path.name.removesuffix(".state.json")
        try:
            tick_spawner(project_root, plan_slug)
        except Exception as exc:
            # Auto-tick is a latency optimization, not a correctness boundary;
            # cron will pick up the answered blocker on the next firing.
            print(f"notify_inbound: tick spawn failed: {exc}", file=sys.stderr)
    return rows[-1][0]


def _auto_tick_enabled(project_root: Path) -> bool:
    """Resolve the per-project opt-out. Defaults True; config errors fall back
    to True so a malformed `.orchestrator.json` doesn't silently disable UX."""
    try:
        return load_project_config(project_root).notify.inbound_auto_tick
    except Exception:
        return True


def _empty_inbound_state() -> dict:
    return {
        "schema_version": INBOUND_STATE_SCHEMA_VERSION,
        "last_inbound_rowid": 0,
        "outbound_rowids": {},
    }


def _drop_legacy_seen(legacy_path: Path) -> None:
    """One-time cleanup of the pre-JSON `seen_msg_rowid` cursor file.

    The cursor resets to 0; the LIMIT cap in poll_once bounds the
    one-time re-scan on hosts that had the legacy file.
    """
    try:
        legacy_path.unlink()
    except OSError:
        pass


def read_inbound_state(
    path: Path | None = None,
    *,
    legacy_path: Path = LEGACY_SEEN_PATH,
) -> dict:
    """Load inbound state from the host database, tolerating an absent store.

    Returns the same `{schema_version, last_inbound_rowid, outbound_rowids}`
    shape it always did — the poller keeps that dict in memory between polls
    and writes it back once per changed poll, so the shape is its cache, not
    its storage format. `path` names the host DATABASE.

    Drops the legacy bare-int cursor file on first call.
    """
    _drop_legacy_seen(legacy_path)
    state = _empty_inbound_state()
    try:
        with db.host_conn(path) as conn, db.read_txn(conn) as cur:
            cursor_row = cur.execute(
                "SELECT v FROM inbound_state WHERE k = 'last_inbound_rowid'"
            ).fetchone()
            floors = cur.execute("SELECT chat_id, floor_rowid FROM outbound_floors").fetchall()
    except db.DEGRADABLE_ERRORS:
        return state
    if cursor_row is not None:
        try:
            state["last_inbound_rowid"] = int(cursor_row[0])
        except (TypeError, ValueError):
            pass
    state["outbound_rowids"] = {chat_id: int(floor) for chat_id, floor in floors}
    return state


def write_inbound_state(path: Path | None, data: dict) -> None:
    """Write the poller's cached state back, cursor and floors together.

    One transaction: a cursor that advanced past rows whose outbound floor
    did not land would let clu's own message loop back in as operator input.
    """
    floors = [
        (chat_id, int(floor)) for chat_id, floor in (data.get("outbound_rowids") or {}).items()
    ]
    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO inbound_state (k, v) VALUES ('last_inbound_rowid', ?)",
            (str(int(data.get("last_inbound_rowid", 0))),),
        )
        # Replace rather than upsert: the dict IS the whole floor map, so a
        # chat dropped from it must not survive in the store.
        cur.execute("DELETE FROM outbound_floors")
        cur.executemany(
            "INSERT INTO outbound_floors (chat_id, floor_rowid) VALUES (?, ?)",
            floors,
        )


def append_outbound_mark(
    chat_id: str,
    sent_at: float,
    *,
    path: Path | None = None,
) -> None:
    """Append a {chat_id, sent_at} mark for the inbound poller to drain.

    Multi-writer safe: one INSERT in one transaction, so N notifiers sending
    at once cannot lose each other's marks the way a read-modify-write over a
    shared list could. Best-effort by convention — callers wrap in try/except
    so a store failure doesn't fail the send. The poll-side reply grammar
    still drops clu's own rows even if no mark gets recorded.
    """
    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        cur.execute(
            "INSERT INTO outbound_marks (chat_id, sent_at) VALUES (?, ?)",
            (chat_id, sent_at),
        )


def drain_outbound_marks(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    path: Path | None = None,
    sanity_timeout: float = OUTBOUND_MARK_SANITY_TIMEOUT_SECONDS,
) -> dict[str, int]:
    """Resolve pending outbound marks into chat_id → max-ROWID floors.

    `conn` is the read-only chat.db handle; `path` is clu's own host database.

    For each mark: query the chat for the highest is_from_me=1 ROWID newer
    than `sent_at`. If found, contribute to the floor and drop the mark.
    If not yet visible and the mark is younger than `sanity_timeout`,
    keep it for next tick. Older marks are dropped — silently-failed
    osascript sends shouldn't accumulate forever.

    **No host transaction is open while chat.db is being queried**, and that
    shape is deliberate. The marks used to live in their own file under their
    own flock, so holding it across these queries blocked nothing else. They
    now share ONE database with the registry, the inbox and the monitor
    marker, so a write transaction held across N queries of Apple's chat.db —
    a large database, joined three ways, entirely outside clu's control —
    would block every other host writer for however long that takes. The
    supervisor's inbox notes are best-effort with a bounded wait, so they
    would be silently dropped rather than delayed. Instead: read the marks,
    let go, query chat.db, then take the write lock once to delete what
    resolved.

    Resolving a mark twice is harmless and is why this split is safe: the
    floor is `MAX(ROWID)` over a fixed window, so two pollers computing it
    reach the same number, and the delete is keyed by id — the second one
    removes nothing rather than removing someone else's mark.
    """
    if now is None:
        now = time.time()
    floors: dict[str, int] = {}
    with db.host_conn(path) as host:
        with db.read_txn(host) as cur:
            marks = cur.execute(
                "SELECT id, chat_id, sent_at FROM outbound_marks ORDER BY id"
            ).fetchall()
        if not marks:
            return floors
        resolved: list[tuple[int]] = []
        for mark_id, chat_id, sent_at in marks:
            row = conn.execute(
                "SELECT MAX(m.ROWID) FROM message m "
                "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
                "JOIN chat c ON c.ROWID = cmj.chat_id "
                "WHERE c.chat_identifier = ? AND m.is_from_me = 1 "
                "AND m.date > ?",
                (chat_id, unix_to_chatdb_ns(sent_at)),
            ).fetchone()
            if row and row[0] is not None:
                floors[chat_id] = max(floors.get(chat_id, 0), row[0])
                resolved.append((mark_id,))
            elif now - sent_at >= sanity_timeout:
                resolved.append((mark_id,))
        if resolved:
            with db.write_txn(host) as cur:
                cur.executemany("DELETE FROM outbound_marks WHERE id = ?", resolved)
    return floors


def imessage_channel_from_registry() -> tuple[str, str | None] | None:
    """First enabled iMessage channel across registered projects.

    Returns `(operator_handle, self_chat_id_override)` or None when no
    project on this host has an iMessage channel. The inbound daemon
    uses this at startup to derive its chat scope without a separate
    host-level config file — the operator's existing project notify
    config is the source of truth.
    """
    for entry in registry.entries():
        try:
            cfg = load_project_config(Path(entry.project_root))
        except Exception:
            continue
        for ch in cfg.notify.channels:
            if ch.kind == "imessage" and ch.enabled:
                to = ch.params.get("to")
                if to:
                    return (to, ch.params.get("self_chat_id"))
    return None


def open_chat_db(db_path: Path = DEFAULT_CHAT_DB) -> sqlite3.Connection:
    """Open chat.db read-only via SQLite URI — never widen this mode."""
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


class IMessageInboundPoller:
    def __init__(
        self,
        db_path: Path | None = None,
        registry_loader: Callable | None = None,
        self_chat_id: str | None = None,
        store_path: Path | None = None,
    ) -> None:
        self._db_path = db_path or DEFAULT_CHAT_DB
        self._registry_loader = registry_loader or registry.entries
        self._self_chat_id = self_chat_id
        # The cursor and the outbound marks share one store now, so one
        # override covers both. `db_path` above is chat.db — Apple's, read-only.
        self._store_path = store_path
        self._conn: sqlite3.Connection | None = None
        self._state: dict | None = None  # cached; read from the store once on first poll

    def poll(self) -> list[Reply]:
        """Run one poll iteration; dispatches matched replies internally.

        Returns [] always — dispatch happens inside poll_once so the
        Protocol return type is satisfied without duplicating dispatch logic.

        No-op when `self_chat_id` is unset — without a chat scope the
        poller has nothing to read.
        """
        if self._self_chat_id is None:
            return []
        if self._conn is None:
            self._conn = open_chat_db(self._db_path)
        if self._state is None:
            self._state = read_inbound_state(self._store_path)

        # Step 1 — drain pending outbound marks into the floor map.
        floors = drain_outbound_marks(self._conn, path=self._store_path)
        state_changed = False
        for chat_id, new_floor in floors.items():
            current = self._state["outbound_rowids"].get(chat_id, 0)
            if new_floor > current:
                self._state["outbound_rowids"][chat_id] = new_floor
                state_changed = True

        # Step 2 — read new inbound, skipping clu's own rows below the floor.
        last_rowid = self._state["last_inbound_rowid"]
        floor = self._state["outbound_rowids"].get(self._self_chat_id, 0)
        new_last = poll_once(
            self._conn,
            last_rowid,
            self_chat_id=self._self_chat_id,
            outbound_floor=floor,
            entries_fn=self._registry_loader,
        )
        if new_last != last_rowid:
            self._state["last_inbound_rowid"] = new_last
            state_changed = True
        if state_changed:
            write_inbound_state(self._store_path, self._state)
        return []
