# Architecture

clu is a cron-driven plan orchestrator. The supervisor itself is a tiny
Python program that runs once every five minutes (via launchd), reads a
JSON state file, and either does nothing or fires a single action. Long-
running work — the LLM that actually edits code — lives in *workers*:
short-lived `claude --print` processes spawned for one phase at a time.
The operator talks to the system through `clu status` and iMessage.

Nothing carries context across processes. The state file is the single
durable artifact; everything else (supervisor, worker, inbound poller)
is replaceable and stateless between invocations.

## Process model

Four pieces, three of them processes:

- **Supervisor.** `clu tick`, fired by `launchd` on a 5-min
  cadence. ~50 ms of Python. Reads the state file under a `flock`,
  picks the highest-priority action, writes one event, optionally spawns
  a worker, exits. Burns zero LLM tokens.
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
the claim on the next tick. If the worker hangs, the 30-minute lease
expires and the next tick frees the claim.

## One tick = one action

`supervisor.tick` walks an eight-priority chain. First match wins; the
tick writes one event and returns. This ordering is load-bearing — every
debugging session that asks "why didn't this tick advance?" reduces to
"which rule fired first?".

1. **Stale lease release.** If `current_claim.lease_expires` is in the
   past, drop the claim and write `lease_expired`. The phase's attempts
   counter ticks up next time it's dispatched.
2. **Stalled heartbeat.** If a live claim hasn't heartbeat within
   `stalled_heartbeat_minutes` (default 10), emit `phase_stalled` once
   and stamp `stalled_notified=True` on the claim. The claim stays —
   the lease still owns retry. This is just the notification trigger.
3. **Blocker SLA escalation.** If an open blocker is older than
   `blocked_question_sla_hours` (default 24), pause the plan and emit
   `blocker_sla_exceeded`. **Skipped during quiet hours** so an
   overnight rollover doesn't ping the user at 3am — the next loud tick
   re-checks.
4. **Answered-blocker resume.** A blocker with `answer != null and not
   consumed` flips to `consumed=True`, the plan returns to `running`,
   and the supervisor returns `blocker_resumed`. The next tick after
   that dispatches the phase again with the answer in state.
5. **Terminal status idle.** `paused / halted / halted_for_replan /
   done` short-circuit to idle. This is what guarantees `halt` and
   `plan_done` notifications fire exactly once per transition.
6. **Active claim idle.** A live, non-stalled claim means a worker is
   running; the supervisor returns idle and waits for the worker's
   callback.
7. **Dispatch.** Walk phases from the master plan's `## Sessions index`
   in order. Skip completed phases (a `phase_completed` event exists)
   and phases with an open blocker. The first remaining phase claims —
   unless it's already at `max_attempts_per_phase`, in which case the
   plan halts. The returned `TickResult` carries the new token, which
   `cmd_tick` then hands to `dispatch.dispatch_for_tick`.
8. **All-done.** All phases completed and no pending spawned tasks →
   write `plan_completed`, set status to `done`, return `plan_done`.
   Otherwise idle.

The dispatch step is the only one that can spawn a worker. The
supervisor never edits source code, runs tests, or calls Claude itself.

The eight-rule chain above runs **inside one plan's tick**. The
host-scoped cron entry (`cmd_tick_all`) adds a tenth rule on top, fired
once per distinct project after every registered plan has ticked: per-
project queue advancement (see "Queue advancement" below). That step
operates on `queue.json`, not on any single state.json, so it lives
outside `supervisor.tick`. The "one tick = one action" invariant still
holds within each plan; the queue advancement step is at-most-one
queue-pop per project per cron interval, independent of the eight in-
tick rules.

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
6. **Normal pop.** Under one queue-lock window:
   `state.empty_state` → append `EVENT_QUEUE_POPPED` → `state.save_atomic`
   → `registry.register` → `queue.pop(0)`. Dispatch fires **outside**
   both locks via `_tick_one_plan`, matching the `cmd_init` order
   (`cli.py:cmd_init`).

The freeze predicate and the busy gate are independent: busy gate is a
property of `current_claim` on any plan in the project; freeze is a
property of the queue head's status. Never short-circuit one through
the other.

**Crash recovery.** The normal-pop sequence is idempotent. If the process
dies between `state.save_atomic` and `registry.register`, the next tick
re-enters: queue head is still present, no current_claim exists, the
freeze predicate is false (the orphan state's status is `running` but
the slug isn't yet registered — the "registered AND status" guard
declines to absorb), and the inner `state.exists()` check skips re-
creating. `registry.register` is idempotent; `queue.pop` then completes
the sequence.

