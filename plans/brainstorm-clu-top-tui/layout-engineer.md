# `clu top` — Adaptive Layout Engine

Scope: place modular panes given `(rows, cols)` for two opposite geometries — **wide-short** (strip under coolant) and **tall-narrow** (phone-over-ssh) — plus normal. Pane *contents* are another persona's job; this owns *placement*. Today's renderer is a single full-screen table (`top.py:464-490`), one `stdscr.erase()` + `addnstr` loop, no panes (`top.py:471-480`).

## 1. Core layout model — recommend: **binary split tree with min/preferred/flex**

Not a fixed slot map (can't adapt aspect), not a full flexbox solver (speculative generality for a tool with ≤4 pane kinds — violates KISS). A recursive split tree is the simplest thing that adapts: each node is either a leaf (one pane) or a split (`H`/`V`, list of children with weights). The engine walks the tree, subtracting 1-col/1-row separators, and assigns each leaf a `Rect(y,x,h,w)`.

A pane declares needs as a small dataclass — no inheritance, no protocol:
```
PaneSpec(min_h, min_w, pref_h, pref_w, flex_h=0, flex_w=0, drop_priority)
```
Allocation per split axis: give every child its `min` first; if total `min` > available → drop the highest `drop_priority` child and re-solve; distribute slack to `pref`, then remaining slack by `flex` weight. `aspect preference` is expressed implicitly — a pane that wants width sets `flex_w>0, flex_h=0`. That covers the two geometries without an aspect field.

## 2. Breakpoints vs continuous — **hybrid: discrete orientation, continuous sizing**

Pick the *tree shape* by breakpoint (orientation is genuinely bimodal — you either stack or you don't), then size leaves continuously via §1. Three regimes, chosen by `cols` and `rows`:

- **(C) Normal**: `cols ≥ 90 and rows ≥ 18` → list + detail side-by-side, status bar.
- **(A) Wide-short**: `rows < 12` (regardless of width) → horizontal strip; drop detail, drop status, list only, fewer columns as height shrinks.
- **(B) Tall-narrow**: `cols < 70 and rows ≥ 18` → vertical stack; list on top, detail below *or* detail as fullscreen toggle (§3).

Order of tests matters: check **wide-short first** (`rows<12` dominates — a 200×6 terminal is a strip even though it's wide), then tall-narrow, else normal. Breakpoints are constants in one dict, not scattered magic numbers.

## 3. Orientation flip — the exact rule

```
if rows < 12:                       regime A (strip): list-only, horizontal column squeeze
elif cols >= 90 and rows >= 18:     regime C: HSplit[ list(flex_w=2), detail(flex_w=3) ]
elif cols < 70:                     regime B: VSplit[ list(flex_h=1), detail(flex_h=2) ]
                                       └ if rows < 24: collapse detail → tab toggle ('w')
else:                               regime C-narrow: VSplit (stack) but keep status
```
Split horizontally when **width is the abundant axis** (`cols/rows` aspect > ~3.5 *and* `cols≥90`); vertically when height is abundant; collapse-to-tabs when *neither* axis affords two panes (`rows<24` in narrow). The existing `detail` boolean (`top.py:465,471`) already *is* the tab toggle — promote it to the collapse fallback rather than inventing a new mode.

## 4. Graceful degradation — the shrink order

Two independent shrink ladders:

**Pane drop order (by `drop_priority`, dropped first → last):** status bar → detail pane → (master list never drops; it's the product). A 3-row strip = list header + 1-2 data rows + nothing else.

**Column drop order inside the list pane (narrow width):** reuse the field priority already encoded in `_flex_widths` (`top.py:343-372`). Drop sequence as `cols` shrinks: `SAYING` (already the elastic remainder) → `WROTE` → `HB`/`PID` (the redundant liveness pair, keep one) → `ACT` → leaving `name + RAN + COMMAND` as the irreducible core. Below ~34 cols the list itself goes two-line-per-worker (name on line 1, command on line 2) before disappearing.

## 5. `curses` mechanics — recommend: **one `stdscr`, manual `Rect` regions, `derwin` per pane**

Verified against docs.python.org/3/library/curses.html:
- **`derwin(nlines,ncols,begin_y,begin_x)`** — window-relative origin ("relative to the origin of the window, rather than … the entire screen"). Use it: the engine produces `Rect`s in stdscr coords; `derwin` lets a pane draw at local `(0,0)`. Cleaner than `newwin` (screen-relative) for nested panes.
- **Avoid pads** unless a pane needs scroll beyond screen (a long worker list). `newpad` + 6-arg `pad.refresh(pminrow,pmincol,sminrow,smincol,smaxrow,smaxcol)` is the scroll mechanism; reserve it for the list pane only if/when row count exceeds height. KISS: ship without pads, add one pad for the list when scroll is needed.
- **Separators:** between H-split panes draw a `vline` in the 1-col gutter; between V-split, an `hline`. Cheaper than per-pane `border()` (4 sides × N panes) and reads cleaner in a strip.
- **Footguns (all confirmed in docs):**
  - *Lower-right corner:* "Attempting to write to the lower right corner … will cause an exception to be raised after the character is printed." → keep using `addnstr` and wrap in `try/except curses.error` exactly as `top.py:476-479` already does; never write the final cell of stdscr.
  - *Refresh order:* with N subwindows, call `noutrefresh()` on each then **one** `doupdate()` — "issuing `noutrefresh()` calls on all windows, followed by a single `doupdate()`" reduces flicker. A single `refresh()` per pane causes visible tearing.
  - *Overlap:* derwin children of the same parent must not overlap — the engine guarantees this by construction (splits subtract separators before allocating).

## 6. Resize handling — recompute the whole tree every frame

The loop already reads `getmaxyx()` each iteration (`top.py:469`) — keep that. On `getch()==KEY_RESIZE`, just re-enter the layout pass; the tree is cheap to rebuild (no persistent pane objects to invalidate). The HOWTO does not document `KEY_RESIZE`, but the practical contract: `getch` returns `curses.KEY_RESIZE`, and curses has already updated `getmaxyx`. Optionally call `curses.is_term_resized(rows,cols)` → `resize_term` to keep the bookkeeping exact, but rebuilding from `getmaxyx` alone is sufficient when we own every window. **Flicker control:** `erase()` (not `clear()`) each window per frame — `clear()` "cause[s] the whole window to be repainted upon next call to refresh," forcing a full redraw and flash; `erase()` lets curses diff. Recreate derwins after a resize (old Rects are stale); don't cache window objects across resizes.

## 7. `_flex_widths` reuse — **reuse verbatim, scoped to the list pane's width**

`_flex_widths` (`top.py:343-372`) is already a min/preferred/flex column solver — the same algorithm §1 applies to panes, one level down. The master-list pane *is* a mini column layout, so feed it `pane.w` instead of `maxx`. No rework: `format_rows(rows, width=pane.w)` already takes a width arg (`top.py:382`). The layout engine and the column allocator are the same idea at two scales — that's the DRY win, and it's real (≥2 call sites: pane-level + column-level), not coincidental.

## 8. Min viable size & fallback

Irreducible core = `name + RAN + COMMAND` ≈ **34 cols × 2 rows** (header + 1 worker). Below that, one-line fallback (no curses geometry): `clu top: N workers · M alive · WxH too small` — a single `addnstr(0,0,…)`. Define `MIN_COLS=34, MIN_ROWS=2`; below either, render the fallback string and skip the tree entirely.

## 9. Performance — full redraw is fine

Poll is ~1.5s (`top.py:467`); N is small (active workers, realistically <20). stdlib curses already diffs the virtual screen against physical in `doupdate` — that's the dirty-region tracking, for free. No manual dirty regions. The cost is `gather_rows` (file I/O over transcripts, `top.py:470`), not drawing. Don't optimize draw; if anything throttle `gather_rows` independently of the redraw cadence later.

## 10. Concrete layouts

**(C) Normal — 100×24, HSplit list|detail:**
```
PROJECT/PLAN·PHASE   RAN   ACT  HB  PID │ end-of-line/top-tui·p2
clu/layout·p1      4m02s   3s  3s  ok  │ RAN 4m02s · ACT 3s · ok
clu/notify ·p3     1m10s  12s 12s  ok  │ CMD * python -m unittest
HealthData/sync·p1 22s     1s  1s  ok  │ SAY Running the suite to
                                       │     confirm green before…
─────────────────────────────────────────────────────────────────
q quit · w detail · ↑↓ select
```

**(B) Tall-narrow — 46×40, VSplit (stack):**
```
PROJECT/PLAN·PHASE   RAN  ACT
clu/layout·p1      4m02s   3s
clu/notify·p3      1m10s  12s
HealthData/sync·p1   22s   1s
──────────────────────────────
▸ clu/layout·p1   RAN 4m02s ok
CMD * python -m unittest
SAY Running the suite to
    confirm green before…
──────────────────────────────
q · w toggle · ↑↓
```

**(A) Wide-short — 120×6 strip, list-only, columns squeezed:**
```
PROJECT/PLAN·PHASE   RAN  ACT HB PID COMMAND                 SAYING
clu/layout·p1      4m02s   3s 3s ok  * python -m unittest    Running suite…
clu/notify·p3      1m10s  12s 12s ok git status              Checking tree…
HealthData/sync·p1   22s   1s 1s ok  Edit sync.swift         Wiring poller…
q quit
```

## Sharpest forks
1. **Detail in tall-narrow: split vs fullscreen tab.** I recommend split when `rows≥24`, collapse to the existing `w`-toggle below that. Forces a selection concept (`↑↓`) that the current table lacks.
2. **derwin vs single-stdscr manual addressing.** I pick derwin for clean local coords + free clipping; the alternative (one stdscr, every pane offsets its own y/x) is fewer objects but leaks geometry into every pane's draw code.
3. **Pads now vs later.** Ship without; add one pad for the list pane only when worker count exceeds height. Don't build scroll speculatively.
