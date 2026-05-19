# coolant-lifecycle-events — emit agent.start/stop on worker dispatch + reap (closes #54)

## Goal

Make clu workers visible to the coolant plugin's agent census + parallel-mode
gating. Currently 4 concurrent clu workers run `go test` / `vitest` etc.
uncapped because coolant's `SubagentStart` / `SubagentStop` hooks only fire
for in-session Task-tool subagents — top-level `claude --print` invocations
(every clu worker) are invisible. Wire clu to shell out to coolant's
`agent-start.sh` / `agent-stop.sh` at dispatch and at every reap site so the
counter, the events log, and `gate.sh`'s parallel-mode formula see workers.

## Non-goals

- No new coolant install path or schema discovery beyond what coolant
  already publishes (`~/.claude/plugins/cache/<author>/<plugin>/<version>/scripts/`).
- No supervisor-side PID tracking to detect worker death. Stop emits fire
  from the worker-callback handlers (`cmd_complete`, `cmd_block`,
  `cmd_force_complete`, `cmd_release_claim`) and from the supervisor's
  existing lease-expiry / orphan-reap branches. Coolant's CPU/MEM gauges
  still cover the ~1s gap via process discovery.
- No retry / queue / fallback if the coolant script call fails. Fire-and-
  forget, `check=False`, swallow exceptions, log a debug line. Coolant's
  own counter floors at 0 so a leaked +1 doesn't compound.
- No abstraction over coolant — no Notifier-style protocol, no pluggable
  observability backends. `coolant.py` is one specific shell-out helper
  (reuse-specialist's hard call: copy-and-defer, not refactor-first;
  duplication with `notify.py` is <30 lines of trivial boilerplate).
  If a second observability sink ever shows up, generalize then.
- No backfill of historical worker lifecycle into coolant's events log.
  Only events that fire after the wiring lands.
- **No `emit_stop` on dispatch-side spawn-failure release paths**
  (`dispatch.py:512` `_pause_and_halt`, `dispatch.py:577`
  `_release_with_failure`). `emit_start` fires only AFTER the fast-fail
  check passes — so these failure paths never had a matching start,
  and emitting stop there would push coolant's counter into the
  floor-to-0 case spuriously.

## Files to touch

- `end_of_line/coolant.py` (NEW) — script-path resolver + `emit_start` /
  `emit_stop` helpers. Pure stdlib subprocess. `~80 LOC` target.
  Subprocess call uses `stdout=DEVNULL, stderr=DEVNULL, timeout=2,
  check=False`. Helper short-circuits if `session_id` or `agent_id`
  is empty.
- `end_of_line/state.py` — new `release_claim_and_emit()` wrapper that
  snapshots `claim["phase_id"]` + `claim["claimed_by"]` BEFORE
  calling `release_claim()`, then fires `coolant.emit_stop(...)`.
  Single insertion point for all worker-callback release sites
  (Q5(a) — centralized at state layer, not inlined at callsites).
- `end_of_line/dispatch.py` — call `coolant.emit_start` post-Popen in
  `_spawn`, AFTER the 0.5s fast-fail check passes (~line 241 area, not
  immediately after the Popen call). Two release sites
  (`_pause_and_halt`, `_release_with_failure`) deliberately do NOT
  emit stop (see non-goals).
- `end_of_line/cli.py` — switch `cmd_complete`, `cmd_block`,
  `cmd_force_complete`, `cmd_release_claim` to call
  `state.release_claim_and_emit(...)` instead of `state.release_claim(...)`.
- `end_of_line/supervisor.py` — in the `release_if_expired` branch
  (~line 231): snapshot `phase_id` / `claimed_by` from the live claim
  BEFORE the call (the function mutates `current_claim = None`
  internally), then fire `coolant.emit_stop` between
  `release_if_expired` and `reap_orphan_pid`.
