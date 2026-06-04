"""Metric/Pane registry for `clu top` (clu-top-tui Phase 1).

`clu top` used to be one hardcoded 8-column table. This module turns each
column into a self-contained `Metric` and the table into a `Pane`, so a new
column or pane is added in *this* file — construct one and register it — with
no edits to the draw loop or the layout engine.

Two value types, two module dicts:

- `Metric` — a `(compute, render)` pair plus display metadata. `compute(snapshot,
  row)` pulls a value off the row dict; `render(value, width)` formats one cell.
  The split lets a metric sort by the raw value and lets a future cross-row
  metric reach the whole `Snapshot` without touching the renderer.
- `Pane` — a `kind`, the metric keys it shows, and a `render(snapshot, width,
  cols)` that returns lines. The one built-in `table` pane is byte-identical to
  `top.format_rows` for the default column set (it delegates straight to it);
  a `--cols` subset takes a small composition path instead.

`Snapshot` owns the single per-tick `gather_rows()` so a hidden metric costs
nothing — it is a per-tick value object, never cached across ticks (a stale
snapshot would show last tick's workers).

**The `gather_rows` row dict is a FROZEN wire contract (D10).** `clu serve`
reads the same 13 keys off `/api/workers` and renders them in its own JS, with
zero shared code. Every metric here reads FROM the row dict; none reshapes it.

Import direction (no cycle, mirrors `top_render`): this module imports pure
helpers from `top` at module level; `top` imports this one lazily, inside its
render functions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from end_of_line.top import (
    _FLEX_MAX,
    _clean,
    _fit,
    human_age,
    human_remaining,
)


# --------------------------------------------------------------------------- #
# Snapshot — the one per-tick gather every metric reads from
# --------------------------------------------------------------------------- #
class Snapshot:
    """One tick's worth of rows. Wraps `top.gather_rows` so the JSONL parse
    happens once; metrics read `.rows` and never re-gather. Build a fresh
    Snapshot each tick — it is deliberately not cached, so a metric can never
    serve a stale row."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    @classmethod
    def gather(cls, **kwargs) -> "Snapshot":
        from end_of_line.top import gather_rows

        return cls(gather_rows(**kwargs))

    @property
    def rows(self) -> list[dict]:
        return self._rows


# --------------------------------------------------------------------------- #
# Metric — one column as a (compute, render) value
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Metric:
    """A column: `compute(snapshot, row) -> value`, `render(value, width) -> cell`.

    `fixed_width` is set for the numeric columns (RAN/ACT/HB/PID) whose width
    never flexes; flex columns leave it None and cap their content at
    `max_width`. `align` is `"left"` | `"right"` (drives both the header and the
    cell). `cost` is `"cheap"` | `"transcript"` — informational today, a hook for
    later skipping the transcript parse when no transcript metric is visible."""

    key: str
    label: str
    compute: Callable[[Snapshot, dict], object]
    render: Callable[[object, int], str]
    sort_key: Callable[[object], object]
    cost: str
    align: str
    fixed_width: int | None = None
    max_width: int | None = None


METRICS: dict[str, Metric] = {}


def register_metric(
    *,
    key: str,
    label: str,
    render: Callable[[object, int], str],
    sort_key: Callable[[object], object] | None = None,
    cost: str = "cheap",
    align: str = "left",
    fixed_width: int | None = None,
    max_width: int | None = None,
) -> Callable[[Callable[[Snapshot, dict], object]], Metric]:
    """Decorator: turn a `compute(snapshot, row)` function into a registered
    `Metric`. The decorated name binds to the `Metric` (not the raw function)."""

    def deco(compute: Callable[[Snapshot, dict], object]) -> Metric:
        metric = Metric(
            key=key,
            label=label,
            compute=compute,
            render=render,
            sort_key=sort_key or (lambda v: v),
            cost=cost,
            align=align,
            fixed_width=fixed_width,
            max_width=max_width,
        )
        METRICS[key] = metric
        return metric

    return deco


