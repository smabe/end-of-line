# Architecture

clu is a cron-driven plan orchestrator. The supervisor itself is a tiny
Python program that runs every 30 seconds (via launchd), reads a
snapshot of plan state out of SQLite, and either does nothing or fires a
single action. Long-
running work — the LLM that actually edits code — lives in *workers*:
short-lived `claude --print` processes spawned for one phase at a time.
The operator talks to the system through `clu status` and iMessage.

Nothing carries context across processes. **Plan state is the single durable
artifact**; everything else (supervisor, worker, inbound poller) is
replaceable and stateless between invocations. It lives in the project's
database, `<project>/<plan_dir>/.orchestrator/clu.db` — see "Storage
topology" below.

## Process model

Four pieces, three of them processes:

- **Supervisor.** `clu tick`, fired by `launchd` on a 30-second
  cadence (`StartInterval 30` in `examples/clu.tick.plist`; `clu complete`
  also push-dispatches the next tick directly). ~50 ms of Python. Takes ONE consistent snapshot of the plan,
  decides the highest-priority action while holding no transaction at all,
  then applies that decision in a single write transaction guarded by a
  compare-and-set over the facts it decided on. Writes one event, optionally
  spawns a worker, exits. Burns zero LLM tokens.
- **Inbound poller.** `clu inbound`, a long-lived LaunchAgent that tails
  `~/Library/Messages/chat.db` for replies to outbound iMessages and
  routes each reply into `clu answer`. Polls every few seconds.
- **Worker.** A fresh `claude --print` process spawned by the supervisor
  for one phase. Reads its sub-plan, does the work, calls `clu complete`
  or `clu block` before exiting. Never reused across phases.
- **Operator.** A human with `clu status` in a terminal and iMessage on
  their phone. The operator answers blockers, runs `clu pause / resume /
  retry`, and reads the fleet view (bare `clu`).

The supervisor never blocks. Worker spawn is fire-and-forget with a
0.5-second fast-fail check (`dispatch.dispatch_for_tick`); if the worker
crashes immediately, the supervisor logs `dispatch_failed` and releases
the claim on the next tick. If the worker hangs, the 60-minute lease
expires and the next tick frees the claim.

Phase workers are not `Popen`d directly — they run as a child of a small
PTY shim (`_pty_spawn_shim.py`) that the dispatcher launches in their place.
`claude --print` block-buffers stdout when it isn't a tty, so a worker that
wedges mid-stream would otherwise leave a 0-byte log exactly when the
post-mortem needs it; the shim allocates a pty so output streams into the
log line-by-line. The shim is the process the supervisor tracks — it becomes
`claim.pid`, the worker is its descendant — so the watchdog stack (stuck-tool
tree walk, idle-CPU sum, killpg reapers, cmdline marker) operates on the shim
pid; the idle watchdog was made tree-aware (phase `idle-treewalk`) precisely
so the shim's own near-zero CPU doesn't false-fire `WORKER_IDLE`.

## Storage topology

Two SQLite databases, never one:

- **Per project** — `<project>/<plan_dir>/.orchestrator/clu.db`: plan state
  (`plans`, `claims`, `blockers`, `spawned_tasks`, `events`,
  `events_archive`), the queue (`queue`, `queue_history`), and the quota
  pause (`quota`). One write lock per project, so two projects' ticks never
  contend with each other.
- **Host** — `~/.config/clu/clu.db`: the registry, the monitor marker, the
  iMessage / Discord sidecars, the inbox, the skill-install receipt.

Both run in WAL mode, so readers never block behind the writer and each
database carries short-lived `-wal` / `-shm` siblings. Three rules hold the
concurrency story up (`end_of_line/db.py` has the full argument):

1. **Every write transaction opens `BEGIN IMMEDIATE`** — taking the write
   lock up front turns SQLite's unrecoverable snapshot-conflict failure into
   an ordinary bounded wait. A wait that outlives its budget raises
   `db.DbBusy`, which is what makes "drop this write rather than freeze a
   worker" a decision a caller can express.
2. **Never nest a write transaction inside another on the same database**,
   even across two connections: the second waits for a lock only the first
   can release. The tick's snapshot-decide-apply shape exists so nothing
   needs to.
3. **Never hold a read transaction across polls.** A reader with an open
   transaction pins the WAL past its autocheckpoint and the file grows
   without bound (probed: 819KB to 12.8MB). Pollers read in short bursts.

A database whose `PRAGMA user_version` is newer than this clu understands is
skipped, never read optimistically and never downgraded — a fleet walk skips
that project and keeps going.

## In-session signaling (the operator dashboard)

