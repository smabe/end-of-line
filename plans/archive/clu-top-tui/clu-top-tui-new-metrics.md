# clu-top-tui-new-metrics — fused health glyph + new metrics, web parity

You are phase `new-metrics` of the `clu-top-tui` plan. Add the operator-ranked
new metrics, each in `top_registry.py` only (proving "one file, no engine
edits"), with thresholds matching `clu serve`'s JS. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/clu-top-tui.md`. Summary:

- **Fused health glyph** 🟢/🟡/🔴 from PID + HB + ACT + stuck-command (D8) — one
  signal, so the PID-ok-but-ACT-stale silent wedge can't be missed.
- **New metrics in `top_registry.py` only:** token-$/phase, attempts X/max,
  lease-remaining countdown, phase X-of-N.
- **Web threshold parity:** `act > 60` = warn (`index.html:238`) and the token
  sum (`index.html:217`) — match exactly so the two dashboards never disagree.

## Read first

- `web/index.html:217-247` — `tokenTotal` (token-sum math) + `toView` (health
  thresholds, `act > 60` → warn). Match these constants exactly.
- `top.py:255-275` — `assemble_row`: the row keys already available (`alive`,
  `heartbeat_age_seconds`, `last_activity_seconds`, `command_running`, `tokens`).
- `end_of_line/state.py` — source for attempts / lease / session-index. **Verify
  these are actually exposed before promising the metric** — grep for the claim's
  attempts and lease TTL fields; if a metric needs a field not in `assemble_row`,
  see the append-only rule below.
- `end_of_line/top_registry.py` — where each new metric registers.

## Produce

1. **Failing tests first.**
   - Each new metric's `compute`/`render` against a fixture row.
   - Fused health glyph maps PID/HB/ACT/stuck combos to the correct color
     (especially PID-alive + ACT-stale → 🟡/🔴, not 🟢).
   - Token-$ matches the JS sum for the same `usage` dict (parity test).
   - Threshold parity: `act = 61` → warn (matches `index.html:238`).

2. **Implementation.**
   - New metrics in `end_of_line/top_registry.py`.
   - **If a metric needs a row key not in `assemble_row`:** ADD it (append-only —
     never rename/drop an existing key, per D10) AND surface it to
     `web/index.html`'s `toView` so the two dashboards stay in parity.
   - Update `docs/operations.md` (`:211`), `docs/reference.md` (`:855`),
     `README.md`.

3. **Acceptance.**
   - All green; full suite (report count).
   - Each new metric was added with no engine edits — `git diff --stat` shows
     only `top_registry.py` (+ `assemble_row`/`toView` if a key was added) for
     the data path, not `top_layout.py`/the render loop. This is the proof the
     registry works.

4. **Commit + attest + complete.**
   - Commit: `clu-top-tui: phase new-metrics — fused health glyph + metrics`.
   - Stage explicit paths (include `web/index.html` only if you added a key).
   - After the commit: `clu verify` then `clu attest --simplify` (each
     `--plan clu-top-tui --phase new-metrics --token <T>`).
   - `clu complete --plan clu-top-tui --phase new-metrics --token <T>`.

## Failure modes to watch

- **Key added to the TUI only** → `clu serve` silently misses it. If you extend
  `assemble_row`, extend `toView` too (the parity rule).
- **Health thresholds drift from the JS** → the two dashboards disagree on a
  worker's health. Pin the constants; the parity test enforces it.
- **attempts/lease not actually in claim state** → the metric shows `—`. Verify
  `state.py` exposes them *before* writing the metric; if not, the metric is out
  of scope for this phase — note it, don't fake it.
