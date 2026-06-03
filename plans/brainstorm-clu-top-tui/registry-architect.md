# `clu top` — pane/metric registry architecture

Grounded in `end_of_line/top.py` (current functions: `gather_rows` :278,
`extract_activity` :174, `assemble_row` :255, `format_rows` :382,
`format_detail` :412) and the existing `registry.py` `@dataclass(frozen=True)`
entry pattern (:24-28).

## 1. Metric abstraction — frozen dataclass, PULL

A metric is a named pure value over one worker row (and optionally the fleet).
Model it as a `@dataclass(frozen=True)` holding the `key`/`label` plus two
plain callables — *not* a `Protocol`. Protocols describe *types you implement*;
a metric is *data + functions*, and a dataclass instance registered by key is
the smaller mental model (KISS). Verified: `typing.Protocol` is for structural
subtyping of classes you write, and `@runtime_checkable` only checks attribute
*presence*, not signatures
(https://docs.python.org/3/library/typing.html#typing.Protocol,
"runtime_checkable" para) — overkill for a value-with-two-functions.

```python
from dataclasses import dataclass, field
from typing import Callable, Any

@dataclass(frozen=True)
class Metric:
    key: str                                   # "ran", "cost"
    label: str                                 # column header / kv label
    compute: Callable[["Snapshot", dict], Any] # (snapshot, worker_row) -> value
    render: Callable[[Any, int], str]          # (value, width) -> cell text
    sort_key: Callable[[Any], Any] = lambda v: (v is None, v)
    cost: str = "cheap"                         # "cheap" | "transcript"
    align: str = "left"                         # "left" | "right"
```

`compute` takes the per-tick `Snapshot` (§4) so a fleet-derived metric (e.g.
"this worker's tokens as % of fleet") can see siblings; row-only metrics ignore
it. **PULL, not push.** The engine, when rendering a pane, pulls
`metric.compute(snapshot, row)` for exactly the metrics that pane declares. A
push bus would force every producer to fire every tick whether or not any
visible pane consumes it — wasted transcript parsing for a hidden column. PULL
makes "what's computed" a function of "what's on screen," which is the whole
point of a configurable layout. The `cost` hint lets the engine skip/defer
`transcript` metrics when no visible pane needs them.

## 2. Pane abstraction — frozen dataclass with a render fn

```python
@dataclass(frozen=True)
class Pane:
    key: str
    kind: str                       # "table" | "detail" | "text"
    metric_keys: tuple[str, ...]    # which Metrics it shows, by key
    render: Callable[["Region", "AppState", "Snapshot"], None]
    focusable: bool = False
    min_height: int = 1
```

A **table** pane = an ordered list of column metric keys; its `render` reuses
the existing flex allocator (`_flex_widths` :343 generalised over declared
columns). A **detail** pane = vertical key/value over `metric_keys` for the
selected worker. A **text** pane = one free-text metric (full SAYING) wrapped
via the existing `_wrap_field` (:404). `render` writes into a `Region` (§9), an
abstraction over curses *and* an in-memory grid, so panes never import curses.
Size hints stay minimal (`min_height`); the layout engine owns geometry.

## 3. Registration — decorator + dict, optional plugin dir

Cleanest for a bundled stdlib tool: two module-level dicts and decorators, in a
new `top_registry.py`.

```python
METRICS: dict[str, Metric] = {}
PANES: dict[str, Pane] = {}

def register_metric(m: Metric) -> Metric:
    if m.key in METRICS: raise ValueError(f"dup metric {m.key}")
    METRICS[m.key] = m; return m

def register_pane(p: Pane) -> Pane:
    PANES[p.key] = p; return p
```