- `end_of_line/config.py` — add top-level `coolant: { enabled: bool,
  script_dir: str|None }` config slot via `CoolantSpec` dataclass +
  `_validate_coolant`. Defaults `enabled=True`, `script_dir=None`
  (auto-discover). Same shape as the `quality` slot.
- `end_of_line/cli.py` (doctor) — add a `_print_coolant_health(cfg)`
  helper called from `cmd_doctor`, matching the existing
  `_print_notify_health` / `_print_effort_health` style.
- `tests/__init__.py` — extend `CluTestCase` to scrub `COOLANT_*` env
  vars + `CLU_COOLANT_SCRIPT_DIR` (Q6). Without this, tests on a
  coolant-installed dev machine mutate the real counter at
  `$TMPDIR/coolant-$USER.count`.
- `tests/test_coolant.py` (NEW) — unit tests for the helper module:
  resolver paths (env var → marketplace glob → None), no-op when
  scripts absent, subprocess failure swallow, JSON payload shape,
  empty-field short-circuit, DEVNULL stdout/stderr redirection.
- `tests/test_dispatch.py` — assert `agent-start` fires on successful
  spawn (post-fast-fail), doesn't fire on Popen failure, doesn't fire
  on fast-fail catching exit-127. Use the existing module-level
  `subprocess.Popen` mock pattern.
- `tests/test_cli.py` — assert `agent-stop` fires on each of the four
  callback paths via `release_claim_and_emit`.
- `tests/test_state.py` — direct unit tests for
  `release_claim_and_emit` (claim-snapshot ordering, emit on success,
  no emit when claim is absent — guards against double-stop races).
- `tests/test_supervisor.py` — assert `agent-stop` fires on lease-expiry
  release, with snapshot ordering verified.
- `README.md` — one-line pointer noting coolant integration is automatic
  when coolant is installed.

## Failure modes to anticipate

- **`systemMessage` JSON stdout pollution.** `agent-start.sh:29` and
  `agent-stop.sh:34` unconditionally emit `{"systemMessage":"..."}` to
  stdout on parallel-mode threshold transitions. Intended for Claude
  Code hook contexts. clu MUST set `stdout=DEVNULL, stderr=DEVNULL` on
  every subprocess invocation — else this JSON leaks into clu's output
  stream and confuses any caller parsing our stdout. Load-bearing.
- **Empty `session_id` / `agent_id` silently accepted by coolant.**
  `_extract_agent_fields` regex matches `""` as a valid capture; coolant
  records empty-field events in its JSONL without erroring. Mitigation:
  `emit_start` / `emit_stop` validate non-empty and short-circuit to
  no-op if either is empty. Test this branch explicitly.
- **Counter race under high contention.** `coolant_lock` falls back to
  "proceed unprotected" after a ~1s spin loop (common.sh:14). Two
  workers dispatched within 50ms can both write the counter without
  the mutex held, losing an increment. Coolant-side limitation, not
  fixable from clu. Acceptable — accounting is approximate by design.
- **Marketplace path schema changes.** `~/.claude/plugins/cache/<author>/
  <plugin>/<version>/scripts/` is observed-but-undocumented. Mitigation:
  glob the version (`/cache/todd-w-shaffer/coolant/*/scripts/`), pick
  newest by lexical sort; fall back to env var `CLU_COOLANT_SCRIPT_DIR`.
  If both fail, coolant integration silently no-ops — clu keeps working.
- **Coolant script hangs / never returns.** Bash scripts use `mkdir`-based
  mutex with a stale-lock breaker but the scripts themselves don't
  timeout. Mitigation: `subprocess.run(..., timeout=2)`. On timeout,
  swallow `TimeoutExpired` + debug log.
- **Stop emitted without matching start.** Coolant decrements counter
  with `next < 0` floored to 0. Safe. But the dispatch-side spawn-failure
  release paths (`dispatch.py:512`, `dispatch.py:577`) MUST NOT emit
  stop — they fire before the fast-fail check that gates emit_start
  (see non-goals). Centralizing at `release_claim_and_emit()` doesn't
  help here because those paths call `release_claim()` directly; they
  stay unchanged.