Beyond the outbound notify channels, clu surfaces events to active
Claude Code sessions through the **operator dashboard**: a persistent
Monitor on `clu watch --all --operator`, armed by a SessionStart hook
(`end_of_line/hooks/clu_session_start.py`). The hook is installed
through `clu install-hook` (or the `/clu-monitor` skill, which is its
user-facing wrapper); a marker in the host database's `monitor` table
records the install for idempotency.

The dashboard streams **forward only**. `clu watch` sets its cursor to
the current end of each plan's event log when it starts, so it reports
what happens while it runs and never replays history. That is the
division of labour with the notify channels: the Monitor covers the
running session, the channels cover everything else.

When the supervisor fires an operator-relevant event, `notify.notify`
sends on every configured channel, gated by `notify.quiet_hours` when
that is set. **The gate drops rather than defers** — it returns without
sending and nothing re-sends later, so a gated notification is simply
lost to that channel. `quiet_hours` ships unset for exactly that
reason; a client-side do-not-disturb keeps the message while staying
silent.

### The retired inbox surface

`notify.notify` also inserts a row into the host database's `inbox`
table, tagged with `project_root`, unconditionally with respect to
quiet hours — the inbox was designed to be read by *the next Claude
turn* rather than to wake anyone, which made it the deferral target the
quiet-hours gate never had.

That reader is now retired. The UserPromptSubmit hook
(`end_of_line/hooks/clu_inbox_surface.py`) still exists and still works
— it filters the inbox to the session's CWD, emits a
`hookSpecificOutput.additionalContext` payload (≤10K chars, 20 most
recent events plus a footer for the overflow), and claims what it
showed in one transaction so two sessions cannot render the same event
— but `clu install-hook` no longer wires it. Pass `--inbox` to bring it
back.

