"""Discord outbound notification backend.

Implements Notifier via Discord's REST API (bot token, DM channel).
stdlib only: urllib.request + json. No third-party deps.

DM channel.id is cached in discord_state.json (keyed by user_id) to
avoid a round-trip on every send. Blocker message_id is persisted on
the plan's state.json for later Reply-UI correlation (phase discord-in).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from . import state as st

if TYPE_CHECKING:
    from .config import ChannelSpec

_API_BASE = "https://discord.com/api/v10"
_USER_AGENT = "clu/1.0 (https://github.com/smabe/end-of-line)"


class DiscordNotifier:
    kind_name = "discord"

    def __init__(
        self,
        bot_token: str,
        user_id: str,
        *,
        state_path: Path | None = None,
        state_root: Path | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.user_id = user_id
        # DM channel ID cache (keyed by user_id in the JSON file)
        self.state_path = state_path or Path.home() / ".config" / "clu" / "discord_state.json"
        # Optional: .orchestrator/ dir for persisting notify_metadata on blockers
        self._state_root = state_root

    @classmethod
    def from_spec(cls, channel: "ChannelSpec") -> "DiscordNotifier":
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
        state_path = self._state_root / f"{plan_slug}.state.json"
        if not state_path.exists():
            return
        with st.mutate(state_path) as data:
            for b in data.get("blockers", []):
                if b["id"] == blocker_id:
                    if "notify_metadata" not in b:
                        b["notify_metadata"] = {}
                    b["notify_metadata"]["discord"] = {
                        "channel_id": channel_id,
                        "message_id": message_id,
                    }
                    break

    def _load_dm_cache(self) -> str | None:
        try:
            with open(self.state_path) as f:
                data = json.load(f)
            return data.get(self.user_id)
        except (OSError, json.JSONDecodeError):
            return None

    def _save_dm_cache(self, channel_id: str) -> None:
        existing: dict = {}
        try:
            with open(self.state_path) as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        existing[self.user_id] = channel_id
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        st.save_atomic(self.state_path, existing)

    def _parse_retry_after(self, exc: urllib.error.HTTPError) -> float:
        header_val = exc.headers.get("Retry-After")
        if header_val is not None:
            try:
                return float(header_val)
            except (ValueError, TypeError):
                pass
        try:
            body_bytes = exc.read()
            body_data = json.loads(body_bytes)
            return float(body_data.get("retry_after", 1.0))
        except Exception:
            return 1.0

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        _retried: bool = False,
    ) -> dict:
        req = urllib.request.Request(
            _API_BASE + path,
            method=method,
            headers={
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            data=json.dumps(body).encode() if body else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and not _retried:
                retry_after = self._parse_retry_after(exc)
                time.sleep(retry_after)
                return self._request(method, path, body, _retried=True)
            if exc.code == 429 and _retried:
                print(
                    f"discord: rate limited twice on {method} {path}, dropping",
                    file=sys.stderr,
                )
                return {}
            raise
