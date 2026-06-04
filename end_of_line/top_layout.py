"""Layout engine for `clu top` (clu-top-tui Phase 2: layout-engine).

Places the dashboard's panes by terminal *width*, following the verified prior
art — htop, k9s, lazygit, fzf, btop all threshold on width, never aspect ratio
(lazygit's `portraitModeAutoMaxWidth: 84` is the canonical move). The ladder (D2):

    width ≥ 80      → split   (master list left | detail right)
    50 ≤ width < 80 → stacked (master list top  / detail bottom)
    width < 50      → master  (list only; Enter→fullscreen detail is Phase 3)
    height < 12     → strip   (wide-short dock: list only, glance density) — a
                              rows floor that wins over the width rungs
    w < 34, h < 2   → fallback (one crammed line for a pathological terminal)

Geometry is a binary split-tree (D1): a `Leaf` names a pane role, a `Split`
divides a `Rect` horizontally or vertically by a weight. Only ≤2 leaves exist
today, but the tree adapts to any geometry in a few lines of pure `Rect` math and
grows to more panes with no new branching — the reason D1 chose it over fixed
slots.

`AppState` is the small mutable bag panes read and write; selection and scroll
are wired in Phase 3, so the engine consumes only its `layout_preset` (the value
the `w` key cycles to force a preset instead of choosing one from geometry).
"""

from __future__ import annotations

from dataclasses import dataclass

from end_of_line.top_render import Rect

# The verified width ladder breakpoints (D2). Names, not magic numbers at the
# call sites, so the ladder reads top-to-bottom in choose_preset.
SPLIT_MIN_WIDTH = 80
STACKED_MIN_WIDTH = 50
STRIP_MAX_HEIGHT = 12          # height < this → strip, regardless of width
FALLBACK_MAX_WIDTH = 34
FALLBACK_MAX_HEIGHT = 2

# The presets a layout can resolve to. `master`/`strip`/`fallback` are list-only.
PRESETS = ("split", "stacked", "master", "strip", "fallback")

# Fraction of the body axis the master list gets; the detail pane takes the rest.
# Width when split L/R, height when stacked T/B.
_LIST_WEIGHT = 0.6

# What `w` cycles through: auto-from-geometry (None) then each meaningful preset
# as a manual override, back to auto. `fallback` is geometry-only — never a thing
# the operator would deliberately ask for — so it is left out of the cycle.
_PRESET_CYCLE = (None, "split", "stacked", "master", "strip")


def choose_preset(width: int, height: int) -> str:
    """Pick a layout preset from terminal geometry. Width-primary, with a rows
    floor (strip) that beats the width rungs and a tiny-terminal fallback."""
    if width < FALLBACK_MAX_WIDTH and height < FALLBACK_MAX_HEIGHT:
        return "fallback"
    if height < STRIP_MAX_HEIGHT:
        return "strip"
    if width >= SPLIT_MIN_WIDTH:
        return "split"
    if width >= STACKED_MIN_WIDTH:
        return "stacked"
    return "master"


def next_preset(current: str | None) -> str | None:
    """The next value for `w`'s preset override: auto → split → stacked → master
    → strip → auto. Anything off the cycle (e.g. a forced `fallback`) restarts at
    the first override."""
    idx = _PRESET_CYCLE.index(current) if current in _PRESET_CYCLE else -1
    return _PRESET_CYCLE[(idx + 1) % len(_PRESET_CYCLE)]


# --------------------------------------------------------------------------- #
# Split-tree — Leaf names a pane role, Split divides a Rect by a weight
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Leaf:
    """A terminal node: this region renders the pane named by `role`."""

    role: str


@dataclass(frozen=True)
class Split:
    """An internal node: divide a region into `first` and `second`.

    `orient` is `"h"` (side by side — divide width) or `"v"` (stacked — divide
    height). `weight` is the fraction of the divided axis given to `first`.
    `gap` is an empty divider channel (in cells) left between the two children so
    side-by-side panes don't visually run together."""

    orient: str
    first: "Leaf | Split"
    second: "Leaf | Split"
    weight: float
    gap: int = 1


def _place(node: "Leaf | Split", rect: Rect, out: dict[str, Rect]) -> None:
    """Recursively assign each `Leaf` a `Rect`, writing role → Rect into `out`.

    `first` then `second` abut across a `gap`-wide divider channel, so the two
    halves never overlap. `max(1, …)` keeps a split from starving `first` to
    zero; a 0-wide/short region (degenerate geometry) still yields a 0-size
    `Rect` the renderer simply skips, and the gap collapses when there is no room
    for it."""
    if isinstance(node, Leaf):
        out[node.role] = rect
        return
    if node.orient == "h":
        gap = node.gap if rect.w > node.gap + 1 else 0
        first_w = min(rect.w - gap, max(1, int((rect.w - gap) * node.weight)))
        _place(node.first, Rect(rect.x, rect.y, first_w, rect.h), out)
        second_x = rect.x + first_w + gap
        _place(node.second, Rect(second_x, rect.y, rect.w - first_w - gap, rect.h), out)
    else:
        gap = node.gap if rect.h > node.gap + 1 else 0
        first_h = min(rect.h - gap, max(1, int((rect.h - gap) * node.weight)))
        _place(node.first, Rect(rect.x, rect.y, rect.w, first_h), out)
        second_y = rect.y + first_h + gap
        _place(node.second, Rect(rect.x, second_y, rect.w, rect.h - first_h - gap), out)