def _left(value: object, width: int) -> str:
    return f"{_fit(str(value), width):<{width}}"


def _age_right(value: object, width: int) -> str:
    return f"{human_age(value):>{width}}"  # type: ignore[arg-type]


# The default flat-table columns, in order. Each mirrors a slice of
# top._row_cells / top._row_line so the table pane's default render stays
# byte-identical (the pane delegates to format_rows; these power --cols subsets).
# `progress` (PHASE) sits between pid and cmd, matching format_rows' fixed block.
DEFAULT_COLS: tuple[str, ...] = (
    "name", "ran", "act", "hb", "pid", "progress", "cmd", "wrote", "saying",
)


@register_metric(
    key="name", label="PROJECT/PLAN·PHASE", render=_left,
    cost="cheap", align="left", max_width=_FLEX_MAX["name"],
)
def _m_name(snapshot: Snapshot, row: dict) -> str:
    return _clean(f"{row.get('project', '?')}/{row.get('plan', '?')}·{row.get('phase_id', '?')}")


@register_metric(
    key="ran", label="RAN", render=_age_right, sort_key=lambda v: v if v is not None else -1,
    cost="cheap", align="right", fixed_width=7,
)
def _m_ran(snapshot: Snapshot, row: dict) -> object:
    return row.get("ran_seconds")


@register_metric(
    key="act", label="ACT", render=_age_right, sort_key=lambda v: v if v is not None else -1,
    cost="transcript", align="right", fixed_width=6,
)
def _m_act(snapshot: Snapshot, row: dict) -> object:
    return row.get("last_activity_seconds")


@register_metric(
    key="hb", label="HB", render=_age_right, sort_key=lambda v: v if v is not None else -1,
    cost="cheap", align="right", fixed_width=6,
)
def _m_hb(snapshot: Snapshot, row: dict) -> object:
    return row.get("heartbeat_age_seconds")


@register_metric(
    key="pid", label="PID", render=lambda v, w: f"{str(v):>{w}}",
    sort_key=lambda v: {"blk": 0, "dead": 1, "ok": 2}.get(str(v), 3),
    cost="cheap", align="right", fixed_width=4,
)
def _m_pid(snapshot: Snapshot, row: dict) -> object:
    # The same `blk`/`ok`/`dead` label the compact table uses (top._liveness_cell)
    # — blocked is checked before the dead path, so a `--cols pid` view and the
    # default view agree for a blocked row (alive=False but needs-you, not dead).
    from end_of_line.top import _liveness_cell

    return _liveness_cell(row)


@register_metric(
    key="cmd", label="COMMAND", render=_left,
    cost="transcript", align="left", max_width=_FLEX_MAX["cmd"],
)
def _m_cmd(snapshot: Snapshot, row: dict) -> str:
    run = "*" if row.get("command_running") else ""
    return _clean(run + (row.get("last_command") or "—"))


@register_metric(
    key="wrote", label="WROTE", render=_left,
    cost="transcript", align="left", max_width=_FLEX_MAX["wrote"],
)
def _m_wrote(snapshot: Snapshot, row: dict) -> str:
    w = row.get("last_write")
    return _clean(f"{Path(w).name} {human_age(row.get('last_write_seconds'))}" if w else "—")


@register_metric(
    key="saying", label="SAYING", render=_left,
    cost="transcript", align="left", max_width=_FLEX_MAX["saying"],
)
def _m_saying(snapshot: Snapshot, row: dict) -> str:
    # A blocked row has no `last_text`; carry the blocker question instead
    # (mirrors top._row_cells — the actionable thing the operator must answer).
    text = row.get("blocker_question") if row.get("blocked") else row.get("last_text")
    return _clean(text or "—")


# --------------------------------------------------------------------------- #
# Phase 4 metrics — fused health glyph + tokens/attempts/lease/progress.
#
# Each is added HERE alone (plus the row keys assemble_row/gather_rows surface)
# and becomes `--cols`-selectable with no layout or render-loop edit — the proof
# the registry works. The health classifier and token sum are pinned to
# web/index.html so the curses TUI and `clu serve` never disagree on a worker.
# --------------------------------------------------------------------------- #

