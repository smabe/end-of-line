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

## In-session signaling (inbox + UserPromptSubmit hook)

Beyond iMessage to the operator, clu has a second notification channel
aimed at active Claude Code sessions: a per-event JSON inbox at
`~/.config/clu/inbox/` surfaced via a UserPromptSubmit hook
(`end_of_line/hooks/clu_inbox_surface.py`). The hook is installed
through `clu install-hook` (or the `/clu-monitor` skill, which is its
user-facing wrapper); a marker at `~/.config/clu/monitor.json` records
the install for idempotency.

When the supervisor fires an operator-relevant event, `notify.notify`
performs two writes:

1. **iMessage** (loud, immediate) — gated by `notify.quiet_hours` so
   the operator isn't woken at 03:00 by a halt that can wait.
2. **Inbox** (quiet, persistent) — `inbox.write_event` drops a JSON
   file tagged with `project_root`. Quiet hours do NOT apply here:
   the inbox is read by *the next Claude turn*, not by the operator.

On the next user message in Claude Code, the UserPromptSubmit hook
reads the inbox, filters to events whose `project_root` matches the
session's CWD (via `git rev-parse --show-toplevel` or `os.getcwd()`),
emits a `hookSpecificOutput.additionalContext` payload (≤10K chars,
20 most recent events plus a footer line for older overflow), and
moves each surfaced event into `inbox/processed/`. Mark-and-sweep
dedup: Claude sees every event exactly once.

This pattern is what makes "queue plans, walk away" work end-to-end:
the operator gets the iMessage on their phone, walks back to a Claude
session (the same one or a fresh `/clear`), types literally anything,
and Claude already knows what halted, completed, or got stuck. No
manual context summary.

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

`supervisor.tick` walks an eight-priority chain. First match wins; the
tick writes one event and returns. This ordering is load-bearing — every
debugging session that asks "why didn't this tick advance?" reduces to
"which rule fired first?".

1. **Stale lease release.** If `current_claim.lease_expires` is in the
   past, drop the claim and write `lease_expired`. The phase's attempts
   counter ticks up next time it's dispatched.
2. **Stalled heartbeat.** If a live claim hasn't heartbeat within the
   threshold returned by `state.stalled_threshold_for_phase` —
   explicit `config.stalled_heartbeat_minutes` if set, else
   `max(15, lease_ttl_for_phase // 2)` — emit `phase_stalled` once and
   stamp `stalled_notified=True` on the claim. The claim stays — the
   lease still owns retry. This is just the notification trigger.
   Deriving from lease TTL prevents false alarms when workers in deep
   tool-use chains skip heartbeats while still inside their lease
   window (60-min default → 30-min threshold).
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

Inside `supervisor.tick`, the same `with st.mutate(state_path) as data:`
window that owns rule selection also snapshots `state.worktree` onto
`TickResult.worktree`. That snapshot rides along to
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

The eight-rule chain above runs **inside one plan's tick**. The
host-scoped cron entry (`cmd_tick_all`) adds two post-loop passes,
fired once per distinct project after every registered plan has
ticked: per-project queue advancement and the worktree conflict scan
(see "Queue advancement" and "Worktree conflict scan" below). Both
operate cross-state (queue.json or paired state.jsons), so they live
outside `supervisor.tick`. The "one tick = one action" invariant
still holds within each plan; the post-loop passes are each at-most-
one effect per project per cron interval.

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
4. **Acquire source plan's state lock** (via `st.mutate`) and call
   `assert_claim_match` — verifies the token is still live and matches
   the declared `--plan`/`--phase`. A stale or forged token exits
   `CLAIM_MISMATCH` (4) via `@_translate_claim_mismatch`.
5. Inside the same `st.mutate` window: check the per-phase add cap
   (count `queue + history` entries where `source_plan == S AND
   source_phase == X`; if `>= max_queue_adds_per_phase` → emit
   `EVENT_QUEUE_REJECTED` with `reason="cap"` + exit `QUEUE_CAP` (11)).
6. **Acquire queue lock** (via `queue.mutate`) — nested inside the state
   lock from step 4. This is the load-bearing lock-ordering rule:
   **state lock first, queue lock second — never reverse.** Reversing
   risks a deadlock with the queue-advancement path, which takes the
   queue lock first (step 6 of the normal-pop branch above) and then
   opens the target state file. Crossing the order creates a classic
   ABBA cycle.
7. Apply idempotency: pending slug → no-op; in-flight slug → no-op;
   history slug → exit `STATUS_TRANSITION` (7).
8. Append the entry (with `source_plan`, `source_phase`,
   `source_token_fp`, `reason`) + append `EVENT_QUEUE_APPENDED` to the
   source plan's events. Both writes happen inside the same nested-lock
   window so they're atomic with respect to each other.

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
alias that delegates here (clu-ship.md).

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
- TDD, `/code-review`, commit format, slug regex, token discipline →
  `conventions.md`
