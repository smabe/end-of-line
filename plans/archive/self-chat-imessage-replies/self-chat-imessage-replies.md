# self-chat-imessage-replies

## Goal
Make iMessage replies from the operator's self-chat thread actually route to
clu (issue #45). At the same time, close the broader "poller reads every
chat on the host" surface by scoping the SQL to the configured chat.

## Diagnosis
- **Hypothesis:** `notify_imessage_inbound.poll_once` filters
  `is_from_me = 0` with no chat scoping. The operator's self-chat
  reply rows have `is_from_me = 1` (operator IS the sender), so they
  are skipped forever; the cursor advances past them and the answer
  never reaches clu.
- **Falsifiable test:** Add a unittest fixture row in a temp chat.db
  with `is_from_me=1`, `chat_identifier=<operator-handle>`, text=`"1"`,
  and one open blocker. Current `poll_once` returns without
  answering. Post-fix it answers.
- **Test result:** Will be run as phase-1's first commit (red →
  green). Diagnosis is already validated against code: see
  `end_of_line/notify_imessage_inbound.py:95` and the operator's
  2026-05-17 blocker-smoke repro in the issue body.

## Non-goals
- **No new notification backend, no Apple-ID-#2 routing.** Self-chat
  stays the canonical thread; macOS doesn't currently support a
  second Messages account simultaneously.
- **No allowlist redesign.** `imessage:access` semantics unchanged;
  self-chat replies route on chat scope + reply grammar alone.
- **No retroactive replay of missed replies.** Cursor still advances
  past unmatched rows. We do not scan history for the 2026-05-17
  miss.
- **No general multi-chat support.** This plan locks the poller to
  ONE configured chat (the self-chat). Replies from other chats
  remain out of scope.

## Files to touch
- `end_of_line/notify_imessage.py` — after `_osascript_send` fires,
  append a `{chat_id, sent_at_epoch}` mark to
  `~/.clu/outbound_pending.json` (atomic write). `send()` stays
  fire-and-forget; no chat.db query on the send path.
- `end_of_line/notify_imessage_inbound.py` — `poll_once` now does
  three steps in order:
    1. **Drain pending marks**: read `outbound_pending.json`; for
       each mark, query `MAX(ROWID) WHERE chat_id = ? AND
       is_from_me = 1 AND date > sent_at_epoch`. Update
       `outbound_rowids[chat_id]` if found. Drop marks older than
       a sanity timeout (~60s) so silently-failed sends don't
       accumulate.
    2. **Read new inbound**: SQL joins `chat_message_join + chat`,
       scopes by `chat.chat_identifier = self_chat_id`, drops the
       `is_from_me = 0` filter. Cursor advances past every row read.
    3. **Filter clu's own rows**: skip any `is_from_me = 1` row
       whose ROWID ≤ `outbound_rowids[chat_id]`. Everything else
       routes through reply grammar + locator as today.
  Adds `_resolve_self_chat_id` (hybrid: honor explicit override,
  else run the `chat → chat_handle_join → handle` lookup; refuse
  when 0 or >1 candidates).
- `~/.clu/seen_msg_rowid` (bare int) → `~/.clu/inbound_state.json`
  `{"schema_version": 1, "last_inbound_rowid": 0, "outbound_rowids": {}}`.
  Migration follows `monitor.py:65-84` pattern: detect legacy file
  presence → unlink → write fresh JSON via `state.locked_json`.
  Cursor resets to 0; LIMIT cap bounds the one-time re-scan.
- `~/.clu/outbound_pending.json` — new state surface, schema-versioned.
  List of `{chat_id, sent_at_epoch}` under
  `{"schema_version": 1, "marks": [...]}`. All reads/writes via
  `state.locked_json` + `state.save_atomic` (fcntl-locked,
  cross-process-safe — no separate lockfile needed).
- `end_of_line/config.py:25-30` + `:105-116` — `ChannelSpec.params`
  passthrough dict already accepts `self_chat_id` with zero schema
  changes (per `config.py:115`). Add a per-field test in
  `tests/test_config_channels.py` matching the existing
  `test_load_channel_*_optional` pattern.
- `end_of_line/cli.py:1867-1927` (`cmd_doctor`) — always-on inline
  check: walk `cfg.notify.channels`, for each iMessage channel call
  `_resolve_self_chat_id`; report 0 / 1 / >1 candidates + override
  hint when not 1.