# Pinned to web/index.html:238 (`act > 60` → warn). The fused glyph adds two
# corroborating wedge signals the flat PID/HB/ACT clocks made you AND by eye
# (D8): an explicit tool-stuck marker, and a heartbeat loop that has gone silent
# past the 25-min stalled ceiling (state.STALLED_HEARTBEAT_MIN_CEILING). Both
# are mirrored into toView so the web classifies health identically.
_ACT_WARN_SECONDS = 60
_HB_WARN_SECONDS = 25 * 60

# Glyph-first (color is redundant reinforcement in the curses attr layer): a
# filled / half / cross circle, same family as the existing `▸` cursor. On a
# no-unicode terminal these degrade the way the cursor already does.
# `!` (blocked) is amber "needs-you" — distinct from red `✗` (dead): a blocked
# plan is waiting on the operator, a dead one's work died. They must read
# differently at a glance.
_HEALTH_GLYPH = {"ok": "●", "warn": "◐", "dead": "✗", "blocked": "!"}


def worker_health(*, alive: bool, act: float | None, hb: float | None, stuck: bool) -> str:
    """Fuse PID + ACT + HB + stuck-command into one `"ok" | "warn" | "dead"`
    state (D8). `dead` (no live PID) dominates; otherwise any wedge signal —
    a stale/absent ACT (the silent-wedge the web's `act > 60` catches), an
    explicit stuck-tool marker, or a heartbeat loop silent past the ceiling —
    tips an otherwise-green worker to `warn`."""
    if not alive:
        return "dead"
    if (
        stuck
        or act is None
        or act > _ACT_WARN_SECONDS
        or (hb is not None and hb > _HB_WARN_SECONDS)
    ):
        return "warn"
    return "ok"


def token_total(usage: object) -> int | float | None:
    """Sum one `usage` dict's flat numeric values — byte-for-byte the rule in
    web/index.html:218 `tokenTotal`. A scalar passes through; a nested dict
    value (e.g. `cache_creation`) is skipped exactly as the JS skips non-number
    values; an empty/None/non-dict yields None so the cell shows `—`."""
    if usage is None:
        return None
    if isinstance(usage, bool):  # bool is an int subclass in Python; JS treats it as non-number
        return None
    if isinstance(usage, (int, float)):
        return usage
    if not isinstance(usage, dict):
        return None
    total: int | float = 0
    seen = False
    for v in usage.values():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total += v
            seen = True
    return total if seen else None


def token_human(n: int | float | None) -> str:
    """Compact token count — mirrors web/index.html:209 `tnum` (`1.25M` / `45K`
    / raw). Half-up rounding on the K rung matches JS `Math.round`."""
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{int(n / 1_000 + 0.5)}K"
    return str(int(n))


@register_metric(
    key="health", label="H", render=lambda v, w: f"{_HEALTH_GLYPH.get(str(v), '?'):<{w}}",
    sort_key=lambda v: {"blocked": -1, "dead": 0, "warn": 1, "ok": 2}.get(str(v), 3),
    cost="cheap", align="left", fixed_width=1,
)
def _m_health(snapshot: Snapshot, row: dict) -> str:
    # Blocked is checked FIRST — a blocked row has `alive=False`, so the
    # 4-signal `worker_health` fusion (kept pure) would otherwise call it dead.
    # A plan waiting on the operator is needs-you (amber), not work-died (red).
    if row.get("blocked"):
        return "blocked"
    return worker_health(
        alive=bool(row.get("alive")),
        act=row.get("last_activity_seconds"),
        hb=row.get("heartbeat_age_seconds"),
        stuck=bool(row.get("stuck")),
    )


