# notify-multi-channel-discord-in — Discord inbound poller

You are phase `discord-in` of the `notify-multi-channel` plan. Implement `DiscordInboundPoller` — REST-polled DM messages, two reply-correlation paths (Reply UI via `message_reference`, text-grammar fallback via shared `route_reply()`), dispatching to `clu answer`. Ship LaunchAgent + systemd templates.

## Locked decisions (do NOT re-litigate)

See `plans/notify-multi-channel.md` §"Phase 6". Summary:
- Module: `end_of_line/notify_discord_inbound.py`. `DiscordInboundPoller` implementing `InboundPoller`.
- Poll: `GET /channels/{dm}/messages?after=<cursor>&limit=100`. Cursor: `~/.config/clu/discord_cursor.json` keyed by channel.id.
- Filter bot's own messages (`author.id != bot_user_id`).
- Reply correlation paths:
  - **Primary:** `message_reference.message_id` present → lookup via `notify_metadata.discord.message_id`.
  - **Fallback:** no `message_reference` → shared `route_reply()`.
- Dispatch: reuse `_cli_dispatch()` from phase 1's extraction.
- Templates: `examples/clu.discord_inbound.plist` + `examples/clu-discord-inbound.service`.

## Read first

- `end_of_line/notify_base.py` — `InboundPoller`, `route_reply`, `Reply`, `_cli_dispatch` (phase 1).
- `end_of_line/notify_imessage_inbound.py` — full shape to mirror.
- `end_of_line/notify_discord.py` — `_request()` for REST; consider extracting to a shared base if both modules want it.
- `end_of_line/state.py` post-phase-5: blocker `notify_metadata` field.
- `examples/clu.inbound.plist` — LaunchAgent template to mirror.

## Produce

