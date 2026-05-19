# lease-reliability — orphan-reap + Effort-scaled leases (closes #57, #58)

Two follow-ups from the icloud-container-sync incident (2026-05-19),
batched into one master because both touch the lease/dispatch path on
`state.py` and serializing them shares one worktree.

Phase order is deliberate: orphan reaping ships first (#57, P0 data
integrity) so that any subsequent lease change can't widen the
two-workers-one-worktree window. Lease scaling from declared Effort
ships second (#58, P1) so non-trivial phases stop hitting the 30min
wall.

The incident receipts live in #57 and #58. Sister issues #59 (real
heartbeats), #60 (worktree handoff context), #61 (verify opt-out)
ship in subsequent batches and are NOT in scope here.

## Locked design decisions

### Phase 1 — reap-core (#57)
- **New helper:** `state.reap_orphan_pid(pid: int, cmdline_match: str | None = None) -> ReapResult` (returns dataclass with `signaled`, `escalated_kill`, `cmdline_mismatch`).
- **Signal sequence:** `os.kill(pid, SIGTERM)` → poll `os.kill(pid, 0)` every 250ms for up to 5s → if still alive, `os.kill(pid, SIGKILL)`. Do NOT use `os.waitpid(WNOHANG)` — we never forked the PID; `WNOHANG` returns `ECHILD`.
- **PID-reuse guard:** when `cmdline_match` is given, before signaling, shell out to `ps -p <pid> -o command=` (macOS-compatible, works on Linux too) and require the substring is present. On mismatch, return `cmdline_mismatch=True` and signal nothing. Cheap defense against PID reuse after a worker crash.
- **Platform:** macOS + Linux. Windows out of scope.
- **New event constant:** `EVENT_PHASE_ORPHAN_REAPED = "phase_orphan_reaped"` in `state.py` (alongside the existing `EVENT_LEASE_EXPIRED` at line 110). Fields: `phase`, `pid`, `signaled` (`"SIGTERM"` or `"SIGTERM+SIGKILL"`), `cmdline_mismatch` (bool).

### Phase 2 — supervisor-wire (#57)
- **Wire site:** `supervisor.py` line ~229, the `if st.release_if_expired(data):` block. Read `claim["pid"]` and `claim["claimed_by"]` BEFORE calling `release_if_expired` (which clears `current_claim`), then call `reap_orphan_pid` AFTER the release.
- **cmdline match string:** `f"/clu-phase {data['plan_slug']} {claim['phase_id']}"` — both plan slug and phase id together are unique enough to defeat realistic PID reuse.
- **Event ordering:** `EVENT_LEASE_EXPIRED` fires inside `release_if_expired` (existing behavior unchanged); `EVENT_PHASE_ORPHAN_REAPED` is appended immediately after by the supervisor. Both land in the same tick.
- **`clu watch` surfaces it:** `watch.py` emits both events; the existing `lease_expired` text-mode line is preserved, and a new `orphan_reaped` line gets a matching shape.

### Phase 3 — effort-parser (#58)
- **Extension to `parse_sessions_index`:** the `Phase` dataclass already carries an `effort: str` field (line 31 of `plan_parser.py`) — it's parsed today but ignored downstream. Add a sibling pure function `parse_effort_minutes(raw: str) -> int | None` returning minutes (or `None` on malformed).
- **Shapes accepted:** `3h`, `1.5h`, `90min`, `30min`, `2-3h` (take upper bound), `1.5-2h` (upper bound). Case-insensitive on the unit; whitespace tolerant.
- **Malformed handling:** return `None`. Caller decides fallback. Don't raise — the Effort column is operator-authored markdown and we accept that some plans pre-date this convention.
- **No change to existing callers:** `parse_sessions_index` returns the same `Phase` objects; `effort_minutes` is computed lazily by `cmd_init` (next phase).

### Phase 4 — ttl-storage (#58)
- **New config field:** `lease_ttl_scale: float = 0.5` in `config.py`'s `ProjectConfig`. Operator-tunable in `.orchestrator.json` under root (not under `quality`).
- **`cmd_init` change:** when registering each phase, compute `per_phase_ttl = max(global_default, round(effort_minutes * scale))` if `effort_minutes` parsed; otherwise omit the field. Store on the phase record (existing phase dict in state.json) under `lease_ttl_minutes`. **The global field at `data["config"]["lease_ttl_minutes"]` is unchanged; per-phase is a strict override that wins when present.**
- **New resolver:** `state.lease_ttl_for_phase(data, phase_id) -> int`. Reads per-phase override → falls back to `data["config"]["lease_ttl_minutes"]` → falls back to `DEFAULT_LEASE_TTL_MIN`.
- **All claim sites use the resolver:** `state.claim_phase` signature already takes `lease_minutes: int`; the supervisor's dispatch site (~line 301) becomes `lease_minutes=st.lease_ttl_for_phase(data, phase_id)`.