@register_metric(
    key="tokens", label="TOKENS", render=lambda v, w: f"{token_human(v):>{w}}",
    sort_key=lambda v: v if v is not None else -1,
    cost="transcript", align="right", fixed_width=8,
)
def _m_tokens(snapshot: Snapshot, row: dict) -> int | float | None:
    return token_total(row.get("tokens"))


def _render_pair(value: object, width: int) -> str:
    """`X/Y` for an `(x, y)` metric, `—` when either side is unknown."""
    x, y = value if isinstance(value, tuple) else (None, None)
    cell = f"{x}/{y}" if x is not None and y is not None else "—"
    return f"{cell:>{width}}"


@register_metric(
    key="attempts", label="ATT", render=_render_pair,
    sort_key=lambda v: v[0] if isinstance(v, tuple) and v[0] is not None else -1,
    cost="cheap", align="right", fixed_width=5,
)
def _m_attempts(snapshot: Snapshot, row: dict) -> tuple:
    return (row.get("attempts"), row.get("max_attempts"))


def _render_lease(value: object, width: int) -> str:
    """Lease countdown — delegates to `top.human_remaining` so the metric and
    `format_detail`'s detail-pane LEASE line render the countdown identically."""
    return f"{human_remaining(value):>{width}}"  # type: ignore[arg-type]


@register_metric(
    key="lease", label="LEASE", render=_render_lease,
    sort_key=lambda v: v if v is not None else float("inf"),
    cost="cheap", align="right", fixed_width=6,
)
def _m_lease(snapshot: Snapshot, row: dict) -> object:
    return row.get("lease_remaining_seconds")


@register_metric(
    key="progress", label="PHASE", render=_render_pair,
    sort_key=lambda v: v[0] if isinstance(v, tuple) and v[0] is not None else -1,
    cost="cheap", align="right", fixed_width=5,
)
def _m_progress(snapshot: Snapshot, row: dict) -> tuple:
    return (row.get("phase_index"), row.get("phase_total"))


# --------------------------------------------------------------------------- #
# Pane — a kind, its metric keys, and a render returning lines
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Pane:
    kind: str
    metric_keys: tuple[str, ...]
    render: Callable[..., list[str]]


PANES: dict[str, Pane] = {}


def register_pane(*, kind: str, metric_keys: tuple[str, ...]) -> Callable[[Callable[..., list[str]]], Pane]:
    """Decorator: register a `render(snapshot, *, width, cols=None) -> lines`
    function as a `Pane`."""

    def deco(render: Callable[..., list[str]]) -> Pane:
        pane = Pane(kind=kind, metric_keys=tuple(metric_keys), render=render)
        PANES[kind] = pane
        return pane

    return deco


def safe_render(pane: Pane, snapshot: Snapshot, *, width: int, cols: tuple[str, ...] | None = None) -> list[str]:
    """Per-pane error boundary: a pane whose render raises becomes a single
    inline error band, so one bad pane never crashes the TUI and its siblings
    still draw."""
    try:
        return pane.render(snapshot, width=width, cols=cols)
    except Exception as exc:  # noqa: BLE001 — the boundary is the point
        return [_fit(f"[{pane.kind} pane error: {exc}]", width)]


def _header_cell(metric: Metric, width: int) -> str:
    label = _fit(metric.label, width)
    return f"{label:>{width}}" if metric.align == "right" else f"{label:<{width}}"


def _subset_widths(metrics: list[Metric], width: int) -> dict[str, int]:
    """Column widths for a `--cols` subset: fixed columns keep their width, flex
    columns split whatever is left after the fixed columns and single-space
    gaps. Minimal by design — content-aware flex fitting is the layout engine's
    job; this just shows the chosen columns within the terminal width."""
    gaps = max(0, len(metrics) - 1)
    fixed_total = sum(m.fixed_width for m in metrics if m.fixed_width is not None)
    flex = [m for m in metrics if m.fixed_width is None]
    remaining = max(0, width - fixed_total - gaps)
    per_flex = remaining // len(flex) if flex else 0
    widths: dict[str, int] = {}
    for m in metrics:
        if m.fixed_width is not None:
            widths[m.key] = m.fixed_width
        else:
            cap = m.max_width if m.max_width is not None else per_flex
            widths[m.key] = max(0, min(per_flex, cap))
    return widths


