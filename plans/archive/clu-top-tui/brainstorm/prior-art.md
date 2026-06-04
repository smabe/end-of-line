# Prior Art: Terminal TUI Layout Patterns for `clu top`

Research date 2026-06-02. Every finding cites a URL. "VERIFIED" = I read the
linked doc/source. "Believed" = inferred from secondary sources only.

## 1. btop — declarative box layout + presets

**VERIFIED.** btop's layout is fully declarative in `btop.conf`. `shown_boxes`
lists which boxes render (`"cpu mem net proc"`, plus `gpu0`–`gpu5`). The
`presets` key holds up to **9 presets** (0–9); preset 0 always shows all boxes.
Format is `"box_name:P:G"` — P = position flag (0/1 for alternate placement),
G = graph symbol (`default`/`braille`/`block`/`tty`). Presets are
space-separated, boxes within a preset comma-separated, e.g.
`"cpu:1:default,proc:0:default cpu:0:block,net:0:tty"`. Cycle presets with the
`p`/`P` keys at runtime; edit via the Options (`O`) menu.
Process box: arrow keys (or vim `hjkl` when `vim_keys=true`) select a row;
pressing Enter/highlight opens a **detail view** for the selected PID;
`proc_follow_detailed=true` makes the list track the selection. Small terminals
fall back to TTY mode (`force_tty`), and btop hard-errors "Terminal size too
small" below ~80×24.
- Layout/presets: https://github.com/aristocratos/btop (README "presets")
- Config system: https://deepwiki.com/aristocratos/btop/2.3-configuration-system
- Min-size behavior: https://github.com/wezterm/wezterm/issues/6101

**Steal:** the `presets` string grammar is the cleanest precedent for
operator-defined pane layouts cycled by one key — fully declarative, no code.

## 2. k9s — declarative columns (views.yaml) + plugins (plugins.yaml)

**VERIFIED.** Two separate declarative mechanisms, both keyed off resource type:

*Custom columns* live in `$XDG_CONFIG_HOME/k9s/views.yaml`, keyed by GVR:
```yaml
views:
  v1/pods:
    sortColumn: AGE:asc
    columns:
      - AGE
      - NAMESPACE|WR        # attributes after |
      - "MEM/RL|S"
      - "ZORG:.metadata.labels.fred"   # JSONPath extraction
```
Column attributes: `W` wide-mode-only, `S` always-visible-not-wide, `H` hide,
`T` time, `N` number, `L`/`R` align, `|` separates name from attrs. Editable
live. — https://k9scli.io/topics/columns/

*Plugins* (`plugins.yaml`) bind a shortcut to a shell command per resource scope:
```yaml
fred:
  shortCut: Ctrl-L
  description: Pod logs
  scopes: [po]          # or 'all'
  command: kubectl
  background: false
  args: [logs, -f, $NAME, -n, $NAMESPACE]
```
Variables `$NAME`, `$NAMESPACE`, `$COL-<COLUMN>` interpolate the selected row.
— https://k9scli.io/topics/plugins/

*Navigation:* Enter drills into a resource, Esc goes back, `d` describe, `/`
filter. — https://k9scli.io/topics/columns/ (plus cheatsheet
https://ahmedjama.com/blog/2025/09/the-complete-k9s-cheatsheet/, secondary)

**Steal:** this is the **best modular-pane precedent.** Columns and actions are
declared in YAML keyed by entity type, with `$VAR` interpolation of the selected
row and per-attribute rendering hints (wide-only, hide, align) — exactly the
"add metrics without hardcoding" the operator wants. The `W`/`S` attributes are
a ready-made answer for narrow-terminal column dropping.

## 3. lazygit — responsive portrait flip (concrete threshold)

**VERIFIED, with exact numbers.** Side panels (Status/Files/Branches/Commits/
Stash) on the left, main diff/log on the right. Responsive behavior is driven by
documented config defaults:
- `portraitMode`: `auto` (default) | `always` | `never` — "Whether to stack UI
  components on top of each other."
- `portraitModeAutoMaxWidth: 84` — in auto mode, **stack vertically when window
  width ≤ 84 columns.**
- `mainPanelSplitMode: flexible` (default) — "split horizontally if the window
  is wide enough, otherwise split vertically."
