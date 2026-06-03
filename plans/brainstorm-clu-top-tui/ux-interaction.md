# clu top — TUI UX & Interaction Design

Read-only, btop-like master/detail view of autonomous workers. Constraints:
stdlib `curses`, zero deps, runs on macOS and **ssh from an iPhone** (~45 cols,
touch keyboard — no F-keys, no comfortable modifier chords).

Grounding: current single-table view lives in `top.py`; keys are `q` quit /
`w` detail toggle (`top.py:472,482-485`), glyphs `·`/`…`/`—`/`*` (`top.py:321,325,336`),
`*`=running command (`top.py:336`), `ok`/`dead` PID (`top.py:398`). Conventions
verified against btop, k9s, lazygit, htop (URLs at bottom). **F-keys are
disqualified** — htop documents them breaking over ssh/PuTTY, which is exactly
the phone path.

## 1. Selection model (master list)

- **Highlight** the selected row with a full-width reverse-video bar (`A_REVERSE`),
  plus a leading `▸` glyph (`>` in no-unicode mode) so it survives no-color
  terminals.
- **Move**: `↑/↓` and vim `j/k` (k9s, lazygit, btop all bind both). `PgUp/PgDn`
  page by visible-rows−1. `g`/`Home` → top, `G`/`End` → bottom (k9s `gg`/`G`).
- **Clamp, don't wrap.** htop/btop clamp at the ends; wrap is disorienting in a
  monitor where row position encodes nothing. Up at row 0 stays at row 0.
- **Default selection: sticky-by-identity, not by index.** On first paint, select
  row 0. On each ~1.5s refresh, re-resolve the selection to the row with the same
  `project/plan·phase` key. If that worker vanished, select the nearest surviving
  row by prior index (clamped). This is the single most important interaction
  decision: selection must never jump under the operator's cursor when an
  unrelated worker appears/completes (§8).

## 2. Focus model across panes

Adopt the **k9s/lazygit "master drives passive preview"** model: the master list
is *always* the input driver; the detail pane is a passive projection of the
selected row. No "active pane" bookkeeping in the common case — simplest model
that still feels btop-like (btop's process detail tracks the selected row the
same way).

