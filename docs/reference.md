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
- `EVENT_*` — every event type as a constant. Never write a raw string;
  a typo silently breaks `completed_phase_ids()` and friends.
- `BLOCKER_INPUT`, `BLOCKER_REPLAN` — blocker types.
- `utcnow()`, `parse_iso(ts)` — single timestamp format
  (`%Y-%m-%dT%H:%M:%SZ`). All UTC.
- `empty_state(plan_slug, plan_dir)` — fresh state dict, with config
  defaults baked in.
- `locked(state_path)` — `flock` context manager with `O_NOFOLLOW`
  on the sibling lockfile.
- `mutate(state_path)` — lock + load + yield + atomic-write. The default
  read-modify-write helper; only drop to `locked()` when coordinating
  multiple files.
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
  `notify`.
- `ProjectConfig.state_path(plan_slug)` — returns the canonical state
  path; raises `InvalidSlug` if resolution escapes the orchestrator dir.
- `DispatchSpec` — `kind` (only `"shell"` in v0.1) + `command` template
  string. Substitutions: `{plan_slug}`, `{phase_id}`, `{token}`,
  `{project}`, `{state_file}`.
- `NotifySpec` — `imessage_to` (handle) + `quiet_hours` (tuple of
  `"HH:MM"` strings).
- `load_project_config(project_root)` — parses
  `<project>/.orchestrator.json` or returns the all-defaults config.
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

- `tick(state_path, config)` — entry point. Walks the eight-priority
  chain (see `architecture.md`) and returns a `TickResult`.
- `TickResult` — dataclass with `action`, `detail`, `phase_id`, `token`,
  and `notify_body` (rendered iMessage for actions that should ping).
- `Action` — typed union of `dispatch`, `idle`, `lease_expired`,
  `escalate`, `blocker_resumed`, `halt`, `plan_done`, `error`,
  `stalled`.
- `ACTION_NOTIFY_KIND` — map from action → notify kind for quiet-hours
  classification. Adding an action here is the one-line change that
  makes a new tick path send iMessage.
- `_detect_stalled(data)` — emits `phase_stalled` once when a claim
  goes past the heartbeat threshold; stamps `stalled_notified=True` on
  the claim so subsequent ticks fall through.
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
- `_detect_stalled` does NOT release the claim — the 30-min lease still
  owns retry. `phase_stalled` is just the notification trigger.

**See also**

- `architecture.md` for the tick lifecycle diagram and the priority
  chain in prose.
- `dispatch.py` for what happens after `tick` returns `dispatch`.

### `dispatch.py`

Fire-and-forget worker spawn. Renders the project's
`DispatchSpec.command` template, `Popen`s it, and either stamps the pid
on the live claim (healthy) or releases the claim with a
`dispatch_failed` event (fast-fail).

**Key types and functions**

- `dispatch_for_tick(result, cfg, plan_slug, state_file)` — the only
  public entry point. Returns `True` on spawn, `False` on no-op or
  fast-fail.
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

### `notify.py`

Outbound iMessage adapter — invokes `osascript` from Python. Stateless;
quiet-hours gating is a pure function of `(NotifySpec, datetime)`.
Renderers produce the strings; `notify()` decides whether to send.

**Key types and functions**

- `KIND_BLOCKER`, `KIND_STALLED`, `KIND_COMPLETED`, `KIND_HALTED` — the
  notification kinds.
- `QUIET_HOURS_BYPASS_KINDS` — frozenset of kinds that ignore quiet
  hours. Currently `{KIND_HALTED}`.
- `notify(spec, kind, body, *, now=None, sender=None)` — gate + send.
  Returns `True` if sent. `sender` is injectable for tests.
- `in_quiet_window(spec, now)` — public quiet-hours predicate, used by
  the supervisor's SLA-deferral branch as well as `notify()` itself.
- `is_quiet_hours(now, start, end)` — wrap-aware time-window check;
  end < start means overnight (e.g. 22:00–08:00).
