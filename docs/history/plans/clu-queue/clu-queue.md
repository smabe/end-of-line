# clu-queue — overnight plan chains via cron-driven queue

Day-5 retro showed clu's `cron tick-all` advances *phases within* a
plan correctly, but inter-plan transitions need a live Claude session
to `clu init` the next plan. This plan ships `clu queue {add, list,
remove}` plus a per-project queue-pop pass in `tick-all` so an
operator can scribble plans, queue them, walk away, and wake up to a
drained chain.

Design pass is done — full six-persona brainstorm plus an 11-decision
grill-me consolidation lives at `.claude/plans/plan-queue-master.md`
(authoritative spec). v2 (worker-callback enqueue) is deferred and
tracked as GitHub issue [#17](https://github.com/smabe/end-of-line/issues/17).

## Goal

After this plan ships, the operator runs `clu queue add foo`, `clu
queue add bar`, walks away, and the next two cron ticks pop and
dispatch each plan in order. A new auto-repair worker recovers from
a corrupted queue.json without ever dropping the operator's pending
entries (clu's validation, not the worker's prompt, is the safety
boundary).

## Locked design decisions (do NOT re-litigate)

Full rationale in `.claude/plans/plan-queue-master.md`. Summary:

- **Storage**: per-project `<plan_dir>/.orchestrator/queue.json`,
  schema `{schema_version: 1, queue: [], history: []}`. `history`
  records failures only (`abandoned | removed | absorbed`).
- **CLI surface (v1, operator-only)**: `clu queue add <slug>
  [--front] [--project P]`, `clu queue list [--project P]`,
  `clu queue remove <slug> [--project P]`. Bare `clu queue` defaults
  to `list`.
- **Bootstrap**: a project must have ≥1 registered plan (`clu init`)
  before `clu queue add` works in it; CLI refuses with a
  bootstrap-instruction error otherwise.
- **Storage primitive**: extract `state.locked_json(path, *,
  expected_version, empty)` from the duplicated `state.mutate` /
  `registry._mutate` pattern; both existing callers refactor onto
  it. The CLAUDE.md `with st.mutate(path)` invariant survives —
  `mutate` becomes a thin wrapper.
- **Tick chain insertion**: per-plan `tick()` is unchanged. New
  post-loop step in `cmd_tick_all` iterates distinct project_roots
  from `registry.entries()`; per project, at most one queue-pop
  per tick.
- **Per-project busy gate**: `any_active_claim()` filtered to that
  project's plans. If anything is mid-claim, skip pop.
- **Head-only freeze**: if queue head's slug is already registered
  with status in `{HALTED, HALTED_REPLAN, PAUSED}`, freeze the chain
  at that head (no pop, no advance). DONE / RUNNING-with-no-claim at
  head → absorb (pop without re-init, history outcome `absorbed`).
- **Pop ordering** (mirrors `cmd_init` at cli.py:340-348):
  state-create → registry.register → queue-pop. All under a single
  queue-lock window. Dispatch worker outside the locks.
- **Missing plan file at pop**: skip + history outcome `abandoned`
  + `KIND_QUEUE_SKIPPED` notification (defers in quiet hours). Never
  halt the chain.
- **Quiet hours**: pop runs 24/7. No `KIND_QUEUE_ADVANCED` ping per
  pop (cut for noise reasons). Skipped/corrupt/repaired pings exist;
  corrupt + repair-failed are halt-bypass.
- **Events / kinds**: one new `EVENT_QUEUE_POPPED` (in popped plan's
  state.json). Four new `KIND_QUEUE_*`: `SKIPPED` (defer), `CORRUPT`
  (halt-bypass), `REPAIRED` (defer), `REPAIR_FAILED` (halt-bypass).
- **Auto-repair worker (v1, opt-in)**: when queue.json fails to
  load, clu writes a backup + dispatches a headless Claude repair
  worker via a new `ProjectConfig.dispatch.repair_command` template
  (unset → falls back to plain notification). After the worker
  exits, clu validates the repaired file against the backup with
  **hard slug-preservation rules** — any dropped pending slug, any
  empty `queue` array (when original was non-empty), or any removed
  history entry → revert from backup, fire `KIND_QUEUE_REPAIR_FAILED`.
  Throttle: max 3 attempts per same-diagnosis-hash; fourth corruption
  falls back to notification.
- **Exit codes**: one new — `ExitCode.REPAIR_DECLINED = 9`. Worker's
  exit when refusing destructive repair.
- **cmd_init unchanged**: manual-init-while-queued is handled by
  absorb-at-pop, not by a new cleanup step in init.
- **Out of scope (explicit)**: cross-project queues, `--before` /
  `--after`, GUI, `clu queue clear`, `--all` on list, `--notes` /
  `--reason`, `KIND_QUEUE_ADVANCED`, `EVENT_QUEUE_APPENDED` /
  `_REMOVED`, schema migration scaffolding, auto-discovery of
  `plans/*.md`. Worker-callback enqueue is v2 (#17).

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| primitive | `clu-queue-primitive.md` | Extract `state.locked_json`; refactor `state.mutate` + `registry._mutate` to use it; new `end_of_line/queue.py` (schema, load/save/mutate, SCHEMA_VERSION=1); add `ProjectConfig.queue_path()`; test helper `isolate_queue`. | 2h |
| add | `clu-queue-add.md` | `cmd_queue_add` with bootstrap check, slug validation, plan-file existence check, duplicate rejection, `--front` insertion. TDD tests for all six exit paths. | 1h |
| list | `clu-queue-list.md` | `cmd_queue_list` (status projection via `fleet.summarize_plan`, failure-history inline section) + `cmd_queue_remove` (move pending → history with outcome=`removed`). Bare `clu queue` → list. | 1.5h |
| pop | `clu-queue-pop.md` | Supervisor post-loop step in `cmd_tick_all`: iterate distinct project_roots, per-project busy gate, head-only freeze check, pop sequence (state→registry→queue-pop under queue lock), `EVENT_QUEUE_POPPED` as first event of new plan, dispatch outside lock. Integration test for a 3-entry chain. | 3h |
| repair | `clu-queue-repair.md` | Auto-repair worker: corruption detection in `cmd_tick_all`, backup-first, throttle file, dispatch via new `repair_command` (opt-in), `validate_repair` with slug-preservation rules, revert path. New `KIND_QUEUE_REPAIRED` / `KIND_QUEUE_REPAIR_FAILED` / `KIND_QUEUE_CORRUPT` and `ExitCode.REPAIR_DECLINED=9`. | 3h |
| footer | `clu-queue-footer.md` | Fleet-view footer hint on bare `clu` when queue non-empty (hidden when empty). CLI corruption handling for `queue add`/`list`/`remove` (refuse loudly with paste-into-Claude friendly diagnosis). | 1h |
| docs | `clu-queue-docs.md` | `contract.md` schema + events + kinds; `architecture.md` post-loop step + freeze predicate + auto-repair sub-architecture; `reference.md` `queue.py` + new helpers + dispatch_repair_worker; `operations.md` bootstrap rule + multi-host caveat + `repair_command` opt-in; CLAUDE.md status section refresh. | 1.5h |
| smoke | `clu-queue-smoke.md` | Real-world dogfood: queue three small plans on `~/projects/end-of-line/`, set cron, walk away, verify chain drains. If anything misbehaves, file follow-ups (do not extend plan in flight). | 1h |

Total estimate: ~14h across 8 sessions.

## Failure modes to anticipate

- **Refactor breaks existing callers.** Phase 1 (`primitive`) refactors
  `state.mutate` and `registry._mutate` onto `locked_json`. The
  CLAUDE.md invariant `with st.mutate(path) as data:` MUST keep
  working bit-for-bit at every existing call site. Tests must pass
  before AND after the refactor — no behavior change.
- **Bootstrap check too strict.** `clu queue add` requires the project
  to be in `registry.entries()`. If the check is by exact path string
  match instead of resolved canonical path, symlinked project roots
  (`~/projects/end-of-line` vs the absolute path) will spuriously
  reject. Use `Path.resolve()` on both sides for the comparison —
  match how `registry.register` already does it (registry.py:68).
- **Per-project busy gate confused by multi-project hosts.**
  `any_active_claim()` filtered to one project must NOT block other
  projects' queue-pops. Test the multi-project case explicitly.
- **Freeze predicate confused with busy gate.** The two are
  independent: busy gate is `current_claim != None on any plan in
  project P`; freeze is `queue head's slug is registered with status
  in {HALTED, HALTED_REPLAN, PAUSED}`. Never short-circuit one through
  the other.
- **Pop sequence partial-crash.** A crash between state-create and
  registry-register leaves an orphan state.json with no registry row.
  `tick()` at supervisor.py:93 handles this as `idle` already, and the
  next post-loop queue-pop re-enters and finishes the sequence (both
  ops idempotent). Tests must simulate the crash window and confirm
  recovery on next tick.
- **Auto-repair worker writes empty queue.** The whole reason
  validation is in clu's Python (not the worker's prompt). The
  best-effort slug regex over the backup bytes is the only line of
  defense against a worker that "cleans up" by emptying the queue.
  Multiple tests for this exact case: worker drops a slug, worker
  writes empty queue, worker truncates history.
- **`repair_command` template missing.** If the operator hasn't set
  `dispatch.repair_command` in `.orchestrator.json`, auto-repair is
  disabled and we fall back to plain `KIND_QUEUE_CORRUPT` notification.
  The throttle counter still increments to avoid infinite
  notification spam.
- **Throttle file lives alongside queue.json.** Path is
  `queue.json.repair-attempts`. If the throttle file itself gets
  corrupted, treat as 0 attempts (best-effort) and continue. Don't
  introduce a "repair-the-throttle" failure mode.
- **`EVENT_QUEUE_POPPED` as first event.** The popped plan's
  state.json gets created in phase 4 (`pop`). The provenance event
  must land BEFORE the worker dispatches, so the worker sees it in
  its initial state. Order matters: `state.save_atomic` with the
  event already appended, then dispatch.
- **`tests.isolate_registry` interaction.** Any test that touches
  queue.json must also patch the registry (queue add's bootstrap
  check reads the registry). Tests in setUp need both
  `isolate_registry` and the new `isolate_queue` (or one helper
  that does both).
- **Docs drift from implementation.** Phase 6 (`docs`) lands last;
  some details may have evolved during 1-5. The docs worker reads
  the actual code, not just this plan. If the plan disagrees with
  the code, the code wins; the plan is updated in the parking lot
  (don't edit the plan in flight).

## Done criteria (whole plan)

- `clu queue add`, `clu queue list`, `clu queue remove` all work end
  to end against the local end-of-line project; bare `clu queue`
  defaults to `list`.
- Bare `clu` shows the footer hint when queue is non-empty; hidden
  when empty or queue file missing.
- A 3-entry queue, set against a project with no other live work,
  drains in order across three consecutive `clu cron tick-all`
  invocations (one pop per tick).
- Per-project semantics verified by integration test: project A
  with a running plan + project B with a queue drains B while A
  holds.
- Freeze predicate verified: a halted plan at the queue head freezes
  the chain (`clu queue list` shows the freeze marker), and removing
  the head with `clu queue remove` unfreezes.
- Auto-repair pipeline: corrupt queue.json → backup written →
  worker dispatched (when `repair_command` set) → validation runs →
  on slug-loss attempt, file reverts and `KIND_QUEUE_REPAIR_FAILED`
  fires; on success, `KIND_QUEUE_REPAIRED` fires and queue is
  parseable next tick.
- Throttle: after 3 failed repair attempts on the same diagnosis,
  the 4th corruption skips dispatch and goes straight to
  `KIND_QUEUE_CORRUPT`.
- `dispatch.repair_command` unset → plain notification path, no
  dispatch, no auto-rewrite.
- Full suite green at the end of every phase commit. Expect
  current 237 → ~280-300 after this plan.
- Eight commits, one per phase, structured commit format, last
  phase's commit closes by satisfying the smoke criteria.
- Docs (contract, architecture, reference, operations) reflect
  the shipped behavior; CLAUDE.md status section updated to mention
  the queue feature.

## Parking lot
(empty at start — append "ooh shiny" thoughts that surface mid-work for later)
