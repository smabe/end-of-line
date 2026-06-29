# Reference

Per-module reference for `end_of_line/`. One H2 per Python module, in load
order from `cli.py`. Each section lists the public surface (the names a
contributor or worker callback talks to), the invariants that aren't
obvious from the code, and pointers to other docs.

For the JSON shape of the state file, the worker callback contract, and
the plan-markdown contract, see `contract.md`. For the tick → dispatch →
worker → callback loop see `architecture.md`. This document answers
"what is `X` and what does it do?", not "how do the pieces fit together?".

The package's `__init__.py` is empty modulo metadata — nothing to
document.

## Modules

### `state.py`

Atomic state-file primitives. Owns the lock, the JSON shape on disk, the
append-only event log, slug validation (a path-traversal guard), claim
lifecycle, and the projections everyone else reads from. Every other
module either mutates the file through this one or reads what it wrote.

This module has no I/O except the lock + load + atomic write. Everything
else is a pure function over the `data` dict, which is why supervisor,
CLI, fleet, and dispatch can all reuse the same code without re-opening
the file.

**Key types and functions**

- `SLUG_PATTERN`, `validate_slug(slug, *, kind)` — regex fragment + check.
  Every `plan_slug`/`phase_id` that touches the filesystem MUST pass
  through this.
- `InvalidSlug`, `ClaimMismatch`, `SchemaVersionMismatch` — typed errors
  the CLI translates into specific `ExitCode`s.
- `SCHEMA_VERSION` — bumped any time the on-disk schema changes; load
  fails loud on mismatch.
- `STATUS_*`, `TERMINAL_STATUSES`, `STATUS_STALLED`, `STATUS_MISSING` —
  the plan-status enum. `STALLED` and `MISSING` are display-only (fleet
  view derives them).
- `GC_ELIGIBLE_STATUSES = TERMINAL_STATUSES − {STATUS_PAUSED}` — the
  status set `clu worktree gc` will act on. Paused plans are excluded
  because they may still resume and need their worktree intact.
- `EVENT_*` — every event type as a constant. Never write a raw string;
  a typo silently breaks `completed_phase_ids()` and friends.
  Worktree-specific events: `EVENT_WORKTREE_MISSING` (dispatch-time,
  paired with status=PAUSED), `EVENT_WORKTREE_CONFLICT_WARNING`
  (tick-time, paired with `in_conflict_with` flag).
- `get_worktree(data)` — reader for the additive-optional `worktree`
  field. Returns `dict | None`; callers never read the raw key.
- `BLOCKER_INPUT`, `BLOCKER_REPLAN` — blocker types.
- `utcnow()`, `parse_iso(ts)` — single timestamp format
  (`%Y-%m-%dT%H:%M:%SZ`). All UTC.
- `empty_state(plan_slug, plan_dir)` — fresh state dict, with config
  defaults baked in.
- `locked(state_path)` — `flock` context manager with `O_NOFOLLOW`
  on the sibling lockfile.
- `locked_json(path, *, expected_version, empty=None)` — generic
  lock + load + yield-for-mutation + atomic-write. The shared primitive
  every clu JSON file (state, registry, queue) is built on. Pass `empty`
  to tolerate a missing-on-first-write file; state.json passes `None` so
  load() raises `FileNotFoundError` as documented.
- `mutate(state_path)` — lock + load + yield + atomic-write. Thin wrapper
  over `locked_json` for state files. The default read-modify-write
  helper; only drop to `locked()` when coordinating multiple files.
- `load(state_path, *, expected_version)` — JSON read + schema check.
  Reused by `registry.py` with its own version.
- `save_atomic(state_path, data)` — tmp + fsync + rename. Caller must
  hold the lock.
- `append_event(data, event_type, **fields)` — the only event-writer.
- `claim_phase(data, phase_id, lease_minutes, claimed_by=None)` — claim a
  phase, write `phase_started`, return the token. Raises if a live claim
  exists.
- `release_claim(data, expected_token=None, expected_phase=None)` —
  clear `current_claim`. Pass both expected fields to validate first;
  passing neither clears unconditionally (supervisor-only).
- `release_if_expired(data)` — drop an expired lease + emit
  `lease_expired`. Shared between `claim_phase` (reclaim) and the
  supervisor (stale-lease rule).
- `assert_claim_match(data, expected_token, expected_phase)` — raises
  `ClaimMismatch` unless the live claim matches both. Every worker-side
  CLI command calls this.
- `record_heartbeat(data, expected_token, expected_phase)` — stamps
  `last_heartbeat_at`; no event written (would flood the log).
- `heartbeat_age_seconds(claim)`, `is_claim_stalled(claim, threshold)` —
  what supervisor and fleet view use to derive stalled status.
