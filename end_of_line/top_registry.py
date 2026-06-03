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


# The 8 columns of today's flat table, in order. Each mirrors a slice of
# top._row_cells / top._row_line so the table pane's default render stays
# byte-identical (the pane delegates to format_rows; these power --cols subsets).
DEFAULT_COLS: tuple[str, ...] = ("name", "ran", "act", "hb", "pid", "cmd", "wrote", "saying")


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
    key="pid", label="PID", render=lambda v, w: f"{('ok' if v else 'dead'):>{w}}",
    cost="cheap", align="right", fixed_width=4,
)
def _m_pid(snapshot: Snapshot, row: dict) -> object:
    return row.get("alive")


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
    return _clean(row.get("last_text") or "—")


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
