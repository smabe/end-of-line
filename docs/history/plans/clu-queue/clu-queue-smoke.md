# clu-queue-smoke — real-world overnight chain dogfood

You are phase `smoke` of the `clu-queue` plan. Phases primitive/add/
list/pop/repair/footer/docs have shipped. Tests are green. The Day-5
problem the brief named ("inter-plan transitions need a live Claude
session") has a code-level fix — your job is to prove it works in
real life by running a three-plan overnight chain end to end.

This is the **integration smoke**, not a unit test. It runs against
the actual end-of-line project, the actual cron LaunchAgent, the
actual `clu` binary installed via `pipx install -e .`.

Design pass is done. Don't redesign. If the smoke surfaces a bug,
file a GitHub issue (don't extend this plan in flight).

## Locked decisions (do NOT re-litigate)

- **Smoke target**: the local end-of-line project itself
  (`~/projects/end-of-line/`).
- **Three trivial plans** that exercise the queue without doing
  anything destructive. Each plan should be a one-phase no-op
  (e.g. "print a sentinel string and commit a marker file in
  `tmp/`") that completes in <30 seconds.
- **Cron cadence**: existing 1-minute tick is fine. No special
  cadence for the smoke.
- **Operator action**: enqueue three plans, walk away, return ≥30
  minutes later, verify all three drained.
- **Success criteria**: all three plans transitioned through
  status RUNNING → DONE in order, with `EVENT_QUEUE_POPPED` as the
  first event in each plan's state.json, queue is empty, history
  has no failure entries, and no notifications fired except the
  expected `KIND_COMPLETED` per plan.
- **If smoke fails**: do NOT debug in-place by extending the plan.
  Capture the symptom, file a GitHub issue with the details, and
  stop the phase. The plan-level done criteria absorb the smoke
  failure (the plan can ship with a known-issue carve-out if the
  failure is non-blocking).

## Read first

- `plans/clu-queue.md` — the master plan; especially the "Done
  criteria (whole plan)" section.
- Existing plan files in `~/projects/end-of-line/plans/`
  (`bundle-recovery`, `bundle-inbound`, etc.) — pick the simplest
  one as a template for the three smoke plans.
- `docs/operations.md` — how to inspect cron logs (the existing
  LaunchAgent setup).

## Produce

1. **Create three trivial smoke plans.** In
   `~/projects/end-of-line/plans/` (NOT in `.claude/plans/`),
   write:
   - `plans/smoke-queue-a.md`
   - `plans/smoke-queue-b.md`
   - `plans/smoke-queue-c.md`

   Each plan has a `## Sessions index` with one trivial phase
   (e.g. "write a sentinel file in `tmp/smoke/<slug>.touched`,
   commit it with a structured message, run `clu complete`"). The
   plans should be small enough that the worker finishes in <30s.

   **DO NOT** make these plans do anything that would interact
   with the real codebase (no edits to `end_of_line/`, no real
   tests). They're purely for the queue's pop+dispatch+complete
   flow.

2. **Enqueue the three plans.** From the project root:
   ```bash
   clu queue add smoke-queue-a
   clu queue add smoke-queue-b
   clu queue add smoke-queue-c
   clu queue list
   ```
   Verify `clu queue list` shows three pending entries with
   STATUS=queued.

3. **Verify bare `clu` footer.** Run `clu` (bare). Output should
   include the footer line `(queue: 3 pending ...)` after the
   fleet table.

4. **Walk away.** Do not run any further commands. The cron
   LaunchAgent ticks every minute. The first tick should pop
   smoke-queue-a, dispatch its worker; the worker runs and calls
   `clu complete`. Next tick: smoke-queue-a is DONE, queue head
   is now smoke-queue-b → pop and dispatch. Same for c.

   Expected timeline: 3 minutes minimum (one tick per pop) +
   worker runtime. Realistic: 5-10 minutes total.

5. **Return after ≥30 minutes.** Verify:
   - `clu queue list` → `(queue is empty)`.
   - `clu` (bare) → no footer (queue empty).
   - `clu` (bare) → all three smoke plans show status DONE in the
     PLAN/STATUS table.
   - For each smoke plan's state.json
     (`.orchestrator/smoke-queue-{a,b,c}.state.json`):
     - `status` is `done`.
     - `events[0]` is `EVENT_QUEUE_POPPED` with the correct slug.
     - `events[1]` or `[2]` is `phase_started`.
     - There's a `phase_completed` and a `plan_done` near the end.
   - `tmp/smoke/smoke-queue-{a,b,c}.touched` files exist
     (sentinel from each worker).
   - Cron LaunchAgent log (`~/Library/Logs/clu-cron.log` or
     wherever the operator's setup writes) shows the expected
     `tick (queue-pop) smoke-queue-X` lines.
   - No `KIND_QUEUE_SKIPPED`, `KIND_QUEUE_CORRUPT`, or
     `KIND_QUEUE_REPAIR_FAILED` notifications fired. (Only
     `KIND_COMPLETED` per plan is expected.)

6. **If anything failed**: capture the failure mode. Run
   `clu queue list`, look at state.json, look at the cron log.
   File a GitHub issue with:
   - The failure timestamp.
   - The state of queue.json + each smoke plan's state.json.
   - The relevant cron log excerpts.
   - The expected vs. actual behavior.

   Then stop. **Do not** extend this plan to fix the bug — it
   ships in a follow-up.

7. **If everything passed**: archive the three smoke plans.
   `git mv plans/smoke-queue-{a,b,c}.md docs/history/plans/clu-queue/`
   (consistent with how other shipped plans are archived under
   `docs/history/plans/`).

   Also delete the `tmp/smoke/*.touched` sentinel files (they
   served their purpose):
   ```bash
   rm -rf tmp/smoke/
   ```

8. **No code changes** in this phase. The only commits are:
   - One commit creating the three smoke plan files (before
     starting the wait).
   - One commit archiving the smoke plans (after success).
   - If a follow-up issue is filed, NO additional commit (the
     issue is the artifact).

9. **`/simplify` is N/A** (no code changes).

10. **Commit.** This is the final commit of the `clu-queue` plan.
    Structured:
    - Title: `clu-queue phase smoke: 3-plan overnight chain drained cleanly`
    - Why: the Day-5 problem the brief named was "inter-plan
      transitions need a live Claude session"; this phase proves
      it's solved in real life, not just unit-tested.
    - What's new: nothing (smoke phase is operational, not code).
    - Under the hood: the three smoke plans drained over ~5
      minutes via cron-driven pop+dispatch; bare `clu` footer
      surfaced the pending count; on completion the queue
      naturally emptied and the footer disappeared.
    - Tests: no new tests; the full suite stays green from phase
      `docs`.
    - Co-Authored-By trailer.

11. **`clu complete --token <token>` with SHA + count summary.**

## Failure modes to watch for

- **Worker never spawns.** Cron isn't running, or the LaunchAgent
  is configured wrong. Check `launchctl list | grep clu`. Symptom:
  queue stays at 3 pending indefinitely, no tick log entries.
- **Worker spawns but fails immediately.** The smoke plan's
  contents have a bug, OR the worker can't find `claude`, OR
  authentication failed. Symptom: state.json has STATUS_HALTED
  after max attempts. The auto-repair pipeline is NOT involved
  (this is per-plan halt, not queue corruption). Check the
  worker's log under `.orchestrator/logs/`.
- **First plan pops but second doesn't.** The busy gate is
  blocking because the first plan's claim never cleared. This
  could mean `clu complete` wasn't called in the worker, or the
  cron is too slow to register the completion before the next
  tick (unlikely with 1-min cadence). Inspect state.json's
  `current_claim` field.
- **`EVENT_QUEUE_POPPED` missing from state.json.** Phase `pop`
  has a bug; file the issue, archive the smoke plans, follow up.
- **Footer says "queue unreadable" mid-run.** Means the queue file
  was momentarily unparseable during a write; rare race. If the
  smoke recovers (next tick succeeds), no issue needed; file if it
  persists.
- **One of the smoke plans goes MISSING.** Plan file got deleted
  between add and pop. Should NOT happen during the smoke (you
  control the file system); if it does, history will have an
  `abandoned` entry — verify the abandonment notification fired
  (deferred during quiet hours).
- **Quiet hours skew the timeline.** If the smoke runs during
  22:00-08:00, the `KIND_COMPLETED` notifications are deferred to
  the next loud window. The QUEUE drains correctly (advancement
  runs 24/7) but the operator-visible "did it complete?" iMessage
  might be delayed. Plan around this if needed.

## Done criteria for this phase

- Three smoke plans drained cleanly via cron without operator
  intervention.
- Each smoke plan's state.json has `EVENT_QUEUE_POPPED` as its
  first event and ends with `status: done`.
- `clu queue list` is empty post-drain.
- Bare `clu` footer disappears when queue empty.
- No unexpected notifications (`KIND_QUEUE_SKIPPED`,
  `KIND_QUEUE_CORRUPT`, etc.) fired.
- Smoke plan files archived to `docs/history/plans/clu-queue/`.
- Sentinel files cleaned up.
- One commit, structured message, no `Fixes` trailer (this plan
  closes #17? — NO. #17 stays open as v2 worker-callback enqueue;
  this plan doesn't address that. The final commit may reference
  the master plan's path but has no `Fixes` trailer).
- `clu complete` with token + SHA + count summary.
- After this phase completes, the operator runs
  `/plan ship clu-queue` to archive `plans/clu-queue.md` and the
  seven sub-plan files into `plans/shipped/` (the `/plan` skill
  workflow's Mode 3).