- `terminalize(data, *, status=halted, event=plan_abandoned, **fields)` —
  compare-and-set flip of a non-terminal plan to a terminal status +
  audit event; no-op (returns False) if already terminal. Used by
  `unregister` and the zombie sweep. (#75)
- `reap_orphan_pid(pid, cmdline_match=None)` /
  `reap_orphan_pgroup(pgid, cmdline_match=None)` — SIGTERM→poll→SIGKILL a
  single worker PID / its whole process group (`os.killpg`). The group
  reaper takes worker + heartbeat together (the worker is a session
  leader, pgid == pid); guards `pgid != getpgid(0)` and a cmdline marker
  before signaling. `reap_claim(data)` wraps the group reaper using the
  plan slug as the marker.
- `is_zombie_state(data)` — True when `status=running` and (no claim OR
  the worker PID is gone). Callers restrict it to unregistered files; the
  zombie sweep's predicate. (#75)
- `add_blocker(...)`, `answer_blocker(blocker_id, answer)`,
  `resolve_blocker_answer(data, blocker_id, raw)` — blocker lifecycle.
  `resolve_*` translates "2" → option-text so the event log records the
  human-readable choice.
- `completed_phase_ids(data)`, `open_blockers(data)`,
  `phase_has_open_blocker(data, phase_id)` — projections. Centralized so
  the predicates can't drift between callers.
- `latest_event(data, event_type, *, phase=None)` — most-recent reverse
  scan. Use this instead of inlining a loop; it keeps the event-type
  literal next to its siblings.
- `attempts_for_phase(data, phase_id)` — phase_started count since the
  most recent `retry_requested`. The retry floor is what lets
  `clu retry` clear the cap without rewriting history.
- `most_recent_halted_phase(data)` — helper for `clu retry` to pick the
  right phase when `--phase` isn't given.
- `status_reason(data)` — derived one-line cause for `paused`/`halted`
  status, read by `clu status`.

**Invariants and gotchas**

- `events` is append-only. Never edit or remove a past event. Projection
  is what makes the state reconstructable.
- Every event-type write MUST go through `EVENT_*` constants. A typo
  silently breaks `completed_phase_ids()` and friends, which is how
  phases double-dispatch.
- Slugs MUST go through `validate_slug` before any code path that builds
  a filesystem path from them. The regex is the path-traversal guard.
- Every worker-side mutation MUST happen inside `mutate()` (or under
  `locked()` for multi-file coordination). Don't read state outside the
  lock and act on it.
- `release_claim`: pass both `expected_token` and `expected_phase` or
  neither. Passing only one raises `ValueError` — that's by design, it's
  always a programming bug.
- `_now_utc()` and `utcnow()` are UTC. Local-time semantics (quiet
  hours) live in `notify.py`, on purpose. Don't mix.

**See also**

- `contract.md` for the JSON schema, event-type roster, and worker
  callback table.
- `architecture.md` for the priority chain that consumes these
  projections.

### `config.py`

Per-project `.orchestrator.json` loader. Resolves the path of every
state file under a project, with a defense-in-depth check that rejects a
resolved path escaping `<project>/<plan_dir>/.orchestrator/`.

**Key types and functions**

- `ProjectConfig` — dataclass: `project_root`, `plan_dir`, `dispatch`,
  `notify`, `test_command: str | None` (shell command run inside the
  scratch worktree by `dry_merge.attempt_merge`, `cmd_validate`, and
  `cmd_ship`'s validate gate; null = textual-merge-only).
- `DispatchSpec.ship_mode: str` — project default for `clu ship` mode
  resolution when neither `--direct` nor `--as-pr` is passed. Values:
  `"direct"` (local merge + push, default) or `"as_pr"` (open a PR
  via `gh pr create`). Validated by `_validate_ship_mode`.
- `ProjectConfig.state_path(plan_slug)` — returns the canonical state
  path; raises `InvalidSlug` if resolution escapes the orchestrator dir.
- `ProjectConfig.queue_path()` — `<project>/<plan_dir>/.orchestrator/
  queue.json`. No slug involved → no path-traversal validation. One
  queue file per project.
- `ProjectConfig.master_plan_path(plan_slug)` — `<project>/<plan_dir>/
  <slug>.md`. Absence is the canonical "archived" signal; used by
  `cmd_unregister --all-archived` and `clu worktree gc` to widen
  scope with `--include-archived`.
- `DispatchSpec` — `kind` (only `"shell"` in v0.1) + `command` template
  string + optional `path` (absolute PATH for worker subprocess) +
  optional `repair_command` template. Worker `command` substitutions:
  `{plan_slug}`, `{phase_id}`, `{token}`, `{project}`, `{state_file}`.
  Repair `repair_command` substitutions: `{corrupt_path}`,
  `{backup_path}`, `{diagnosis}`, `{schema_json}`, `{log_path}`. Unset
  `repair_command` disables queue auto-repair (clu still backs up and
  notifies via `KIND_QUEUE_CORRUPT`).
- `NotifySpec` — `imessage_to` (handle) + `quiet_hours` (tuple of
  `"HH:MM"` strings).
- `load_project_config(project_root)` — parses
  `<project>/.orchestrator.json` or returns the all-defaults config.
- `load_session_dirs(path=None)` — reads the machine-wide
  `~/.config/clu/config.json`'s top-level `session_dirs` list → a deduped
  list of `expanduser`'d + resolved absolute dir strings. Fail-open to
  `[]` (missing/malformed/non-list). The cwds whose non-clu Claude
  sessions `clu top`/`clu serve` surface without a registered plan;
  `cmd_top`/`cmd_serve` load it and thread it to `top.gather_rows`
  (unioned into the scan roots) and `webserver.resolve_session_transcript`
  (extra feed candidate roots). See `docs/operations.md` "Watch extra
  session directories".
- `CONFIG_FILENAME` (`.orchestrator.json`), `ORCHESTRATOR_DIR`
  (`.orchestrator`) — the layout constants.

**Invariants and gotchas**

- `state_path` is double-guarded: slug-regex first, then a resolved
  `relative_to` check. The slug check is the strong guarantee; the
  resolve check just refuses to lose silently if a future caller
  forgets.
- A missing `.orchestrator.json` is fine — defaults apply. A malformed
  one raises during JSON parse, which `cli.main` lets propagate.
- `project_root` itself is not checked for symlink escape. The user owns
  it; the slug is the attacker.

**See also**

- `state.validate_slug` for the regex.
- `dispatch.dispatch_for_tick` for how `DispatchSpec.command` is
  substituted and `Popen`d.

### `plan_parser.py`

Parses the master plan's `## Sessions index` markdown table into a list
of `Phase` records. The supervisor walks this list in order; the worker
finds its sub-plan via the same table.

**Key types and functions**

- `Phase` — dataclass: `id`, `plan_file`, `scope`, `effort`.
- `parse_sessions_index(plan_path)` — returns `list[Phase]` or `[]` when
  the master plan has no Sessions index.

**Invariants and gotchas**

- Phase id is derived from the plan-file stem with the master-plan stem
  + `-` stripped. `clu-docs-reference.md` under master `clu-docs.md` →
  phase id `reference`. The stripping is what keeps phase ids short
  without forcing the author to maintain two names.
- Each derived phase id goes through `state.validate_slug` before it's
  appended — so a malformed sub-plan filename fails at parse time, not
  at the first attempt to write a path.
- Empty list is meaningful: the supervisor reports `error` so `clu
  status` shows it. Single-phase synthesis isn't wired in v0.1.

**See also**

- `contract.md` § "Plan markdown contract" for the table format authors
  have to follow.

### `supervisor.py`

The single-tick decision engine. Pure function over `data`: it reads,
mutates, optionally appends one event, and returns a `TickResult`
describing what to do next. The supervisor never spawns a worker
itself — `dispatch_for_tick` does that after the lock is released.

**Key types and functions**

- `tick(state_path, config)` — entry point. Walks the nine-priority
  chain (see `architecture.md`) and returns a `TickResult`.
- `sweep_zombie_states(cfg, registered_slugs, *, dry_run=False)` — the
  registry-independent reaper. Scans a project's `.orchestrator/*.state.json`
  for unregistered files at `status=running` whose worker is gone
  (`state.is_zombie_state`), terminalizes + reaps them, and returns a
  list of `ZombieSweepResult`. Run automatically per project by
  `cmd_tick_all` and dry-run by `cmd_doctor`. Backstops the
  unregistered-while-running window the registry walk can't reach (#75).
- `TickResult` — dataclass with `action`, `detail`, `phase_id`, `token`,
  `notify_body` (rendered iMessage for actions that should ping), and
  `side_notifies: list[tuple[kind, body]]` (gap-fill emissions that
  ride alongside the primary action — see `_emit_*` helpers below).
- `Action` — typed union of `dispatch`, `idle`, `lease_expired`,
  `worker_dead`, `escalate`, `blocker_resumed`, `halt`, `plan_done`,
  `error`, `stalled`.
- `ACTION_NOTIFY_KIND` — map from action → notify kind for quiet-hours
  classification. Adding an action here is the one-line change that
  makes a new tick path send iMessage. `worker_dead` maps to
  `KIND_STALLED` (auto-recovery via re-dispatch; quiet-hours gated).
- `_detect_stalled(data)` — emits `phase_stalled` once when a claim
  goes past the heartbeat threshold; stamps `stalled_notified=True` on
  the claim so subsequent ticks fall through. Now priority 3 (after
  the dead-PID rule).
- Dead-PID detection (priority 2, issue #72) is inlined in `tick()`
  rather than a separate `_detect_*` helper because it threads through
  multiple state-mutation steps (event → release_claim_and_emit →
  best-effort reap) under the existing `with st.mutate()` window.
- `_emit_stuck_blocker_repings(data, config, side_notifies)` — re-pings
  any open blocker un-consumed for ≥30 min (and again every 30 min
  thereafter via `last_repinged_at`). Mutates `data` + appends to
  `side_notifies` + writes an inbox event. Runs before the main chain
  so it fires regardless of the tick's primary action.
- `_emit_stalled_claim_notify(data, config, side_notifies)` — one-shot
  signal on the lease-expiry transition while plan status is
  `RUNNING`. Stamps `stalled_notified=True` on the (about-to-be-
  released) claim. Sits ahead of `release_if_expired` so the
  notification fires before the claim is cleared.
- `_emit_worker_idle(data, config, side_notifies)` — wedge gap-fill:
  fires `EVENT_WORKER_IDLE` once per claim when the worker is PID-alive
  but doing nothing (no active Bash tool, CPU ≤1% over ≥10 min, no open
  Anthropic API socket). CPU is sampled across the **whole worker
  process tree** — `walk_worker_tree(claim.pid)` for the pid set, then
  ONE `ps -p <pids> -o %cpu=` summed — not `claim.pid` alone, so a
  worker idling at ~0% while a child (test run, build) burns CPU does
  not false-fire. Instantaneous %cpu stays the metric
  (`append_cpu_sample` / `worker_idle_window_satisfied` unchanged);
  `Descendant.cpu_seconds` is cumulative time and deliberately unused.
  `ps_output` (the `%cpu=` output), `tree_ps_output` (the walk
  snapshot), and `lsof_output` are test seams.
- `_local_now()` — indirection so tests can pin wall-clock time for
  quiet-hours assertions.

**Invariants and gotchas**

- One tick = one action. If a tick would do two things, the second is
  the next tick's job. This is what keeps the chain debuggable — every
  "why didn't this tick advance?" reduces to "which rule fired first?".
- Priority order is load-bearing. Don't reshuffle without re-reading
  `architecture.md` § "One tick = one action".
- SLA escalation is gated by `notify.in_quiet_window`. The blocker stays
  aged and the next loud tick re-checks; this prevents 3am pings on
  overnight rollover.
- The halt branch only fires from `STATUS_RUNNING`, which is what
  guarantees the halt iMessage fires exactly once per transition
  (subsequent ticks short-circuit via `TERMINAL_STATUSES`).
- `_detect_stalled` does NOT release the claim — the 60-min lease still
  owns retry. `phase_stalled` is just the notification trigger.

**See also**

- `architecture.md` for the tick lifecycle diagram and the priority
  chain in prose.
- `dispatch.py` for what happens after `tick` returns `dispatch`.

### `dispatch.py`

Fire-and-forget worker spawn. Renders the project's
`DispatchSpec.command` template, `Popen`s it, and either stamps the pid
on the live claim (healthy) or releases the claim with a
`dispatch_failed` event (fast-fail). The recommended permission posture
for the command template (`dontAsk` + scoped `--allowedTools` + OS
sandbox, never `bypassPermissions`) is documented in operations.md
"Hardened worker dispatch"; `clu doctor` warns when a template still
bypasses permission checks.

Phase workers are wrapped in the PTY shim (`_pty_spawn_shim.py`): the
outer `Popen` is `[sys.executable, <shim_path>, "--", <rendered cmd>]`
(list argv, no `shell=True`) instead of the bare command string. The shim
allocates a pty so `claude --print` line-buffers into the log in real time
instead of block-buffering until exit — a wedged worker otherwise leaves a
0-byte log. The shim becomes `claim.pid` (the worker is its child); phase
`idle-treewalk` made the idle watchdog tree-aware so this doesn't false-fire
`WORKER_IDLE`. Repair workers stay on the direct `shell=True` path.

**Key types and functions**

- `dispatch_for_tick(result, cfg, plan_slug, state_file)` — the only
  public entry point for phase dispatch. Returns `True` on spawn,
  `False` on no-op or fast-fail.
- `build_worker_env(cfg, *, plan_slug=None, phase_id=None, token=None)`
  — env dict for the worker `Popen`, or `None` to inherit. Merges (not
  replaces) `os.environ` with the optional `dispatch.path` PATH
  override. When the claim kwargs are provided (phase dispatch), also
  injects `CLU_PLAN` / `CLU_PHASE` / `CLU_TOKEN` / `CLU_PROJECT` so
  Claude Code hooks inside the worker (the activity hook) inherit the
  claim identity — worker-side `export` can't deliver this because env
  doesn't persist across Bash tool calls in headless `--print`
  sessions (#91). Repair workers pass no kwargs: they carry no claim
  or token, and the activity hook's empty-token short-circuit is the
  correct behavior for them. Cfg-only call with no PATH override keeps
  returning `None` — `clu doctor`'s "(source: inherited)" display
  depends on it.
- `dispatch_repair_worker(cfg, corrupt_path, backup_path, diagnosis,
  log_path, *, timeout_sec=60)` — synchronous repair-worker spawn for a
  corrupt `queue.json`. Renders `cfg.dispatch.repair_command`, waits for
  the worker to exit (or kills it on timeout, returning
  `REPAIR_RC_TIMEOUT = -1`), and returns the rc. Caller MUST follow up
  with `queue.validate_repair` regardless of rc — the rc is advisory.
  Stays separate from `dispatch_for_tick` because the contracts differ:
  there's no claim or token, the wait is synchronous (the cron tick
  blocks), and the logs go to `repair-queue-<UTCstamp>.log` instead of
  the per-token path.
- `DEFAULT_REPAIR_TIMEOUT_SEC` (60s), `REPAIR_RC_TIMEOUT` (-1) —
  sentinel for the timeout-killed path.
- `_FAST_FAIL_WAIT_SEC` (0.5s) — how long `proc.wait()` polls before
  declaring the worker healthy. Exits sooner if the worker crashed.
- `_release_with_failure(state_file, result, *, reason)` — clears the
  just-made claim + writes `dispatch_failed`.
- `_stamp_pid(state_file, result, pid, log_path)` — best-effort
  pid/log_path stamp on the active claim.

**Invariants and gotchas**

- Workers are spawned with `start_new_session=True` so a killed cron
  parent doesn't cascade-kill the worker.
- All template arguments are wrapped in `shlex.quote` before
  substitution. The dispatch command itself is operator-trusted; the
  values are attacker-untrusted.
- stdout AND stderr stream to the per-token log at
  `<orchestrator>/logs/<phase>.<token>.log`. The token is in the
  filename so a failed spawn's log doesn't shadow the next attempt's.
- Fast-fail rc != 0 within 0.5s → `_release_with_failure`. If the lease
  expired between the supervisor's claim and the dispatch (vanishingly
  rare), `release_claim` raises `ClaimMismatch` and we leave the claim
  alone — safer than racing.
- `dispatch.command == ""` is treated as a misconfiguration, not a
  silent no-op: the claim is released + `dispatch_failed` is written.

**See also**

- `architecture.md` § "Process model" for why the supervisor and
  dispatcher are split.
- `operations.md` for example `dispatch.command` templates.
- `_pty_spawn_shim.py` for the PTY wrapper phase workers run under.

### `quota.py`

Quota-death classification, reset-time parsing, and the project quota
pause file (#94). Stdlib-only. A worker killed by the operator's Claude
subscription limit prints a recognizable line and exits; this module
turns that line into attempt forgiveness + a project-level dispatch
pause. The signature table mirrors the systemic table in `dispatch.py`:
hard-coded, grows via PR only, first match wins. Bucketing is by
**parseability** — a quota match whose reset time parses schedules an
auto-resume pause; one that doesn't routes to the stuck bucket (no
auto-resume, loud notify), so the parser returns `None` for anything it
can't read confidently (weekly `resets Mon 12:00am`, date forms).

**Key types and functions**

- `QuotaMatch(signature, line)` — a classified death: the table key
  (`session_limit` | `weekly_limit` | `model_limit` | `usage_credits` |
  `extra_usage`) and the verbatim matched line.
- `classify_quota(tail)` / `classify_log_tail(log_path)` — first
  signature matching a line of the worker-log tail, or `None`.
  `classify_log_tail` is the None-safe file wrapper the three death
  sites call with `claim["log_path"]`.
- `parse_reset(line, now)` — the `resets <time> [(tz)]` fragment → aware
  UTC datetime (next occurrence; candidate ≤ now rolls to tomorrow), or
  `None`. `now` must be aware. `ZoneInfo` from the parens, system local
  when absent. First `zoneinfo` use in the codebase.
- `read_log_tail(log_path, lines=50)` — deque-bounded tail read; `""`
  on `OSError`. Shared with the systemic matcher (`dispatch.py` reads
  through it).
- `record_quota_pause(orchestrator_dir, match, now)` — writes
  `quota.json` (`paused_until = reset + 120s`, or `null` for stuck) and
  returns `paused_until`. Always resets the canary fields. Takes the
  **orchestrator dir** (state-file parent), not the project root —
  `plan_dir` is configurable and every death site already holds the
  state path.
- `record_quota_death(data, match, *, phase_id, token, orchestrator_dir)`
  — the shared recorder all three death sites call: writes the pause file
  plus `quota_death` (forgiveness marker) + `quota_paused` events into the
  open state-mutation `data`. Returns `paused_until`.
- `gate_decision(orchestrator_dir, plan_slug, now)` → `GateDecision(dispatch, detail, resumed)`
  — the dispatch gate's four-state machine (see architecture.md "Quota
  pause gate"). `dispatch=False` → supervisor returns idle; `resumed=True`
  → supervisor also appends `quota_resumed`.
- `PAUSE_BUFFER_SEC` (120), `CANARY_WINDOW_SEC` (180),
  `QUOTA_FILE_NAME` (`"quota.json"`), `QUOTA_SCHEMA_VERSION` (1),
  `LOG_TAIL_LINES` (50) — module constants; no config knobs (no second
  caller exists).

**Invariants and gotchas**

- **File absent == not paused** is the one invariant the hot path
  (`Path.exists()` before any lock) and the operator escape hatch
  (`rm quota.json`) both rely on. Resume *unlinks*; it never writes a
  cleared sentinel.
- `gate_decision` does NOT reuse `record_quota_pause`'s `locked_json` —
  `locked_json` unconditionally re-saves on exit, which would resurrect
  the file on the unlink-resume path. The gate uses raw `state.locked`
  + `state.load` + conditional `save_atomic`/`unlink`. Their save
  semantics genuinely differ; don't merge them.
- `quota_paused` events carry **no `phase` key** — consumers iterating
  `data["events"]` must not assume one.
- A corrupt or field-malformed `quota.json` degrades to dispatch (a
  malformed file must never freeze the fleet); a benign
  `FileNotFoundError` from a concurrent resume race is caught
  separately and stays silent.

**See also**

- `contract.md` § "Quota-death event semantics" + "Quota pause file
  schema" for the event kwargs and `quota.json` shape.
- `architecture.md` § "Quota pause gate" for the state machine.
- `operations.md` § "Recovering from a quota pause" for the operator
  runbook.

### `_pty_spawn_shim.py`

The long-lived PTY intermediary every phase worker runs under. `claude
--print` block-buffers stdout (~4–8 KB) when it isn't a tty, so a worker
that wedges mid-stream leaves a 0-byte log exactly when the post-mortem
needs it (the 2026-05-26 incident). The shim allocates a pty, runs the
worker as its child with the pty slave as the child's stdout/stderr, drains
the master continuously, and writes normalized bytes to fd 1 (the log) — so
output streams line-by-line. It must be a separate long-lived process: an
in-supervisor drain dies with the cron tick (the v1 parking reason; see
`plans/archive/line-buffer-worker-output/`).

Invoked by `dispatch.py` as `[sys.executable, <abs shim path>, "--", <cmd>]`
— by **file path**, not `-m`, because the worker's cwd is the worktree where
the package isn't importable; the shim is stdlib-only and self-contained, so
a path invocation is cwd-independent. It runs the cmd STRING through `sh -c`
itself, so the slug-bearing cmdline marker still rides in argv.

**Key types and functions**

- `strip_ansi(data: bytes) -> bytes` — pure: removes CSI / OSC / Fe-Fp-Fs
  escape sequences (one compiled regex, matched on the ESC byte so literal
  "ESC" text is untouched) and folds `\r\n` → `\n`. Unit-tested against a
  spike-derived byte sample.
- `main(argv=None)` — parse `-- <cmd>`, `os.openpty()`, run, propagate rc.

**Invariants and gotchas**

- **Dual EOF handling**: reading the master after the child exits returns
  `b""` on macOS but raises `OSError` (EIO) on Linux — both are EOF (mirrors
  CPython's `pty._copy`). The drain blocks in `select`, never sleep-polls, so
  macOS can't discard the tail at child exit.
- **Slave hygiene**: TIOCSWINSZ 80x24 (fresh ptys are 0x0); ONLCR cleared on
  the slave termios so `\n` isn't translated to `\r\n`; the parent closes its
  slave copy after spawn or the master never EOFs.
- **Signal propagation**: a child killed by signal N is mirrored by restoring
  `SIG_DFL` and re-raising N on self (`os.kill(getpid(), N)`), so the outer
  `Popen.returncode` reads the POSIX `-N` the fast-fail branch expects — never
  `sys.exit(-N)`, which truncates to `& 0xFF`. The drain finishes (EOF) before
  the re-raise so no tail bytes are lost.
- **Fallback**: if `os.openpty()` raises (PTY exhaustion), the shim writes one
  stderr warning and `os.execvp`s the command directly — degraded to today's
  block-buffered logging, preserving this pid (= claim pid) and the rc, never
  a dead dispatch. Scoped to the openpty call so a started child is never
  double-run.

**See also**

- `dispatch.py` for the wrap site and the repair-worker exemption.

### `heartbeat_daemon.py`

The detached heartbeat loop behind `clu heartbeat-daemon` — the worker's
step-2 replacement for the bash `( while kill -0 ...; do clu heartbeat;
sleep 120; done ) &` compound, which scoped-permission dispatch denies
(subshell/loop constructs don't survive permission decomposition; #90
spike Test B). One flat command double-forks + setsids, then pings the
live claim every 120s while the worker PID is alive.

**Key types and functions**

- `DEFAULT_INTERVAL_SECONDS = 120`, `STRIKE_LIMIT = 3` — the cadence and
  the consecutive-failure count that fires the operator self-report.
- `ACTION_OK / ACTION_STRIKE / ACTION_EXIT_WORKER_DEAD /
  ACTION_EXIT_CLAIM_GONE` — the per-tick verdicts.
- `tick_once(state_path, phase, token, worker_pid, *, pid_alive, ping)
  -> str` — the pure decision core: dead worker PID → exit; ping
  rejected (`ClaimMismatch` — claim released or superseded) → exit, NOT
  a strike; any other failure → strike. The ping is in-process
  (`state.record_heartbeat` under `state.mutate`) — the same code path
  `cmd_heartbeat` uses, so the daemon holds no subprocess PATH
  assumptions.
- `run_loop(...)` — ticks until an exit action. The 3rd consecutive
  strike fires the `notify-heartbeat-failure` path once (best-effort —
  a broken transport never kills the loop); success resets the counter.
  `sleep` / `tick` / `notify_failure` / `max_ticks` are injectable so
  tests run without wall-clock or forking.
- `run(..., detach=True)` — `_daemonize` (double-fork + setsid, stdio
  redirected to the sidecar log `logs/<phase>.<token>.hb.log`), then
  the loop. The parent returns 0 immediately; the daemon `os._exit`s
  after the loop so it can't fall back into CLI plumbing. `detach=False`
  is the test seam — real forking is covered by live smoke, not unit
  tests.

**Invariants and gotchas**

- The daemon is never the claim PID — claim.pid stays the worker's
  (supervisor-lifecycle PTY constraint: wrappers must not change
  claim.pid).
- setsid puts the daemon in its own process group, so
  `reap_orphan_pgroup`'s killpg never reaches it — accepted by design.
  A reaped worker is a dead PID (exit on the next liveness probe) and a
  released claim is a token rejection (clean exit on the next ping). Do
  not add the daemon to any reaper.
- Post-`clu complete` shutdown is the rejection path, not a signal: the
  callback releases the claim, the next ping raises `ClaimMismatch`,
  and the daemon exits ≤120s later.
- `cmd_heartbeat_daemon` validates phase slug, worker PID, and token
  against the live claim BEFORE forking — the validation ping doubles
  as the first heartbeat, so a forged token dies with `CLAIM_MISMATCH`
  in the caller's face instead of silently in a daemon log.

**See also**

- `skills/clu-phase/SKILL.md` step 2 — the worker-side arming command
  and the five watchdog layers it participates in.
- `state.py` `record_heartbeat` / `assert_claim_match` for the token
  discipline the ping inherits.

### `notify.py`

Notification dispatcher. Owns kind constants, quiet-hours gating, the
per-channel registry (iMessage / Discord / clu-watch-only), and the
`render_*` body builders. Stateless; quiet-hours gating is a pure
function of `(NotifySpec, datetime)`. The actual transport is
delegated to per-backend modules (`notify_imessage.py`,
`notify_discord.py`); `notify()` decides whether to send and which
channels to fan out to.

**Key types and functions**

- `KIND_BLOCKER`, `KIND_STALLED`, `KIND_COMPLETED`, `KIND_HALTED`,
  `KIND_QUEUE_SKIPPED`, `KIND_QUEUE_REPAIRED`, `KIND_QUEUE_REPAIR_FAILED`,
  `KIND_QUEUE_CORRUPT`, `KIND_STUCK_BLOCKER`, `KIND_STALLED_CLAIM`,
  `KIND_QUOTA_PAUSED`, `KIND_QUOTA_RESUMED`, `KIND_QUOTA_STUCK` —
  the notification kinds. See `contract.md` § "Notification kinds" for
  the trigger + quiet-hours matrix. `KIND_STUCK_BLOCKER` /
  `KIND_STALLED_CLAIM` are the "gap-fill" kinds added with the inbox in
  #20; the three `KIND_QUOTA_*` are the project quota pause (#94).
- `QUIET_HOURS_BYPASS_KINDS` — frozenset of kinds that ignore quiet
  hours. Currently `{KIND_HALTED, KIND_QUEUE_REPAIR_FAILED,
  KIND_QUEUE_CORRUPT, KIND_QUOTA_STUCK}` — the
  unrecoverable-without-operator set.
- `_NOTIFIER_REGISTRY` — `kind_name → Notifier-class` map. Backends
  register here by appearing in the module-level dict; new transports
  add one line. `set_global_suppress(True)` short-circuits the entire
  dispatch path (the `--no-notify` global CLI flag).
- `notify(spec, kind, body, *, now=None, plan_slug=None, project_root=None, inbox_writer=None)` —
  gate + fan out + optionally drop an inbox event. Returns `True` if
  any enabled channel sent. The inbox write happens independently of
  the quiet-hours gate, and only when `plan_slug` + `project_root` are
  both supplied. `inbox_writer` is injectable for tests.
- `in_quiet_window(spec, now)` — public quiet-hours predicate, used by
  the supervisor's SLA-deferral branch as well as `notify()` itself.
- `is_quiet_hours(now, start, end)` — wrap-aware time-window check;
  end < start means overnight (e.g. 22:00–08:00).
- `parse_hhmm(s)` — `"HH:MM"` → `datetime.time`.
- `render_blocker(plan_slug, blocker_id, phase, question, options)` —
  the user-facing prompt that includes the reply grammar hint.
- `render_stalled(plan_slug, phase, age_seconds)`,
  `render_completed(plan_slug, commit_count)`,
  `render_halted(plan_slug, phase, attempts)`,
  `render_queue_skipped(slug, reason)`,
  `render_queue_corrupt(diagnosis, backup_path)`,
  `render_queue_repaired(slug_count, backup_path)`,
  `render_queue_repair_failed(reason, backup_path)`,
  `render_systemic_failure(plan_slug, phase, signature)`,
  `render_stuck_blocker(plan_slug, blocker_id, phase, question, options, age_min)`,
  `render_stalled_claim(plan_slug, phase, age_min)`,
  `render_worktree_missing(plan_slug, worktree_path)`,
  `render_worktree_conflict(...)`,
  `render_quota_paused(plan_slug, line, paused_until)`,
  `render_quota_stuck(plan_slug, line, quota_file)`,
  `render_quota_resumed(plan_slug)` —
  kind-specific bodies. Render-only; no I/O.
- `quota_pause_notification(plan_slug, line, paused_until, quota_file)`
  — `(kind, body)` selector shared by the three death sites: a parseable
  reset (`paused_until` set) routes to `KIND_QUOTA_PAUSED`, an
  unparseable one (`None`) to `KIND_QUOTA_STUCK`. Single source of truth
  for the paused-vs-stuck branch so the supervisor and dispatch sites
  can't drift.

**Invariants and gotchas**

- Quiet hours use local time. Don't switch to UTC to "match"
  `state.py`; quiet hours are user-facing wall-clock semantics.
- `notify()` swallows `(subprocess.SubprocessError, OSError)` from
  individual backends and logs to stderr — a broken Messages.app or
  Discord outage can't take down the supervisor. Per-backend silent
  failures (e.g. osascript returning non-zero) are the backend's
  responsibility to surface (see `notify_imessage.py` § "Invariants").
- Adding a new kind: declare the constant, add a `render_*` function,
  and decide whether it goes in `QUIET_HOURS_BYPASS_KINDS` and
  `supervisor.ACTION_NOTIFY_KIND`. Those two membership tests are the
  full integration surface.
- Adding a new transport: implement `Notifier`-shaped class with
  `kind_name`, `from_spec(channel)`, and `send(kind, body, *, plan_slug, blocker_id)`;
  add it to `_NOTIFIER_REGISTRY`.

**See also**

- `operations.md` for the notification model in user terms (kinds,
  quiet hours, reply grammar).
- `notify_imessage.py` and `notify_discord.py` for the per-backend
  transport details.
- `notify_inbound.py` for the symmetric inbound path.

### `notify_imessage.py`

Outbound iMessage transport. Spawns `osascript` to drive Messages.app;
argv carries the handle + body so user-controlled text never touches
the AppleScript source.

**Key types and functions**

- `IMessageNotifier` — Notifier-shaped class registered in
  `notify._NOTIFIER_REGISTRY` under `kind_name = "imessage"`.
  `from_spec(channel)` reads `channel.params["to"]`.
- `_osascript_send(to, body)` — the fire-and-forget Popen call.
  stdout=DEVNULL; stderr appended to `imessage_log_path()` so
  AppleScript runtime errors land in a tail-able file.
- `imessage_log_path()` — resolves to
  `$XDG_CONFIG_HOME/clu/imessage.log` (default
  `~/.config/clu/imessage.log`), XDG-safety-guarded.

**Invariants and gotchas**

- `osascript` is invoked via `subprocess.Popen` with
  `start_new_session=True` and stdout DEVNULL'd — a hung Messages.app
  must not deadlock cron. Stderr is captured to a log file
  (`imessage_log_path()`); pre-#49 it was DEVNULL'd, so all AppleScript
  failures (Automation permission denials, buddy lookup, etc.) vanished
  silently. Never re-introduce `stderr=DEVNULL` here.
- Don't add `Popen.wait()` or `Popen.communicate()` to the happy path —
  fire-and-forget Popen semantics are load-bearing for cron-tick
  latency. If you need exit-code detection, poll on a short timeout
  AFTER the dispatch (so the success path stays zero-latency).
- Argv-passing: the AppleScript reads handle + body from `argv`, never
  string-interpolated into the script source. Don't refactor to
  inline — it's the injection guard.
- The log file's parent dir is created lazily on each send. First
  dispatch on a fresh host doesn't need any pre-init.

### `notify_inbound.py`

Long-lived poller over `~/Library/Messages/chat.db`. Cron handles
outbound; inbound needs a persistent process because chat.db has no
API. Reads are read-only via the SQLite URI mode.

The reply grammar:

```
^\s*(?:<plan-slug>\s+)?[0-9]\s*$
```

A bare digit is honored only when exactly one plan on the host has an
open blocker. With more than one, the poller refuses to guess.

**Key types and functions**

- `OpenBlocker` — frozen dataclass: `project_root`, `plan_slug`,
  `blocker_id`.
- `REPLY_RE` — the compiled grammar. Uses `state.SLUG_PATTERN` as a
  fragment so drift between slug regex and inbound matching is
  impossible.
- `route_reply(text, open_blockers)` — pure function: returns
  `(target, "<digit>")` or `None`. The disambiguation rule lives here.
- `poll_once(conn, last_rowid, *, entries_fn, shell_answer_fn, tick_spawner)` —
  one read of chat.db; calls `state_locator.find_blocker_for_reply` for
  each row. Returns the new high-water rowid. Always advances past every
  row read.
- `open_chat_db(db_path)` — opens the SQLite connection in read-only
  URI mode.
- `read_seen(path)`, `write_seen(path, rowid)` — checkpoint helpers for
  `~/.clu/seen_msg_rowid`.
- `_cli_dispatch(target, answer)` — default dispatcher; shells out to
  `python -m end_of_line.cli answer`. Injectable for tests.
- `main(argv)` — the daemon loop, polled every 4 seconds.
- `DEFAULT_CHAT_DB`, `DEFAULT_SEEN_PATH`, `DEFAULT_POLL_SECONDS`,
  `POLL_BATCH_LIMIT` — tunables.

**Invariants and gotchas**

- chat.db is opened with `mode=ro`. Never widen — full disk access is a
  Mac entitlement; writes are not something we want this process to
  have the option of doing.
- `poll_once` always advances `last_rowid` past every row read, matched
  or not. Otherwise a chatty stranger could pin the cursor on an old
  digit-shaped message and resurrect it once a future blocker opens.
- `POLL_BATCH_LIMIT` (500) caps first-tick blowup when `seen_rowid=0`
  meets a chat.db with years of history.
- `route_reply`'s ambiguous-bare-digit case returns `None` (drop the
  reply) rather than guessing. The render_blocker hint already nudges
  users toward `<slug> <digit>`.
- Errors inside the loop are caught and logged; the daemon never exits
  on a transient `sqlite3` / dispatch error.

**See also**

- `operations.md` for the LaunchAgent plist that keeps this alive.
- `notify.py` for the outbound render that defines the prompt grammar.

### `state_locator.py`

Single-responsibility module: "which plan's blocker does this reply
target?" Extracted from the three callers that each maintained a private
registry walk (`notify_imessage_inbound`, `cli.cmd_answer`,
`notify_discord_inbound`).

**Key types and functions**

- `LocatorResult` — dataclass: `variant` (`FOUND | AMBIGUOUS | NOT_FOUND`),
  optional `state_path`, `blocker_id`, `answer_index`, `project_root`, and
  `candidates: list[OpenBlocker]` for the `AMBIGUOUS` case.
- `find_blocker_for_reply(entries, reply_text) -> LocatorResult` — walks
  the registry, loads each plan's state file tolerantly (skipping
  unreadable ones with a log warning), and resolves `reply_text` to a
  single open blocker.

**Invariants**

- Exactly one registry walk in the codebase lives here. Callers must not
  re-implement the walk.
- State files that are missing, corrupt, or schema-mismatched are skipped;
  the walk continues with the remaining plans.
- `AMBIGUOUS` is returned when a bare digit matches multiple plans; callers
  decide the UX (drop silently, print to stderr, etc.).

### `registry.py`

Host-level index of `(project_root, plan_slug)` pairs. Multi-plan
features — fleet view, inbound reply routing — walk this to find every
state file on the host without scanning the filesystem.

Stored at `$XDG_CONFIG_HOME/clu/registry.json` (default
`~/.config/clu/registry.json`).

**Key types and functions**

- `PlanEntry` — frozen dataclass: `project_root`, `plan_slug`,
  `registered_at`.
- `SCHEMA_VERSION` — independent from `state.SCHEMA_VERSION`; passed to
  `state.load` via `expected_version`.
- `registry_path()` — resolves `$XDG_CONFIG_HOME` → path. Don't inline.
- `entries(path=None)` — list every registered plan.
- `register(project_root, plan_slug, *, path=None)` — add a pair.
  Returns `False` if it was already present. Auto-invoked by `clu init`.
- `unregister(project_root, plan_slug, *, path=None)` — remove a pair.
  Returns `False` if it wasn't there.
- `load_entry_state(entry)` — `entry → loaded state dict` or `None`.
  Tolerant of every failure mode (missing project, deleted state file,
  schema drift). Never raises.
- `_mutate(path)` — internal lock + load + atomic-write helper. Mirrors
  `state.mutate` but tolerates a missing file (first register creates).

**Invariants and gotchas**

- Reads and writes go through `state.locked` + `state.save_atomic`. The
  same primitive that protects per-plan state protects the registry —
  no second locking model.
- `load_entry_state` is the boundary between "registry says X exists"
  and "X is actually loadable." Every multi-plan walker (fleet,
  inbound) MUST go through this so a stale entry can't take them down.
- `register` validates `plan_slug` and resolves `project_root` to an
  absolute path — that absolute string is what unregister keys against,
  so two calls with different relative paths to the same dir collapse
  correctly.

**See also**

- `fleet.py` and `notify_inbound.py` are the two consumers.
- `contract.md` § "Host-level registry" for the JSON shape on disk.

### `queue.py`

Per-project plan queue. Holds the list of plans waiting to be `init`ed
after the current one finishes. Storage at `<plan_dir>/.orchestrator/
queue.json`; schema in `contract.md` § "Queue schema". The
auto-repair safety boundary lives here too — `validate_repair` is what
makes "trust the prompt" optional.

**Key types and functions**

- `SCHEMA_VERSION` — independent from `state.SCHEMA_VERSION`; passed to
  `state.load` via `expected_version`.
- `_empty()` — fresh shape: `{"schema_version": 1, "queue": [],
  "history": []}`. Private to the module; callers use `mutate`.
- `load(path)` — `state.load` with the queue's schema version. Raises
  `FileNotFoundError`, `json.JSONDecodeError`, or
  `state.SchemaVersionMismatch`. Callers (cli + supervisor) bundle these
  into `_QUEUE_LOAD_ERRORS` for `try/except`.
- `save_atomic(path, data)` — thin alias over `state.save_atomic`.
- `mutate(path)` — lock + load + yield-for-mutation + atomic-write,
  tolerant of a missing file via `state.locked_json(empty=_empty)`. The
  read-modify-write helper for every queue write.
- `best_effort_extract_slugs(data: bytes)` → `set[str]` — regex over raw
  bytes for every `"slug": "..."` match. Catches catastrophic loss; the
  worker can't surgically corrupt around it because the slug values
  usually survive even a truncated JSON.
- `best_effort_extract_history_slugs(data: bytes)` → `set[str]` — scans
  the `"history": [ ... ]` block specifically, with a small
  bracket-counter that respects escaped strings. Pending-only slug set
  is `best_effort_extract_slugs - best_effort_extract_history_slugs`.
- `validate_repair(backup_bytes, repaired_path)` → `ValidationResult` —
  the hard slug-preservation check. Returns
  `ValidationResult(ok=False, reason=...)` on any rule violation;
  caller MUST revert from backup when `ok=False`. See `contract.md`
  § "Auto-repair contract" for the rules.
- `ValidationResult` — dataclass: `ok: bool`, `reason: str | None`.
- `read_throttle(throttle_path, diagnosis_hash)` → `int` — current
  attempt count for `diagnosis_hash`. Returns 0 on any read failure
  (FileNotFound, corrupt JSON, mismatched hash) — we don't want a
  "repair-the-throttle" sub-failure.
- `increment_throttle(throttle_path, diagnosis_hash)` — bump the
  counter. Writes
  `{"attempts": N, "last_at": "...", "diagnosis_hash": "..."}`.
- `reset_throttle(throttle_path)` — unlink the throttle file. Called
  after a successful repair so the next failure starts fresh.

**Invariants and gotchas**

- The validation step is the safety boundary, NOT the worker's prompt.
  Even a perfectly-prompted worker can hallucinate; the regex over the
  backup bytes is what makes "delete slug X to make the file parse"
  impossible to slip past.
- `history` is append-only at the semantic level — `validate_repair`
  enforces it. `cmd_queue_remove` and the supervisor's
  abandon/absorb branches all append; no code path removes.
- `read_throttle` resets to 0 on a hash mismatch: a *different*
  corruption gets its own three attempts. The throttle is per-error-
  type, not per-file-lifetime.
- Slug regex (`_SLUG_RE`) is bytes-mode and case-sensitive — it matches
  exactly what `state.SLUG_PATTERN` accepts via JSON. Don't widen.

**See also**

- `contract.md` § "Queue schema" and "Auto-repair contract" for the
  on-disk shape and worker/clu responsibility split.
- `dispatch.dispatch_repair_worker` for the spawn side.
- `cli.cmd_queue_*` for the operator surface.

### `monitor.py`

Background-monitoring marker file (account-wide, not per-project). The
`clu install-hook` CLI writes this after registering the
`UserPromptSubmit` hook in `~/.claude/settings.json`; clu CLI commands
read it to suppress monitoring tips when the hook is already in place.
Tolerant by design — missing file, corrupt JSON, schema mismatch, and
legacy v1 markers all surface as `None` / `False` so the install
workflow re-runs cleanly.

**Key types and functions**

- `SCHEMA_VERSION = 2` — bumped from v1 (the broken `/schedule`-based
  install) when `clu install-hook` shipped. v1 markers are treated as
  "needs reinstall" rather than migrated in place.
- `marker_path()` → `Path` — XDG-respecting location
  (`$XDG_CONFIG_HOME/clu/monitor.json` or `~/.config/clu/monitor.json`).
- `load_marker(path=None)` → `dict | None` — marker contents, `None`
  on any failure mode (missing, corrupt JSON, schema version mismatch,
  v1 legacy marker).
- `is_scheduled(path=None)` → `bool` — `True` iff `load_marker` returns
  a dict. The single predicate every CLI suppression branch keys off.
- `record_hook_installed(hook_path, settings_json_path, *, path=None)` —
  atomic v2 marker write via `state.locked_json`. Overwrites stale v1
  markers in place.
- `clear_marker(path=None)` — idempotent delete; no error on absent
  file. Invoked by `clu uninstall-hook` after `settings.json` is
  pruned.

**Invariants and gotchas**

- The marker is advisory, not load-bearing. A drifted marker (e.g.
  operator hand-edited `settings.json` to remove the hook) makes the
  CLI suppress the install tip wrongly until `clu uninstall-hook` is
  run. v2 trusts the marker — coupling clu to `settings.json`
  introspection on every CLI invocation would be wasted I/O.
- `record_hook_installed` follows the "write after side effect"
  ordering: `clu install-hook` updates `settings.json` first and only
  writes the marker on success. A failed install leaves the marker
  absent so the next attempt retries cleanly.
- The path resolution mirrors `registry.registry_path()` — same XDG
  rules, same parent directory (`$XDG_CONFIG_HOME/clu/`).

**See also**

- `operations.md` § "Background monitoring" for the user-facing setup
  + reset workflow.
- `contract.md` § "Background-monitoring marker" for the v2 JSON shape
  and the v1 → v2 migration story.
- `cli.cmd_install_hook` / `cli.cmd_uninstall_hook` for the install
  workflow that writes/clears this marker.
- `end_of_line/skills/clu-monitor/SKILL.md` for the skill that shells
  out to `clu install-hook` on the operator's behalf.

### `inbox.py`

Per-event JSON inbox surfaced to active Claude Code sessions via the
`UserPromptSubmit` hook. One file per event under `~/.config/clu/inbox/`,
mark-and-sweep dedup into a `processed/` subdir.

**Key types and functions**

- `SCHEMA_VERSION = 1` — embedded in every event payload; the hook
  ignores events with a higher version it doesn't understand.
- `inbox_root()` → `Path` — XDG-respecting
  (`$XDG_CONFIG_HOME/clu/inbox/` or `~/.config/clu/inbox/`).
- `write_event(*, type, plan_slug, project_root, summary, details=None, inbox=None)` → `str` —
  atomic `tmp + rename` write of one event JSON; returns the event id
  (`evt-<8hex>`). `project_root` is resolved to an absolute path so the
  hook's `git rev-parse --show-toplevel` filter compares apples to
  apples.
- `read_unprocessed(inbox=None)` → `list[dict]` — every payload in
  `inbox/` (NOT `inbox/processed/`), sorted by filename (== arrival
  order thanks to the `time.time_ns()` suffix). Corrupt files are
  silently skipped.
- `mark_processed(event_id, inbox=None)` → `None` — scans for the file
  whose payload has `id == event_id` and moves it into `processed/`.
  Idempotent: missing inbox, empty inbox, and unknown id all return
  silently — never propagate cleanup failures into the hook.
- `list_for_project(project_root, inbox=None)` → `list[dict]` —
  `read_unprocessed` filtered to events whose `project_root` resolves
  to the given path. The hook calls this once per Claude turn.

**Invariants and gotchas**

- Filename format `<safe_ts>-<time_ns>-<type>-<short>.json` makes
  lexical order == arrival order. The `time_ns()` suffix is the
  monotonicity guarantee (the second-resolution `timestamp` field ties
  under tight-loop writes); the 8-char short id is the cross-process
  collision tiebreaker.
- No flock on writes — each event is its own file, atomic via
  `tmp + rename`. Concurrent writers race on filenames, but the
  ns-suffix + random-short combo is collision-free in practice.
- `mark_processed` reads every event in the directory looking for a
  matching id. This is O(N) but N caps low — the surfacer processes
  events serially, and the hook is bounded to 20 events per turn.
- Inbox writes are unconditional w.r.t. quiet hours. `notify.notify()`
  gates the iMessage but always calls `write_event` when `plan_slug` +
  `project_root` are in scope. The asymmetry is deliberate: the inbox
  is for the next Claude turn, not for waking the operator.

**See also**

- `contract.md` § "Inbox event files" for the JSON payload shape and
  filename convention.
- `end_of_line/hooks/clu_inbox_surface.py` — the canonical consumer;
  reads stdin, calls `list_for_project`, emits
  `hookSpecificOutput.additionalContext`, calls `mark_processed` per
  surfaced event.
- `notify.notify()` for the integration point that writes inbox events
  alongside iMessage sends.

### `watch.py`

Streaming projection of plan state-machine events for AI-agent
consumption (Claude's `Monitor` tool). Polls state files on disk and
emits one concise line per meaningful transition to stdout.

**Key types and functions**

- `project_event(event, plan_slug, *, verbose=False) -> str | None` —
  pure projector. Given a raw event dict and the plan slug, returns a
  rendered one-liner (e.g. `"my-plan/setup: started (attempt 1)"`) or
  `None` if the event is filtered out. Verbose-only events return
  `None` when `verbose=False`. All field truncation (100-char cap) and
  phase-prefix logic lives here.
- `project_event_task(event, plan_slug, *, verbose=False) -> str | None` —
  task-list projector. Same inputs as `project_event`; returns a
  `TASK_CREATE` or `TASK_UPDATE` protocol line for Claude's TaskCreate
  UI, or `None` for events with no task-list representation. Phase-
  scoped lines carry `parent=<slug>` (right after `task=`); plan-scoped
  lines (`plan_completed`, `paused`, `resumed`) omit it. Status mapping:
  `phase_started` → `in_progress`, `phase_completed` → `completed`,
  `plan_completed` → `TASK_UPDATE task=<slug> status=completed`,
  blocked/stalled/paused/resumed → `in_progress` with a descriptive
  `msg=`. Double-quotes in `msg` are escaped; msg is capped at 100
  chars. See `_TASK_STATUS_MAP` for the full mapping.
- `bootstrap_task_list(state_paths, cfg_loader, sink)` — emit one
  `TASK_CREATE task=<slug> status=pending` line for each watched plan,
  followed by one `TASK_CREATE task=<slug>/<phase_id> parent=<slug>
  status=pending` per phase in the master plan's `## Sessions index`.
  The parent line itself omits `parent=`; child lines always include
  it. Called by `stream_loop` before the first poll tick when
  `task_list_mode=True`. `cfg_loader` is a callable
  `(state_path) -> ProjectConfig` so tests can inject fakes. Missing
  master plan → `UNKNOWN_TASK` (6). Empty Sessions index → emits only
  the parent TASK_CREATE.
- `stream_loop(state_paths, *, json_mode, verbose, sink, poll_interval,
  max_ticks, _before_first_tick, task_list_mode) -> int` — poll loop
  over a list of state file paths. On startup, emits a `[snapshot]`
  baseline line per plan (current status + active phase), sets
  per-path cursors, then polls at `poll_interval` seconds. New events
  since the last tick are projected through `project_event` (text
  mode), `project_event_task` (task-list mode), or the JSON encoder
  (json mode) and written to `sink`. Returns `ExitCode.OK` (0) on
  SIGINT. `max_ticks`, `_before_first_tick`, and `task_list_mode` are
  test seams / mode switches; `task_list_mode=False` by default.
  `json_mode` and `task_list_mode` are mutually exclusive.
- `_DEFAULT_VISIBLE` — `frozenset` of event type strings emitted by
  default (phase starts/completes, blocks, answers, max-attempts,
  stalls, dispatch failures, worktree issues, queue pops, etc.).
- `_VERBOSE_ONLY` — `frozenset` for noisy bookkeeping events (lease
  expiry, force-releases, heartbeat-based notifications, worktree
  lifecycle). Only emitted with `verbose=True`.
- `_TASK_STATUS_MAP` — `dict` mapping event type strings to
  `(status, msg_template)` pairs for the task-list projector. Hard-
  coded; not configurable.

**Invariants and gotchas**

- `project_event` is a pure function — no I/O, no state mutation. Safe
  to call from tests without any setup.
- Queue v2 constants (`EVENT_QUEUE_APPENDED`, `EVENT_QUEUE_REJECTED`)
  are spliced in via `getattr(st, ..., None)` — the module loads
  cleanly on builds that predate the queue-worker-callback merge.
- `stream_loop` silently drops state paths that go missing mid-watch
  (plan archived while watching) — the cursor map entry is removed and
  no error is emitted.
- `_slug_for_path` extracts the plan slug from `<slug>.state.json` by
  stripping the `.state` suffix from the stem. Relies on the canonical
  naming convention from `state.state_path_for`.

**See also**

- `cli.py` `cmd_watch` for the argument-parsing wrapper.
- `operations.md` § "Live in-session feed (`clu watch`)" for usage and
  Monitor-tool pairing.

### `top.py`

Read-only `top`-like dashboard of every active worker on the host (the
`clu top` command). Where `watch.py` *streams* state events, this is a
*snapshot poller*: each tick it joins each plan's claim state with the
worker's own Claude Code session transcript and renders one row per
active worker. The command / file-write / assistant-line columns come
from the transcript (harness-written), not the worker's self-report — so
the view is an independent check that a worker is producing work.

**Key types and functions**

- `gather_rows(*, projects_root, now, project_filter) -> list[dict]` —
  one render-row per active claim across every registered plan
  (`registry.entries()` → `registry.load_entry_state`), optionally scoped
  to one project. Derives the worker cwd (worktree path or project root),
  locates + tails its transcript, joins the activity with claim state.
  Tolerant at the per-plan level (an unreadable plan/transcript
  contributes nothing); a corrupt host registry surfaces rather than
  rendering an empty dashboard.
- `locate_transcript(cwd, projects_root, session_id=None) -> Path | None`
  — finds the transcript under `~/.claude/projects/<encoded-cwd>/`. With
  `session_id` (stamped on the claim when `dispatch.command` uses
  `{session_id}`) it reads the exact `<id>.jsonl`; otherwise it
  forward-encodes the cwd, confirms each candidate by its in-file `cwd`
  field, rejects `isSidechain` subagent transcripts, and picks the
  newest. The cwd encoding is lossy and non-reversible (every non-alnum
  char → `-`), so in-file confirmation — never the dir name alone — is
  load-bearing. A stamped id whose exact file is absent falls back to
  cwd-matching.
- `tail_records(path, want=60) -> list[dict]` — bounded seek-from-end
  read of the last `want` JSON records; tolerates a half-written final
  line and undecodable lines.
- `extract_activity(records) -> dict` — defensive reduce to the latest
  signals (last Bash command + running flag, last file write, last
  assistant text, last-activity timestamp, token usage). Switches on
  `type`, tolerates string-or-array `message.content` and unknown kinds
  (the transcript schema drifts across Claude Code versions).
- `format_rows(rows, *, width) -> list[str]` — compact view, one row per
  worker, single source of layout. Columns size to `width` by priority:
  name / command / wrote get their full content first, SAYING absorbs the
  remainder; all three shrink proportionally only when the terminal is
  too narrow. Free-text cells collapse newlines/control chars so one
  worker stays on one line.
- `format_detail(rows, *, width) -> list[str]` — detail view; each worker
  is a block with a metadata line plus full, word-wrapped COMMAND and
  SAYING (never truncated). Toggled live with `w`.
- `run(*, once, interval, project_filter, projects_root, stream, cols) -> int`
  — entry point: curses when attached to a TTY (`q` quits, `w` toggles
  detail), a single plain snapshot for `--once` or non-TTY. `cols` (the
  `--cols` flag) narrows the compact table to a subset of metric columns
  via the `top_registry` table pane; `None` is today's full 8-column
  `format_rows` output, byte-for-byte.

**Invariants and gotchas**

- `format_rows` / `format_detail` / `extract_activity` / `tail_records` /
  `locate_transcript` are pure over their inputs — testable with fixture
  JSONL, no curses or registry setup.
- The transcript is written by the *worker's* Claude Code harness, so it
  is independent of the worker LLM's self-report — but it is not a
  third-party observer. The fully-external columns are PID liveness
  (`state.claim_worker_alive`, a `kill -0` probe) and the git-based
  cross-checks (parking-lot, not yet built).
- v1 follows only the worker's MAIN session, not its subagent/sidechain
  transcripts; the main transcript still advances (the `Agent` tool_use
  and its result) while subagents run, so liveness/progress detection is
  unaffected.

**See also**

- `cli.py` `cmd_top` for the argument-parsing wrapper.
- `dispatch.py` for `{session_id}` generation + `_stamp_pid` stamping.
- `operations.md` § "Watching workers — `clu top`" for usage, the
  `{session_id}` placeholder, and the `w` detail toggle.

### `top_registry.py`

The metric/pane registry for `clu top` (clu-top-tui Phase 1). Each of
today's 8 table columns is now a self-contained `Metric`; the compact
table is a `Pane`. A new column or pane is added in *this* file — no
edits to the draw loop or the layout engine.

**Key types and functions**

- `Metric` (frozen dataclass) — `{key, label, compute(snapshot, row) -> v,
  render(v, width) -> cell, sort_key, cost, align, fixed_width, max_width}`.
  `compute` pulls a value off the row dict; `render` formats one cell. The
  split lets a metric sort by the raw value and lets a future cross-row
  metric reach the whole `Snapshot` without touching the renderer.
- `Pane` (frozen dataclass) — `{kind, metric_keys, render(snapshot, *,
  width, cols)}`. The one built-in `table` pane is byte-identical to
  `top.format_rows` for the default column set (it delegates straight to
  it); a `--cols` subset takes a small composition path over the named
  metrics instead.
- `Snapshot` — wraps one tick's `gather_rows()` so the JSONL parse happens
  once; a per-tick value object, never cached across ticks (a stale
  snapshot would show last tick's workers).
- `register_metric(...)` / `register_pane(...)` — decorators that populate
  the `METRICS` / `PANES` module dicts. `DEFAULT_COLS` is the 8-column
  order; `metric_keys()` is the known-key set `--cols` validates against
  (the 8 defaults plus the modular extras).
- The modular metrics (Phase 4, `--cols`-selectable, not in the default
  table): `health` (fused glyph), `tokens`, `attempts`, `lease`,
  `progress`. Each is added in this file alone — the proof the registry
  needs no engine edit.
- `worker_health(*, alive, act, hb, stuck) -> "ok"|"warn"|"dead"` — the
  fused-glyph classifier (D8). `dead` dominates; otherwise any of a
  stale/absent ACT (`> _ACT_WARN_SECONDS = 60`, pinned to the web),
  an explicit stuck-tool marker, or a heartbeat past
  `_HB_WARN_SECONDS = 25min` tips an otherwise-green worker to `warn`.
- `token_total(usage)` / `token_human(n)` — sum the raw usage dict's flat
  numeric values and format compactly (`1.25M` / `45K`); byte-for-byte the
  same math as `web/index.html` `tokenTotal` / `tnum`, so the curses and
  web token columns never disagree.
- `safe_render(pane, snapshot, *, width, cols) -> list[str]` — the
  per-pane error boundary: a pane whose render raises becomes a single
  inline error band, so one bad pane never crashes the TUI.
- `parse_cols(spec) -> tuple[str, ...]` — parse/validate a `--cols a,b,c`
  spec; raises `ValueError` (→ clean argparse usage error) on an unknown
  key or empty spec.

**Invariants and gotchas**

- **`gather_rows()`'s row dict is a FROZEN wire contract (D10):** `clu
  serve` reads the same keys off `/api/workers` and renders them in its
  own JS, with zero shared code. Every metric reads FROM the row dict;
  none reshapes it. The contract is **append-only** — Phase 4 added six
  keys (`stuck`, `attempts`, `max_attempts`, `lease_remaining_seconds`,
  `phase_index`, `phase_total`), each mirrored into `web/index.html`'s
  `toView` so both renderers see them; nothing was renamed or dropped.
  `tests/test_top.py:GatherRowsWireContractTest` is the guard — it asserts
  the full key set (19 keys) by exact name, across all three row types.
- **Three row tiers** share that one key set: a clu worker (claim-derived
  fields populated), a claimless **blocked** row (the `blocked`/
  `blocker_question`/`blocked_seconds` discriminators), and a non-clu
  **session** row (`assemble_session_row` — the `session`/`session_name`/
  `session_id` discriminators, claim/plan fields `None`). A session is a
  fresh non-worker Claude transcript in a registered project
  (`gather_session_rows`); every render surface checks `session` (then
  `blocked`) BEFORE the dead path, since both carry a non-live `alive`.
  Health glyph `◇`, PID cell `sess`; the NAME cell shows `session_name`
  via the shared `top.row_display_name`.
- Import direction (no module-level cycle): `top_registry` imports pure
  helpers from `top` at module level; `top` imports `top_registry` lazily,
  inside its render functions (mirrors `top_render`).

### `webserver.py`

The `clu serve` command — a read-only web dashboard over the same
`top.gather_rows()` data. Where `top.py` renders to curses, this serves
the rows as JSON to a bundled HTML page (`web/index.html`). Localhost-only
and unauthenticated by default; one `--lan` switch flips on the entire
security layer (token auth, Host-header allowlist, auto self-signed HTTPS).

**Key types and functions**

- `ServeConfig` — the resolved run config the server + handler close over:
  bind `host`/`port`, `project_filter`, `include_transcript`, `token` (None
  → no auth gate), `host_allowlist`, `tls` context. `__post_init__` fills
  the allowlist from the bind host + loopback names.
- `build_config(*, lan, host, port, …, cert, key, http) -> ServeConfig` —
  the one place the security policy lives: resolves the bind host (loopback
  default / `--lan` LAN-IP / explicit `--host`), provisions a token for any
  exposed bind, enforces the "exposed bind needs a token" guardrail, and
  selects TLS (explicit `--cert/--key` > auto self-signed > `--http`
  cleartext). Raises `ConfigError` on an unsafe or contradictory config.
- `workers_json(*, project_filter, include_transcript) -> bytes` —
  `gather_rows()` as JSON; `include_transcript=False` drops the
  transcript-content fields (the `--no-transcript` feed).
- `make_handler(*, index_html, cfg)` — builds the
  `BaseHTTPRequestHandler`. `_dispatch` enforces, in order: Host-allowlist
  (`421`) → `/login` cookie mint → auth gate (`401`) → exact-match routes
  (`/`, `/api/workers`, `/api/feed`, else `404`). Request logging is
  silenced; a `gather_rows` exception yields `500` without killing the
  thread.
- `feed_json(query, *, project_filter) -> (status, bytes)` — the
  `/api/feed` endpoint: an incremental transcript tail for the detail
  pane's activity feed.
  `GET /api/feed?plan=<slug>&proj=<name>&phase=<id>&cursor=<n>&tid=<id>`
  → `{events: [{ts, kind, text}], cursor, tid, reset}`. `kind` is
  `say` (assistant text) / `tool` (Bash command) / `write` (write-tool
  file path) / `result` (tool_result text) / `agent` (an Agent/Task
  spawn — the subagent type, e.g. a `/code-review` fan-out); event text
  is truncated
  server-side at `FEED_TEXT_CAP` (2000 chars). `cursor` is a byte offset
  into the transcript: `-1` backfills the last `FEED_BACKFILL_BYTES`
  (64 KB); each poll reads at most `FEED_READ_CAP` (256 KB) and consumes
  only to the last complete line. `tid` (the transcript file stem =
  session id) binds the cursor to a transcript identity: a `tid` mismatch
  (new attempt) or a file shrunk under the cursor (rotation) answers
  `reset:true` with a fresh backfill. Bad slug/cursor → `400`; unknown
  plan, no live claim, claim on a different phase, or no transcript →
  `404`. `plan`/`phase` go through `state.validate_slug`; `proj` is
  matched against registry entry basenames, never path-joined.
  A **non-clu session** row has no claim/phase, so it feeds by session id:
  `GET /api/feed?proj=<name>&sid=<session-id>&cursor=<n>&tid=<id>`.
  `sid` present routes the session resolver (validated via
  `state.validate_slug`); otherwise the claim path above. Same
  `{events, cursor, tid, reset}` body.
- `read_feed_window(path, cursor)` / `record_events(rec)` /
  `resolve_feed_transcript(plan, proj, phase, *, project_filter,
  projects_root)` / `resolve_session_transcript(proj, sid, *,
  project_filter, projects_root)` — the pieces behind `feed_json`: the
  bounded cursor reader, the per-record event decoder (same record shapes
  as `top.extract_activity`, but keeping every occurrence rather than the
  last of each kind), the registry → claim → worktree-cwd →
  `locate_transcript` resolution `gather_rows` uses, and the session
  counterpart that resolves the EXACT `<sid>.jsonl` (no newest-file
  fallback — an unknown id must resolve to nothing, never another
  session's tail), `_confirms`-checked the same way.
- `build_server(cfg) -> _Server` — `ThreadingHTTPServer` (reuse-address,
  daemon threads); wraps the listening socket in TLS when `cfg.tls` is set
  (a wrap failure → `ConfigError`). `_Server.handle_error` is silenced so a
  scanned LAN server doesn't spew tracebacks.
- `serve(cfg) -> int` — installs SIGINT/SIGTERM handlers (shutdown fires on
  a separate thread to avoid the `serve_forever` deadlock), prints the
  banner (login URL + LAN/cleartext warnings, flushed), blocks in
  `serve_forever`.
- `detect_lan_ip()` — primary outbound IPv4 via the UDP-connect trick.
  `load_or_create_token()` / `ensure_self_signed()` — token + cert
  provisioning, written `0600`-from-birth (`mkstemp`+`os.replace`; openssl
  under `umask 0o077`). `_san_for` validates the bind value before it
  reaches the cert SAN; openssl is always an arg-list (`shell=False`), with
  a temp-config fallback for `-addext`-less builds.

**Invariants and gotchas**

- Auth comparison is `hmac.compare_digest` on both the `Bearer` header and
  the `clu_session` cookie; the token is never empty (None or 43 chars).
- The Host-allowlist is the primary DNS-rebinding defense and runs before
  auth. An absent `Host` is tolerated only on the unauthenticated loopback
  default; any exposed bind rejects it.
- Every worker-derived string in `web/index.html` is inserted via an
  `esc()` helper, never raw `innerHTML` — `/api/workers` and `/api/feed`
  carry semi-untrusted LLM/tool text (commands, SAYING, file paths,
  feed events).
- **`--no-transcript` disables `/api/feed` entirely** (the route is not
  registered → `404`): the feed is 100% transcript-content data, the
  exact class the flag strips from `/api/workers` rows. The feed also
  sits after the auth gate, so a tokened bind never serves transcript
  content unauthenticated.
- The page is read once at startup via `importlib.resources` (`web/*.html`
  package-data) and served from in-memory bytes by exact-match route —
  never a directory handler (no path traversal).

**See also**

- `cli.py` `cmd_serve` for the flag-parsing wrapper.
- `top.py` `gather_rows` for the row shape both surfaces share.
- `operations.md` § "Serving the dashboard on the web — `clu serve`".

### `demo_worker.py`

The synthetic, deterministic core of `clu demo` (the verify-the-install
tool). Fabricates real-format Claude Code session transcripts so `clu top` /
`clu serve` render live demo rows without a real LLM — zero token cost, fully
deterministic. The records must satisfy `top.py`'s locator/parser contract
exactly, or the dashboard shows empty rows.

**Key types and functions**

- `SCENARIOS = ("busy", "idle", "block", "dead")` — the four demo
  personalities, in dashboard order. `ACT_WRITE / ACT_QUIET / ACT_BLOCK /
  ACT_DEAD` — the per-step actions the loop dispatches on.
- `transcript_path(cwd, session_id, projects_root) -> Path` — reconstructs
  the exact path the locator globs (`encode_project_dir(cwd)/<session_id>.jsonl`).
- `build_records(scenario, step, *, cwd, session_id, now) -> list[dict]` — one
  step of synthetic work as JSONL records. Every record carries the real `cwd`
  and `isSidechain: False` (the locator's requirement) and exercises every
  `extract_activity` branch (a Bash command, a Write, an assistant line, token
  usage). `now` is the `...Z` stamp the parser ages into ACT. `busy`/`block`/
  `dead` leave the Bash command running (a live `*`); `idle` resolves it.
- `scenario_action(scenario, step) -> str` — the pure per-step planner: `busy`
  always works; `idle` works `_IDLE_WRITE_STEPS` then goes quiet; `block`/`dead`
  work `_PRE_LIFECYCLE_STEPS` then run their lifecycle event.
- `command_template(scenario, *, python) -> str` — the `.orchestrator.json`
  `dispatch.command`. Surfaces `{plan_slug}` as a bare, space-bounded positional
  (the #83 cmdline-marker reaper needs the slug as a whole token) and
  `{session_id}` so dispatch stamps a deterministic transcript filename.
- `run_worker(plan, phase_id, token, scenario, *, project, session_id, …) -> int`
  — the paced loop: write + heartbeat every `step_seconds` (default 5s, lease-
  safe), bounded by `max_steps` (~1h; teardown is the normal exit). `block`
  opens a blocker and returns; `dead` returns with no callback so dead-PID
  detection flags it. `clock` / `sleep` / `runner` are injectable so tests run
  with no wall-clock, sleep, or subprocess.

**Invariants and gotchas**

- The default `runner` invokes `clu heartbeat` / `clu block` in-process via
  `cli.main` — the demo worker is already a `clu` process, so this exercises
  the real token-validated callbacks without a subprocess.
- `cmd_demo_worker` calls `notify.set_global_suppress(True)`: the in-process
  `block` callback would otherwise fire a real iMessage/Discord push.
- The default `clock` is `state.utcnow` — the single source of truth for the
  dashboard `...Z` stamp.

**See also**

- `top.py` `extract_activity` / `locate_transcript` for the contract the
  records satisfy.
- `demo.py` for the orchestration that scaffolds + dispatches these workers.

### `demo.py`

The `clu demo` orchestration: scaffold + run + tear down a synthetic fleet
that verifies an install end-to-end. Decision A (operator-approved): demo plans
live in the *real* registry, namespaced `demo-`, so the operator's own
`clu top` / `clu serve` see them — with guaranteed teardown bounding orphan risk.

**Key types and functions**

- `DEMO_SLUG_PREFIX = "demo-"` · `demo_root() -> Path` — the throwaway project
  tree under `clu_config_dir()/demo`.
- `scaffold(scenarios, *, root) -> list[DemoPlan]` — writes one project per
  scenario (each needs its own `dispatch.command`, so each gets its own
  `.orchestrator.json` + one-phase master). The config masks every inherited
  global notify channel (`{kind, enabled: false}` per backend in
  `notify._NOTIFIER_REGISTRY`). Pure filesystem.
- `up(scenarios, *, root) -> list[DemoPlan]` — scaffold → `init` (auto-registers)
  → `tick` (dispatches each worker). `_dispatch` is split out so tests stub the
  real subprocess spawn.
- `sweep() -> list[str]` — every `demo-*` slug in the registry (host-wide; the
  `clu doctor` printer uses it).
- `down(*, root, projects_root) -> list[str]` — teardown: reap each live worker
  pgroup (`reap_orphan_pgroup`, slug-guarded against PID reuse), drop each
  project's whole transcript dir (re-dispatch mints a fresh `session_id` per
  attempt, so one session file would miss prior ones), unregister every `demo-*`
  plan, remove the tree. Idempotent; never touches non-demo entries.

**Invariants and gotchas**

- The notify mask is the *only* defense for the out-of-process cron supervisor
  (which would otherwise notify about the demo's dead worker); `cmd_demo` also
  `set_global_suppress` for the in-process path.
- `cmd_demo` runs `up()` and the foreground wait inside the teardown
  `try/finally`, with the SIGTERM trap installed first, so a partial-launch
  failure or a `kill` mid-launch still unwinds to `down()`.
- Project roots are `.resolve()`d so the registered root matches dispatch's
  `{project}` (both resolve), keeping the locator's cwd comparison exact.

**See also**

- `demo_worker.py` for the synthetic worker `up` dispatches.
- `cli.py` `cmd_demo` / `cmd_demo_worker` / `_print_demo_sweep_health`.
- `operations.md` § "Verify your install — `clu demo`".

### `fleet.py`

Pure projection: take every registry entry, project into a one-line
summary, render a table. This is what `clu` (no args) prints. Never
mutates, never writes.

**Key types and functions**

- `PlanSummary` — frozen dataclass: `plan_slug`, `project_root`,
  `status`, `current_phase`, `open_blocker_count`,
  `last_event_age_seconds`.
- `summarize_plan(entry)` — `PlanEntry → PlanSummary | None`. Returns
  `None` when `registry.load_entry_state` does, so the renderer can
  show a `missing` placeholder.
- `render(entries)` — formatted multi-line table (header + one row per
  plan). Returns a single string ending in a newline.
- `humanize_age(seconds)` — seconds → `"42s"` / `"3m"` / `"2.1h"` /
  `"1.5d"`.

**Invariants and gotchas**

- `stalled` status is derived here, not stored. If `current_claim`
  exists and its heartbeat age exceeds the threshold, the projection
  swaps the status to `STATUS_STALLED`.
- `missing` is a display-only label rendered when state can't be
  loaded — the registry knows about the plan but the state file is
  gone.
- This module imports `registry` and `state` only. No side effects, no
  network, no subprocess. Safe to call repeatedly.

**See also**

- `operations.md` for how the fleet view is meant to be read as a
  diagnostic tool.
- `registry.load_entry_state` for the tolerant loader the projection
  hangs off.

### `dry_merge.py`

Pure-function engine for textual + suite integration testing of parallel
branches. No state I/O; no cross-plan rule logic. Called by
`cross_plan_rules.dry_merge_gate_rule` (automatic), `cmd_validate`
(on demand), and both `cmd_ship` direct + as-pr paths (validate gate).

**Key types and functions**

- `MergeResult` — dataclass: `outcome` (`"clean" | "textual_conflict" |
  "suite_failed"`), `conflict_files`, `test_exit_code`, `stderr_tail`,
  `merged_branches`, `base_sha`.
- `attempt_merge(project_root, base_ref, branches, test_command=None, *,
  timeout=300) → MergeResult` — resolves `base_ref` to a SHA, creates a
  scratch worktree via `git worktree add --detach $(mktemp -d)`, merges
  each branch sequentially with `git merge --no-ff --no-edit`. On textual
  conflict returns immediately with `_OUTCOME_TEXTUAL_CONFLICT`. On clean
  merge, runs `test_command` if provided; outcome is `_OUTCOME_SUITE_FAILED`
  if the command exits non-zero. `try/finally` always tears down the scratch
  worktree — leak prevention is load-bearing.

**Invariants and gotchas**

- Caller owns the trust of `test_command`: it runs with `shell=True` inside
  the scratch worktree, no env isolation.
- `_STDERR_TAIL_CHARS = 2000` — cap on captured stderr for `MergeResult`.
- Teardown errors are printed to stderr but NOT re-raised. A leaked worktree
  is better than masking the actual merge result.

### `cross_plan_rules.py`

Post-loop rule chain that fires once per `cmd_tick_all` cycle for each
distinct project root. Enforces the "at most one cross-plan effect per
project per tick" invariant, paralleling `supervisor.tick`'s per-plan chain.

**Key types and functions**

- `ProjectPlan` — dataclass: `slug`, `state`, `state_path`.
- `RuleResult` — dataclass: `events_per_plan`, `rule_name`, `notifies`,
  `field_updates_per_plan`. Applied atomically by `_apply`.
- `ProjectRule` — `Callable[[Path, list[ProjectPlan]], RuleResult | None]`.
- `register_rule(rule)` — append to `_RULES`. Rules run in order; first
  non-None wins.
- `run_rules(project_root, plans) → RuleResult | None` — iterate `_RULES`,
  call each, apply and return on first non-None. Called by `cmd_tick_all`
  after the worktree conflict scan.
- `load_plans_for_project(project_root, cfg) → list[ProjectPlan]` —
  registry walk; tolerates missing/corrupt state files (logs + skips).
- `dry_merge_gate_rule(project_root, plans)` — fires when ≥2 DONE plans
  share a `batch_id` and have live worktree branches. See `architecture.md`
  § "Multi-plan batch integration gate" for trigger conditions, idempotency
  key, and clean/dirty outcomes.

**Invariants and gotchas**

- `_RULES` ordering matters: `queue_advancement_rule` and
  `worktree_conflict_rule` precede `dry_merge_gate_rule` by design.
- Rule-first-match-wins means the gate never fires in the same tick that
  queue advancement or conflict detection fires.
- `_apply` takes ALL paths from both `events_per_plan` and
  `field_updates_per_plan`; state writes are batched per path into a single
  `st.mutate` window.

### `cli.py`

argparse dispatch + the `ExitCode` enum + the `_die` helper + the
`@_translate_claim_mismatch` decorator + every subcommand. Both the
operator (`tick`, `status`, `pause`, `resume`, `retry`, `init`,
`register`, `unregister`, `list`, `answer`) and the worker
(`complete`, `block`, `spawn`, `task-done`, `heartbeat`,
`heartbeat-daemon`) talk to clu through this.

**Key types and functions**

- `ExitCode` — IntEnum: `OK`, `GENERIC`, `INVALID_SLUG`, `BAD_SHA`,
  `CLAIM_MISMATCH`, `SPAWN_CAP`, `UNKNOWN_TASK`, `STATUS_TRANSITION`,
  `REPAIR_DECLINED`, `WORKTREE_SETUP_FAILED`, `QUEUE_CAP`. Cron and
  inbound poller key off these codes. See `contract.md` § "Exit codes"
  for the full table.
- `_die(rc, msg)` — write `error: <msg>` to stderr, return `int(rc)`.
  Use this from every error path; don't return bare ints.
- `_translate_claim_mismatch(fn)` — decorator that catches a leaked
  `state.ClaimMismatch` and returns `ExitCode.CLAIM_MISMATCH`. Every
  worker-side command wears this so forged tokens get a uniform exit.
- `main(argv)` — argparse + dispatch table.
- Operator-side commands: `cmd_init`, `cmd_tick`, `cmd_tick_all`,
  `cmd_status`, `cmd_register`, `cmd_unregister` (+
  `cmd_unregister_one`, `cmd_unregister_all_archived`), `cmd_list`,
  `cmd_fleet`, `cmd_pause`, `cmd_resume`, `cmd_retry`, `cmd_answer`,
  `cmd_extend_lease`, `cmd_release_claim`, `cmd_archive`, `cmd_logs`,
  `cmd_prior_blocker`,
  `cmd_queue` (+ `cmd_queue_add`, `cmd_queue_list`, `cmd_queue_remove`),
  `cmd_worktree` (+ `cmd_worktree_gc`, `cmd_worktree_attach`,
  `cmd_worktree_reattach`),
  `cmd_blockers` (+ `cmd_blockers_list`, `cmd_blockers_show`),
  `cmd_install_skill`, `cmd_install_hook`, `cmd_uninstall_hook`,
  `cmd_doctor`, `cmd_watch`.
- `cmd_extend_lease(args)` — add N minutes to the live claim's expiry.
  `--project`, `--plan`, positional `minutes` (int, >0). New expiry =
  `max(now, current_expires) + timedelta(minutes=N)`. Appends
  `EVENT_LEASE_EXTENDED`. Operator-only; no `--token`.
- `cmd_release_claim(args)` — force-release the current claim. Flags:
  `--force` (skip the fresh-heartbeat guard), `--reason` (audit string),
  `--reset-attempts` (append `EVENT_ATTEMPTS_RESET` so next dispatch's
  attempt counter starts from zero).
- `cmd_tick_all` is the host-scoped cron entry point: walks
  `registry.entries()` and runs the per-plan tick + dispatch + notify
  dance for each, then makes a second pass over distinct project_roots
  for queue advancement via `_advance_queue_for_project`. Per-plan
  exceptions are caught and logged to stderr so one broken plan can't
  poison the cadence. Replaces the old `examples/clu-tick-all.sh`
  parser of `clu list` output.
- `cmd_queue(args)` — dispatch on `args.queue_cmd` to add / list /
  remove. Bare `clu queue` (`queue_cmd is None`) routes to `cmd_queue_list`,
  mirroring bare `clu` → `cmd_fleet`.
- `cmd_queue_add(args)` — append (or `--front` prepend) a plan slug to
  the project's queue. Two modes selected by the presence of `--token`:
  - **Operator mode** (no `--token`): multi-slug, `--front` allowed;
    refuses with a bootstrap message if no registered plans; refuses on
    `<plan_dir>/<slug>.md` absence; refuses with `STATUS_TRANSITION` on
    duplicate. Operator path is uncapped.
  - **Worker mode** (`--token T --plan S --phase X`): single slug,
    `--front` forbidden. Runs full claim validation, per-phase add cap
    (`max_queue_adds_per_phase`), and the nested lock sequence (state
    lock first, queue lock second). Emits `EVENT_QUEUE_APPENDED` or
    `EVENT_QUEUE_REJECTED` in the source plan's events. See
    `architecture.md` § "Worker enqueue flow" for the full validation
    order. Decorated with `@_translate_claim_mismatch`.
- `_cmd_queue_add_worker(args, cfg, queue_path)` — the worker-mode
  dispatch path extracted from `cmd_queue_add`. Sibling of `cmd_spawn`:
  both are worker callbacks that take `--token`, both wear
  `@_translate_claim_mismatch`, both open the source state under
  `st.mutate` before touching secondary resources (queue vs spawned-
  task list). Not a public CLI subcommand — called only by
  `cmd_queue_add` after mode discrimination.
- `cmd_queue_list(args)` — render the pending queue + a `Recent
  failures:` tail of the last 10 history entries. Uses `registry.entries`
  + `registry.load_entry_state` to derive a STATUS column per pending
  entry; the head's status drives a `chain frozen at head` NOTE when the
  freeze predicate fires.
- `cmd_queue_remove(args)` — pop the named pending slug + append a
  `history` entry with outcome `removed`. Returns `UNKNOWN_TASK` if the
  slug isn't pending.
- `cmd_blockers(args)` — dispatch on `args.blockers_cmd` to `list` or
  `show`. Bare `clu blockers` (no subcommand) prints usage to stderr
  and exits `GENERIC`.
- `cmd_blockers_list(args)` — read-only: prints open blockers (where
  `answer is None`) from `data["blockers"]` with id, phase, asked_at,
  question, and numbered options. Empty case prints `"no open blockers
  on <plan>"` to stdout and exits `OK`.
- `cmd_blockers_show(args)` — read-only: prints full payload for one
  blocker by id (question, options, context, asked_at, answer if set)
  plus any related events from `data["events"]` where
  `event.blocker_id` matches. Not-found → `UNKNOWN_TASK`.
- `_advance_queue_for_project(project_root)` — the supervisor-side
  queue-pop step (see `architecture.md` § "Queue advancement").
- `_detect_worktree_conflicts_for_project(project_root)` — the
  post-loop conflict scan (see `architecture.md` § "Worktree conflict
  scan"). Emits `EVENT_WORKTREE_CONFLICT_WARNING` + halt-bypass
  iMessage once per (project, sorted-slug-pair) onset.
- `_plans_for_project(project_root, cfg)` — generator yielding
  `(slug, state, state_path)` for every plan registered under the
  project. Centralizes the registry-walk + state-load pattern used
  by `_advance_queue_for_project`, `_detect_worktree_conflicts_for_
  project`, and `_active_no_worktree_siblings`.
- `_setup_worktree(args, cfg)` / `_rollback_worktree(project_root,
  record)` — `clu init --worktree` materializes a `git worktree add`
  on branch `clu/<slug>` forked from `--base-ref` (default HEAD).
  Returns the persisted `{path, branch, base_ref}` dict or an
  `ExitCode.WORKTREE_SETUP_FAILED` rc. Rollback on state-save
  failure tears down both the worktree dir and the branch.
- `_resolve_ref(project_root, ref)` — `git rev-parse --verify
  <ref>^{commit}` wrapper, returns the resolved SHA or `None`.
  Distinct from `_verify_commit_shas` which only handles raw SHAs.
- `cmd_worktree_gc(args)` — list candidates (status DONE / HALTED /
  HALTED_REPLAN + has worktree), `--confirm` runs `git worktree
  remove --force` with a 30s timeout, `--delete-branch` adds `git
  branch -D`, `--include-archived` widens to plans whose master
  `<slug>.md` is gone. Action-time re-reads each candidate's status
  so a `clu retry` mid-gc doesn't lose its worktree.
- `cmd_worktree_attach(args)` — retrofit a worktree onto a plan that
  was init'd without `--worktree`. Reuses `_setup_worktree` so the
  same refusals (`WORKTREE_SETUP_FAILED`) and rollback-on-state-save-
  failure shape apply. Refuses if `state.worktree` already exists.
- `cmd_worktree_reattach(args)` — recovery: re-create the worktree
  dir from the `path` + `branch` already recorded in `state.worktree`,
  using `git worktree add`. Refuses on a non-git target path so an
  operator can't silently re-attach to a directory git no longer
  manages. Distinct from `attach` — `attach` adds a worktree record
  where none exists, `reattach` materializes an existing record.
- `cmd_archive(args)` — post-ship cleanup: removes the clu-managed
  worktree + branch (when fully reachable from origin; warns and
  retains when ahead) AND `git mv plans/<slug>*.md
  plans/archive/<slug>/` (master + sub-plans). Idempotent on the
  file-move step (skips if already gone). Surfaces `WORKTREE_SETUP_FAILED` if `git mv` fails
  with the file present.
- `cmd_migrate_archive(args)` — one-shot migration from the pre-#65
  flat `plans/shipped/<file>.md` layout to the nested
  `plans/archive/<slug>/<file>.md` layout. Groups stems via
  `_group_shipped_files_by_master` (longest-prefix-master rule), runs
  one `git mv` per group into the target subdir, removes the empty
  `plans/shipped/` directory, and commits the renames in a single
  `chore: migrate-archive ...` commit. No-op when `plans/shipped/`
  is absent. `--dry-run` prints the grouping without mutating.
- `cmd_unregister_all_archived(args)` — batch prune of registry
  entries whose master plan file no longer exists. `--dry-run`
  previews. Emits a per-entry stderr warning when the orphan state
  file still has a `worktree` record (the operator should follow up
  with `clu worktree gc --include-archived --confirm`).
- `cmd_install_skill(args)` — copies the bundled `end_of_line/skills/*`
  trees into `~/.claude/skills/`. Flags: `--force` (overwrite regular
  files; symlinks always overwritten), `--dry-run` (preview), `--only
  <name>` (one skill), `--list` (enumerate bundled skills + exit).
  Also handles the `--add-claude-md-note` / `--no-claude-md-note` pair
  that appends an idempotent autonomous-loop hint to `~/.claude/CLAUDE.md`.
- `cmd_install_hook(args)` / `cmd_uninstall_hook(args)` — register or
  remove the `UserPromptSubmit` hook in `~/.claude/settings.json` that
  surfaces inbox events into the active Claude Code session. The
  `/clu-monitor` skill is the user-facing wrapper; the CLI is the
  underlying mechanism. Both maintain the v2 marker at
  `~/.config/clu/monitor.json`.
- `cmd_doctor(args)` — smoke-test what a worker subprocess sees:
  prints the effective PATH (after `dispatch.build_worker_env`),
  resolves common tools (`claude`, `gh`, `git`, `python3`), and exits
  `OK` / `GENERIC` based on whether the required ones are found. Also
  runs read-only health printers: notify / coolant / effort / stuck-tool
  / worker-idle, a **dispatch-permission guard**
  (`_print_dispatch_permission_health` — shlex-tokenizes
  `dispatch.command` and `dispatch.repair_command`, warns when either
  carries `bypassPermissions` or `--dangerously-skip-permissions`, and
  points at operations.md "Hardened worker dispatch"; tolerant of
  unparseable templates, mirroring `dispatch.resolved_model`), a
  **dispatch-marker guard** (`_print_dispatch_marker_health` — renders
  `dispatch.command` with a sentinel slug through the dispatch
  placeholder set and warns, via the production matcher
  `state._cmdline_marker_present`, when the slug doesn't survive as a
  bounded token, which blinds the PID-reuse liveness checks;
  `repair_command` excluded — repair workers carry no claim; #83), a
  **zombie-sweep dry-run preview**
  (`_print_zombie_health` → `supervisor.sweep_zombie_states(..., dry_run=True)`),
  and a **skill-drift guard** (`_print_skill_drift_health` — SHA-256
  compares each bundled skill against `~/.claude/skills/<name>/SKILL.md`,
  warns on mismatch). No state writes — purely diagnostic.
- `_ensure_worker_settings()` — `cmd_init` helper: when
  `~/.config/clu/worker-settings.json` is absent, writes it from the
  bundled `worker-settings.template.json` (`importlib.resources`,
  registered as package-data like `skills/`) and prints the path plus a
  hardened-command hint. NEVER overwrites an existing file — same
  operator-intent contract as `_ensure_quality_stub`. Template content:
  Seatbelt sandbox on, fail-closed, `clu *` exempt, network limited to
  GitHub (operations.md "Hardened worker dispatch").
- `cmd_watch(args)` — streaming state-event feed. Resolves state
  paths from the registry (single plan, all plans in a project, or
  registry-wide with `--all`), then delegates to
  `watch.stream_loop`. Args: `--project PATH`, `--plan SLUG` (mutually
  exclusive with `--all`), `--all`, `--json`, `--verbose`,
  `--interval FLOAT`, `--task-list`. Default mode (no
  `--plan`/`--all`): every registered plan in the CWD project. Exit
  codes: `OK` on SIGINT; `UNKNOWN_TASK` if `--plan` isn't registered.
  `--task-list` and `--json` are mutually exclusive; `--task-list` and
  `--all` are mutually exclusive (v1 limitation — multi-plan task trees
  deferred).
- `cmd_logs(args)` — tail the active worker's log
  (`<project>/plans/.orchestrator/logs/<phase>.<token>.log`); falls
  back to the newest log file when no claim is live. `--follow` for
  `tail -f` semantics.
- `cmd_prior_blocker(args)` — worker-side helper that prints the most
  recent answered blocker for the current phase, so a re-dispatched
  worker can read the operator's choice without parsing state.json
  itself.
- `_resolve_project_arg(args)` — centralizes the four-site
  `args.project or Path.cwd()` pattern with uniform `.resolve()`.
  `getattr` tolerates the bare `clu queue` shape where the
  Namespace has no `--project` attribute.
- `_handle_corrupt_queue(cfg, exc, queue_path)` — the auto-repair
  pipeline (see `architecture.md` § "Auto-repair worker").
- `_refuse_on_corrupt_queue(queue_path, exc)` — operator-at-keyboard
  refusal path for `cmd_queue_*`. Surfaces backup paths + a
  paste-into-Claude diagnosis. The auto-repair pipeline only runs from
  `cmd_tick_all`, never from the operator CLI.
- `_queue_footer(entries)` — one-line summary of pending queue work,
  printed under the fleet table when bare `clu` finds any project with
  a non-empty queue. `None` when nothing's pending and nothing's
  unreadable.
- `_QUEUE_LOAD_ERRORS` — `(JSONDecodeError, SchemaVersionMismatch,
  KeyError, OSError)` tuple every queue-loader wraps with `try/except`.
- `cmd_validate(args)` — operator-on-demand dry-merge (mode-agnostic;
  reused by `clu ship --check` in both direct and as-pr modes).
  Accepts `--project` + (`--batch B` or `--branches a,b`). Resolves
  branches via `cross_plan_rules.load_plans_for_project` in batch mode
  (filters to `status==done AND batch_id==B AND worktree != None`);
  drops branches whose refs can't be `git rev-parse`-d. Calls
  `dry_merge.attempt_merge` with `test_cmd = None if args.no_suite
  else cfg.test_command`. Prints outcome + conflict files to stdout;
  exits `OK` on clean, `GENERIC` on dirty. Does **not** mutate plan
  state and does **not** write follow-up plans (the cross-plan rule
  owns those).
- `cmd_integrate(args)` — stderr-warning **deprecation alias** for
  `cmd_validate`. The verb 'integrate' was misleading (never updated
  main; only dry-merged). Kept for one version of operator script
  compatibility; future code should call `cmd_validate` directly.
- `cmd_ship(args)` — one-action post-worker integration.
  Resolves mode from `--direct` / `--as-pr` flag or, when neither is
  set, from `cfg.dispatch.ship_mode` (default `"direct"`). Dispatches
  to one of `_cmd_ship_direct_plan`, `_cmd_ship_direct_all_done`,
  `_cmd_ship_as_pr_plan`, `_cmd_ship_as_pr_all_done`. All paths
  share: status-DONE gate, worktree gate, already-merged refusal,
  validate via `dry_merge.attempt_merge`, `--check` → validate-only
  exit, `--yes`-less preview exit. Direct paths additionally check
  canonical-dirty, merge with FF-first/merge-commit fallback, push
  origin main + branch, and trigger `_spawn_post_action_tick` so
  `auto_archive_rule` fires immediately. PR paths additionally run
  `_gh_preflight` (gh installed + authenticated), `_gh_create_pr`
  (idempotent via `gh pr view` fallback), and stamp
  `state.ship_pending = {"mode": "as_pr", "pr_url", "ts"}`.
- `_ship_apply_one_direct(project_root, branch, plan_slug)` and
  `_ship_apply_one_as_pr(project_root, cfg, branch, plan_slug,
  state_path)` — shared per-plan apply helpers consumed by both
  single-plan (`--plan`) and batch (`--all-done`) ship paths.
  Returns `(success: bool, message: str)`.
- Worker-side commands: `cmd_complete`, `cmd_block`, `cmd_spawn`,
  `cmd_heartbeat`, `cmd_task_done`, `cmd_verify`, `cmd_attest`. All
  require `--token` matching the live claim (except `cmd_verify` in
  operator mode), all wear `@_translate_claim_mismatch`.
- `cmd_verify(args)` — runs `quality.verify_command` (falling back
  to `test_command`) via `subprocess.run`. Captures HEAD SHA before
  the command starts (so a mid-test commit can't slip a stale SHA
  into the stamp). On rc=0 stamps `current_claim.attestations.verify`
  and emits `EVENT_VERIFY_STAMPED`. On rc!=0 exits non-zero with
  stderr tail; state file untouched so the operator can re-run.
  `--token` validates against the live claim when present (worker
  mode); operator omits it for manual re-verification or rescue.
- `cmd_attest(args)` — worker self-attestation. `--simplify` stamps
  `current_claim.attestations.simplify` with current HEAD and emits
  `EVENT_SIMPLIFY_STAMPED`. Token required. No command execution —
  clu cannot run `/code-review` (a Claude-side skill); the stamp is the
  worker's word that it ran. Extensible: future `--lint`,
  `--type-check` flavors stamp different keys on the same command.
  At least one flag required (bare `clu attest` is an error).
- `cmd_complete` gains `--skip-verify` and `--skip-simplify` flags.
  Each bypass emits `EVENT_OPERATOR_SKIP_VERIFY` /
  `EVENT_OPERATOR_SKIP_SIMPLIFY` as audit events; the phase still
  completes. Operator-owned — workers should `clu block` rather than
  skip. The quality gates evaluate in order: verify first (HEAD match
  required), then simplify (cumulative diff from branch base vs
  threshold).
- `_verify_commit_shas(project_root, shas)` — runs `git cat-file -e`
  per SHA; returns the first error or `None`. Called from
  `cmd_complete` and `cmd_force_complete`; any unknown SHA →
  `ExitCode.BAD_SHA`.
- `cmd_force_complete` — operator recovery for stalled-with-work-on-disk
  phases (#48). Validates phase id against `parse_sessions_index`,
  refuses on already-completed (`STATUS_TRANSITION`), unknown phase
  (`UNKNOWN_TASK`), and never-started phases without `--really`
  (`STATUS_TRANSITION`). Releases any active claim without token
  validation; emits `EVENT_OPERATOR_FORCE_COMPLETE` (audit) followed by
  `EVENT_PHASE_COMPLETED` so the supervisor's plan_done detection fires
  on the next tick via the existing path.
- `_format_heartbeat(data, claim)`, `_humanize_age(seconds)` — display
  helpers for `clu status`.

**Invariants and gotchas**

- Every worker-side CLI command MUST require `--token` and assert it
  through `state.assert_claim_match` before mutating. This is the
  whole security model; a new worker command that skips token check
  breaks it. Use the `@_translate_claim_mismatch` decorator + let
  `ClaimMismatch` propagate from inside `mutate()`.
- `args.plan` is validated through `state.validate_slug` at the top of
  `main`, before any `state_path` resolution. Any subcommand that takes
  a phase id (e.g. `--phase`) also re-validates inside the command.
- Bare `clu` (no subcommand) is intentional: it routes to `cmd_fleet`.
  Don't make subcommand required.
- `cmd_complete` verifies every passed `--commit` SHA against the
  project's git repo BEFORE entering `mutate()`. Bad SHA → no event
  written; the worker can re-call after fixing.
- `cmd_task_done`: `--force` and `--token` are mutually exclusive. The
  force path is for operator cleanup of leaked tasks; the token path is
  for workers.
- `cmd_status` reads outside `mutate()` (no write needed). Any read-only
  command can do this; reads under a lock are unnecessary contention.

**See also**

- `contract.md` for the worker callback table this implements.
- `conventions.md` for the structured commit format, `--token`
  discipline, and `ExitCode` / `_die` usage rules.