def _body_tree(preset: str, drill: bool = False) -> "Leaf | Split":
    """The split-tree for a preset's body (everything between header and hint).

    `drill` only bites on the list-only `master` preset (the narrow `<50` cols
    phone case): Enter swaps the list for a fullscreen detail of the selected
    worker, Esc swaps back. The split/stacked presets already show a detail pane,
    so drill is a no-op there."""
    if drill and preset == "master":
        return Leaf("detail")
    if preset == "split":
        return Split("h", Leaf("list"), Leaf("detail"), _LIST_WEIGHT)
    if preset == "stacked":
        return Split("v", Leaf("list"), Leaf("detail"), _LIST_WEIGHT)
    return Leaf("list")  # master, strip — list only


@dataclass(frozen=True)
class Layout:
    """A resolved layout: the chosen `preset` and the role → `Rect` placement."""

    preset: str
    rects: dict[str, Rect]


class LayoutEngine:
    """Maps terminal geometry to pane `Rect`s via the width ladder + split-tree.

    Stateless — `layout(width, height, override=None)` is pure, so the property
    test drives every geometry without a terminal."""

    def layout(
        self, width: int, height: int, override: str | None = None, drill: bool = False
    ) -> Layout:
        if width <= 0 or height <= 0:
            return Layout("fallback", {})
        preset = override if override in PRESETS else choose_preset(width, height)
        if preset == "fallback":
            return Layout(preset, {"list": Rect(0, 0, width, height)})

        rects: dict[str, Rect] = {}
        top = 0
        bottom = height
        # Fleet header: top row, whenever a header + at least one body row fit.
        if height >= 3:
            rects["header"] = Rect(0, 0, width, 1)
            top = 1
        # Key hint: bottom row, whenever a row remains beneath the body.
        if bottom - top >= 2:
            bottom -= 1
            rects["hint"] = Rect(0, bottom, width, 1)
        body = Rect(0, top, width, max(0, bottom - top))
        _place(_body_tree(preset, drill), body, rects)
        return Layout(preset, rects)


def row_key(row: dict) -> tuple[str | None, str | None, str | None]:
    """A worker's stable identity `(project, plan, phase_id)` — the key the
    cursor sticks to across ticks. Mirrors the web's `wkey` (web/index.html:249),
    so the two dashboards re-resolve selection identically."""
    return (row.get("project"), row.get("plan"), row.get("phase_id"))


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@dataclass
class AppState:
    """Mutable state the panes share. The engine reads `layout_preset` (the `w`
    override) and `drill`, and stamps `geometry` each tick; the rest is the
    selection model.

    Selection is **sticky by identity** (D5): `selected_key` is the worker the
    cursor is bound to; `selected_index` is where it currently sits. Every tick
    `sync_selection` re-resolves the index from the key, so a worker that
    completes *above* the cursor never silently retargets it to a different
    worker — the #1 QA risk this whole model exists to kill. `focus` flips
    list↔detail (Tab) to scroll an overflowing detail; `scroll` is that pane's
    offset, reset whenever the selection changes so it never shows a stale
    position for a different worker; `drill` is the narrow-preset fullscreen."""

    selected_key: tuple[str | None, str | None, str | None] | None = None
    selected_index: int = 0
    geometry: tuple[int, int] = (0, 0)
    layout_preset: str | None = None
    focus: str = "list"
    scroll: int = 0
    drill: bool = False

    def sync_selection(self, rows: list[dict]) -> None:
        """Re-resolve the cursor against this tick's rows, by identity not index.

        If the bound worker is still present, follow it wherever it moved. If it
        dropped out, clamp the old index into the new (shorter) list. If the list
        emptied, clear the selection. Resetting the scroll on a key change keeps
        the detail pane from showing the prior worker's scroll offset."""
        if not rows:
            self.selected_key = None
            self.selected_index = 0
            return
        keys = [row_key(r) for r in rows]
        if self.selected_key in keys:
            self.selected_index = keys.index(self.selected_key)
        else:
            self.selected_index = _clamp(self.selected_index, 0, len(rows) - 1)
        self._restamp(rows)

    def move(self, delta: int, rows: list[dict]) -> None:
        """Move the cursor by `delta` rows, clamped (no wrap, per D5)."""
        self.move_to(self.selected_index + delta, rows)

    def move_to(self, index: int, rows: list[dict]) -> None:
        """Move the cursor to an absolute `index`, clamped to the row range."""
        if not rows:
            return
        self.selected_index = _clamp(index, 0, len(rows) - 1)
        self._restamp(rows)

    def _restamp(self, rows: list[dict]) -> None:
        """Bind `selected_key` to the row now under the cursor; reset the detail
        scroll if that changed which worker is selected."""
        key = row_key(rows[self.selected_index])
        if key != self.selected_key:
            self.scroll = 0
            self.selected_key = key

    def scroll_by(self, delta: int) -> None:
        """Scroll the focused detail pane, clamped at the top (the draw step
        clamps the bottom against the rendered line count)."""
        self.scroll = max(0, self.scroll + delta)

    def toggle_focus(self) -> None:
        """Tab: flip focus between the list and the detail pane."""
        self.focus = "detail" if self.focus == "list" else "list"

    def drill_in(self) -> None:
        """Enter: open the fullscreen detail (narrow preset)."""
        self.drill = True

    def drill_out(self) -> None:
        """Esc: leave the fullscreen drill first; otherwise drop detail focus."""
        if self.drill:
            self.drill = False
        elif self.focus == "detail":
            self.focus = "list"
