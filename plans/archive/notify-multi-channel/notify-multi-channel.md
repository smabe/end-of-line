# notify-multi-channel — pluggable notification backends + clu-watch UX

Closes [#11](https://github.com/smabe/end-of-line/issues/11). Extracts the iMessage notify stack behind a `Notifier` / `InboundPoller` protocol, adds Discord as a non-macOS backend, and makes the active Claude Code session a first-class blocker UX through the existing inbox-hook surface.

Today notify is iMessage-only — `notify.py` calls osascript directly, `notify_inbound.py` polls `~/Library/Messages/chat.db`. clu's CLI / dispatch / state / worker contract are all OS-agnostic, but a Linux or Windows operator can't receive a blocker prompt. That gap is the structural blocker to clu being clone-and-go off Mac.

Plan ordering: protocol extraction first (refactor, no behavior change, keeps the existing 880+ tests green), then the multi-channel config schema with auto-migration (so existing operators are unaffected), then the inbox-surface "active blocker" section (clu-watch-only UX works regardless of any outbound config), then the `/clu-reply` skill (in-session reply escape hatch), then Discord outbound, then Discord inbound, then docs + defaults + smoke.

## Locked design decisions

### Cross-cutting

- **clu-watch is orthogonal infrastructure, not a channel kind.** `notify.channels` contains only *outbound transports* (iMessage, Discord). The inbox-hook surface always shows currently-BLOCKED plans regardless of `notify.channels` config — that's a property of the inbox projection, not a notify subscription. **Why:** clu-watch streams state; it doesn't "send." Modeling it as a Notifier would be a category error. **How to apply:** do not add a `kind: "clu_watch"` channel; the inbox-surface section is always-on infrastructure independent of channel config.

- **Reply UX in-session: natural language, Claude disambiguates.** No required prefix grammar in the Claude Code session path. The inbox surface tells Claude: *"If the user's next message reads as a reply to a blocker (letter, number, or natural pick), call `clu answer <slug> <blocker_id> <answer>` via Bash. If multiple blockers are open and the reply is ambiguous, ask which plan first — don't guess."* `/clu-reply` is the explicit escape hatch when Claude needs unambiguous args (multi-blocker disambiguation, scripted contexts). **Why:** operator has Claude in the loop; pushing prefix grammar onto a human when an LLM can disambiguate is the wrong layer. **How to apply:** don't add prefix-required parsing to the inbox-surface instruction or expect operators to type slug prefixes in-session.

- **Transport-level (iMessage / Discord text) reply grammar stays as-is.** `notify_inbound.py:37` already accepts `<plan-slug> <digit>` or bare `<digit>` (falls back to last-pinged routing). That's the parser for typed-text replies on iMessage and the Discord-bare-text fallback path. Don't rewrite — extract into shared helper. **Why:** the grammar works and has tests; only thing missing is reuse for Discord. **How to apply:** phase 1 moves `route_reply()` from `notify_inbound.py` to `notify_base.py`.

- **Channels can be disabled four ways.** (1) Per-kind filter via `ChannelSpec.kinds` (this channel only fires for these kinds). (2) Whole-channel via `ChannelSpec.enabled: false` (keeps config + credentials, silences delivery). (3) Runtime per-invocation via `clu --no-notify <cmd>` (debug/dry-run, suppresses all dispatch for the single CLI call). (4) Permanent clu-watch-only mode via `channels: []`. **Why:** different operator needs — temporary silence during focused work shouldn't require deleting bot tokens; debugging a misbehaving phase shouldn't fire real DMs. **How to apply:** schema validation accepts `enabled: false`; dispatcher checks `ch.enabled` after registry lookup and `kinds` filter, before instantiating the Notifier. `--no-notify` short-circuits at the router entry point before any channel loop.

### Phase 1 — Notifier + InboundPoller protocol extraction
- **Two Protocols in new `end_of_line/notify_base.py`:**
  ```python
  class Notifier(Protocol):
      kind_name: str
      def send(self, kind: str, body: str, *, plan_slug: str, blocker_id: str | None) -> str | None: ...

  class InboundPoller(Protocol):
      def poll(self) -> list[Reply]: ...
  ```
- iMessage code splits: outbound → `notify_imessage.py` (`IMessageNotifier`), inbound → `notify_imessage_inbound.py` (`IMessageInboundPoller`). Each implements one Protocol.
- `notify.notify()` becomes a thin router: iterates `spec.channels`, looks up backend in a `_NOTIFIER_REGISTRY` dict, calls `.send()`.
- Render helpers (`render_blocker`, `render_halted`, etc.) stay in `notify.py` — they're backend-agnostic body formatters.
- `route_reply()` + `OpenBlocker` + `Reply` dataclasses extracted to `notify_base.py` for Discord-inbound reuse without circular imports.
- **No behavior change.** Pure refactor. Test churn is import-path updates only.

### Phase 2 — Multi-channel config schema + auto-migration
- New shape:
  ```json
  "notify": {
    "channels": [
      {"kind": "imessage", "to": "+1...", "kinds": ["halted", "blocker"], "enabled": true},
      {"kind": "discord", "bot_token": "...", "user_id": "...", "kinds": null, "enabled": true}
    ],
    "quiet_hours": ["22:00", "08:00"],
    "inbound_auto_tick": true
  }
  ```
- Per-channel `kinds` filter is optional (null/omitted = all kinds).
- Per-channel `enabled: bool` defaults to `true`. `false` keeps the channel in config (credentials preserved) but silences delivery.
- Auto-migrate at config load: if old `notify.imessage.to` present and `channels` absent, synthesize `channels: [{kind: "imessage", to: <old>, kinds: null, enabled: true}]`. Operator action zero.
- ChannelSpec validation: `kind` required, per-kind required fields (`to` for iMessage, `bot_token`+`user_id` for Discord) — schema-level check, Discord backend itself ships in phase 5.
- Dispatcher iterates matching channels. Order of checks: registry lookup → `enabled` → `kinds` filter → instantiate → send. Quiet-hours + halt-bypass check is notification-level (before the channel loop), preserves today's semantics.
- Backend-not-yet-registered (`kind: "discord"` before phase 5 ships) → router logs + skips gracefully, doesn't crash.

### Phase 3 — Inbox-surface active-blocker section
- Insertion point: `end_of_line/hooks/clu_inbox_surface.py:_build_context()` after the existing event list (after line 79).
- Section format (lock verbatim for tests):
  ```
  
  ## Active blockers
  
  Plan `<slug>`, phase `<phase-id>`, blocker `<blocker-id>`:
  Question: <question text>
  Options:
    [0] <option text>
    [1] <option text>
  
  <next blocker, if any>
  
  If the user's next message reads as a reply to one of these blockers
  (letter, number, or natural pick), call `clu answer --plan <slug>
  <blocker_id> <answer>` via Bash. If multiple blockers are open and the
  reply is ambiguous, ask the user which plan they mean — don't guess.
  ```
- Data: extend `open_blockers_for_host()` (post-phase-1 likely in `notify_base.py`) to include `question` + `options[]` from `state.data["blockers"][blocker_id]`. New helper or extended OpenBlocker dataclass — pick the option that doesn't break iMessage-inbound's existing use.
- Project scope: blockers filtered to inbox's current project per `inbox.list_for_project()` pattern (lines 133-141).
- Empty case: section omitted entirely.
- Cap blockers shown at 10 with `... +N more` footer (hook has a 10K-char truncation).

### Phase 4 — `/clu-reply` bundled skill
- Location: `end_of_line/skills/clu-reply/SKILL.md`. Markdown-only deliverable (Claude executes via Bash).
- Args: `<plan-slug> <answer>` (e.g. `/clu-reply notify-multi-channel B`).
- Behavior: look up open blocker for plan, shell `clu answer --project . --plan <slug> <blocker_id> <answer>`. Refuses if no open blocker.
- Add `"clu-reply"` to `BUNDLED_SKILLS` tuple in `cli.py:1426`.
- Role: explicit escape hatch when natural-language disambiguation isn't appropriate (multi-blocker ambiguity, scripted contexts).

### Phase 5 — Discord backend, outbound
- New `end_of_line/notify_discord.py`. `DiscordNotifier`, `kind_name = "discord"`.
- Auth: `Authorization: Bot <token>` header. **stdlib only:** `urllib.request` + `json`.
- DM channel creation: POST `/users/@me/channels` with `{recipient_id: user_id}`. Cache `channel.id` in `~/.config/clu/discord_state.json` to avoid repeat lookups.
- Message send: POST `/channels/{id}/messages?wait=true`. Capture returned `message_id`.
- Blocker metadata: persist `discord_message_id` + `discord_channel_id` in the blocker record's new `notify_metadata: dict[str, dict]` field (state-schema addition, keyed by backend name). Missing field defaults to `{}` on load — no migration breakage.
- Rate-limit: parse `Retry-After` header / `retry_after` body field on 429, sleep, retry once. Log + drop on second 429.
- Register `DiscordNotifier` in `notify._NOTIFIER_REGISTRY`.

### Phase 6 — Discord backend, inbound
- New `end_of_line/notify_discord_inbound.py`. `DiscordInboundPoller` implementing `InboundPoller`, mirrors `notify_imessage_inbound.py` shape.
- Poll: `GET /channels/{dm_channel_id}/messages?after=<cursor>&limit=100`. Cursor file: `~/.config/clu/discord_cursor.json` keyed by `channel.id`.
- Filter bot's own messages (`msg.author.id != bot_user_id`) — Discord analog to iMessage's `is_from_me=0`.
- Two reply-correlation paths:
  - **Reply UI (preferred):** `message_reference.message_id` present → lookup blocker via `notify_metadata.discord.message_id`. Direct correlation.
  - **Text grammar fallback:** no `message_reference` → run text through shared `route_reply()`. Bare digit → last-pinged routing (same as iMessage today).
- Dispatch: reuse `_cli_dispatch()` helper extracted in phase 1. Shells `clu answer ...` identically.
- LaunchAgent template: `examples/clu.discord_inbound.plist` mirrors `examples/clu.inbound.plist` (no FDA needed).
- systemd unit template: `examples/clu-discord-inbound.service` for Linux operators.

### Phase 7 — Defaults, docs, smoke
- **Default behavior:** empty/unset `notify.channels` is valid — clu-watch-only mode. Inbox surface still works; no outbound. No "default to iMessage on macOS" silent fallback — silently-defaulting surprises non-Mac operators with errors. **Why:** explicit opt-in is cleaner than a runtime OS check.
- `clu init` adds interactive prompts (additive, skippable with `--no-notify-prompt`):
  - "Wire iMessage? [Y/n]" (default Y on macOS, N elsewhere)
  - "Wire Discord? [y/N]"
- **`clu --no-notify <cmd>` global flag** suppresses all notify dispatch for a single CLI invocation. Lives at the argparse root, threaded through to the dispatcher entry point as a short-circuit. Useful for debugging, dry-runs, and "I'm just testing this locally, don't ping my phone."
- `clu notify-test [--channel KIND]` smoke command: load config, fire test notification through one or all configured channels (skipping disabled ones), print delivery status.
- `docs/operations.md`: three setup sections (iMessage / Discord / clu-watch-only) plus a "Suppressing notifications" section covering `enabled: false`, `--no-notify`, and per-kind filters.
- `docs/contract.md`: new notify schema + ChannelSpec + migration semantics.
- `README.md`: drop "macOS only" caveat; point at three setup paths.

## Non-goals

- **Slack backend.** Operator rejected — issue #11's Slack proposal folds into this plan only in spirit (the pluggable protocol shape), not the code.
- **stdout-only / log-file backend.** Operator rejected — clu-watch + inbox-surface is the in-session equivalent.
- **Telegram, ntfy.sh, Pushover, email, GitHub Issues, generic webhook.** Considered, deferred. Telegram: operator doesn't use. ntfy.sh: unidirectional. Pushover: locked-no in project memory. Protocol from phase 1 leaves room for any of these as a future 3rd-party `Notifier` impl.
- **Discord Gateway (WebSocket) inbound.** REST polling is the v1 path — matches iMessage chat.db poll mental model, no daemon, no stdlib-WS hand-rolling. Gateway can land later if real-time latency becomes a constraint.
- **Multi-operator routing.** "Ping Alice and Bob simultaneously" — deferred. Channels list could grow per-operator metadata later; not in scope.
- **`is_from_me=0` rework (#45).** Independent bug, separate fix.
- **Persistent per-channel disable schedule.** `enabled: false` is binary. "Disable Discord 9-5 weekdays" isn't in scope — operator can wire that externally if needed.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood / Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan notify-multi-channel --phase <id> --token <T>` with the worker token on success.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| protocol | `notify-multi-channel-protocol.md` | Notifier/InboundPoller Protocols + iMessage extraction + shared route_reply (pure refactor) | 1.5h |
| schema | `notify-multi-channel-schema.md` | Multi-channel config schema + `enabled: bool` + auto-migration from flat shape | 2h |
| inbox-surface | `notify-multi-channel-inbox-surface.md` | Active-blocker section in inbox hook + Claude disambiguation instruction | 1.5h |
| reply-skill | `notify-multi-channel-reply-skill.md` | `/clu-reply` bundled skill (explicit escape hatch) | 1h |
| discord-out | `notify-multi-channel-discord-out.md` | Discord outbound (bot DM, REST, message_id persistence) | 2h |
| discord-in | `notify-multi-channel-discord-in.md` | Discord inbound (REST polling, Reply-UI correlation, text fallback) + LaunchAgent/systemd templates | 2h |
| docs | `notify-multi-channel-docs.md` | `clu init` prompts + `--no-notify` global flag + `clu notify-test` smoke + docs + README | 2h |