1. **Failing tests first** in `tests/test_notify_discord_inbound.py`:
   - `test_poll_fetches_messages_after_cursor` — mock GET, URL contains `?after=<cursor>&limit=100`.
   - `test_poll_advances_cursor_to_latest_message_id` — after polling, cursor file's entry for the channel = last message_id seen.
   - `test_poll_filters_bot_own_messages` — response includes one bot-authored + one operator-authored message; only operator's routed.
   - `test_reply_with_message_reference_routes_by_metadata` — operator message has `message_reference.message_id == "X"`, blocker with `notify_metadata.discord.message_id == "X"` exists → dispatches `clu answer` for that blocker.
   - `test_reply_without_message_reference_falls_back_to_text_grammar` — bare-text "1" with one open blocker → `route_reply()` last-pinged path → dispatches.
   - `test_reply_unrecognized_text_advances_cursor_but_no_dispatch` — "lol whatever" → cursor advances, no dispatch.
   - `test_poll_handles_rate_limit_429` — mock 429, assert sleep + retry.
   - `test_dm_channel_resolved_at_startup` — first `.poll()` resolves DM channel.id (or accepts cached value from outbound's state file).
   - `test_cursor_keyed_by_channel_id` — cursor file is `{"<channel_id>": "<message_id>"}` shape, not flat.

2. **Implementation.**
   - `end_of_line/notify_discord_inbound.py`:
     ```python
     class DiscordInboundPoller:
         def __init__(self, bot_token: str, user_id: str, bot_user_id: str, *, cursor_path: Path | None = None):
             self.bot_token = bot_token
             self.user_id = user_id
             self.bot_user_id = bot_user_id
             self.cursor_path = cursor_path or Path.home() / ".config/clu/discord_cursor.json"

         def poll(self) -> list[Reply]:
             channel_id = self._ensure_dm_channel()  # shared with outbound's state file
             cursor = self._read_cursor().get(channel_id)
             messages = self._get_messages(channel_id, after=cursor)
             replies = []
             for msg in messages:
                 if msg["author"]["id"] == self.bot_user_id:
                     continue
                 reply = self._route_message(msg)
                 if reply:
                     _cli_dispatch(reply.target, reply.answer)
                     replies.append(reply)
             if messages:
                 self._write_cursor(channel_id, messages[-1]["id"])
             return replies

         def _route_message(self, msg: dict) -> Reply | None:
             ref_id = (msg.get("message_reference") or {}).get("message_id")
             if ref_id:
                 target = self._find_blocker_by_discord_message_id(ref_id)
                 if target:
                     return Reply(target=target, answer=msg["content"].strip())
             return route_reply(msg["content"], open_blockers=self._load_open_blockers())
     ```
   - `__main__` entry: instantiate from `.orchestrator.json`'s Discord channel spec, run polling loop with `time.sleep(POLL_INTERVAL)` (default 30s, mirror iMessage interval).
   - `examples/clu.discord_inbound.plist`:
     ```xml
     <key>Label</key><string>com.clu.discord_inbound</string>
     <key>ProgramArguments</key><array>
         <string>/usr/bin/python3</string>
         <string>-m</string>
         <string>end_of_line.notify_discord_inbound</string>
     </array>
     <key>RunAtLoad</key><true/>
     <key>KeepAlive</key><true/>
     <key>ThrottleInterval</key><integer>10</integer>
     <key>StandardOutPath</key><string>/tmp/clu-discord-inbound.out</string>
     <key>StandardErrorPath</key><string>/tmp/clu-discord-inbound.err</string>
     ```
   - `examples/clu-discord-inbound.service`:
     ```ini
     [Unit]
     Description=clu Discord inbound poller
     After=network.target

     [Service]
     ExecStart=/usr/bin/python3 -m end_of_line.notify_discord_inbound
     Restart=always
     RestartSec=10
     StandardOutput=journal
     StandardError=journal

     [Install]
     WantedBy=default.target
     ```

3. **Acceptance.**
   - 9 new tests green.
   - Existing iMessage-inbound tests still green (phase 1's extraction of `route_reply`/`_cli_dispatch` is the load-bearing piece).
   - Manual smoke (operator-side, NOT in CI): `python3 -m end_of_line.notify_discord_inbound` running with a real bot, Discord DM reply triggers `clu answer`, state.json reflects the answer.

4. **Commit + complete.**
   - Title: `notify-multi-channel: phase discord-in — DiscordInboundPoller (REST poll, reply correlation) + LaunchAgent/systemd templates`
   - Stage: `end_of_line/notify_discord_inbound.py`, `examples/clu.discord_inbound.plist`, `examples/clu-discord-inbound.service`, `tests/test_notify_discord_inbound.py`.
   - `clu complete --plan notify-multi-channel --phase discord-in --token <T>`.

## Failure modes to watch

- **Cursor keyed by channel_id.** Critical — flat cursor would replay or skip on DM-channel recreation. Test explicitly.
- **Bot's own messages.** Filter via `author.id != bot_user_id`. Without this you'll loop: bot sends → poll reads bot's message → no match → cursor advances → fine actually, but if the parser ever matched its own message text the loop would be catastrophic.
- **`message_reference` without `referenced_message`.** Operator deleted reply target → `referenced_message` null but `message_reference.message_id` still present. Handle gracefully (still try metadata lookup).
- **Race with outbound persistence.** Phase 5 writes `notify_metadata` after send completes. If operator replies *during* the send (unlikely but possible), Reply-UI path misses → falls through to text-grammar → still works for unambiguous cases.
- **Separate state from iMessage inbound.** Different cursor file, different startup. Both can run as simultaneous LaunchAgents.
- **POLL_INTERVAL choice.** 30s matches iMessage's cadence and is well under any Discord per-bot rate limit. Don't go below 10s without re-checking rate-limit headers.
- **bot_user_id discovery.** Either operator supplies (config field) or poller calls `/users/@me` at startup to fetch it. Latter is friendlier; bake into `_ensure_dm_channel()` or startup hook.
