"""Rendering seam for `clu top` (clu-top-tui Phase 0).

`_run_curses`'s inner draw loop needed a live terminal, so it was untestable.
A `Surface` decouples *where* text goes from *what* gets drawn:

- `CursesSurface` wraps a real curses window — the production path. It bakes in
  today's bottom-right-cell reservation (width = `maxx-1`, height = `maxy-1`),
  sanitizes via `top._clean` at the boundary, truncates to width (mirroring the
  old `addnstr(..., maxx-1)`), and swallows the corner `curses.error`.
- `BufferSurface` records `addstr` calls in memory — the test path. It records
  the *requested* text untruncated, so a property test can drive the exact same
  draw code across every geometry and catch a row that would overflow.

`Rect` is pure geometry (no curses import) for the later layout engine.

Import direction (no cycle): this module imports the pure helper `_clean` from
`top` at module level; `top` imports this module lazily, inside `_run_curses`,
exactly as it already imports `curses`/`locale` there.
"""

from __future__ import annotations

from dataclasses import dataclass

from end_of_line.top import _clean


@dataclass(frozen=True)
class Rect:
    """A rectangular region — pure geometry, no curses. For the layout engine."""

    x: int
    y: int
    w: int
    h: int


class Surface:
    """Abstract drawing target: place text at `(y, x)` within `width`/`height`.

    Subclasses define `width`, `height`, and `addstr`. The two real backends
    deliberately differ in only one respect — `CursesSurface` truncates to the
    surface width (the real terminal would clip or raise), while `BufferSurface`
    records untruncated so over-width draws stay visible to tests.
    """

    @property
    def width(self) -> int:
        raise NotImplementedError

    @property
    def height(self) -> int:
        raise NotImplementedError

    def addstr(self, y: int, x: int, text: str) -> None:
        raise NotImplementedError


class CursesSurface(Surface):
    """Production `Surface` over a curses window.

    Reserves the bottom-right cell the way `_run_curses` always has: reported
    `width`/`height` are one less than the window's `getmaxyx()`, so writing the
    last column/row (where curses raises on cursor-advance) never happens. The
    `try/except curses.error` stays as a belt-and-suspenders for the corner.
    """

    def __init__(self, win) -> None:
        import curses

        self._win = win
        self._error = curses.error  # cached once, not re-imported per cell write
        maxy, maxx = win.getmaxyx()
        self._w = max(0, maxx - 1)
        self._h = max(0, maxy - 1)

    @property
    def width(self) -> int:
        return self._w

    @property
    def height(self) -> int:
        return self._h

    def addstr(self, y: int, x: int, text: str) -> None:
        if not (0 <= y < self._h) or x < 0:
            return
        cap = self._w - x
        if cap <= 0:
            return
        s = _clean(text)[:cap]
        try:
            self._win.addnstr(y, x, s, cap)
        except self._error:
            pass


class BufferSurface(Surface):
    """In-memory `Surface` for tests — records `(y, x, text)` per `addstr`.

    Faithful by design: it sanitizes (length-preserving) but does NOT truncate,
    so a row the draw code tried to write wider than `width` is recorded in full
    and a `len(text) <= width` assertion can catch it. Rows outside `height` are
    dropped, mirroring the draw loop's own row clip.
    """

    def __init__(self, width: int, height: int) -> None:
        self._w = width
        self._h = height
        self.cells: list[tuple[int, int, str]] = []

    @property
    def width(self) -> int:
        return self._w

    @property
    def height(self) -> int:
        return self._h

    def addstr(self, y: int, x: int, text: str) -> None:
        if not (0 <= y < self._h) or x < 0:
            return
        self.cells.append((y, x, _clean(text)))
