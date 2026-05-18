# make-chat-db-dict-only

## Goal
Collapse `_make_chat_db` in `tests/test_notify_inbound.py` from a
4-branch tuple-or-dict dispatch ladder to a single dict-body block,
converting the 13 tuple-form callsites to the dict form that the 5
attributedBody / tapback tests already use. Pure mechanical refactor,
no behavior change, no production code touched.

Census (21 callsites): 5 already dict, 3 pass empty lists, 13 are
tuple — 12 × 3-tuple, 1 × 4-tuple (line 296), 1 × 5-tuple (line 638).

## Non-goals
- No changes to `end_of_line/notify_imessage_inbound.py` or any other
  production module — this is test-fixture cleanup only.
- No widening of the fixture's column surface (no new optional
  parameters, no helpers split out).
- No changes to other test fixture builders (`_make_resolver_db`, etc.) —
  they don't have the dual-API problem.
- No drive-by test renames, parametrization passes, or assertion
  tightening. The 21 callsites should look identical to before, just
  in dict form.

## Files to touch
- `tests/test_notify_inbound.py` — (a) delete the `elif len(row) == 3 /
  4 / else 5`-tuple branches from `_make_chat_db`'s row-unpacking; keep
  the `isinstance(row, dict)` body unconditional. (b) Convert the 13
  tuple-form callsites: `(rowid, is_from_me, text)` →
  `{"rowid": ..., "is_from_me": ..., "text": ...}`; 4-tuple at line 296
  adds explicit `chat_id`; 5-tuple at line 638 adds `date_ns` (omit
  `chat_id` — default returns `DEFAULT_CHAT_ID`). (c) Update the
  function docstring to describe the dict-only API.

## Failure modes to anticipate
- **Typo in a converted callsite** — a dropped key or wrong field name
  makes the row look empty. The 986-test suite covers every existing
  callsite by construction (each callsite is inside a test that
  asserts something), so any conversion bug surfaces as a red test
  during the suite run. No "silent regression" path.
- **Default-handling drift** — tuple form silently used `chat_id =
  DEFAULT_CHAT_ID` and `date_ns = 0` for the 3-tuple shape. The dict
  form already uses `row.get("chat_id", DEFAULT_CHAT_ID)` and
  `row.get("date_ns", 0)`, so omitting those keys preserves the same
  defaults. Sanity-check during conversion that 3-tuple callsites
  become dicts with only the three required keys, not with explicit
  `chat_id`/`date_ns` keys (the latter is noise).
- **The sole 5-tuple callsite** — line 638,
  `test_append_then_drain_resolves_floor` in `OutboundPendingTestCase`,
  passes `(7, 1, "BLOCKED: pick framework", DEFAULT_CHAT_ID,
  unix_to_chatdb_ns(sent_at + 1))`. Converts to
  `{"rowid": 7, "is_from_me": 1, "text": "...", "date_ns": <expr>}`
  with `chat_id` omitted (the `.get("chat_id", DEFAULT_CHAT_ID)`
  default carries it).
- **Test-collection order changes** — Python `dict` ordering is
  insertion-order since 3.7, and chat_rowids assignment in
  `_make_chat_db` depends on the row sequence, not the row form. No
  ordering risk.
- **Imports / module-level helpers** — none added or removed. The
  `_make_chat_db` signature stays the same (`path, rows: list`).

## Done criteria
- `_make_chat_db`'s row-unpacking is exactly the dict-body block;
  the `isinstance` check and the three `elif len(...) == N` branches
  are gone. Function body shrinks by ~10 lines.
- All 13 tuple-form callsites in `tests/test_notify_inbound.py` are
  converted to dict form (the other 8 are already-dict or empty-list,
  no change needed).
- Function docstring describes the dict-only API; the legacy "tuple
  or dict" wording is removed.
- 986/986 suite green (no test count change; the existing tests
  drive every callsite).
- /simplify pass run before commit — single-file mechanical diff,
  but worth one pass to catch any awkward dict literal formatting.

## Parking lot
(empty)