- `sidePanelWidth: 0.3333` — fraction of width for the side column.
- `screenMode: normal|half|full` — focused-pane zoom, toggled with `+`/`_`.
— https://github.com/jesseduffield/lazygit/blob/master/docs/Config.md
(VERIFIED via raw Config.md)

**Steal:** the **84-column portrait threshold** and the `flexible` rule
("horizontal if wide enough, else vertical") are the single most directly
applicable adaptive-layout pattern, and they come from a battle-tested tool.
Plus the `screenMode` zoom-the-focused-pane idea for tiny terminals.

## 4. htop / top — field-management screens + selection

**VERIFIED.**
*htop:* `F2`/`S` opens Setup → Meters and Columns (Active vs Available lists;
`F7`/`F8` reorder, `F9` remove, Enter add). Meter widths are percent-of-window
(responsive). Selection: Up/Down or `Alt-j`/`Alt-k`; sort `F6`/`<`/`>`; help
`F1`/`h`/`?`; tag `Space`. — https://man7.org/linux/man-pages/man1/htop.1.html

*top:* `f`/`F` opens Fields-Management: Up/Down navigate, `Space`/`d` toggle a
field on/off (asterisk = shown, "screen width permitting"), Right arrow picks a
field to move, Left/Enter commits, `s` sets sort field.
— https://www.computerhope.com/unix/top.htm (secondary; man-page-derived)

**Steal:** the two-list "Active | Available" field picker with reorder keys is
the de-facto UX for letting users choose metrics interactively, complementing a
YAML default. "Asterisk shown, screen width permitting" matches narrow-terminal
auto-drop.

## 5. fzf — master/detail preview with conditional reflow

**VERIFIED.** `--preview-window` controls a list+preview split:
`up|down|left|right` (default `right`), size as `%` or absolute. Conditional
layout switches by terminal size inline:
`--preview-window 'right,border-left,<50(up,30%,border-bottom)'` — "if preview
width < 50 cols, switch to preview-above at 30% height." `<50(hidden)` hides the
preview entirely below threshold. `auto` picks position from terminal size.
— https://deepwiki.com/junegunn/fzf/4.1-preview-window-system

**Steal:** the `<N(alternate-spec)` grammar is a compact way to express "below N
columns, reflow/collapse the detail pane" — and `<N(hidden)` is the cleanest
"drop the detail pane on a phone" rule.

## 6. Adaptive layout — concrete documented thresholds

Three independent, citable threshold systems converge:
- **lazygit:** width ≤ 84 → vertical stack; else horizontal (VERIFIED, §3).
- **fzf:** width < N → reflow detail above / hide (VERIFIED, §5).
- **terminal.shop TUI** (Go/Bubbletea) defines an explicit breakpoint ladder
  (VERIFIED): `undersized` <20 cols or <10 rows → resize warning; `small`
  <50 cols → stacked layouts + simplified header; `medium` <80 cols → centered
  container, some simplifications; `large` ≥80 cols → full side-by-side panels.
  Content height = container − header − footer − breadcrumb − 2.
  — https://deepwiki.com/terminaldotshop/terminal/4.2-terminal-ui-(tui)

**Pattern:** all three key off **width** (not aspect ratio per se), with the
flip point clustering at **~50 (collapse) and ~80–84 (side-by-side)** columns.
None I verified flips by true aspect ratio; lazygit's `flexible` is the closest
("wide enough"). A defensible `clu top` rule: ≥80 cols → master-left/detail-
right; 50–80 → stacked master-over-detail; <50 → master only, detail on Enter as
full-screen; <20×10 → resize warning.

## 7. Modular / plugin panes — ranked precedents

**VERIFIED.** Best → weakest:
1. **k9s** (§2): YAML columns + YAML plugins, both scoped by entity, with
   `$VAR` row interpolation. Closest to "arbitrary metric, declared not coded."
2. **btop** (§1): `presets` string + `shown_boxes` — declarative *layout* of a
   fixed box set; metrics are not user-extensible, only arranged.
3. **lnav** (§8 below): log formats are JSON files in `~/.lnav/formats/`
   defining captured fields (string/int/float/json/timestamp), a `line-format`
   display array, plus `.sql` files that create helper **views/tables** run at
   startup — i.e. user-defined columns *and* derived metrics via SQL.
   — https://docs.lnav.org/en/latest/formats.html

