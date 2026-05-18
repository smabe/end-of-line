# imessage-attributed-body-decode

## Goal
Make `poll_once` route real typed-from-phone self-chat replies on
modern macOS by decoding the message body out of `attributedBody`
(NSAttributedString typedstream) when the `text` column is NULL.
Closes the inbound-parsing gap left by #45.

## Diagnosis
- **Hypothesis:** the WHERE clause at
  `end_of_line/notify_imessage_inbound.py:188` (`m.text IS NOT NULL`)
  drops every modern self-chat row, because macOS stores the message
  body in `attributedBody` as an NSArchiver typedstream rather than
  in `text`. The `is_from_me=1` routing key, send-side floor, and
  auto-resolver shipped in #45 are correct but unreachable because
  the row is filtered before they run.
- **Falsifiable test:** operator types a fresh message into the
  self-chat from iPhone (produces a new chat.db row with
  `is_from_me=1`, target chat). Then call
  `poll_once(last_rowid=<row-1>, outbound_floor=<clu-send>)` with
  empty registry. The poller MUST advance past the new row and skip
  it (no dispatcher call expected because there's no open blocker)
  — if it instead returns the prior cursor unchanged, the WHERE
  filter excluded the row, confirming the hypothesis.
- **Test result:** RUN + CONFIRMED in this session.
  ROWID 329018 = `clu notify-test` send; ROWID 329019 = operator
  typed-from-phone reply. Both rows have `is_from_me=1`,
  chat=`abraham.awadallah@gmail.com`, `text=None`, attributedBody
  populated (179–196 bytes). Running
  `poll_once(last_rowid=329017, outbound_floor=329018,
  self_chat_id='abraham.awadallah@gmail.com')` returned `329017`
  (cursor stuck) and dispatched nothing — the WHERE filter drops
  both rows before scoring. Aggregate: 0 / 63 rows in the operator's
  self-chat have `text IS NOT NULL`. Hypothesis confirmed.

## Non-goals
- Decoding attachment payloads, sticker rows, or image-only messages
  (body stays None → row skipped, same as today).
- Decoding tapbacks / reactions (`associated_message_type != 0`).
  These should be skipped, not routed.
- Handling edited-message history (newer macOS stores prior
  revisions in `message_summary_info`). We take the current
  attributedBody only.
- A general-purpose typedstream library — we only need the leading
  `NSString` payload of an `NSAttributedString`. Other type tokens
  → return None (caller treats as un-routable, advances cursor).
- Changing the routing key, floor logic, or auto-resolver paths.
  Those are correct; only the row-acceptance filter changes.
- Adding a runtime dependency (project rule: stdlib only).

## Files to touch
- `end_of_line/notify_imessage_inbound.py` —
  (a) add `_decode_attributed_body(blob: bytes) -> str | None`
  (pure stdlib, returns the raw decoded UTF-8 string *including*
  any `U+FFFC` object-replacement characters; returns None on any
  parse error, missing `START_PATTERN`, truncated length, or
  invalid UTF-8); (b) update the `poll_once` query at L184–192 to
  `SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me … WHERE
  m.ROWID > ? AND (m.text IS NOT NULL OR m.attributedBody IS NOT
  NULL) AND m.associated_message_type = 0 AND c.chat_identifier = ? …`;
  (c) compute `body = text if text is not None else
  _decode_attributed_body(blob)`, then strip `U+FFFC` from the
  decoded body and skip the row when the result is None / empty
  after strip; (d) preserve the existing `is_from_me=1 AND rowid
  <= outbound_floor` skip — runs *before* decode so we never burn
  CPU decoding our own sends; (e) two short reference comments
  near the decoder: Sardegna's typedstream writeup URL +
  `imessage-database/src/util/streamtyped.rs` (canonical
  byte-pattern source).
- `tests/test_notify_inbound.py` — new top-level fixture helper
  `_make_attributed_body(text: str) -> bytes` (NOT nested inside
  the chat.db builder — the builder stays clean) that emits a
  valid typedstream for an NSString payload by emitting
  HEADER + class-chain bytes + `START_PATTERN` + length-encoded
  UTF-8 + `END_PATTERN`; extend the existing chat.db schema
  builder to include `attributedBody BLOB` and
  `associated_message_type INTEGER` columns; new cases under the
  `PollOnceTests` class covering: (1) ascii body decodes and
  dispatches; (2) emoji + multi-byte UTF-8 body decodes and
  dispatches; (3) attributedBody-only row with `is_from_me=1` at
  or below `outbound_floor` is skipped; (4) attributedBody-only
  row above floor passes the filter and reaches the locator;
  (5) malformed attributedBody (truncated mid-length) returns
  None and the row is skipped while the cursor still advances;
  (6) text + attributedBody both populated → text wins;
  (7) **129-byte ASCII boundary fixture** exercising the
  `0x81` u16-LE length-sentinel path — naive `0x81 →
  literal-length` decoders silently corrupt this case;
  (8) attachment-only body (decodes to just `U+FFFC`) is
  stripped and skipped; (9) `associated_message_type != 0`
  (tapback row) is filtered at SQL level before the decoder runs.

## Format reference (authoritative — pin in code as comments)
- **HEADER** (16 fixed bytes, skip outright):
  `b"\x04\x0bstreamtyped\x81\xe8\x03"`. The trailing `\x81\xe8\x03`
  is system version `1000` encoded as `\x81` (u16-LE sentinel) +
  u16-LE `0x03e8`.
- **START_PATTERN** (immediately precedes length byte of UTF-8
  payload): `b"\x01\x2b"` — SOH + `+`, the ObjC type encoding
  for a UTF-8 NSString. The decoder scans for this byte pair to
  locate the body, NOT for the ASCII string `"NSString"` (class
  back-references would silently break that approach in
  edited-message blobs).
- **Length encoding** (signed-byte interpretation, three tiers):
  - head `0x00–0x80` → literal unsigned length (0–128 bytes).
  - head `== 0x81` → next 2 bytes are u16-LE length.
  - head `== 0x82` → next 4 bytes are u32-LE length.
  - head `== 0x83` → next 8 bytes are u64-LE length.
  Length is in BYTES of UTF-8, not codepoints. **A naive
  `length = head_byte` decoder corrupts every body ≥ 129 bytes**
  because `0x81` is sentinel-not-literal; the 129-byte fixture
  pins this.
- **END_PATTERN** (boundary between text payload and attribute
  dict, optional to read — length prefix already tells us where
  to stop): `b"\x86\x84"`.
- **Decoder responsibility**: return raw decoded UTF-8 string
  including any `U+FFFC` chars. `poll_once` strips `U+FFFC` +
  empty-checks. Strict UTF-8 decode — return None on
  `UnicodeDecodeError` (we deliberately diverge from the Rust
  parser's lossy fallback; better to skip a row than route
  garbage).

## Failure modes to anticipate
- **129-byte boundary off-by-2 corruption** — head byte `0x81`
  triggers the u16-LE sentinel path. A naive decoder treats `0x81`
  as literal length 129 and reads the u16-LE bytes as the first
  two text bytes. The hand-crafted 129-byte ASCII fixture
  (test case 7) is the canary for this; if it fails the decoder
  is silently wrong on every body longer than 128 chars.
- **Multi-byte UTF-8 / emoji** — operator routinely texts self
  reminders with emoji. Length is in BYTES — decode with strict
  `bytes.decode("utf-8")`, return None on `UnicodeDecodeError`.
  Test case 2 (`"héllo 👋"`, 11 UTF-8 bytes) pins this.
- **Reactions / tapbacks** — `associated_message_type != 0` rows
  have decodable bodies ("Liked '…'", "Loved an image", etc.)
  but they're Messages-rendered placeholders, not operator input.
  **Filtered at SQL level** (`AND m.associated_message_type = 0`)
  — cheapest and matches operator intent. Test case 9 pins this.
- **Attachment-only bodies** — body decodes to one or more
  `U+FFFC` (NSAttachmentCharacter) and nothing else. Decoder
  returns the raw string; `poll_once` strips `U+FFFC` and
  empty-checks, skipping the row. Test case 8 pins this. (Don't
  strip inside the decoder — a future caller might want
  attachment offsets.)
- **Malformed / truncated blobs** — corrupt chat.db rows, partial
  writes during chat.db checkpoint, future macOS schema changes.
  Decoder must return None, never raise — `poll_once` advances
  the cursor past unparseable rows so a wedged blob can't
  re-fire. Test case 5 pins truncated-mid-length; the same
  no-raise contract covers any other malformed input.
- **Class back-references** (`≥ 0x92`) — second-occurrence
  classes use a short back-ref index, not a fresh `0x84` def.
  Not a current concern: user text is always the *first* NSString
  in a chat.db message blob, so scanning for `START_PATTERN`
  finds it before any back-ref appears. Add a one-line comment
  noting this assumption so a future maintainer extending to
  edited-message history knows where to look.
- **Outbound rows still skipped under the floor** — `is_from_me=1
  AND rowid <= outbound_floor` skip MUST run *before* decode in
  the for-loop, otherwise every clu-sent row burns decoder CPU.
- **chat.db schema columns** — extending the test fixture
  builder to include `attributedBody BLOB` and
  `associated_message_type INTEGER` will touch existing tests
  that build chat.db. Verify all existing `INSERT` sites still
  pass (likely they default `attributedBody=NULL,
  associated_message_type=0`, which is correct legacy behavior).
- **Decoder performance** — runs every tick (≥ every 30s under
  default LaunchAgent cadence), capped at `POLL_BATCH_LIMIT` rows
  per call, blobs typically 100–500 bytes. The decoder is O(N)
  on blob size; no perf concern, but add a `len(blob) > 64KB`
  short-circuit defensively (return None) in case some
  pathological row ever lands.
- **Decoder performance** — `poll_once` runs every tick (≥ every
  30s under default LaunchAgent cadence). Decoder must be O(N) on
  blob size; the cap is `POLL_BATCH_LIMIT` rows per call, blobs
  typically 100–500 bytes. Test the worst case (1 KB body) stays
  sub-millisecond; if not, add a `len(blob) > 64KB` short-circuit.
- **Outbound rows still skipped under the floor** — must verify
  the `is_from_me=1 AND rowid <= outbound_floor` skip runs BEFORE
  decode, otherwise we waste CPU on every clu-sent row. (Phase
  ordering inside the for-loop.)
- **chat.db schema drift** — `attributedBody` has been present
  since macOS 10.13 but the column type is BLOB and SQLite returns
  it as `bytes` via stdlib `sqlite3` — verify the test fixtures
  set it as `bytes`, not `str`, and that `conn.execute` returns
  `bytes` (the default behavior — only `text_factory` overrides
  affect TEXT columns).
- **Operator's chat.db live state** — the project's existing test
  rows in `tests/test_notify_inbound.py` build chat.db schemas
  by hand. Need to extend that builder to include `attributedBody`
  + `associated_message_type` columns and `INSERT` the new fields,
  or existing tests break.

## Done criteria
- `_decode_attributed_body` is a pure-stdlib function in
  `end_of_line/notify_imessage_inbound.py` that returns the
  decoded `str` for a well-formed NSString payload and `None`
  for any malformed / non-string / empty input.
- `poll_once` accepts rows where `text` OR `attributedBody` is
  non-null; computes the body as `text if text else
  _decode_attributed_body(blob)`; skips rows whose body decodes
  to None or empty; preserves the existing `is_from_me=1 AND
  rowid <= outbound_floor` filter as an *earlier* short-circuit.
- New fixture-driven tests in `tests/test_notify_inbound.py`
  cover the six cases listed under "Files to touch" item 2.
- The full suite is green (≥ 973 + new tests, no regressions).
- Live re-test on the operator's machine: with an open blocker
  whose answers include "yes" (or any operator-driven seed), the
  operator sends a typed reply from their phone and observes
  `poll_once` dispatch the answer (verified via `clu watch` or
  the state file's `answered_at`). Recorded in the commit body
  or follow-up note.

## Parking lot
(empty)

## References
- Sardegna, Chris — *Reverse Engineering Apple's typedstream
  Format*. <https://chrissardegna.com/blog/reverse-engineering-apples-typedstream-format/>
  Canonical writeup of the format. Cite in a one-line comment
  near `_decode_attributed_body`.
- ReagentX/imessage-exporter —
  `imessage-database/src/util/streamtyped.rs:59-102` (`parse`
  fn). Canonical byte-pattern source: `START_PATTERN =
  [0x01, 0x2b]`, `END_PATTERN = [0x86, 0x84]`. We diverge by
  using strict UTF-8 decode + return-None instead of lossy +
  U+FFFD. Cite in a second one-line comment.
- dgelessus/python-typedstream —
  `src/typedstream/stream.py` (`_read_integer` ~L1108,
  `_read_unshared_string` ~L1219). Reference for the
  `0x81 / 0x82 / 0x83` length-encoding tiers. GPL-3.0 — cite
  for byte semantics, don't copy code.
