"""Discord outbound notification backend.

Implements Notifier via Discord's REST API (bot token, DM channel).
stdlib only: urllib.request + json. No third-party deps.

DM channel.id is cached in the host database's `discord_dm_cache` table
(keyed by user_id) to avoid a round-trip on every send. Blocker message_id
is persisted on the plan's state.json for later Reply-UI correlation
(phase discord-in).

This class is constructed once per notification (`notify.py`), so the cache
read opens a connection and closes it again before the HTTP request goes
out — a handle held across the network call would pin the store for as long
as Discord takes to answer.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import db, notify_discord_http, plan_store
from . import state as st

if TYPE_CHECKING:
    from .config import ChannelSpec


class DiscordNotifier:
    kind_name = "discord"

    def __init__(
        self,
        bot_token: str,
        user_id: str,
        *,
        db_path: Path | None = None,
        state_root: Path | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.user_id = user_id
        # Host database holding the DM channel ID cache. None means the
        # default XDG-derived one; tests inject their own.
        self.db_path = db_path
        # Optional: .orchestrator/ dir for persisting notify_metadata on blockers
        self._state_root = state_root

    @classmethod
    def from_spec(cls, channel: ChannelSpec) -> DiscordNotifier:
        return cls(
            bot_token=channel.params["bot_token"],
            user_id=channel.params["user_id"],
        )

    def send(
        self,
        kind: str,
        body: str,
        *,
        plan_slug: str,
        blocker_id: str | None = None,
    ) -> str | None:
        try:
            channel_id = self._ensure_dm_channel()
            message_id = self._post_message(channel_id, body)
            if blocker_id and message_id and self._state_root:
                self._persist_metadata(plan_slug, blocker_id, channel_id, message_id)
            return message_id
        except Exception as exc:
            print(f"discord: send failed ({kind}): {exc}", file=sys.stderr)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_dm_channel(self) -> str:
        cached = self._load_dm_cache()
        if cached:
            return cached
        resp = self._request("POST", "/users/@me/channels", {"recipient_id": self.user_id})
        channel_id = resp["id"]
        self._save_dm_cache(channel_id)
        return channel_id

    def _post_message(self, channel_id: str, body: str) -> str | None:
        resp = self._request(
            "POST",
            f"/channels/{channel_id}/messages?wait=true",
            {"content": body},
        )
        return resp.get("id")

    def _persist_metadata(
        self,
        plan_slug: str,
        blocker_id: str,
        channel_id: str,
        message_id: str,
    ) -> None:
        if self._state_root is None:
            return
        # The path is the store's KEY, not a file — keep building it so the
        # no-op-when-the-plan-is-absent contract stays exactly as it was.
        state_path = self._state_root / f"{plan_slug}{st.STATE_SUFFIX}"
        if not plan_store.exists_for_path(state_path):
            return
        # One UPDATE of one blocker's metadata column. The whole-plan write
        # this replaces ran while a Discord round-trip was still in flight
        # upstream of it, holding the project's write lock the whole time.
        plan_store.op_stamp_blocker_metadata(
            *plan_store.key_for_state_path(state_path),
            blocker_id=blocker_id,
            channel="discord",
            metadata={"channel_id": channel_id, "message_id": message_id},
        )

    def _load_dm_cache(self) -> str | None:
        try:
            with db.host_conn(self.db_path) as conn:
                row = conn.execute(
                    "SELECT channel_id FROM discord_dm_cache WHERE user_id = ?",
                    (self.user_id,),
                ).fetchone()
        except db.DEGRADABLE_ERRORS:
            return None
        return row[0] if row else None

    def _save_dm_cache(self, channel_id: str) -> None:
        # One upsert in one transaction. The file version read the whole map,
        # edited it, and wrote it back unlocked — two notifiers for different
        # users racing there silently lost one of the two entries.
        with db.host_conn(self.db_path) as conn, db.write_txn(conn) as cur:
            cur.execute(
                "INSERT OR REPLACE INTO discord_dm_cache (user_id, channel_id) VALUES (?, ?)",
                (self.user_id, channel_id),
            )

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        return notify_discord_http.request(
            self.bot_token,
            method,
            path,
            body,
            log_prefix="discord",
            empty_on_double_429=lambda _method: {},
        )