- `parse_hhmm(s)` — `"HH:MM"` → `datetime.time`.
- `render_blocker(plan_slug, blocker_id, phase, question, options)` —
  the user-facing prompt that includes the reply grammar hint.
- `render_stalled(plan_slug, phase, age_seconds)`,
  `render_completed(plan_slug, commit_count)`,
  `render_halted(plan_slug, phase, attempts)` — kind-specific bodies.

**Invariants and gotchas**

- `osascript` is invoked via `subprocess.Popen` with
  `start_new_session=True` and DEVNULL'd I/O. A hung Messages.app must
  not deadlock cron.
- Argv-passing: the AppleScript reads handle + body from `argv`, never
  string-interpolated into the script source. Don't refactor this
  to inline — it's the injection guard.
- Quiet hours use local time. Don't switch to UTC to "match"
  `state.py`; quiet hours are user-facing wall-clock semantics.
- A failed `osascript` returns `False` and logs to stderr; never raises.
  A broken Messages.app can't take down the supervisor.
- Adding a new kind: declare the constant, add a `render_*` function,
  and decide whether it goes in `QUIET_HOURS_BYPASS_KINDS` and
  `supervisor.ACTION_NOTIFY_KIND`. Those two membership tests are the
  full integration surface.

**See also**

- `operations.md` for the notification model in user terms (kinds,
  quiet hours, reply grammar).
- `notify_inbound.py` for the symmetric inbound path.

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
- `open_blockers_for_host(entries)` — walks the registry, returns one
  `OpenBlocker` per plan with an open question (first only).
- `route_reply(text, open_blockers)` — pure function: returns
  `(target, "<digit>")` or `None`. The disambiguation rule lives here.
- `poll_once(conn, last_rowid, *, open_blockers_fn, dispatcher)` —
  one read of chat.db. Returns the new high-water rowid. Always
  advances past every row read.
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

### `cli.py`

argparse dispatch + the `ExitCode` enum + the `_die` helper + the
`@_translate_claim_mismatch` decorator + every subcommand. Both the
operator (`tick`, `status`, `pause`, `resume`, `retry`, `init`,
`register`, `unregister`, `list`, `answer`) and the worker
(`complete`, `block`, `spawn`, `task-done`, `heartbeat`) talk to clu
through this.

**Key types and functions**

- `ExitCode` — IntEnum: `OK`, `GENERIC`, `INVALID_SLUG`, `BAD_SHA`,
  `CLAIM_MISMATCH`, `SPAWN_CAP`, `UNKNOWN_TASK`, `STATUS_TRANSITION`.
  Cron and inbound poller key off these codes.
- `_die(rc, msg)` — write `error: <msg>` to stderr, return `int(rc)`.
  Use this from every error path; don't return bare ints.
- `_translate_claim_mismatch(fn)` — decorator that catches a leaked
  `state.ClaimMismatch` and returns `ExitCode.CLAIM_MISMATCH`. Every
  worker-side command wears this so forged tokens get a uniform exit.
- `main(argv)` — argparse + dispatch table.
- Operator-side commands: `cmd_init`, `cmd_tick`, `cmd_tick_all`,
  `cmd_status`, `cmd_register`, `cmd_unregister`, `cmd_list`,
  `cmd_fleet`, `cmd_pause`, `cmd_resume`, `cmd_retry`, `cmd_answer`.
- `cmd_tick_all` is the host-scoped cron entry point: walks
  `registry.entries()` and runs the per-plan tick + dispatch + notify
  dance for each. Per-plan exceptions are caught and logged to stderr
  so one broken plan can't poison the cadence. Replaces the old
  `examples/clu-tick-all.sh` parser of `clu list` output.
- Worker-side commands: `cmd_complete`, `cmd_block`, `cmd_spawn`,
  `cmd_heartbeat`, `cmd_task_done`. All require `--token` matching the
  live claim, all wear `@_translate_claim_mismatch`.
- `_verify_commit_shas(project_root, shas)` — runs `git cat-file -e`
  per SHA; returns the first error or `None`. Called from
  `cmd_complete`; any unknown SHA → `ExitCode.BAD_SHA`.
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
