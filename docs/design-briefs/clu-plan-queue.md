# Design brief: `clu queue` — ADHD-friendly plan chains

Pre-design seed for the multi-plan queue concept. Open to brainstorm.
Not a `/plan` yet — read this first, brainstorm the unknowns, then a
real plan + Sessions index gets written from the consolidated output.

## Why this exists

Day 5 post-ship retrospective: clu's `cron tick-all` correctly drives
phases *within* a plan, but inter-plan transitions need a human (or a
patient Claude session) to `clu init` the next plan. Day-4 and Day-5
both hit this — a chain of 3-5 plans got stuck mid-night because the
operator slept and Claude ended its turn after dispatching plan N
instead of queuing plans N+1, N+2.

The operator's pattern (ADHD-tuned) is: draft plans in bursts, then
walk away. Specifically: scribble a `plans/whatever.md`, decide
*later* "oh yeah, also add this one to the chain." A fixed
declaration at init time (`clu init --queue a,b,c`) fails the "later"
moment.

## Proposed surface (sketch, not locked)

```bash
clu queue add <slug>              # append a plan to the queue
clu queue add <slug> --front      # priority bump (head of queue)
clu queue list                    # show queued plans + status
clu queue remove <slug>           # pull a pending plan out
clu queue clear                   # empty the queue (pending only)
```

**Supervisor behavior:** during `tick-all`, if **no active claim
across any registered plan AND queue is non-empty**, init the head of
queue (auto-`register` + dispatch). When a registered plan transitions
to `status: done`, pop it from the queue and pick up the next.

**ADHD workflow becomes:** scribble `plans/whatever.md` → `clu queue
add whatever` → walk away. Mid-flight adds are first-class.

## Open scoping questions for brainstorm

1. **Storage location.** Per-project `.orchestrator/queue.json`
   (mirrors `state.json`, local + atomic) vs. per-host `registry.json`
   field vs. a `plans/QUEUE.md` operator-editable file. Tradeoff:
   atomicity / discoverability / operator visibility.

2. **Priority insertion semantics.** Just `--front`, or do we need
   `--before <other-slug>` / `--after <other-slug>` for finer control?
   Default FIFO — when does priority actually matter for an ADHD
   workflow vs. for over-engineering?

3. **Worker-callback enqueue.** Can a worker mid-phase call
   `clu queue add <slug> --token T` to chain follow-up work? Mirrors
   the existing `clu spawn` task pattern. Probably yes — but does the
   queued plan inherit token / source / lineage metadata so the
   operator can trace "this plan was enqueued by phase X of plan Y"?

4. **Cross-project queues.** Out of scope for v1, but is there a
   future world where a host-level queue lets the operator say "do
   thing A on HealthData, then thing B on end-of-line"? Decide
   whether to leave a hook for it or explicitly punt.

5. **Failure modes when the queue head's plan file doesn't exist.**
   Block with an operator question, silently skip, or refuse to add
   nonexistent slugs at `queue add` time? (Lean: refuse at add-time
   with a clear error.)

6. **Interaction with the registry.** Today: `clu init` writes to both
   registry + creates `state.json`. With queue: should `queue add`
   pre-register the plan or wait until the queue pulls it? Cleanest is
   probably: queue holds slug strings only, registration happens at
   pull-time (lazy). Confirm.

7. **Resume semantics.** If clu restarts mid-chain (host reboot, cron
   gap), does the queue survive? Yes if `.orchestrator/queue.json`,
   trivially. The supervisor picks up where it left off on next tick.

8. **Visibility / discoverability.** `clu` (bare command, fleet view)
   currently lists registered plans. Should it also show the queue
   inline? Distinct section? Or rely on `clu queue list`?

## Constraints from existing code

- `clu` is multi-plan from day one (see
  `feedback_multi_plan.md` user memory). The queue is the natural next
  layer — same pluralization stance.
- Atomic writes under `flock` (see `end_of_line/state.py:mutate`) are
  the project's invariant. Queue mutations must use the same window.
- `EVENT_*` constants are the audit trail (see CLAUDE.md
  "EVENT_* constants, never raw strings"). New queue ops likely need
  new event types: `EVENT_QUEUE_APPENDED`, `EVENT_QUEUE_POPPED`,
  `EVENT_QUEUE_REMOVED` — but these live where? In each plan's
  state.json, or in a queue-level event log?
- Workers call back via `--token` validated against the live claim
  (CLAUDE.md "--token on every worker callback"). A worker-callback
  `clu queue add` would need similar validation.

## What success looks like

After this lands:

```bash
# scribble plan files at will
$ vim plans/feature-a.md
$ vim plans/feature-b.md
$ vim plans/feature-c.md

# enqueue and walk away
$ clu queue add feature-a
$ clu queue add feature-b
$ clu queue add feature-c
$ clu queue list
1. feature-a    queued
2. feature-b    queued
3. feature-c    queued

# 4 hours later
$ clu queue list
1. feature-a    DONE
2. feature-b    DONE
3. feature-c    running (phase 2 of 3)
```

No Claude sessions in between. No "ping me when done." The supervisor
just walks the queue.

## What to brainstorm (suggested personas)

- **Operator** — ADHD workflow, what reads natural at the CLI, what
  fails the "I forgot what state we're in" test
- **Supervisor architect** — race conditions, lock ordering, what
  events fire when, can a queued plan be claimed by accident
- **Worker-callback designer** — token validation, lineage, can/should
  workers enqueue, what's the contract
- **Storage / persistence** — where queue lives, atomic mutations,
  schema evolution, multi-host vs single-host
- **Failure-mode finder** — corrupt queue file, plan slug typos,
  queue HEAD's plan file deleted mid-flight, host reboot, clu version
  mismatch
- **Out-of-scope auditor** — what the brainstorm convinces itself it
  needs but actually doesn't (cross-project, priorities beyond
  `--front`, GUI, etc.)

After the personas land, the consolidated output should produce a
locked-decisions list ready to feed into `/plan` for a real
multi-phase plan file with `## Sessions index`.

## Recommended brainstorm prompt

> Read `docs/design-briefs/clu-plan-queue.md` (this file), then run
> `/brainstorm` with the six personas listed above to settle the
> eight open scoping questions. Output a consolidated decisions list
> + a phase breakdown ready for `/plan`. Don't write code; this is a
> design pass. Reference `end_of_line/{cli,supervisor,state,registry}.py`
> and `CLAUDE.md` conventions as you go.