One exception: when the detail body is taller than its pane (long SAYING / tail),
the user needs to scroll *it* without moving the selection. Bind that to a
**`Tab` focus toggle** (lazygit's panel-cycle key): Tab moves focus list↔detail;
while detail has focus, `j/k/↑↓/PgUp/PgDn` scroll the detail body and the focused
pane shows a `▌` title accent. `Esc` returns focus to the list. Two panes only,
so Tab toggles rather than cycles. List focus is the resting state; you can ignore
Tab entirely and never lose core functionality.

## 3. Keybinding set (phone-typeable, no chords)

| Key | Action |
|---|---|
| `↑ ↓` / `j k` | move selection (or scroll detail when focused) |
| `g G` / `Home End` | top / bottom |
| `PgUp PgDn` / `Ctrl-d Ctrl-u`* | page |
| `Tab` | toggle focus list ↔ detail pane |
| `Enter` | drill in — fullscreen detail for selected worker (esp. phone) |
| `Esc` | back / unfocus detail / close help / clear filter |
| `w` | **keep** — cycle layout: split → detail-only → compact-table |
| `/` | filter by project/plan/phase substring (k9s, htop `/`) |
| `?` or `h` | help overlay (btop, k9s, lazygit, htop all use `?`) |
| `q` | quit |

`Ctrl-d/u` is the one optional chord (vim half-page); arrows/PgUp are the
phone-friendly primaries, so the chord is never required. **No destructive keys
ever** (§10) — no kill, no signal, no `dd`. `/` opens a one-line input at the
status row; typing filters live, `Enter` commits, `Esc` clears. Honors de-facto
conventions exactly: nothing invented from memory.

`w` is retained but **repurposed from a boolean toggle to a layout cycle**, which
subsumes the current detail toggle (`top.py:472`) while giving phone users a
"detail-only" stop without the split overhead.

## 4. Detail pane content (selected worker)

Far more than the table row truncates. Top-to-bottom:

1. **Identity line**: `project/plan·phase` + `ok/dead` PID glyph.
2. **Metrics grid** (2 cols): `RAN · ACT · HB`, plus `tokens` in/out (`tokens`
   dict already on the row, `top.py:274`) and a derived idle/active state.
3. **COMMAND** — full, word-wrapped, `*` if running (reuse `_wrap_field`,
   `top.py:404`). No truncation.
4. **WROTE** — full file *path* (the table shows only `Path(...).name`,
   `top.py:338`) + age.
5. **SAYING** — the full last assistant line, word-wrapped. This is the headline
   reason the detail pane exists; the row truncates it hard.
6. **Transcript tail** — last ~6 assistant/tool lines (a `clu top`-side reduction
   of `tail_records`) as a mini-log, newest at bottom, dim styling.

Sections 5–6 can exceed the pane → **scrollable via the Tab-focused detail**
(§2). A `▲/▼` scroll affordance appears at top/bottom edges when clipped.

## 5. WIDE-SHORT geometry (coolant strip, 3–8 rows tall, wide)

**Vertical split: master list LEFT (~55% width), detail RIGHT.** Mirrors
lazygit/k9s/btop, which all keep the list left and the rich pane right. The list
keeps its existing flex columns minus SAYING (detail owns the full text); the
right pane shows identity + metrics + SAYING + a 1–2 line command/wrote.

When height is brutal (3 rows): collapse to **list-only with a one-line detail
ticker** pinned at the bottom — selected worker's SAYING scrolling in a single
row. Glanceable answer to "what is the selected worker saying *right now*"
without spending a column.

## 6. TALL-NARROW geometry (phone ssh, ~45 cols)

Width is the scarce axis, so **do not split horizontally**. Two modes:

- **List mode** (default): a compact stacked list. Per worker, 2 lines —
  line 1 `▸ project/plan·phase  RAN HB ok`, line 2 dim-indented SAYING truncated
  to width. `j/k` move; the selected worker's 2nd line shows more of SAYING.
- **Drill-in** (`Enter`): **fullscreen detail** for the selected worker — the
  full §4 stack, scrollable. `Esc` returns to the list with selection intact.
  This is the canonical phone pattern (btop/htop `Enter` = process detail); a
  side-by-side split is unreadable at 45 cols.

`Tab` is redundant on phone (drill-in *is* the focus), so it's a no-op there;
`Enter`/`Esc` carry the model. Geometry is chosen automatically from
`getmaxyx()`: width < ~80 → tall-narrow; height < ~10 → wide-short ticker;
else → wide-short split.

## 7. State indication (degrades to no-color)

| State | Color | Glyph (always present) |
|---|---|---|
| selected row | `A_REVERSE` bar | `▸` / `>` |
| focused pane | accent border char | `▌` title prefix |
| running cmd | green | `*` (existing, `top.py:336`) |
| live worker | default | `ok` (existing) |
| dead PID | red + `A_BOLD` | `dead` (existing) |
| stale heartbeat | yellow HB cell | — |
| idle (ACT old) | dim row | — |

**Glyph carries the meaning; color is redundant reinforcement.** On
`curses.has_colors()==False`, every state is still distinguishable by glyph +
reverse/bold/dim. Init colors defensively; never assume a palette.

## 8. Empty & transition states

- **No workers**: centered `no active workers · cron is watching` (not a blank
  screen). Reuse the `(no active workers)` string already in `format_detail`
  (`top.py:416`).
- **Worker completes while selected**: §1 sticky-by-identity catches it — the
  vanished worker's slot collapses, selection slides to the nearest survivor by
  prior index. Optionally hold a just-completed row for one refresh with a dim
  `✓ done` tag so the operator sees the transition rather than a silent
  disappearance.
- **Worker appears**: inserted in sorted order (stable sort by project/plan);
  selection stays pinned to its identity, so a new row never shoves the cursor.

## 9. Status / legend line

Bottom row, always present when height allows:
`q quit · /filter · ↑↓ select · Enter detail · ? help`. The current code already
reserves a hint row (`top.py:472`). In a 3-row strip there's no budget — drop the
legend and rely on `?` (htop/k9s keep help one keystroke away precisely so the
chrome can vanish). When a filter is active, the legend row becomes the filter
input/echo: `/heal_ ` then `filter: heal (3/8)`.

## 10. Discoverability + safety

- **Read-only is a hard invariant.** No key mutates worker or claim state — no
  kill, no signal, no lease edits. The UI observes; `clu` CLI acts. This is
  stated so a future contributor doesn't add a tempting `k`-kills-worker.
- **Discovery**: `?` from anywhere (universal convention), plus the persistent
  legend, plus a 3-second first-run toast `press ? for keys`. htop/k9s lean on
  an always-visible bar + `?`; we match.

## 11. Accessibility

- **No-color**: §7 — glyphs + attrs, never color-only.
- **Non-unicode**: a `--ascii` flag / `LANG`-detect maps `·→.  …→...  —→-
  ▸→>  ▌→|  ▲▼→^v  ✓→ok`. The locale guard already exists (`top.py:459-462`);
  extend it to pick the glyph table.
- **Narrow**: tall-narrow geometry (§6) is the 45-col contract; never assume ≥80.
- **No F-keys / minimal chords**: every primary action is a single unmodified
  key reachable on an iOS ssh keyboard.

---

### Sources (keybind conventions, verified this session)
- btop: ↑↓ select, Enter detail, `f` filter, `?`/`Esc` menu —
  https://www.terminal.guide/tools/system-monitor/btop/ ,
  https://itsfoss.com/btop-plus-plus/
- k9s: j/k + arrows, `gg`/`G`, `/` filter (regex/fuzzy), `?` help, Enter in /
  Esc back — https://k9scli.io/topics/hotkeys/ , https://k9salpha.io/topics/navigation/
- lazygit: Tab/1-5 panel focus, arrows+j/k in panel, Enter dive, Esc back, `?`
  help — https://lazygit.dev/keybindings/ , https://lazygit.dev/docs/guide/
- htop: F-keys **and** letter aliases (`h`/`?` help, `/` search, `\` filter);
  F-keys break over ssh/PuTTY — https://man7.org/linux/man-pages/man1/htop.1.html ,
  https://www.ezeelogin.com/kb/article/function-keys-in-htop-command-not-working-in-putty-380.html
