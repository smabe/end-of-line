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
  `notify_body` (rendered iMessage for actions that should ping), and
  `side_notifies: list[tuple[kind, body]]` (gap-fill emissions that
  ride alongside the primary action — see `_emit_*` helpers below).
- `Action` — typed union of `dispatch`, `idle`, `lease_expired`,
  `escalate`, `blocker_resumed`, `halt`, `plan_done`, `error`,
  `stalled`.
- `ACTION_NOTIFY_KIND` — map from action → notify kind for quiet-hours
  classification. Adding an action here is the one-line change that
  makes a new tick path send iMessage.
- `_detect_stalled(data)` — emits `phase_stalled` once when a claim
  goes past the heartbeat threshold; stamps `stalled_notified=True` on
  the claim so subsequent ticks fall through.
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
  public entry point for phase dispatch. Returns `True` on spawn,
  `False` on no-op or fast-fail.
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

### `notify.py`

Outbound iMessage adapter — invokes `osascript` from Python. Stateless;
quiet-hours gating is a pure function of `(NotifySpec, datetime)`.
Renderers produce the strings; `notify()` decides whether to send.

**Key types and functions**

- `KIND_BLOCKER`, `KIND_STALLED`, `KIND_COMPLETED`, `KIND_HALTED`,
  `KIND_QUEUE_SKIPPED`, `KIND_QUEUE_REPAIRED`, `KIND_QUEUE_REPAIR_FAILED`,
  `KIND_QUEUE_CORRUPT`, `KIND_STUCK_BLOCKER`, `KIND_STALLED_CLAIM` —
  the notification kinds. See `contract.md` § "Notification kinds" for
  the trigger + quiet-hours matrix. The last two are the "gap-fill"
  kinds added with the inbox in #20.
- `QUIET_HOURS_BYPASS_KINDS` — frozenset of kinds that ignore quiet
  hours. Currently `{KIND_HALTED, KIND_QUEUE_REPAIR_FAILED,
  KIND_QUEUE_CORRUPT}` — the unrecoverable-without-operator set.
- `notify(spec, kind, body, *, now=None, sender=None, plan_slug=None, project_root=None, inbox_writer=None)` —
  gate + send + optionally drop an inbox event. Returns `True` if the
  iMessage was sent (the inbox write happens independently of the
  quiet-hours gate, and only when `plan_slug` + `project_root` are
  both supplied). `sender` and `inbox_writer` are injectable for tests.
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
  `render_stalled_claim(plan_slug, phase, age_min)` —
  kind-specific bodies.

**Invariants and gotchas**

- `osascript` is invoked via `subprocess.Popen` with
  `start_new_session=True` and stdout DEVNULL'd. A hung Messages.app
  must not deadlock cron. Stderr is appended to
  `imessage_log_path()` (default `~/.config/clu/imessage.log`) so
  AppleScript runtime errors are debuggable — previously DEVNULL'd, all
  failures vanished silently (#49).
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
  retains when ahead) AND `git mv plans/<slug>.md
  plans/shipped/<slug>.md`. Idempotent on the file-move step (skips
  if already gone). Surfaces `WORKTREE_SETUP_FAILED` if `git mv` fails
  with the file present.
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
  `OK` / `GENERIC` based on whether the required ones are found. No
  state writes — purely diagnostic.
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
- Worker-side commands: `cmd_complete`, `cmd_block`, `cmd_spawn`,
  `cmd_heartbeat`, `cmd_task_done`. All require `--token` matching the
  live claim, all wear `@_translate_claim_mismatch`.
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