- `tests/test_notify_inbound.py` — extend `_make_chat_db` (currently
  inserts only `message` rows) to also populate `chat`, `handle`,
  `chat_handle_join`, `chat_message_join` so the new SQL has
  something to join against. Add `unix_to_chatdb_ns(t)` helper for
  `message.date` (Apple-epoch nanoseconds). New fixtures:
  (a) self-chat reply with `is_from_me=1` (currently dropped, post-fix routes),
  (b) clu's own outbound row skipped via floor,
  (c) reply in OTHER chat dropped by chat-identifier scope,
  (d) bare-int legacy seen file detected → unlinked → fresh JSON,
  (e) drain step updates floor when row found, drops mark when sanity timeout exceeded,
  (f) auto-resolver: 0 candidates → refuse, 1 → use, >1 → refuse with override hint.
- `docs/operations.md` § iMessage — self-chat replies now work;
  document `self_chat_id` override + auto-resolve fallback +
  `outbound_pending.json` / `inbound_state.json` paths.

## Failure modes to anticipate
- **chat.db join ambiguity.** Operator's handle may map to multiple
  chats (phone + email registered). Auto-resolver scopes to chats
  where `c.room_name IS NULL`, `service_name='iMessage'`,
  `chat_identifier = handle.id`, single participant. Refuses with a
  clear error pointing to `self_chat_id` override on 0 or >1
  candidates.
- **Stale-mark accumulation.** Drain step queries `MAX(ROWID) ... AND
  date > sent_at_epoch`. If osascript send fails silently (no row
  ever appears), the mark stays forever. Sanity timeout (~60s)
  drops it; covered by test fixture (e).
- **Brief floor-staleness window.** Between send and next poll tick
  (poller cadence default 5s), the floor for that chat isn't
  updated yet. Clu's own row reaches the locator and is dropped by
  reply-grammar narrowness (clu's text is multi-line). Operator's
  reply (higher ROWID) is matched + routed. Floor catches up on
  next tick. No correctness loss, just a momentary reliance on
  grammar for clu's own rows.
- **State-file migration.** Bare-int `seen_msg_rowid` files exist on
  every shipped install. Loader detects bare-int format and upgrades
  to JSON on first write. Tested via fixture (d).
- **Cross-process file access.** `outbound_pending.json` is written
  by cron-spawned send and drained by LaunchAgent-spawned poller.
  `state.locked_json` (fcntl advisory lock + `save_atomic`) handles
  the serialization — no separate lockfile.
- **chat.db date unit drift.** `message.date` is Apple-epoch
  nanoseconds (offset 978_307_200 s from Unix epoch, `* 1e9` for ns).
  All SQL bindings against `message.date` MUST convert via
  `unix_to_chatdb_ns()`. Forgetting lands rows 31 years in the past
  or scaled by 1e9.
- **`chat_message_join` many-to-many.** Schema is many-to-many (no
  UNIQUE on `message_id`); forwarded-as-SMS and group splits can
  duplicate rows. Defense is the explicit `cmj.chat_id = ?` scope
  on every read.
- **Multi-device sends.** Operator replies from iPhone — row syncs
  to Mac's chat.db with `is_from_me=1`, same `chat_identifier`.
  Works once chat-scoped; covered by fixture (a).
- **Empty `self_chat_id` with unresolvable handle.** Neither
  override nor auto-resolve yields a chat. Poller MUST NOT fall back
  to "read all chats" — refuses to poll, logs loudly, surfaces in
  `clu doctor` so the operator gets a fix path.

## Done criteria
- Phase-1 red→green test passes: self-chat `is_from_me=1` reply
  answers an open blocker via the poller.
- Other-chat replies dropped by SQL scope (no longer reach the
  locator).
- Clu's own outbound rows in self-chat don't trigger spurious
  answers, even with grammar-shaped text (verified by ROWID skip
  fixture).
- `self_chat_id` config override path covered by a test.
- Auto-resolve picks the correct chat when one self-chat exists;
  refuses cleanly when zero or >1 candidates exist.
- Full suite green (`python3 -m unittest discover -s tests`),
  pass count reported.
- `docs/operations.md` updated.
- Commit closes #45.

## Parking lot
(empty)
