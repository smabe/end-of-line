# clu-dashboard-blocked-top — blocked-row data layer + health state + clu top render

You are phase `top` of the `clu-dashboard-blocked` plan. Make `gather_rows` emit
a claimless "blocked" row for plans with an open blocker, add a `"blocked"`
health state that sorts to the top, render it in `clu top` (curses), and add a
blocked count to the fleet header. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/clu-dashboard-blocked.md`. Summary:

- **Read the persisting blocker — NO lifecycle change.** `gather_rows` reads
  `open_blockers(data)` for claimless plans; `cmd_block`/claim/lease/reaper are
  untouched.
- One flat row schema + discriminator: blocked rows carry the same keys as claim
  rows, claim-only fields `None`, plus append-only `blocked=True`,
  `blocker_question`, `blocked_seconds`. `alive=False`. `phase_index`/
  `phase_total`/`max_attempts` computed like a claim row.
- `"blocked"` health: `_m_health` returns it first; glyph `!` (amber); sort_key
  `{"blocked": -1, "dead": 0, "warn": 1, "ok": 2}`. Blocked-to-top stable sort in
  `gather_rows`.
- Curses: PID cell → `blk`, SAYING cell → blocker question; `format_detail`
  `BLOCKED <Nm> · <question>` block. `fleet_summary` blocked count (reverse the
  "no blocked count" comment).

## Read first

- `end_of_line/top.py:311-352` — `gather_rows` loop + the `if not claim: continue`
  skip (`:331`) where the blocked branch goes; `:339` plan/project enrichment;
  the phase_index/phase_total/max_attempts computation to mirror.
- `end_of_line/top.py:282-308` — `assemble_row` (the claim-row schema to mirror,
  same keys with `None` holes for a blocked row); `_age_seconds` (None-safe).
- `end_of_line/top.py` `_row_cells`/`_row_line`/`format_rows`/`format_detail` —
  the render path (PID cell `"ok"/"dead"`, SAYING cell). `_clean` for the
  question.
- `end_of_line/state.py:1072-1092` (`add_blocker` dict: `id`,`phase_id`,`type`,
  `question`,`options`,`context`,`asked_at`,`answer`,`answered_at`),
  `:1155-1162` (`open_blockers` = `answer is None`).
- `end_of_line/top_registry.py:228` (`_HEALTH_GLYPH`), `:231-246` (`worker_health`
  — keep pure), `:283-294` (`_m_health` + sort_key `:285`), `:432-448`
  (`fleet_summary` + the comment to reverse).
- `tests/test_top.py:282-322` (`GatherRowsTest` + `_claim`/`_transcript`
  helpers — omit `_claim` to leave it claimless, add a blocker via `st.mutate` +
  `st.add_blocker`), MetricsTest health test (`~:1418`), FormatRows/Detail +
  PhaseProgress tests for render patterns.

## Produce

1. **Failing tests first** (`tests/test_top.py`):
   - `GatherRowsTest`: a registered plan with an open blocker and NO claim →
     exactly one row, `blocked=True`, `blocker_question` set, `phase_index`/
     `phase_total` present, `alive=False`, no crash. A blocked + a running plan →
     the blocked row sorts first.
   - MetricsTest: `_m_health` returns `"blocked"` for a `blocked=True` row; the
     glyph set now has 4 distinct glyphs.
   - `format_rows`: a blocked row shows `blk` (not `dead`) + the question;
     `format_detail` shows `BLOCKED` + the question. `fleet_summary` counts a
     blocked row.

2. **Implementation.**
   - `top.py`: `assemble_blocked_row(data, blocker, now)`; `gather_rows` branch
     after `:331` (`if open_blockers(data): rows.append(...); ... ; continue`) +
     blocked-to-top stable sort before return; `_row_cells`/`_row_line`/
     `format_rows`/`format_detail` blocked branches.
   - `top_registry.py`: `_m_health` blocked-first; `_HEALTH_GLYPH["blocked"]="!"`;
     sort_key gains `"blocked": -1`; `fleet_summary` blocked count + comment
     rewrite.

3. **Acceptance.**
   - All new tests green; full suite green (report count).
   - Manual: a state with an open blocker + no claim → `gather_rows` returns a
     blocked row sorted first; `format_rows`/`format_detail` render `blk` + the
     question; `fleet_summary` shows `N blocked`.
   - `grep` confirms NO edit to `cmd_block`/`release_claim_and_emit` (lifecycle
     unchanged).
   - The byte-identical table-pane test still passes (claim-only rows → no sort
     change).

4. **Commit + attest + complete.**
   - Commit: `clu-dashboard-blocked: phase top — blocked-row data layer + health + curses render`.
   - Stage: `end_of_line/top.py`, `end_of_line/top_registry.py`, `tests/test_top.py`.
   - After the commit: `clu verify --plan clu-dashboard-blocked --phase top --token <T>`
     then `clu attest --simplify --plan clu-dashboard-blocked --phase top --token <T>`.
   - `clu complete --plan clu-dashboard-blocked --phase top --token <T>`.

## Failure modes to watch

- **Blocked conflated with dead.** `alive=False` → `worker_health` / the PID
  cell `"ok" if alive else "dead"` / would say *dead* unless `blocked` is checked
  FIRST. Check `blocked` before the dead path everywhere. This is THE
  correctness case.
- **Claim AND open blocker.** Can't happen (`clu block` releases the claim first)
  — but code defensively: only `not claim AND open_blockers` → blocked row, so
  the claim branch always wins.
- **`asked_at` None/foreign tz** → `_age_seconds` returns None → render `—`, not
  a crash.
- **Sort must be stable** — two blocked plans keep registry order among
  themselves.