**For clu top:** k9s `views.yaml` + `plugins.yaml` is the model to copy — a
`panes`/`columns` YAML keyed by entity (worker/plan/phase) with attribute hints
and `$VAR` interpolation; lnav shows how far you can go (SQL-derived metrics) if
that's ever wanted.

## 8. lnav — declarative formats + SQL-backed metrics

**VERIFIED.** Formats are `.json` files in `/etc/lnav/formats` or
`~/.lnav/formats/<dir>/`. Each defines value captures with types
(string/integer/float/json/quoted/timestamp), a boolean per field for display,
and a `line-format` array for layout. Co-located `.sql` files run at startup to
create helper **views/tables**; fields are queryable via `:fieldname`.
— https://docs.lnav.org/en/latest/formats.html
— source: https://github.com/tstack/lnav/blob/master/docs/source/formats.rst

## 9. Python stdlib curses — multi-pane building blocks

**VERIFIED (mechanism), believed (no canonical exemplar app found).** stdlib
gives `curses.newwin(h,w,y,x)` for sub-windows and the `curses.panel` stack
(overlapping windows, depth ordering — last refresh draws on top). **Critical
resize gotcha:** sub-windows created with `newwin()` do **not** auto-resize on
terminal change; on `KEY_RESIZE` you must `erase()` + `mvwin()` + `resize()` (or
`mvderwin()`) and redraw each pane yourself. `curses.is_term_resized()` /
`resize_term()` help detect/apply.
— https://docs.python.org/3/library/curses.html
— https://docs.python.org/3/howto/curses.html
I did **not** find a well-known pure-stdlib-curses multi-pane reference app to
copy (most modern Python TUIs use rich/textual/urwid, which the project bars).
The pattern to implement by hand: own a list of pane objects, each holding its
own `newwin`; a layout function computes rects from `(maxy, maxx)`; on
`KEY_RESIZE`, recompute rects and resize/move every pane; one "focused pane"
index drives input routing.

## 10. Phone / narrow-terminal (~45 col) behavior

**VERIFIED (thresholds), believed (45-col specifics).** No tool I verified
documents a hard 45-col target, but the breakpoints bracket it: at <50 cols
terminal.shop stacks everything and simplifies the header (§6); lazygit goes
portrait at ≤84 (§3); fzf can `<50(hidden)` the detail (§5); k9s `W`-attribute
columns and top's "width permitting" asterisks drop non-essential columns. So at
~45 cols the consensus move is: **single column, master list only, detail
reached via Enter as a full-screen swap, non-essential columns dropped.**
— https://deepwiki.com/terminaldotshop/terminal/4.2-terminal-ui-(tui)
— TUI-on-phone context: https://cosyra.com/guides/tui-apps-on-phone.html
  (secondary; confirms arrow keys + alternate-screen are the phone constraints)

## 11. De-facto keybindings (cross-tool, VERIFIED per tool)

| Action | Keys | Source |
|---|---|---|
| Select row | ↑/↓; `j`/`k` (vim); htop `Alt-j`/`Alt-k` | btop, htop, k9s |
| Drill into detail | **Enter** | k9s, btop |
| Back / cancel | **Esc** | k9s |
| Pane focus cycle | **Tab** (lazygit), arrows | lazygit panels |
| Help | **`?`** (also `F1`/`h`) | htop, k9s, fzf |
| Field/column mgmt | **`f`** (top), **`F2`** (htop) | top, htop |
| Sort | `F6` / `<` / `>` | htop |
| Filter/search | **`/`** | k9s, lnav |
| Cycle layout/preset | btop `p`/`P`; lazygit `+`/`_` zoom | btop, lazygit |
| Quit | `q` | universal (btop/htop/k9s) |

Sources: htop man page (https://man7.org/linux/man-pages/man1/htop.1.html);
top (https://www.computerhope.com/unix/top.htm); k9s columns/cheatsheet
(https://k9scli.io/topics/columns/); btop README
(https://github.com/aristocratos/btop); lazygit Config.md
(https://github.com/jesseduffield/lazygit/blob/master/docs/Config.md).