**Lock ordering.** When two locks are taken together, the order is
always `queue → state` (and `registry` reads/writes are queue-lock-
protected by virtue of happening inside `queue.mutate`'s window). The
normal-pop branch nests `state.locked(state_path)` *inside*
`queue.mutate(queue_path)`. Don't invert; the queue is the higher-level
resource and must be acquired first.

## Auto-repair worker

When the queue advancement step's `queue.load(queue_path)` raises
(catastrophic JSON or schema corruption), clu can dispatch a headless
Claude worker to repair the file. The full contract — including the
hard rules clu enforces post-repair — lives in `contract.md` §
"Auto-repair contract"; this section describes the runtime topology.

```
cmd_tick_all (per-project)
        │
        │  queue.load fails (JSONDecodeError | SchemaVersionMismatch | OSError)
        ▼
_handle_corrupt_queue
        │
        │  1. read original bytes
        │  2. write queue.json.corrupt-<UTCstamp> (backup)
        │  3. throttle check (≥3 attempts on same diagnosis_hash → notify only)
        │  4. repair_command unset → KIND_QUEUE_CORRUPT, increment throttle
        │  5. dispatch_repair_worker (synchronous, 60s timeout)
        │  6. queue.validate_repair(backup_bytes, queue_path)
        │
        ├─ validation fails ─▶ revert from backup → KIND_QUEUE_REPAIR_FAILED
        └─ validation passes ─▶ KIND_QUEUE_REPAIRED, reset throttle
```

Three reasons clu's validation is the safety boundary, not the worker's
prompt:

- The prompt is operator-authored and can be wrong. Validation is in
  Python, version-controlled, tested.
- The worker is an LLM. Even with a correct prompt, "clean up" is a
  plausible failure mode. The regex-based slug extraction over the
  *backup* bytes is what makes "delete slug X to make the file parse"
  impossible to get past clu.
- The throttle (per-diagnosis-hash, capped at 3) keeps a worker that
  keeps producing the same broken output from looping forever.

`dispatch_repair_worker` is synchronous because the cron tick should not
move on until the queue is either repaired or definitively-not-repaired
— otherwise the next tick would race the in-flight repair on the same
file. Synchronous wait + 60s timeout + post-validation is the simplest
correct shape.

The `repair_command` template variables (`{corrupt_path}`,
`{backup_path}`, `{diagnosis}`, `{schema_json}`, `{log_path}`) are
documented in `operations.md` § "Enabling auto-repair" alongside a
recommended `claude --print` template.

## Typical happy path

```
                        ┌────────────────────────┐
  cron 5 min ─tick──▶   │      supervisor        │ ─▶ state.json
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
                              state.json
                          (phase_completed)
                                   │
                                   ▼
            next cron tick ─▶ supervisor dispatches next phase
```

Step by step:

1. Operator runs `clu init --project ~/projects/foo --plan my-feature`.
   The state file is created at
   `~/projects/foo/plans/.orchestrator/my-feature.state.json`, and the
   host registry at `~/.config/clu/registry.json` learns about it.
2. Cron fires `clu tick`. The supervisor finds phase
   `design` pending, claims it (writing `phase_started` with a fresh
   token), and returns to `cmd_tick`.
3. `cmd_tick` exits the state lock, then calls
   `dispatch.dispatch_for_tick`, which renders the project's
   `dispatch.command` template — substituting `{plan_slug}`,
   `{phase_id}`, `{token}`, `{state_file}`, `{project}` — and `Popen`s
   it. The worker's stderr is captured to
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
worker          clu (state.json)            notify          iMessage          operator
  │ clu block ─▶│                             │                 │                 │
  │             │ phase_blocked, claim cleared │                 │                 │
  │             │─ render_blocker ─────────▶  │                 │                 │
  │             │                             │ osascript ───▶  │ "❓ slug/q-1"  │
  │             │                             │                 │ ◀── "2"  ─────  │
                                                                  ▲                 │
                            inbound poller (chat.db)──────────────┘
                                  │ parse "<slug>? <digit>"
                                  ▼
                            clu answer q-1 2 ──▶ state.json
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
   after dropping the state lock, so a hung Messages.app can't deadlock
   future ticks. Quiet hours gate everything except the
   `QUIET_HOURS_BYPASS_KINDS` set (currently `halted`).
3. **iMessage → poller.** The operator replies on their phone. The
   inbound LaunchAgent (`notify_inbound.poll_once`) reads new rows from
   `chat.db`, matches the reply against
   `^\s*(<plan-slug>\s+)?[0-9]\s*$`, and resolves the target plan.
   A bare digit is honored only when exactly one plan has an open
   blocker; ambiguous bare digits are dropped silently.
4. **Poller → state.** `route_reply` shells out to `clu answer <id>
   <index>` against the resolved plan. That command writes the answer
   into the blocker and appends `blocker_answered`.
5. **Next tick.** Rule 4 of the priority chain fires: `consumed=True`,
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
- TDD, `/simplify`, commit format, slug regex, token discipline →
  `conventions.md`
