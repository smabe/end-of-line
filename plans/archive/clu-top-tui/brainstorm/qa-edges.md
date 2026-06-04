# `clu top` TUI redesign — QA / edge-cases & test strategy

Adversarial review of the proposed modular multi-pane `clu top`. Curses semantics verified against
[docs.python.org/3/library/curses.html](https://docs.python.org/3/library/curses.html). Code refs are to
`end_of_line/top.py` and `tests/test_top.py` at current HEAD.

## The architecture seam (most important QA decision)

**Route all rendering through a `Surface` protocol** — `addstr(y, x, text, attr=0)`, `width`, `height` — with two
impls: `CursesSurface` (wraps `stdscr.addnstr`, swallows `curses.error` exactly as the loop does today at
`top.py:477-479`) and `BufferSurface` (a list-of-rows grid). Every pane's `render(surface, rect)` draws into a
clipped sub-rect; layout math (`Rect` splitting) is pure. This makes the entire pane tree testable in `unittest`
with zero real terminal: instantiate `BufferSurface(w, h)`, run a frame, assert on the grid. It also gives us the
single invariant property tests need: a Surface **clips** writes to its bounds, so "draws past the edge" becomes a
caught contract rather than a `curses.error` crash. Without this seam, `_run_curses` stays untested (it is today —
the docstring at `top.py:455` and the absent test confirm it) and every geometry bug ships blind.

## Highest-risk failure modes

### 1. Selection identity vs index across the 1.5s refresh (highest risk)
- **Trigger:** `gather_rows` (`top.py:278`) returns a *fresh* list each tick; order is registry order, not stable.
  A plan above the selection completes → rows shift up → index-based selection silently points at a different
  worker. The selected worker completing → index dangles past list end.
- **Symptom:** operator acts on / reads detail for the wrong worker; or detail pane renders a stale/empty row.
- **Guard:** select **by identity** = `(project, plan, phase_id)` tuple (these fields exist on every row,
  `top.py:305-306`, `assemble_row` `phase_id` `top.py:264`). Each tick, re-resolve the selected key to its new
  index; if the key vanished, fall back to clamped previous index (or row 0); empty list → no selection.
- **Test:** `BufferSurface` + a controller fed three successive `gather_rows` snapshots (selected row moves up /
  drops out / list empties); assert the resolved selection key is preserved-or-gracefully-degraded. Deterministic,
  no terminal, no timing.

### 2. Per-pane error boundary — one bad pane must not kill the TUI
- **Trigger:** a metric pane's `compute()` or `render()` raises (transcript parse edge, `None` where a str is
  expected, a divide in layout). Today the whole frame is one try/except only around the curses write
  (`top.py:477`), not around data assembly — an exception in `format_detail`/`gather_rows` escapes the loop and
  `curses.wrapper` tears down the terminal and re-raises (docs: "restore the terminal to a sane state before
  re-raising").
- **Symptom:** the entire dashboard exits to a traceback because one pane choked.
- **Guard:** wrap each pane's compute+render in try/except inside the frame; a failed pane renders an inline
  `[pane X error]` band and the rest of the frame survives. Panes declare a `min_rect`; a pane that can't fit its
  minimum is dropped, not crashed.
- **Test:** register a `BoomPane` whose `compute` raises; assert the frame still renders the other panes and an
  error band, return code stays 0.

### 3. Extreme / zero / negative geometries
- **Trigger:** 1-row, 1-col, 2×2, 200×5 (coolant strip), 30×120 (phone ssh), and the dangerous one — **0 or
  negative** dims observed mid-resize. Layout that splits height/width across panes divides by pane count or
  subtracts chrome → `budget`/`avail` go negative or zero. Note existing `_flex_widths` already floors with
  `max(40, …)` (`top.py:350`) and `max(3, …)` (`top.py:366`) — the new layout must carry the same discipline.
  `_wrap_field` uses `max(20, width-7)` (`top.py:406`); `_fit` guards `width<=0` (`top.py:319`). New code that
  forgets these is where the crash lives. Docs are explicit that **writing the lower-right corner raises
  `curses.error` even on success** ("an exception to be raised after the string is printed"), and writing outside
  the window raises — so a 1-row terminal hitting the last cell *will* raise; the Surface must clip and the catch
  must stay.
- **Symptom:** `curses.error`, `ZeroDivisionError`, negative-width slice giving empty/garbled rows, or a
  `newwin(0, …)` of undocumented behavior (docs give **no** guarantee for zero/negative dims — treat as UB and
  never call it).
- **Guard:** clamp every derived dimension to `>= 1` (or skip the pane) *before* any `newwin`/`addstr`; never pass
  a computed dim to curses unchecked; Surface clips. Layout returns `[]` rects when there's no room.
- **Test:** property test over `width, height in product(range(0, 6), repeat=2)` plus the named geometries
  (1×N, N×1, 200×5, 30×120): assert `render_frame` **never raises** and **every emitted row length ≤ width**. This
  is the single highest-value new test and it needs the Surface seam to exist.

### 4. `--once` / non-tty plain path must survive the redesign (regression contract)
- **Trigger:** redesign moves rendering into panes but `run(...)` still branches to `render_once` for
  `once or not isatty()` (`top.py:504`). If panes become the only renderer, the piped/CI snapshot breaks.
- **Symptom:** `clu top --once | …` loses the stable single-snapshot contract asserted by
  `RenderOnceTest.test_writes_snapshot_to_stream` (`test_top.py:408`) — header + one line per worker to a stream.
- **Guard:** keep `render_once` as the canonical non-interactive path; it can render the compact pane through a
  `BufferSurface` flushed line-by-line to the stream, but its output contract (`"PLAN"` header, plan slug present)
  must not change. `format_rows`/`format_detail` stay pure and keep their existing tests.
- **Test:** keep every `FormatRowsTest`/`FormatDetailTest`/`RenderOnceTest` green unmodified; add a snapshot test
  asserting `BufferSurface`-rendered compact view == `format_rows` output at the same width.

### 5. SAYING sanitization regression in the new panes
- **Trigger:** `_clean` (`top.py:311`) collapses non-printable chars to spaces; every field flows through it today
  (`_row_cells` `top.py:333-340`, `format_detail` `top.py:419-430`, `_wrap_field` `top.py:406`). A new pane that
  renders `row["last_text"]` directly skips this. Embedded `\n` then splits a cell across rows; control chars /
  zero-width / RTL marks corrupt the grid; wide CJK + emoji make `len(text)` (counted) ≠ display columns, so a
  "fits" line overflows by N cells and trips the lower-right-corner `curses.error`.
- **Symptom:** grid corruption, mis-truncation, occasional crash on East-Asian/emoji-heavy SAYING.
- **Guard:** sanitize at the Surface boundary (`addstr` runs `_clean` on its text) so *no* pane can bypass it;
  width accounting must use display width, not `len`, for the wide-char case (a known follow-up — at minimum keep
  the catch so overflow degrades to a swallowed error, never a crash).
- **Test:** golden-snapshot the buffer for a row whose SAYING contains `"a\nb"`, `"\x07ctrl"`, `"日本語"*40`, and
  emoji; assert single-row containment and `len ≤ width` (counted), and that newlines became spaces.

## Secondary findings (trigger → symptom → guard → test)

6. **Resize storm / resize-during-render.** Rapid `KEY_RESIZE` between `getmaxyx` (`top.py:469`) and the write
   means dims are already stale. Symptom: writes past the new (smaller) bounds → `curses.error`. Guard: re-read
   `getmaxyx` immediately before drawing each frame, never cache across the getch boundary; rely on the Surface
   catch; do **not** call `resize_term` manually (docs: it blank-fills and the app must repaint — `KEY_RESIZE`
   from `getch` + a full re-`erase`/redraw, which the loop already does at `top.py:474`, is sufficient and
   simpler). Test: feed a script of shrinking `getmaxyx` values to `BufferSurface`; assert no raise.

7. **Terminal smaller than min layout.** Trigger: total pane minimums exceed height/width. Symptom: overlap or
   negative split. Guard: layout drops lowest-priority panes until the rest fit; if even the compact table's one
   row + hint won't fit, render a single `"terminal too small"` line. Test: layout at 3×3 returns a degraded
   single-pane plan.

8. **Focus model.** Tab with **zero** focusable panes (all dropped by #7) → modulo-by-zero on focus cycling;
   focus on a pane that disappeared after relayout → dangling focus index. Guard: focus is a pane **identity**,
   re-resolved each frame like selection (#1); `len(focusable)==0` → focus is `None`, Tab is a no-op. Test:
   relayout that removes the focused pane; assert focus degrades to first-focusable-or-None.

9. **Detail for a vanished selection / scroll past bounds.** Trigger: selected worker completes while detail pane
   open; or scroll offset exceeds content height. Symptom: empty/blank detail or negative slice. Guard: vanished
   selection → detail shows `"(worker finished)"` and the loop reverts to compact; clamp scroll to
   `[0, max(0, content_len - viewport)]`. Test: drive a snapshot where the detailed key drops out; assert the
   placeholder line and clamped offset.

10. **Empty fleet / single / many (scroll) / all-null worker.** `format_rows([])` keeps a header (`test_top.py:403`)
    and `format_detail([])` returns `"(no active workers)"` (`test_top.py:400`) — preserve both. A worker with no
    transcript yet has all-null activity (`test_top.py:317` proves the row still appears, fields `None` → `"—"`).
    Many workers must scroll the table pane, not overflow. Guard/test: viewport with offset + a selection-follows-
    scroll rule; property-test row count from 0..200.

11. **Unparseable / partial transcript.** Already handled in the data layer — `tail_records` drops truncated final
    lines (`test_top.py:182`), `extract_activity` tolerates unknown types / string content (`test_top.py:223`).
    The redesign must not move parsing into panes; keep `gather_rows` the single producer so these tests still
    cover it. Test: existing `ExtractActivityTest`/`TailRecordsTest` stay green unmodified.

12. **No-color / dumb `$TERM` / unset / non-unicode.** Glyphs `· … —` and `*` appear in `_row_cells`/`human_age`.
    Guard: gate color on `has_colors()` (docs: returns False when unsupported) and never `init_pair` blind; the
    locale set is already best-effort with a fallback (`top.py:460-462`). For non-UTF-8, the Surface catches the
    `addstr` of a non-encodable glyph; offer an ASCII fallback glyph set selected once at startup. Test: render
    with a `BufferSurface` flagged `ascii_only=True`; assert no multibyte glyph in output.

13. **Teardown on exception.** `curses.wrapper` restores cooked mode + echo + disables keypad on any exit, normal
    or exceptional (docs, verbatim). The per-pane error boundary (#2) means most pane failures never reach
    wrapper, so the terminal isn't even disturbed; only a layout/controller bug escapes — acceptable, wrapper
    cleans up. Test: a pane that raises does **not** propagate (asserted in #2); a controller that raises is
    allowed to and wrapper handles it (can't unit-test the real restore without a tty — document as out of unit
    scope, smoke-verify manually).

14. **Metric cost / stale cache.** Expensive transcript-derived metrics recomputed every tick × N workers × M
    panes is the runaway cost. `gather_rows` already does one transcript `tail_records` per worker per tick;
    fanning M panes over the same data must **not** re-read the file. Guard: compute the row dict once per tick
    (as today), pass the immutable snapshot to all panes — panes are pure functions of the snapshot, never I/O
    sources. If a pane wants a derived metric, memoize keyed by `(selection_key, last_activity_ts)` so an idle
    worker isn't recomputed. Test: a fake `gather_rows` counter asserts exactly one call per frame regardless of
    pane count; a memo test asserts no recompute when `last_activity_ts` is unchanged.

15. **Conflicting space claims.** Two panes each requesting >50% width. Guard: layout is the single authority —
    panes request *preferred* + *min*, layout allocates and clips; a pane never positions itself. Test: two greedy
    panes at fixed size produce non-overlapping rects covering ≤ the screen.

## Regression guardrails (must stay green or migrate)

- All of `tests/test_top.py` is pure-function over `format_rows`/`format_detail`/`gather_rows`/`extract_activity`/
  `locate_transcript`/`tail_records` — **none touches curses**. The redesign must keep these functions pure and
  these tests unchanged. New TUI tests are *additive*, via the `BufferSurface` seam.
- The `--once` stream contract (`RenderOnceTest`, `top.py:435-452`) is the public snapshot API for pipes/CI — do
  not alter its output shape.
- `gather_rows` stays the **single** data producer; panes consume its row dicts and never do I/O. This both bounds
  cost (#14) and keeps the existing end-to-end test (`GatherRowsTest`) authoritative.