- **Worker callback runs before the worker process actually exits.**
  The agent count decrements ~1s early. Accepted per Q2; coolant's
  CPU/MEM gauges cover the gap via `comm`-prefix discovery.
- **Multiple stops for the same claim.** If `cmd_complete` and then
  `cmd_force_complete` both run (operator races worker), the second
  call sees `current_claim` already absent. `release_claim_and_emit()`
  must check claim presence before snapshotting — no claim, no emit.
- **Snapshot-vs-mutation ordering in supervisor.** `release_if_expired`
  sets `current_claim = None` internally (state.py:431). The supervisor
  must read `phase_id` / `claimed_by` BEFORE calling it, not after.
  Without the snapshot the emit would have to dig through events to
  reconstruct the claim, which is fragile.
- **Test pollution of real coolant counter.** Tests must point
  `COOLANT_COUNTER` / `COOLANT_EVENTS` at `tmp_path` via env. Without
  CluTestCase scrubbing `COOLANT_*` + `CLU_COOLANT_SCRIPT_DIR`, any
  test that exercises a dispatch/reap path mutates the dev machine's
  real coolant counter at `$TMPDIR/coolant-$USER.count`.
- **Config schema regression.** Adding a top-level `coolant` slot to
  `.orchestrator.json` must not break existing init files. `config.py`
  parsers use `.get(key, default)`; the `CoolantSpec` defaults handle
  the absent-key case cleanly (verified pattern from `quality`).
- **`cmd_block` path leaves the worker process alive temporarily but
  releases the claim.** Stop emit fires on claim release, which is
  correct behavior — worker process may still be alive but its `--print`
  invocation has returned. Match the existing release semantics.

## Done criteria

- `coolant.py` resolves script path via env var → marketplace-cache glob
  → `None`, with unit-test coverage for each branch.
- `coolant.py` subprocess call uses `stdout=DEVNULL, stderr=DEVNULL,
  timeout=2, check=False`. Unit test asserts the kwargs.
- `coolant.py` short-circuits on empty `session_id` OR empty `agent_id`
  — unit-tested both branches.
- `emit_start` fires AFTER the 0.5s fast-fail check passes, not pre-fast-
  fail. Spawn-failure release paths (`dispatch.py:512`, `dispatch.py:577`)
  do NOT emit stop. Tests exercise both the healthy-spawn and
  fast-fail-catches-exit-127 paths and verify the emit count.
- `state.release_claim_and_emit()` snapshots `phase_id` + `claimed_by`
  before delegating to `release_claim`; no-op when claim is absent.
- All four worker-callback handlers (`complete`, `block`,
  `force-complete`, `release-claim`) route through
  `release_claim_and_emit` instead of `release_claim`.
- Supervisor's `release_if_expired` branch snapshots claim fields BEFORE
  the call, then emits stop between the release and the orphan-reap.
- Dispatching a worker increments coolant's counter and appends a
  `"event":"agent.start"` line to `$COOLANT_EVENTS` within 1s of spawn.
- `clu doctor` prints a coolant status line matching the
  `_print_notify_health` / `_print_effort_health` shape.
- `.orchestrator.json` accepts top-level `coolant.enabled` opt-out
  without breaking existing init files (default `true`).
- `CluTestCase` scrubs `COOLANT_*` + `CLU_COOLANT_SCRIPT_DIR` env vars.
- Full suite green (1122 → ~1140+ expected).
- **Live-verified once** on this dev machine against the real coolant
  install: `clu queue add` dispatch confirms counter increments + JSONL
  event append, worker callback decrements + appends stop event. Manual
  sign-off recorded in the final commit message (per Q7).

## Parking lot

(empty at start)
