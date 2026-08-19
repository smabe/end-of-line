"""Discord inbound poller — DiscordInboundPoller.

Polls the operator's DM channel for replies, correlates them to open
blockers via two paths:
  1. Discord Reply-UI: message_reference.message_id → notify_metadata lookup.
  2. Text grammar fallback: state_locator.find_blocker_for_reply for
     bare-digit / slug-prefixed grammar.

Mirrors notify_imessage_inbound.py shape; runs as a standalone daemon via
__main__ or a LaunchAgent / systemd unit.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import db, notify_discord_http, registry, state_locator
from . import state as st
from .notify_base import OpenBlocker, Reply
from .notify_imessage_inbound import _cli_dispatch

POLL_INTERVAL = 30  # seconds; mirrors iMessage cadence


class DiscordInboundPoller:
    """Polls one operator DM channel for blocker replies; dispatches clu answer."""

    def __init__(
        self,
        bot_token: str,
        user_id: str,
        bot_user_id: str,
        *,
        db_path: Path | None = None,
        registry_loader: Callable | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.user_id = user_id
        self.bot_user_id = bot_user_id
        # The per-channel cursor and the DM cache share the host database, so
        # one override covers what used to be two files.
        self.db_path = db_path
        self._registry_loader = registry_loader or registry.entries
        self._dm_channel_id: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def poll(self) -> list[Reply]:
        """One poll iteration: fetch, filter, route, dispatch, advance cursor."""
        channel_id = self._ensure_dm_channel()
        cursor = self._read_cursor().get(channel_id)
        messages = self._get_messages(channel_id, after=cursor)
        replies = []
        for msg in messages:
            if msg["author"]["id"] == self.bot_user_id:
                continue
            reply = self._route_message(msg)
            if reply:
                try:
                    _cli_dispatch(reply.target, reply.answer)
                    replies.append(reply)
                except Exception as exc:
                    print(f"discord_inbound: dispatch failed: {exc}", file=sys.stderr)
        if messages:
            self._write_cursor(channel_id, messages[-1]["id"])
        return replies

    # ------------------------------------------------------------------
    # DM channel resolution
    # ------------------------------------------------------------------

    def _ensure_dm_channel(self) -> str:
        if self._dm_channel_id:
            return self._dm_channel_id
        cached = self._load_dm_cache()
        if cached:
            self._dm_channel_id = cached
            return cached
        resp = self._request("POST", "/users/@me/channels", {"recipient_id": self.user_id})
        assert isinstance(resp, dict)  # _request returns a list only for GET double-429 fallback
        channel_id = str(resp["id"])
        self._save_dm_cache(channel_id)
        self._dm_channel_id = channel_id
        return channel_id

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
        with db.host_conn(self.db_path) as conn, db.write_txn(conn) as cur:
            cur.execute(
                "INSERT OR REPLACE INTO discord_dm_cache (user_id, channel_id) VALUES (?, ?)",
                (self.user_id, channel_id),
            )

    # ------------------------------------------------------------------
    # Message fetch
    # ------------------------------------------------------------------

    def _get_messages(self, channel_id: str, after: str | None = None) -> list[dict]:
        if after:
            path = f"/channels/{channel_id}/messages?after={after}&limit=100"
        else:
            path = f"/channels/{channel_id}/messages?limit=100"
        try:
            result = self._request("GET", path)
            return result if isinstance(result, list) else []
        except Exception as exc:
            print(f"discord_inbound: get_messages failed: {exc}", file=sys.stderr)
            return []

    # ------------------------------------------------------------------
    # Reply routing
    # ------------------------------------------------------------------

    def _route_message(self, msg: dict) -> Reply | None:
        ref_id = (msg.get("message_reference") or {}).get("message_id")
        if ref_id:
            target = self._find_blocker_by_discord_message_id(ref_id)
            if target:
                return Reply(target=target, answer=msg["content"].strip())
        # Text grammar fallback: use the shared locator.
        result = state_locator.find_blocker_for_reply(self._registry_loader(), msg["content"])
        if result.variant != "FOUND":
            return None
        state_path, blocker_id = result.state_path, result.blocker_id
        answer_index, project_root = result.answer_index, result.project_root
        assert (
            state_path is not None
            and blocker_id is not None
            and answer_index is not None
            and project_root is not None
        )  # FOUND sets all (state_locator)
        plan_slug = state_path.name.removesuffix(".state.json")
        ob = OpenBlocker(
            project_root=project_root,
            plan_slug=plan_slug,
            blocker_id=blocker_id,
            options_count=1,
            last_notified_at="",
        )
        return Reply(target=ob, answer=str(answer_index))

    def _find_blocker_by_discord_message_id(self, discord_message_id: str) -> OpenBlocker | None:
        for entry in self._registry_loader():
            data = registry.load_entry_state(entry)
            if data is None:
                continue
            for b in st.open_blockers(data):
                meta = b.get("notify_metadata", {}).get("discord", {})
                if meta.get("message_id") == discord_message_id:
                    return OpenBlocker(
                        project_root=Path(entry.project_root),
                        plan_slug=entry.plan_slug,
                        blocker_id=b["id"],
                        options_count=len(b.get("options", [])),
                        last_notified_at="",
                    )
        return None

    # ------------------------------------------------------------------
    # Cursor persistence
    # ------------------------------------------------------------------

    def _read_cursor(self) -> dict:
        """channel_id → last-seen message_id. Empty when nothing was recorded.

        A cursor that reads empty re-fetches the channel from the top, which
        is why losing it mattered: the unlocked read-modify-write the file
        version used could drop a just-written cursor when two pollers
        overlapped, and the poller would replay messages it had answered.
        """
        try:
            with db.host_conn(self.db_path) as conn:
                rows = conn.execute("SELECT channel_id, message_id FROM discord_cursor").fetchall()
        except db.DEGRADABLE_ERRORS:
            return {}
        return dict(rows)

    def _write_cursor(self, channel_id: str, message_id: str) -> None:
        with db.host_conn(self.db_path) as conn, db.write_txn(conn) as cur:
            cur.execute(
                "INSERT OR REPLACE INTO discord_cursor (channel_id, message_id) VALUES (?, ?)",
                (channel_id, message_id),
            )

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | list:
        return notify_discord_http.request(
            self.bot_token,
            method,
            path,
            body,
            log_prefix="discord_inbound",
            empty_on_double_429=lambda m: [] if m == "GET" else {},
        )


# ------------------------------------------------------------------
# __main__ entry — polling daemon
# ------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    from .config import load_project_config

    parser = argparse.ArgumentParser(description="clu Discord inbound poller")
    parser.add_argument("--project", default=".", help="Project root")
    parser.add_argument("--interval", type=float, default=POLL_INTERVAL)
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    cfg = load_project_config(project_root)

    discord_spec = next(
        (ch for ch in cfg.notify.channels if ch.kind == "discord" and ch.enabled),
        None,
    )
    if discord_spec is None:
        print("discord_inbound: no enabled discord channel configured", file=sys.stderr)
        sys.exit(1)

    bot_token = discord_spec.params["bot_token"]
    user_id = discord_spec.params["user_id"]
    bot_user_id = discord_spec.params.get("bot_user_id", "")
    if not bot_user_id:
        print(
            "discord_inbound: bot_user_id not configured; "
            "add 'bot_user_id' to the discord channel spec in .orchestrator.json",
            file=sys.stderr,
        )
        sys.exit(1)

    poller = DiscordInboundPoller(bot_token=bot_token, user_id=user_id, bot_user_id=bot_user_id)
    print(f"discord_inbound: polling every {args.interval}s")
    while True:
        try:
            poller.poll()
        except Exception as exc:
            print(f"discord_inbound: poll error: {exc}", file=sys.stderr)
        time.sleep(args.interval)