@register_pane(kind="table", metric_keys=DEFAULT_COLS)
def _table(snapshot: Snapshot, *, width: int, cols: tuple[str, ...] | None = None) -> list[str]:
    """The compact worker table. With no `cols` (or the full default set) it is
    byte-identical to `top.format_rows`; a `cols` subset renders only the named
    metrics."""
    from end_of_line.top import format_rows

    rows = snapshot.rows
    if not cols or tuple(cols) == DEFAULT_COLS:
        return format_rows(rows, width=width)

    metrics = [METRICS[k] for k in cols]
    widths = _subset_widths(metrics, width)
    header = " ".join(_header_cell(m, widths[m.key]) for m in metrics)
    out = [header[:width]]
    for row in rows:
        cells = [m.render(m.compute(snapshot, row), widths[m.key]) for m in metrics]
        out.append(" ".join(cells)[:width])
    return out


def fleet_summary(rows: list[dict], width: int) -> str:
    """The one-line fleet header: `N running · N blocked · N dead · oldest-ACT Xm`.

    Mirrors `web/index.html` `header()` — counts come straight from the snapshot
    rows, the same row dicts `clu serve` renders. The `blocked` count surfaces
    plans waiting on the operator: `clu block` releases the claim, but
    `gather_rows` reads the persisting blocker back into a claimless blocked row
    (clu-dashboard-blocked). A blocked row has `alive=False`, so it must be
    pulled out of the dead bucket explicitly — otherwise the most actionable
    state hides inside the dead count."""
    if not rows:
        return _fit("no active workers", width)
    alive = [r for r in rows if r.get("alive")]
    running = len(alive)
    blocked = sum(1 for r in rows if r.get("blocked"))
    dead = len(rows) - running - blocked
    acts = [r.get("last_activity_seconds") for r in alive if r.get("last_activity_seconds") is not None]
    oldest = human_age(max(acts)) if acts else "—"
    return _fit(f"{running} running · {blocked} blocked · {dead} dead · oldest-ACT {oldest}", width)


@register_pane(kind="header", metric_keys=())
def _header(snapshot: Snapshot, *, width: int, cols: tuple[str, ...] | None = None) -> list[str]:
    """Fleet-summary header pane — one line of fleet-wide counts."""
    return [fleet_summary(snapshot.rows, width)]


@register_pane(kind="detail", metric_keys=DEFAULT_COLS)
def _detail(snapshot: Snapshot, *, width: int, cols: tuple[str, ...] | None = None) -> list[str]:
    """Detail pane — full, word-wrapped per-worker blocks. Phase 3 makes it
    selection-aware (the selected worker, full untruncated SAYING + transcript
    tail); today it mirrors `top.format_detail` for the whole fleet so the split
    and stacked geometries have something real to render."""
    from end_of_line.top import format_detail

    return format_detail(snapshot.rows, width=width)


# --------------------------------------------------------------------------- #
# --cols parsing — validated against the registered metric keys
# --------------------------------------------------------------------------- #
def metric_keys() -> tuple[str, ...]:
    """The known metric keys `--cols` validates against (default-column order
    first, then any extras)."""
    extras = tuple(k for k in METRICS if k not in DEFAULT_COLS)
    return DEFAULT_COLS + extras


def parse_cols(spec: str) -> tuple[str, ...]:
    """Parse a `--cols a,b,c` spec into validated metric keys. Raises
    `ValueError` on an empty spec or an unknown key — the CLI turns that into a
    clean argparse usage error."""
    keys = tuple(part.strip() for part in spec.split(",") if part.strip())
    if not keys:
        raise ValueError("--cols requires at least one metric key")
    known = set(metric_keys())
    for key in keys:
        if key not in known:
            raise ValueError(f"unknown column '{key}' (known: {', '.join(metric_keys())})")
    return keys
