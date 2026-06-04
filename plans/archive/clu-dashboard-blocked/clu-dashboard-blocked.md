# clu-dashboard-blocked — surface blocked plans in clu top + clu serve

A plan waiting on the operator is the single most actionable state, yet both
dashboards hide it: `clu top`/`clu serve` are claim-scoped, and `clu block`
releases the claim (`cmd_block` → `release_claim_and_emit`), so `gather_rows`
skips the now-claimless plan (`top.py:331`, `if not claim: continue`). The
blocker itself persists in `data["blockers"]` (`state.py:1082-1092`) and is
readable via `open_blockers(data)` (`state.py:1155-1162`, unanswered =
`answer is None`).

This plan makes both dashboards show a claimless **blocked row** — phase
position + the blocker question inline + blocked-since age — in a distinct
amber "needs-you" health state that sorts to the top, with a blocked count in
the fleet header. Phase `top` lands the shared data layer + health state +
curses render; phase `serve` renders it in the web dashboard off the same
frozen `gather_rows` wire contract (D10). Greenfield-feature (additive); no
Diagnosis section.

## Locked design decisions

### Phase top — data layer + health state + clu top
- **Read the persisting blocker — NO lifecycle change (the design fork).**
  `gather_rows` adds a branch: for a plan with no `current_claim` but
  `open_blockers(data)`, emit a blocked row from the blocker record. `cmd_block`,
  `release_claim_and_emit`, the lease, and the reaper are **untouched**. The
  rejected alternative ("make `clu block` keep the claim") would rewire the
  load-bearing claim/lease/reaper lifecycle for no benefit — the data's already
  there.
- **One flat row schema + discriminator** (research dim 1/3): blocked rows carry
  the SAME keys as claim rows (`assemble_row`, `top.py:282-308`), claim-only
  fields set to `None`, plus append-only keys `blocked=True`, `blocker_question`,
  `blocked_seconds`. A new `assemble_blocked_row(data, blocker, now)` builds it;
  `alive=False`, `phase_id` from the blocker, and `phase_index`/`phase_total`/
  `max_attempts` computed the same way `gather_rows` does for claims (so a
  blocked row still shows WHERE in the plan it's stuck). `blocked_seconds =
  _age_seconds(blocker["asked_at"], now)` (`asked_at` is `utcnow()` ISO at
  creation, `state.py:1089`; `_age_seconds` is None-safe).
- **`"blocked"` health state, amber, sorts to top.** `_m_health`
  (`top_registry.py:288`): `if row.get("blocked"): return "blocked"` BEFORE
  calling `worker_health` (keep that 4-signal fusion pure). `_HEALTH_GLYPH`
  (`:228`) gains `"blocked"` → `!` (amber "needs-you", distinct from red `✗`
  dead — prior art: blocked=you-must-act, dead=work-died, must read differently).
  Health `sort_key` (`:285`): `{"blocked": -1, "dead": 0, "warn": 1, "ok": 2}`.
- **Blocked-to-top, stable.** `gather_rows` stable-sorts blocked rows first
  before returning (`rows.sort(key=lambda r: 0 if r.get("blocked") else 1)`);
  running/dead keep registry order. Web sticky-by-identity selection re-resolves
  by key so order changes are safe.
- **Curses render of the claimless row.** `format_rows`/`_row_cells`/`_row_line`
  (`top.py`): a blocked row's PID cell → `blk` (not `dead`), SAYING cell → the
  blocker question (`last_text` is None for blocked, so reuse that column).
  `format_detail`: a `BLOCKED <Nm> · <question>` block above CMD/SAY.
  `fleet_summary` (`top_registry.py:432-448`): add `N blocked`; rewrite the
  comment that says there's deliberately no blocked count (this plan reverses it).

### Phase serve — clu serve (web)
- **`toView` parity** (`web/index.html:235-247`): carry `blocked`/
  `blockerQuestion`/`blockedSeconds`; `health = r.blocked ? "blocked" : (!alive
  ? "dead" : …)` — blocked checked FIRST (mirrors `_m_health`). `DOT` map +
  `.dot.blocked` CSS in amber.
- **Row + detail render:** `buildRow`/`patchRow` → amber `BLK` badge, the blocker
  question in `.sub` (`esc()`'d), `— blocked <Nm> —` in the metrics slot;
  `detailHTML` → a `blocker` kv row + health-chip "blocked". `statusInner`
  (`:300-312`) → a `N blocked` count beside running/dead.

## Non-goals
- **No change to the block/claim lifecycle.** `clu block` still releases the
  claim. *Safe:* the blocker persists in `data["blockers"]` independent of the
  claim — visibility needs only a read.
- **No answer-from-dashboard.** Dashboards stay strictly read-only (D7); a
  blocked row is informational. Answering stays a CLI/inbox action. *Safe:*
  read-only is an existing hard invariant; a write path is a separate change.
- **Only OPEN blockers** (`answer is None`); answered/historical produce no row.
- **One blocker per row** — the primary (first `open_blockers`) blocker's
  question; multi-blocker detail listing is parked. *Safe:* a phase rarely has
  >1 open blocker; the first is the actionable one.
- **No full health re-sort of the fleet.** Only blocked floats to top (stable);
  running/dead keep registry order.

## Files touched
- `end_of_line/top.py` — P-top modified — `gather_rows` blocked branch + sort;
  new `assemble_blocked_row`; `format_rows`/`_row_cells`/`_row_line`/
  `format_detail` blocked render. **API hotspot: `gather_rows` row-dict gains
  append-only keys `blocked`/`blocker_question`/`blocked_seconds` (D10 wire
  contract, read by clu serve).**
- `end_of_line/top_registry.py` — P-top modified — `_m_health` blocked-first;
  `_HEALTH_GLYPH` + health `sort_key` gain `blocked`; `fleet_summary` blocked
  count.
- `end_of_line/web/index.html` — P-serve modified — `toView` blocked health +
  keys; `.dot.blocked` CSS; `buildRow`/`patchRow`/`detailHTML` blocked render;
  `statusInner` blocked count.
- `end_of_line/state.py` — read-only (no edit) — `open_blockers` (`:1155`),
  blocker dict (`:1082`), `add_blocker` (`:1072`). Confirm no change needed.
- `tests/test_top.py` — P-top modified — gather_rows blocked row + sort; health
  "blocked" (4 distinct glyphs); curses render; fleet count.
- `tests/test_webserver.py` — P-serve modified — frontend guard (`r.blocked`,
  `blockerQuestion`, `.dot.blocked`, blocked count).

## Per-phase done checklist
- TDD: failing tests first (AAA, factory helpers).
- `/code-review` after (each phase is >1 file / >30 lines).
- Full suite green: `python3 -m unittest discover -s tests` (report count).
- Structured commit (Title / Why / What's new / Under the hood / Tests /
  `Co-Authored-By:` trailer). Stage explicit paths — no `git add -A`.
- After the commit: `clu verify` then `clu attest --simplify` (each
  `--plan clu-dashboard-blocked --phase <id> --token <T>`), then
  `clu complete --plan clu-dashboard-blocked --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| top | `clu-dashboard-blocked-top.md` | Data layer (`gather_rows` blocked branch + `assemble_blocked_row` + sort), `"blocked"` health state (amber, sorts top), curses render (`blk` PID + question + detail block), fleet blocked count | 3h |
| serve | `clu-dashboard-blocked-serve.md` | clu serve: `toView` blocked health + `.dot.blocked`, row badge + question, detail pane, header blocked count | 2h |