The writes continue because every write site is already guarded and
treats the inbox as a parallel surface, never the source of truth
(`cli.py`'s attestation mirror says so directly). With no reader
attached the rows simply accumulate, unread and harmless.

The supervisor extends `TickResult` with `side_notifies: list[(kind,
body)]` so a single tick can emit multiple parallel notifications
*alongside* whatever first-match action the priority chain selected
(not instead of it). Two rules currently use this slot:

- **Stuck-blocker re-ping.** Any blocker with `consumed: false` AND
  `(now - created_at) > 30min` AND no re-ping within the last 30min
  fires `KIND_STUCK_BLOCKER` (iMessage + inbox) and stamps
  `last_repinged_at` on the blocker. Repeats every 30min until the
  blocker is consumed. The original blocker iMessage from clu is
  fire-and-forget; this rule covers the case where the operator
  missed it and nothing escalated.
- **Stalled-claim transition.** A `current_claim` whose `lease_expires`
  has passed while plan status is still `RUNNING` fires
  `KIND_STALLED_CLAIM` once and stamps `stalled_notified: true` on
  the claim. The lease-release rule in the priority chain (rule #1)
  still fires next tick to actually drop the claim — this rule is
  just the operator-visible early warning.

Both side rules respect `notify.quiet_hours` for iMessage (these are
escalations, not emergencies) but write to the inbox unconditionally.

## One tick = one action

`supervisor.tick` walks a ten-priority chain. First match wins; the
tick writes one event and returns. This ordering is load-bearing — every
debugging session that asks "why didn't this tick advance?" reduces to
"which rule fired first?".

**Snapshot, decide, apply.** The tick cannot be one transaction: its decisions
rest on `ps`, `lsof` and a process-group reap that polls for seconds, and WAL
allows exactly one writer — a transaction held across that work would starve
every worker callback in the project. So the tick takes ONE snapshot, walks the
chain holding **no transaction at all**, accumulates its changes in a delta,
and applies them in a single `BEGIN IMMEDIATE` at the end.

What makes that safe is not a version counter but a set of **preconditions**:
the specific facts the decision actually rested on, re-asserted inside the
write transaction (`plan_store.TickPreconditions`) —

- `expect_claim` — the claim's `(claimed_by, phase_id)`, or "no claim row".
- `expect_max_event_id` — the highest event id the snapshot saw.
- `expect_claim_fields` — the exact claim fields a gap-fill emitter judged.
- `expect_blocker_state` — per-blocker `(unanswered, consumed)`.
- `expect_status` — the plan status the decision assumed.

A whole-plan version counter would be blind here: it cannot tell "the claim was
released" from "a heartbeat landed", so it would abort most watchdog ticks
against exactly the plans a watchdog is watching. The two highest-frequency
writers — the heartbeat every ~120s and the activity stamp on every Bash call —
write no events and touch only their own claim columns, so they cannot false-
trip a precondition they were not judged against. If a fact HAS moved, the
apply raises `TickConflict`, nothing is written, the tick reports
`idle / concurrent_write`, and the next cron tick re-derives from fresh state.
A detection path needing a guarantee none of the five gives ADDS a field rather
than widening one — widening is how a precondition set decays into a version
counter.

1. **Stale lease release.** If `current_claim.lease_expires` is in the
   past, drop the claim and write `lease_expired`. The phase's attempts
   counter ticks up next time it's dispatched.
2. **Dead-PID release.** If the claim's worker PID is gone (ESRCH) or
   recycled to an unrelated process (cmdline mismatch), drop the claim
   and write `phase_worker_dead`. Fires before stalled-heartbeat so a
   zombie heartbeat-keeper (issue #72) doesn't keep the lease looking
   fresh until full TTL — detection now happens within one tick instead
   of waiting 60 min. Probe via `state.claim_worker_alive(claim,
   cmdline_match=...)` where the marker is the **plan slug** — present in
   every dispatch template's worker cmdline AND the heartbeat cmdline, so
   it doesn't false-negative on `/plan ...`-style templates the way the
   old `/clu-phase <plan> <phase>` marker did (#75). On dead, emit event →
   release_claim_and_emit (atomic release + coolant) → best-effort
   `reap_orphan_pid`. Best-
   effort reap is last so a wedged `ps` can't crash the tick.
3. **Stalled heartbeat.** If a live claim hasn't heartbeat within the
   threshold returned by `state.stalled_threshold_for_phase` —
   explicit `config.stalled_heartbeat_minutes` if set, else
   `min(25, max(15, lease_ttl_for_phase // 2))` — emit `phase_stalled`
   once and stamp `stalled_notified=True` on the claim. The claim
   stays — the lease still owns retry. This is just the notification
   trigger. Floor keeps short Effort-scaled leases from triggering too
   eagerly; ceiling keeps long leases from leaving wedged workers
   undetected (60-min default → 25-min threshold; the heartbeat daemon
   pings every 120s independent of the worker's tool-use depth,
   so staleness past the ceiling is structural, not legitimate).
4. **Blocker SLA escalation.** If an open blocker is older than
   `blocked_question_sla_hours` (default 24), pause the plan and emit
   `blocker_sla_exceeded`. **Skipped during quiet hours** so an
   overnight rollover doesn't ping the user at 3am — the next loud tick
   re-checks.
5. **Answered-blocker resume.** A blocker with `answer != null and not
   consumed` flips to `consumed=True`, the plan returns to `running`,
   and the supervisor returns `blocker_resumed`. The next tick after
   that dispatches the phase again with the answer in state.
6. **Terminal status idle.** `paused / halted / halted_for_replan /
   done` short-circuit to idle. This is what guarantees `halt` and
   `plan_done` notifications fire exactly once per transition.
7. **Active claim idle.** A live, non-stalled claim means a worker is
   running; the supervisor returns idle and waits for the worker's
   callback.
8. **Project quota pause gate (#94).** Consulted only when this plan has
   a dispatchable phase (so the canary slot is stamped for a plan that
   will actually dispatch), immediately before the claim. If
   the project's `quota` row is present and the pause is active, return idle
   (`quota_paused` / `quota_stuck` / `quota_canary` in the detail). Past
   the reset, the first plan to tick stamps itself canary and dispatches
   as the survival probe; once the canary outlives its window, the gate
   deletes the row, emits `quota_resumed`, and the fleet dispatches
   normally. Watchdog rules 1–5 keep running against in-flight claims
   while paused — only *dispatch* is gated. See "Quota pause gate" below.
9. **Dispatch.** Walk phases from the master plan's `## Sessions index`
   in order. Skip completed phases (a `phase_completed` event exists)
   and phases with an open blocker. The first remaining phase claims —
   unless it's already at `max_attempts_per_phase`, in which case the
   plan halts. The returned `TickResult` carries the new token, which
   `cmd_tick` then hands to `dispatch.dispatch_for_tick`.
10. **All-done.** All phases completed and no pending spawned tasks →
    write `plan_completed`, set status to `done`, return `plan_done`.
    Otherwise idle.

The dispatch step is the only one that can spawn a worker. The
supervisor never edits source code, runs tests, or calls Claude itself.

Inside `supervisor.tick`, the same snapshot that rule selection reads also
carries `worktree` onto `TickResult.worktree`. That snapshot rides along to
`dispatch_for_tick` so the dispatch step can `Popen(cwd=worktree.path)`
without a second state load. When `state.worktree` is absent the field
is `None` and dispatch keeps `cwd=cfg.project_root` (pre-worktree
behavior). The `{project}` template substitution in
`dispatch.command` always resolves to `project_root` regardless of
worktree — that's the callback target, not the worker's cwd.

Worktree-bearing dispatch adds two pre-Popen guards: `_worktree_alive`
checks both `Path.exists()` and `git rev-parse --git-dir` (catching
the `git worktree prune` case where the dir lingers but git has
detached its admin metadata), and a `FileNotFoundError` fallback
around `Popen` itself catches the millisecond race where the dir
vanishes between stat and chdir. Both paths funnel into
`_pause_for_missing_worktree`, which appends `EVENT_WORKTREE_MISSING`,
releases the just-made claim without burning a phase attempt, flips
the plan to `PAUSED`, and fires a halt-bypass iMessage naming the
missing path.

The ten-rule chain above runs **inside one plan's tick**. The
host-scoped cron entry (`cmd_tick_all`) adds two post-loop passes,
fired once per distinct project after every registered plan has
ticked: per-project queue advancement and the worktree conflict scan
(see "Queue advancement" and "Worktree conflict scan" below). Both
operate across plans (the queue tables, or two plans' states), so they live
outside `supervisor.tick`. The "one tick = one action" invariant
still holds within each plan; the post-loop passes are each at-most-
one effect per project per cron interval.

## Quota pause gate

When a worker is killed by the operator's Claude subscription limit (a
session/weekly/model limit or exhausted usage credits), the death is
classified at all three death sites — supervisor dead-PID, supervisor
lease-expiry, dispatch fast-fail — by `quota.classify_log_tail` reading
the worker-log tail before the claim is released. A classified death
(a) forgives the phase attempt (`quota_death` is a subtraction marker
for `attempts_for_phase`, exactly like `systemic_failure`), (b) writes
the project's single `quota` row with `paused_until = reset + 120s`,
and (c) fires a `KIND_QUOTA_PAUSED` iMessage carrying the local resume
time. The plan status never flips — quota pause is project-level, gated
at dispatch, not a plan halt.

The pause is a four-state machine resolved inside one write transaction
(`quota.gate_decision`):

```
                 quota row absent ───────────────► DISPATCH (hot path: one read-only SELECT)
                 │
   present ──► now < paused_until ───────────────► IDLE  (quota_paused)
                 │
                 ├─ paused_until == null ─────────► IDLE  (quota_stuck — operator clears)
                 │
                 ├─ now ≥ paused_until, no canary ─► STAMP self as canary (+180s), DISPATCH
                 │
                 ├─ canary stamped, now < deadline ► IDLE if another plan; DISPATCH if it's me
                 │
                 └─ now ≥ canary_deadline ─────────► DELETE row, emit quota_resumed, DISPATCH
```

The row is read twice — once cheaply outside any transaction, once inside the
transaction that may write — and the second read is authoritative. That
re-read is why there is no branch for "the pause vanished between the check
and the lock": it cannot race.

The **canary** is the first plan to tick past the reset: it dispatches a
single probe while the rest of the fleet idles. If that worker also dies
on quota, the death machinery overwrites the row with a fresh
`paused_until` and clears the canary slot — so a still-throttled account
re-pauses automatically, no special-casing. If the canary survives its
180s window, the next gate tick deletes the row (keeping "row absent ==
not paused" the one invariant) and the fleet resumes. A `STUCK` pause
(unparseable reset → `paused_until: null`) idles indefinitely; only
`clu quota clear` ends it.

Degradation is split two ways, deliberately: an **unreadable** store (corrupt
database, or one written by a newer clu) and a field-malformed row both
DISPATCH with a stderr note — no malformed pause may freeze the fleet — while
**contention** (`db.DbBusy`) IDLES, because a busy database is not a broken
one, and dispatching into a pause we merely could not read costs a worker and
a quota hit where idling costs one tick.

## Queue advancement

`cmd_tick_all` walks `registry.entries()` to tick every registered plan,
then makes a second pass over the distinct project_roots and runs
`_advance_queue_for_project` on each. This is where inter-plan
transitions happen — `supervisor.tick` only moves phases within a plan.

```
                  ┌──────────────────────────────┐
  cron tick-all ─▶│  for plan in registry:       │
                  │      tick + dispatch + notify│
                  └──────────────┬───────────────┘
                                 │
                  ┌──────────────▼───────────────┐
                  │  for project in distinct:    │
                  │      advance_queue(project)  │  ← at most one pop
                  └──────────────────────────────┘
```

For each project, `_advance_queue_for_project` walks a first-match-wins
branch chain:

1. **Queue empty / missing** → return.
2. **Per-project busy gate.** Any plan registered under this project has
   `current_claim != None` → return. Other projects' queues are
   unaffected; the gate is per-project, not host-wide.
3. **Head-only freeze.** If the queue head's slug is already registered
   AND its state's `status` is in `{HALTED, HALTED_REPLAN, PAUSED}` →
   freeze the chain at that head. No pop. The operator must `clu retry`/
   `clu resume`/`clu queue remove` to unblock.
4. **Absorb.** If the head is registered AND status ∈ `{DONE, RUNNING}`,
   pop without re-`init`-ing — the plan already exists, the queue entry
   was just bookkeeping. `history` outcome `absorbed`.
5. **Abandon.** If the head's plan file (`<plan_dir>/<slug>.md`) doesn't
   exist, pop with `history` outcome `abandoned` and fire
   `KIND_QUEUE_SKIPPED` (gated by quiet hours — abandonment can wait).
6. **Normal pop.** TWO coordinated transactions, in this order:
   - **Transaction one, project database.** Move the queue head into
     `queue_history` (outcome `popped`) AND insert the new plan's rows,
     together. The head check is the `DELETE`'s own `WHERE` clause, so a
     second tick racing this one writes nothing rather than creating a
     duplicate; and `plans.slug` being the primary key means the insert IS
     the duplicate check.
   - **Transaction two, host database.** `registry.register` — idempotent.

   They cannot be one transaction: two databases, two write-ahead logs, no
   atomicity across the pair whatever the syntax. Dispatch fires **outside**
   both, via `_tick_one_plan`.

The freeze predicate and the busy gate are independent: busy gate is a
property of `current_claim` on any plan in the project; freeze is a
property of the queue head's status. Never short-circuit one through
the other.

**Crash recovery.** The crash window that matters is *between* the two
transactions — a created-but-unregistered plan. It is the same window the
nested flocks had, and it self-heals the same way: the next tick re-enters,
`registry.register` is idempotent, and a replayed pop finds the head already
gone (transaction one committed) or re-runs it whole (it did not).

That self-heal only works while the **queue head is still present**. It
cannot reach a plan that is `running` *and* fully unregistered with no queue
entry: `tick-all` walks the registry, so an unregistered slug is never
visited, and the pop path never fires. That window — a plan unregistered
(or finished) while `running`, the `fm-docs-sweep` zombie shape (#75) —
is closed by the **registry-independent sweep**
(`supervisor.sweep_zombie_states`). After its per-project rule pass,
`tick-all` walks the *plans in the project database* (not files on disk),
picks out the unregistered ones at `running` whose worker PID is gone
(`state.is_zombie_state`), terminalizes them (→ `halted` +
`plan_abandoned`) and reaps the worker process group. `clu doctor` previews
the same sweep dry-run. Residual gap: a project whose *every* plan is
unregistered never appears in the registry, so its zombies surface only via
`clu doctor --project <it>`.

**No lock ordering, by construction.** There used to be one — `queue →
state`, with a nested-flock rule and an ABBA hazard behind it. Plan state
and the queue are now tables in ONE database with ONE write lock, so there
is no pair to order; what replaced the rule is the ban on nesting write
transactions (see "Storage topology"). Where two DATABASES are genuinely
involved (the pop's registry write, the worker-enqueue path's registry
read), the second is done strictly outside the first's transaction — a query
of one store while holding another's write lock is the shape this migration
kept finding.

### Worker enqueue flow

A worker running a phase can append a follow-up plan to the project
queue mid-flight via `clu queue add <slug> --token T --plan S --phase X`.
This is the reverse direction of queue advancement — the queue-pop path
reads from the queue into state; the worker-enqueue path writes from
state into the queue.

**Validation order inside `cmd_queue_add` (worker mode):**

1. Slug syntax via `state.validate_slug`.
2. Plan-file existence: `<plan_dir>/<slug>.md` must exist. Absent →
   `EVENT_QUEUE_REJECTED` with `reason="missing_plan_file"` + exit
   `UNKNOWN_TASK` (6).
3. Registered-project check (same as the operator path).
4. **Check the claim against a snapshot** — `assert_claim_match` verifies
   the token is still live and matches the declared `--plan`/`--phase`. A
   stale or forged token exits `CLAIM_MISMATCH` (4) via
   `@_translate_claim_mismatch`.
5. Read the registry (a different database) to learn whether the slug is
   already in flight — **before** the queue transaction opens, never inside
   it.
6. **One queue transaction** does the cap check, the idempotency check and
   the insert together: count `queue + queue_history` entries where
   `source_plan == S AND source_phase == X`, and if
   `>= max_queue_adds_per_phase`, insert nothing. Pending slug → no-op;
   in-flight slug → no-op; history slug → exit `STATUS_TRANSITION` (7);
   over cap → `EVENT_QUEUE_REJECTED` with `reason="cap"` + exit
   `QUEUE_CAP` (11).
7. The outcome event (`EVENT_QUEUE_APPENDED` or `EVENT_QUEUE_REJECTED`)
   lands on the source plan through a plan-store op that **re-checks the
   claim in its own `WHERE` clause**. Three transactions, never nested: the
   token is still the boundary; what changed is where the compare happens.

**Token fingerprint.** `sha256(token.encode()).hexdigest()[:8]` — computed
once at append time. The raw token is never written to disk.

## Worktree conflict scan

After queue advancement, `cmd_tick_all` runs
`_detect_worktree_conflicts_for_project` on each distinct project
root. This is the only mechanism that emits `EVENT_WORKTREE_CONFLICT_
WARNING` — `supervisor.tick` itself is single-plan and can't see
sibling plans.

The scan reuses the `_plans_for_project(project_root, cfg)` helper to
load every plan's state once, then computes the "conflicting" set:
plans that are **active** (`current_claim != None` OR `status ==
RUNNING`) AND have no `worktree` record. For each plan whose target
peer-set differs from its persisted `in_conflict_with` field, the
field is rewritten and — for each newly-conflicting pair where this
plan is the **lexicographically-smaller** slug — `EVENT_WORKTREE_
CONFLICT_WARNING` is appended and a KIND_HALTED iMessage fires
naming the pair.

The canonical-pair rule (`slug_a < slug_b` emits, the other side
only updates its `in_conflict_with`) guarantees exactly one event +
one iMessage per (project, pair) onset. Pairs auto-clear when one
side stops being active: the next tick sees the transition, computes
a smaller target-set, and rewrites `in_conflict_with` accordingly —
no separate clear path needed.

```
                  ┌──────────────────────────────┐
  cron tick-all ─▶│  for plan in registry:       │
                  │      tick + dispatch + notify│
                  └──────────────┬───────────────┘
                                 │
                  ┌──────────────▼───────────────┐
                  │  for project in distinct:    │
                  │      advance_queue(project)  │  ← at most one pop
                  │      detect_conflicts(project)│ ← at most one emit/pair
                  └──────────────────────────────┘
```

`clu init` runs a one-shot version of the same scan at plan-creation
time (without the event-write side effect) and prints a stderr hint
when the new plan would land into an existing same-project conflict
— giving the operator a chance to add `--worktree` before the first
tick fires the iMessage.

## Multi-plan batch integration gate

When N plans drain in parallel via `clu queue add --batch <name>`, each
worker reads the codebase as of queue-time HEAD and is blind to sibling
workers' changes. Textual auto-merge usually succeeds, but **hidden
semantic conflicts** — one plan renames a function while a sibling's new
test calls it by its old name — slip through silently and only surface
at runtime.

### Rule trigger

`dry_merge_gate_rule` (registered last in `cross_plan_rules._RULES`) fires
when the post-loop rule chain runs for a project where **≥2 plans** with
the **same non-null `batch_id`** are:
- `status == done`
- Have a live `worktree` record (branch still resolvable via `git rev-parse`)

Eligible set is computed per `batch_id`; multiple batches may co-exist.

### Idempotency

The rule skips a batch whose sorted-HEAD-SHA key matches `gate_result.sha_key`
already stamped on any member plan. Same set of commits → no re-run. The
key advances only when a plan pushes a new commit (e.g. after repairing
a conflict).

### On clean

`gate_result` is stamped on every member plan's state. `KIND_GATE_CLEAN`
notification fires (gated by quiet hours). No plan files written; no queue
mutation.

### On dirty (textual conflict or suite failure)

`gate_result` is stamped with the outcome. `KIND_GATE_DIRTY` notification
fires (bypasses quiet hours — this is a hard stop). A follow-up plan pair
is **written to disk** (`plans/merge-resolve-<batch>-<YYYYMMDDhhmm>.md` +
`-fix.md`) but **not queued** — the operator runs `clu queue add
merge-resolve-...` manually after reviewing the conflict report.

### `clu validate` — operator override

`clu validate --project P --batch B` lets the operator re-run the
dry-merge engine on demand (e.g. after fixing conflicts and pushing
new commits). Wraps `dry_merge.attempt_merge` directly; does **not**
fire the rule, no state mutation, no follow-up emission. Useful for
replay-after-fix, stuck batches, or CI-side verification.
`--branches a,b,c` bypasses batch resolution entirely for ad-hoc
cross-branch checks. `clu integrate` is a stderr-warning deprecation
alias that delegates here.

```
┌── cron tick-all ──────────────────────────────┐
│  for project in distinct:                     │
│    advance_queue(project)                     │
│    detect_conflicts(project)                  │
│    run_rules(project, plans) ─────────────────┤
│      queue_advancement_rule                   │
│      worktree_conflict_rule                   │
│      dry_merge_gate_rule ← fires when ≥2 DONE │
│      ready_to_ship_rule ← DONE + unmerged    │
│      auto_archive_rule ← merged → cleanup    │
└───────────────────────────────────────────────┘

operator (on demand):
  clu validate --project P [--batch B | --branches a,b]
  clu ship     --project P --plan X [--direct | --as-pr] [--check] [--yes]
  clu ship     --project P --all-done [--direct | --as-pr] [--yes]
```

### `clu ship` — post-worker integration

The single operator action after `STATUS_DONE`. Two modes, picked
from `.orchestrator.json` `dispatch.ship_mode` (default `direct`):

- **direct**: validate → merge worker branch into main (FF-first;
  fall back to `--no-ff --no-edit` merge-commit) → push origin
  main + branch → trigger an immediate tick so `auto_archive_rule`
  fires without waiting for cron.
- **as_pr**: validate → push branch with `--set-upstream` →
  `gh pr create` → stamp `state.ship_pending`. The supervisor's
  `ready_to_ship_rule` suppresses re-surfacing while the PR is
  open; `auto_archive_rule` picks up cleanup when GitHub merges
  the PR and the next fetch bumps local `origin/main`.

**Why FF-first-then-merge-commit?** `gh pr merge`, `git-town ship`,
and `jj` all commit to one merge strategy. clu deliberately
diverges: prefer the cleaner history when FF works, fall back to
merge-commit when main has diverged. The solo-agentic loop hits
both shapes often enough that picking one upfront wastes either
linear history or merged-status detection. The fallback is
two extra git invocations per ship — cheap insurance against
operator-surprise commits on main.

**Why preview-then-confirm via `--yes`?** Destructive multi-step:
local merge → push origin main → push branch → tick. Without
explicit `--yes`, `clu ship` prints the action list and exits OK.
This matches the operator-approval-checkpoint mandate at the cost
of one extra invocation per ship.

**ready_to_ship_rule** (slotted between `dry_merge_gate_rule` and
`auto_archive_rule`) emits `KIND_READY_TO_SHIP` to the inbox when
DONE plans exist with unmerged branches and no in-flight
`ship_pending` stamp. Body includes the exact copy-paste `clu
ship` command. Dedup via `state.ready_to_ship_announced.branch_sha`
so the surface re-fires only when the worker pushes new commits.

## Auto-archive on merge

`auto_archive_rule` is the final priority in the cross-plan rule chain
(`cross_plan_rules._RULES`). Each cron tick, for every plan with
`status == STATUS_DONE` and a live `worktree` record, the rule checks
whether the worktree's branch is an ancestor of `origin/main` via
`state.is_branch_merged_into`. On hit, it invokes
`_perform_archive(cfg, slug, unregister=True)` and emits
`KIND_PLAN_AUTO_ARCHIVED`. First-eligible-wins in registry order; one
fire per tick per project per the ADR-0002 invariant (one tick = one
action).

The branch-merged check uses `git merge-base --is-ancestor` against
`origin/main` (not local `main`) — the operator must have pushed the
merge before the rule fires. No `git fetch` is run; freshness is the
caller's responsibility. Plans without a worktree record, or whose
branch is not yet an ancestor of `origin/main`, are skipped silently.

Disabled per-project via `.orchestrator.json:auto_archive: false`.

## Auto-repair worker — deleted

There is no repair worker, and the deletion is deliberate rather than
deferred. It existed because a JSON queue file interrupted mid-write is
recoverable text: clu backed up the bytes, dispatched a headless Claude to
rewrite them, and validated the result with a regex over the backup so no
slug could be dropped. A WAL database never hands a reader a half-written
store — the pop either committed or it did not — so the failure it repaired
cannot occur.

What replaced it is smaller: an unreadable project database (genuinely
corrupt, or written by a newer clu) makes the queue-advancement rule skip
that project with a stderr note and keep walking the fleet. That is not
something a headless worker edits back to health, so it surfaces to the
operator instead. `dispatch.repair_command` in an `.orchestrator.json` is
parsed, ignored, and reported once on stderr.

## Typical happy path

```
                        ┌────────────────────────┐
  cron 5 min ─tick──▶   │      supervisor        │ ─▶ clu.db
                        │ (priority chain)       │     (one event)
                        └──────────┬─────────────┘
                                   │ dispatch (fork + Popen)
                                   ▼
                        ┌────────────────────────┐
                        │   worker (claude)      │
                        │   reads sub-plan       │
                        │   edits + tests + git  │
                        └──────────┬─────────────┘
                                   │ clu complete --token T --commit SHA
                                   ▼
                                 clu.db
                          (phase_completed)
                                   │
                                   ▼
            next cron tick ─▶ supervisor dispatches next phase
```

Step by step:

1. Operator runs `clu init --project ~/projects/foo --plan my-feature`.
   The plan's rows are created in
   `~/projects/foo/plans/.orchestrator/clu.db`, and the host registry in
   `~/.config/clu/clu.db` learns about it.
2. Cron fires `clu tick`. The supervisor finds phase
   `design` pending, claims it (writing `phase_started` with a fresh
   token), and returns to `cmd_tick`.
3. `cmd_tick` commits the tick's write transaction, then calls
   `dispatch.dispatch_for_tick`, which renders the project's
   `dispatch.command` template — substituting `{plan_slug}`,
   `{phase_id}`, `{token}`, `{state_file}`, `{project}` — and `Popen`s
   it. (`{state_file}` still renders a `<slug>.state.json` path; it is the
   store KEY the worker hands back to clu, not a file it opens.) The worker's stderr is captured to
   `plans/.orchestrator/logs/<phase>.<token>.log`. The worker's PID is
   stamped onto the live claim.
4. The worker reads its sub-plan (per the `## Sessions index` row),
   edits code, runs tests, commits, and calls
   `clu complete --token <T> --commit <SHA>`. That CLI command
   validates `T` against `current_claim.claimed_by` (forged tokens →
   `CLAIM_MISMATCH`, exit 4), verifies each SHA with `git cat-file -e`,
   appends `phase_completed`, and clears the claim.
5. Five minutes later, the next tick sees `design` completed and
   dispatches the next phase. Loop.
6. When all phases complete and no spawned tasks remain, the supervisor
   writes `plan_completed`, flips status to `done`, and sends one final
   iMessage.

## Blocker round-trip

The blocker flow is the most non-obvious path because it crosses three
processes and the user's phone. A worker that calls `clu block` does
*not* fail — it cleanly releases the claim and asks the operator a
question.

```
worker          clu (plan state)            notify          iMessage          operator
  │ clu block ─▶│                             │                 │                 │
  │             │ phase_blocked, claim cleared │                 │                 │
  │             │─ render_blocker ─────────▶  │                 │                 │
  │             │                             │ osascript ───▶  │ "❓ slug/q-1"  │
  │             │                             │                 │ ◀── "2"  ─────  │
                                                                  ▲                 │
                            inbound poller (chat.db)──────────────┘
                                  │ parse "<slug>? <digit>"
                                  ▼
                            clu answer q-1 2 ──▶ plan state
                                  │
                                  │ next tick: blocker_resumed
                                  │ next-next tick: re-dispatch phase
                                  ▼
                            worker resumes with answer in state
```

1. **Worker → state.** `clu block --question ... --option A --option B`
   validates the token, appends `phase_blocked` with a fresh blocker id
   (`q-1`, `q-2`, …), and releases the claim. Worker exits 0.
2. **State → iMessage.** On that same tick the supervisor renders
   `notify.render_blocker(...)` and `cmd_tick` shells out to `osascript`
   AFTER the write transaction has committed, so a hung Messages.app cannot
   hold the project's write lock against every other plan. Quiet hours gate
   everything except the `QUIET_HOURS_BYPASS_KINDS` set (currently `halted`
   and `quota_stuck`).
3. **iMessage → poller.** The operator replies on their phone. The
   inbound LaunchAgent (`notify_inbound.poll_once`) reads new rows from
   `chat.db`, matches the reply against
   `^\s*(<plan-slug>\s+)?[0-9]\s*$`, and resolves the target plan.
   A bare digit is honored only when exactly one plan has an open
   blocker; ambiguous bare digits are dropped silently.
4. **Poller → state.** `route_reply` shells out to `clu answer <id>
   <index>` against the resolved plan. That command writes the answer
   into the blocker and appends `blocker_answered`.
5. **Next tick.** Rule 5 of the priority chain fires: `consumed=True`,
   status flips back to `running`, event `blocker_consumed` is logged,
   tick returns `blocker_resumed`.
6. **Tick after that.** The phase no longer has an open blocker, so
   the dispatch rule reclaims it. The new worker reads the answered
   blocker out of state and continues with the operator's choice in
   hand.

The whole round-trip can take minutes or days. The plan just waits —
no process holds memory, no lease counts against the worker, and the
operator can answer from anywhere with iMessage.

## See also

- Per-module API and invariants → `reference.md`
- State schema, event types, worker callback contract → `contract.md`
- macOS install, Full Disk Access, LaunchAgent plists, log locations →
  `operations.md`
- TDD, `/code-review`, commit format, slug regex, token discipline →
  `conventions.md`
