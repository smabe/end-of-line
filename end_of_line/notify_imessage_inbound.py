"""iMessage inbound poller — IMessageInboundPoller + chat.db helpers.

All iMessage-specific inbound logic lives here. notify_inbound.py is a thin
shim that re-exports this module's surface and provides the __main__ entry.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path
import logging
from typing import Callable

from . import registry, state as st, state_locator
from .config import load_project_config
from .notify_base import OpenBlocker, Reply

DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
DEFAULT_SEEN_PATH = Path.home() / ".clu" / "seen_msg_rowid"
DEFAULT_POLL_SECONDS = 4
POLL_BATCH_LIMIT = 500

Dispatcher = Callable[[OpenBlocker, str], None]
OpenBlockersFn = Callable[[], list[OpenBlocker]]
TickSpawner = Callable[[Path, str], None]
ShellAnswerFn = Callable[[Path, str, int], None]

log = logging.getLogger(__name__)


def _cli_dispatch(target: OpenBlocker, answer: str) -> None:
    """Fire `clu answer` as a subprocess. Raises CalledProcessError on rc!=0
    so callers know whether to auto-tick; the outer poll loop catches it so a
    bad `clu answer` can't tank the poller."""
    subprocess.run(
        [
            sys.executable, "-m", "end_of_line.cli", "answer",
            "--project", str(target.project_root),
            "--plan", target.plan_slug,
            target.blocker_id, answer,
        ],
        check=True,
    )


def _spawn_tick(project_root: Path, plan_slug: str) -> None:
    """Fire-and-forget `clu tick` for the plan whose blocker was just
    answered, so the next phase dispatches immediately instead of waiting for
    the next cron firing. Mirrors `dispatch.py`'s Popen pattern."""
    subprocess.Popen(
        [
            sys.executable, "-m", "end_of_line.cli", "tick",
            "--project", str(project_root),
            "--plan", plan_slug,
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


def poll_once(
    conn: sqlite3.Connection,
    last_rowid: int,
    *,
    self_chat_id: str,
    entries_fn: Callable[[], list[registry.PlanEntry]] = registry.entries,
    shell_answer_fn: ShellAnswerFn = _shell_clu_answer,
    tick_spawner: TickSpawner = _spawn_tick,
    _locator_fn=None,  # (list[PlanEntry], str) -> LocatorResult; None = use state_locator
) -> int:
    """Scan chat.db for inbound rows after `last_rowid` in the operator's
    self-chat. Returns new high-water.

    Scoped to `self_chat_id` so the poller only sees one chat. No
    `is_from_me` filter: self-chat replies have `is_from_me = 1` because
    the operator IS the sender. Clu's own outbound rows pass the chat
    scope but are dropped at the locator by reply-grammar narrowness.

    Always advances the high-water past every row we read, matched or
    not — otherwise a chatty stranger could keep the cursor stuck on an
    old digit-shaped message and resurrect it once a future blocker opens.
    """
    # LIMIT caps first-tick blowup (e.g. seen_rowid=0 against a chat.db with
    # a year of history); next tick advances the cursor and picks up the rest.
    rows = conn.execute(
        "SELECT m.ROWID, m.text FROM message m "
        "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
        "JOIN chat c ON c.ROWID = cmj.chat_id "
        "WHERE m.ROWID > ? AND m.text IS NOT NULL "
        "AND c.chat_identifier = ? "
        "ORDER BY m.ROWID ASC LIMIT ?",
        (last_rowid, self_chat_id, POLL_BATCH_LIMIT),
    ).fetchall()
    if not rows:
        return last_rowid
    entries = entries_fn()
    _find = _locator_fn or state_locator.find_blocker_for_reply
    for _rowid, text in rows:
        result = _find(entries, text)
        if result.variant != "FOUND":
            log.info("imessage inbound: dropping %r — %s", text, result.variant)
            continue
        try:
            shell_answer_fn(result.state_path, result.blocker_id, result.answer_index)
        except Exception as exc:
            # answer write failed — don't auto-tick on stale state; cursor
            # still advances so a wedged reply can't re-fire forever.
            print(f"notify_inbound: dispatch failed: {exc}", file=sys.stderr)
            continue
        if result.project_root and not _auto_tick_enabled(result.project_root):
            continue
        plan_slug = (
            result.state_path.name.removesuffix(".state.json")
            if result.state_path else ""
        )
        try:
            tick_spawner(result.project_root, plan_slug)
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


def read_seen(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def write_seen(path: Path, rowid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(str(rowid))
    tmp.replace(path)


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
    ) -> None:
        self._db_path = db_path or DEFAULT_CHAT_DB
        self._registry_loader = registry_loader or registry.entries
        self._self_chat_id = self_chat_id
        self._conn: sqlite3.Connection | None = None
        self._last_rowid: int | None = None  # cached; read from disk once on first poll

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
        if self._last_rowid is None:
            self._last_rowid = read_seen(DEFAULT_SEEN_PATH)
        new_last = poll_once(
            self._conn, self._last_rowid,
            self_chat_id=self._self_chat_id,
            entries_fn=self._registry_loader,
        )
        if new_last != self._last_rowid:
            write_seen(DEFAULT_SEEN_PATH, new_last)
            self._last_rowid = new_last
        return []