Built-ins register at import of `top_metrics.py` / `top_panes.py`. **User
plugins:** optionally scan `clu_config_dir()/top_plugins/` (the XDG dir from
`_xdg_guard.py`, commit 98049b0) with `pkgutil.iter_modules([plugin_dir])` +
`importlib.import_module`; each imported module calls `register_metric`/
`register_pane` as a side effect. Verified: `pkgutil.iter_modules(path, prefix)`
yields `ModuleInfo` for submodules on the given path
(https://docs.python.org/3/library/pkgutil.html#pkgutil.iter_modules) — this is
the standard stdlib discovery primitive, no `importlib.metadata` entry-points
(those need installed distributions, wrong fit for a drop-a-file plugin dir).
**YAGNI gate:** ship the decorator+dict now; the plugin-dir scan is ~10 lines
behind a flag — implement only when a second consumer asks. No entry-points, no
namespace packages.

## 4. Decoupling data from presentation — the Snapshot

`gather_rows()` (:278) stays the *data* layer but is split: keep cheap claim
joins inline; wrap the per-tick result in a `Snapshot` that owns the **single**
transcript parse so panes never re-read JSONL.

```python
@dataclass
class Snapshot:
    rows: list[dict]                 # cheap fields from assemble_row (:255)
    records: dict[str, list[dict]]   # worker_id -> tail_records() once per tick
    now: datetime
    _cache: dict = field(default_factory=dict)  # memoize transcript metrics
```

One `Snapshot` is built per tick and passed to every pane. Today
`extract_activity` (:174) already does one parse per worker; the Snapshot just
makes that parse the *shared* source for all `transcript`-cost metrics, keyed in
`_cache` so two panes showing token+cost parse the file zero extra times. Data
(gather/parse) and presentation (compute/render) are now separate files.

## 5. Migrating today's columns into registered metrics (dogfood)

Every current column becomes a built-in `Metric`, proving the registry by the
built-ins themselves:

```python
register_metric(Metric("ran", "RAN",
    compute=lambda s, r: r.get("ran_seconds"),
    render=lambda v, w: human_age(v), sort_key=lambda v:(v is None,v),
    cost="cheap", align="right"))
register_metric(Metric("saying", "SAYING",
    compute=lambda s, r: r.get("last_text"),
    render=lambda v, w: _fit(_clean(v or "—"), w), cost="transcript"))
# ran, act, hb, pid(alive), name, cmd(+running *), wrote, saying
```

`format_rows` (:382) becomes the `render` of a built-in `compact` table pane
whose `metric_keys` are exactly `(name, ran, act, hb, pid, cmd, wrote, saying)`
— identical output, now data-driven. `format_detail` (:412) becomes the
`detail`/`text` panes. No behaviour change; the existing `test_top.py` asserts
the migration preserved output byte-for-byte.

## 6. Shared selection — AppState

```python
@dataclass
class AppState:
    selected_worker: str | None = None   # worker id (project/plan·phase)
    detail_open: bool = False
    cols_spec: dict | None = None        # §7
```

One `AppState` instance, owned by the loop, passed to every pane's `render`.
The master table pane writes `selected_worker` on cursor move; the detail/text
panes read it and render that worker from the same `Snapshot`. Panes never talk
to each other — they share state, not references.

## 7. Config binding — declarative spec → metric keys

A layout is a list of pane specs; each names a registered pane key and, for
tables, an ordered metric-key list with per-column width hints. This *is* the
generalised form of the in-flight `--cols` idea
(`plans/clu-top-column-sizing.md`): that plan's `name:40`/`saying:hide`/
`cmd:full` tokens map directly onto a metric key + a width sentinel
(`hide`/`full`/int-floor). The colspec validator checks tokens against
`METRICS.keys()` instead of a hardcoded set — so a user-added metric is
`--cols`-addressable for free.

```python
LAYOUT = [{"pane": "compact",
           "cols": ["name", "ran:full", "saying:hide", "cost"]}]
```

Stored under `clu_config_dir()/top.json` (the persistence the column-sizing
plan already specs), validated against the registry at load, tolerant of
unknown keys (drop with a warning, never crash — matches that plan's
corrupt-file fallback).

## 8. The YAGNI line — what NOT to build

The simplest registry that delivers "add a metric/pane in one place, no engine
edits" is: **two dicts + two decorators + a Snapshot + an AppState.** That's it.
Do **not** build: a push/event bus; a plugin *sandbox* or capability system
(the operator owns what runs — same trust model as workers, per project
`CLAUDE.md`); hot-reload / file-watching of plugins; `importlib.metadata`
entry-points; metric dependency graphs / DAG scheduling; async compute; a
DSL beyond the existing `--cols` token grammar. A pane is a dataclass with a
render function, full stop. The plugin-dir scan (§3) is the *one* deferred
extension, gated on a real second consumer.

## 9. Testability without curses

A metric is two pure functions: `compute(snapshot, row)` and `render(value,
width)` — unit-test each in isolation with a hand-built `Snapshot` and fake
`row` dict (the existing `test_top.py` factory style). For panes, introduce a
`Region` with a tiny in-memory backend:

```python
class GridRegion:
    def __init__(self, h, w): self.cells = [[" "]*w for _ in range(h)]; ...
    def write(self, y, x, text): ...   # bounds-clamped, mirrors addnstr
```

`pane.render(GridRegion(h,w), state, snapshot)` produces a grid the test asserts
on as joined strings — zero curses, fully deterministic. The curses `Region`
adapter (wrapping `addnstr`, the only curses call) stays thin and untested, same
discipline the column-sizing plan applies to `_run_curses` (:455).

## 10. Worked example — add a "token cost" pane, no engine edits

One new file, `~/.config/clu/top_plugins/cost.py` (or a built-in in
`top_metrics.py`), zero edits to the loop, allocator, or Snapshot:

```python
from end_of_line.top_registry import register_metric, register_pane, Metric, Pane

# pricing per Mtoken; cheap arithmetic over the usage dict the transcript
# already yields (extract_activity :199 reads message.usage).
def _cost(snapshot, row):
    u = row.get("tokens") or {}
    inp = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
    out = u.get("output_tokens", 0)
    return inp/1e6*3.0 + out/1e6*15.0      # USD

register_metric(Metric("cost", "COST",
    compute=_cost,
    render=lambda v, w: f"${v:,.2f}" if v else "—",
    sort_key=lambda v: v or 0.0, cost="cheap", align="right"))

register_pane(Pane(
    key="cost_panel", kind="detail", metric_keys=("name", "cost", "saying"),
    render=detail_render, focusable=False))   # detail_render is the shared built-in
```

Then `clu top --cols "name,cost:full,saying:hide"` shows it, or add
`{"pane":"cost_panel"}` to `top.json`. The engine learns about `cost` purely
through `METRICS["cost"]` at registration — no `format_rows`, `_flex_widths`,
`gather_rows`, or `assemble_row` edit. That is the proof of "no hardcoding."