### Phase 5 — default-bump-and-warn (#58)
- **Constant change:** `DEFAULT_LEASE_TTL_MIN = 30` → `60` in `state.py` line 78. Empirically motivated per the #58 body (most recent phases run 25-55min).
- **`clu doctor` warning:** scan each registered plan's phases for malformed Effort cells (where `effort` is set but `parse_effort_minutes` returns `None`); emit a warning line listing the affected `<plan>:<phase>` pairs. Existing `clu doctor` patterns in `cli.py` show the report shape.
- **`docs/conventions.md` line:** brief paragraph on the `lease_ttl_scale` knob (default 0.5 means "I trust Effort half-way"; raise to 1.0 if you trust it; never go below the global default).

## Non-goals

- **No worker-side reset of the worktree.** Auto-reset between attempts is #60's territory; this batch only kills the orphan, doesn't touch the filesystem state.
- **No Windows support for reaping.** macOS + Linux per the operator's base assumption.
- **No deprecation of the global `lease_ttl_minutes`.** Per-phase overrides are additive.
- **No change to in-flight plans.** Plans already registered keep their existing per-phase TTLs (none, so they fall back). The new behavior applies to plans registered AFTER ship.
- **No retry-budget change.** Lease expiry still re-pops the phase up to `max_attempts`; this batch only adds reaping + scales the lease itself.
- **No heartbeat ping from the worker** (that's #59).

## Files touched

- `end_of_line/state.py` — P1 NEW (`reap_orphan_pid`, `EVENT_PHASE_ORPHAN_REAPED`), P4 modified (`lease_ttl_for_phase`, per-phase storage), P5 modified (default constant). **API hotspots:** `reap_orphan_pid` new public helper; `lease_ttl_for_phase` new public resolver; `DEFAULT_LEASE_TTL_MIN` constant value changes.
- `end_of_line/supervisor.py` — P2 modified — reap wired at the `release_if_expired` call (~line 229); dispatch site uses resolver.
- `end_of_line/plan_parser.py` — P3 modified — `parse_effort_minutes` helper added; `Phase` dataclass unchanged.
- `end_of_line/cli.py` — P4 modified (`cmd_init` writes per-phase TTLs), P5 modified (`clu doctor` warning).
- `end_of_line/config.py` — P4 modified — `lease_ttl_scale: float = 0.5`.
- `end_of_line/watch.py` — P2 modified — emit `orphan_reaped` line.
- `docs/conventions.md` — P5 modified — `lease_ttl_scale` paragraph.
- `tests/test_reap_orphan.py` — P1 NEW — unit tests for `reap_orphan_pid` (live subprocess, dead PID, cmdline mismatch).
- `tests/test_supervisor.py` — P2 modified — assert reap fires on lease expiry; assert event ordering.
- `tests/test_plan_parser.py` — P3 modified — `parse_effort_minutes` shape tests.
- `tests/test_init_config.py` — P4 modified — per-phase TTL written; resolver fallback chain.
- `tests/test_lease_default.py` — P5 NEW — default-bump regression test; `clu doctor` warning surfaced.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood / Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- `clu verify` + `clu attest --simplify` before `clu complete` per the #55 attestation gate.
- Call `clu complete --plan lease-reliability --phase <id> --token <T>` with the worker token on success.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| reap-core | `lease-reliability-reap-core.md` | `reap_orphan_pid` helper + `EVENT_PHASE_ORPHAN_REAPED` (#57) | 1.5h |
| supervisor-wire | `lease-reliability-supervisor-wire.md` | Wire reap into `release_if_expired` path + `clu watch` (closes #57) | 1h |
| effort-parser | `lease-reliability-effort-parser.md` | `parse_effort_minutes` permissive parser (#58) | 1h |
| ttl-storage | `lease-reliability-ttl-storage.md` | Per-phase TTL at `cmd_init` + `lease_ttl_for_phase` resolver (#58) | 1.5h |
| default-bump | `lease-reliability-default-bump.md` | Default 30→60min + `clu doctor` Effort warning (closes #58) | 30min |
