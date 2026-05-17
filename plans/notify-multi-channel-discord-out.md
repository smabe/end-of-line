# notify-multi-channel-discord-out — Discord outbound backend

You are phase `discord-out` of the `notify-multi-channel` plan. Implement `DiscordNotifier` — outbound DMs via Discord's REST API. Bot-token auth, cached DM channel.id, message_id persisted on blocker records for later Reply-UI correlation.

## Locked decisions (do NOT re-litigate)

See `plans/notify-multi-channel.md` §"Phase 5". Summary:
- Module: `end_of_line/notify_discord.py`. `DiscordNotifier`, `kind_name = "discord"`.
- Auth: `Authorization: Bot <token>` header.
- **stdlib only:** `urllib.request` + `json`. No third-party libs.
- DM channel: POST `/users/@me/channels` → cache `channel.id` in `~/.config/clu/discord_state.json`.
- Send: POST `/channels/{id}/messages?wait=true` → capture `message_id`.
- Persist `notify_metadata.discord.{channel_id, message_id}` on blocker records (state-schema addition with default-on-load).
- Rate-limit: parse `Retry-After`/`retry_after`, sleep+retry once. Log+drop on second 429.
- Register in `notify._NOTIFIER_REGISTRY`.

## Read first

- `end_of_line/notify_base.py` — `Notifier` Protocol (phase 1).
- `end_of_line/notify_imessage.py` — IMessageNotifier shape to mirror (phase 1).
- `end_of_line/notify.py` — `_NOTIFIER_REGISTRY` location.
- `end_of_line/state.py` lines 432-446 (blocker record) — extension point for `notify_metadata`.
- `end_of_line/config.py` post-phase-2: `ChannelSpec` shape.
- Discord REST docs (verified in plan research):
  - https://docs.discord.com/developers/resources/user (Create DM)
  - https://docs.discord.com/developers/resources/message (Create Message)
  - https://docs.discord.com/developers/topics/rate-limits (429 + `X-RateLimit-*`)
- `tests/test_notify.py` — mock-pattern for outbound calls.

## Produce

1. **Failing tests first** in `tests/test_notify_discord.py`:
   - `test_discord_notifier_kind_name` — `DiscordNotifier(...).kind_name == "discord"`.
   - `test_discord_notifier_is_a_notifier` — isinstance check.
   - `test_from_spec_reads_channel_params` — `DiscordNotifier.from_spec(ChannelSpec(kind="discord", params={"bot_token": "T", "user_id": "U"}))` works.
   - `test_send_creates_dm_channel_on_first_call` — mock urlopen; assert POST to `/users/@me/channels` body `{"recipient_id": "U"}`, then POST to `/channels/{returned}/messages?wait=true`.
   - `test_send_caches_dm_channel_id` — second `.send()` skips create-DM call.
   - `test_send_returns_message_id` — mock response `{"id": "12345"}` → `.send()` returns `"12345"`.
   - `test_send_uses_bot_auth_header` — assert request `Authorization: Bot T` header.
   - `test_send_persists_notify_metadata` — with `plan_slug="p"` + `blocker_id="q-0"`, state.json's blocker gains `notify_metadata.discord.{channel_id, message_id}`.
   - `test_send_retries_once_on_429` — mock raises HTTPError 429 with `Retry-After: 1`, second call succeeds; assert one sleep + one retry.
   - `test_send_gives_up_after_second_429` — both 429 → returns None, logs warning.
   - `test_send_handles_retry_after_in_body` — 429 response body `{"retry_after": 1.5}` honored when header absent.
   - `test_dm_state_cache_persisted_across_processes` — first `DiscordNotifier(...)` writes cache, second instance reads it without API call.

2. **Implementation.**
   - `end_of_line/notify_discord.py`:
     ```python
     class DiscordNotifier:
         kind_name = "discord"
         API_BASE = "https://discord.com/api/v10"

         def __init__(self, bot_token: str, user_id: str, *, state_path: Path | None = None):
             self.bot_token = bot_token
             self.user_id = user_id
             self.state_path = state_path or Path.home() / ".config/clu/discord_state.json"

         @classmethod
         def from_spec(cls, channel: ChannelSpec) -> "DiscordNotifier":
             return cls(bot_token=channel.params["bot_token"], user_id=channel.params["user_id"])

         def send(self, kind: str, body: str, *, plan_slug: str, blocker_id: str | None) -> str | None:
             channel_id = self._ensure_dm_channel()
             message_id = self._post_message(channel_id, body)
             if blocker_id and message_id:
                 self._persist_metadata(plan_slug, blocker_id, channel_id, message_id)
             return message_id

         def _ensure_dm_channel(self) -> str: ...
         def _post_message(self, channel_id: str, body: str) -> str | None: ...
         def _persist_metadata(self, plan_slug, blocker_id, channel_id, message_id): ...

         def _request(self, method: str, path: str, body: dict | None = None, *, _retried: bool = False) -> dict:
             req = urllib.request.Request(
                 self.API_BASE + path,
                 method=method,
                 headers={
                     "Authorization": f"Bot {self.bot_token}",
                     "Content-Type": "application/json",
                     "User-Agent": "clu/1.0 (https://github.com/smabe/end-of-line)",
                 },
                 data=json.dumps(body).encode() if body else None,
             )
             try:
                 with urllib.request.urlopen(req, timeout=10) as resp:
                     return json.loads(resp.read())
             except urllib.error.HTTPError as e:
                 if e.code == 429 and not _retried:
                     retry_after = self._parse_retry_after(e)
                     time.sleep(retry_after)
                     return self._request(method, path, body, _retried=True)
                 raise
     ```
   - `end_of_line/state.py`: extend blocker dataclass with `notify_metadata: dict[str, dict] = field(default_factory=dict)`. Load: missing field → empty dict (no migration breakage).
   - `end_of_line/notify.py`: `_NOTIFIER_REGISTRY["discord"] = DiscordNotifier`.

3. **Acceptance.**
   - 12 new tests green.
   - Existing tests green (state.py addition is default-on-load, no migration needed).
   - Manual smoke (operator-side, NOT in CI): real bot_token + user_id in a test config, `DiscordNotifier(...).send(KIND_BLOCKER, "test", plan_slug="x", blocker_id="q-0")` → DM received.

4. **Commit + complete.**
   - Title: `notify-multi-channel: phase discord-out — DiscordNotifier (REST, bot DM, message_id persistence)`
   - Stage: `end_of_line/notify_discord.py`, `end_of_line/notify.py`, `end_of_line/state.py`, `tests/test_notify_discord.py`.
   - `clu complete --plan notify-multi-channel --phase discord-out --token <T>`.

## Failure modes to watch

- **Cache file race.** Use clu's existing atomic-write helper. Don't roll your own.
- **Token in logs.** Never log `bot_token` or full request headers on error paths. Test with a sentinel token and grep test logs.
- **`urllib` 429 quirks.** `HTTPError` is the path; check `e.code == 429`, parse `Retry-After` header AND `retry_after` body field (Discord puts it in both for 429s). Header is seconds-int; body is seconds-float.
- **DM channel disappears.** If operator kicks bot from personal server, `_ensure_dm_channel()` will fail. Surface clear error pointing at re-invite docs.
- **Don't sneak in Gateway/WebSocket code.** Phase 6 is REST polling. No `websockets`, no asyncio.
- **stdlib User-Agent.** Discord requires a meaningful User-Agent; without it some endpoints 403.
