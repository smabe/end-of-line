# queue-worker-callback-gates — cap + idempotency + missing-file refusals

You are phase `gates` of `queue-worker-callback`. Add the three
validation gates around the dispatch body shipped in phase `dispatch`:
per-phase cap (count source-tagged entries across queue + history),
idempotency rules (pending=noop, running=noop, done=error), and
re-confirm the missing-plan-file gate emits an `EVENT_QUEUE_REJECTED`
on rejection.

## Locked decisions (do NOT re-litigate)

See `plans/queue-worker-callback.md` § Phase 4. Summary:
- Cap: count `data["queue"] + data["history"]` entries with matching
  `source_plan` AND `source_phase`. `>= max_queue_adds_per_phase`
  exits `QUEUE_CAP` (11) and emits `EVENT_QUEUE_REJECTED`
  `reason="cap"` in source state events.
- Idempotency: pending slug = OK no-op + print "already queued";
  running slug (registered + has live claim) = OK no-op;
  done slug (in queue history) = `STATUS_TRANSITION` (7).
- Missing plan file: `UNKNOWN_TASK` (6) +
  `EVENT_QUEUE_REJECTED` `reason="missing_plan_file"`.

## Read first

- `end_of_line/cli.py:1726-1807` — full `cmd_queue_add` operator
  body (idempotency on pending duplicates is currently
  `STATUS_TRANSITION` for operator; we keep that strictness for
  operator but relax to OK no-op for worker — different intent).
- `end_of_line/queue.py:37-52` — queue empty/load/mutate shape.
- `end_of_line/cli.py:2878-2887` — spawn cap shape to mirror.
- `end_of_line/registry.py` (search for `load_entry_state` /
  `entries`) — how to check whether a queued slug is currently
  running (has registry entry + live `current_claim`).

## Produce

1. **Failing tests first**
   (`tests/test_queue_worker_gates.py`, new):
   - `test_cap_exceeded_at_default_three` — same source phase
     enqueues 3 slugs OK, 4th exits `QUEUE_CAP` (11). Assert
     `EVENT_QUEUE_REJECTED` event in source state with
     `reason="cap"`.
   - `test_cap_counts_history_too` — enqueue 1, pop it (moves to
     history), enqueue 2 more (cap=3 — total queue+history is 3,
     next add at 4 fails).
   - `test_cap_per_phase_independent` — phase `c-extract` adds 3;
     phase `d-extract` (same plan) can still add 3 of its own.
   - `test_cap_doesnt_count_operator_entries` — operator adds 5
     entries; worker on phase `x` can still add 3 (operator entries
     have `source_phase: None`, not the worker's phase).
   - `test_pending_slug_noop_worker` — worker enqueues `foo`, then
     enqueues `foo` again → second call exits OK, queue still has
     one entry, no second event.
   - `test_running_slug_noop_worker` — `foo` pending, popped, now
     running (registered with live claim). Worker enqueues `foo` →
     OK no-op.
   - `test_done_slug_rejected_worker` — `foo` ran and is in queue
     history. Worker enqueues `foo` → `STATUS_TRANSITION` (7).
   - `test_missing_plan_file_emits_rejected_event` — worker enqueues
     `nonexistent` → `UNKNOWN_TASK` (6) AND
     `EVENT_QUEUE_REJECTED` in source state with
     `reason="missing_plan_file"`.

2. **Implementation.**
   - In `_cmd_queue_add_worker`, insert before the inner
     `queue.mutate` window:
     - Cap check: under the queue lock (peek-then-decide). Count
       entries in `qdata["queue"] + qdata["history"]` with
       `e.get("source_plan") == args.source_plan` AND
       `e.get("source_phase") == args.source_phase`. If `>= cap`,
       call `st.append_event(state_data,
       st.EVENT_QUEUE_REJECTED, slug=slug,
       source_phase=args.source_phase, reason="cap")` THEN return
       `_die(ExitCode.QUEUE_CAP, ...)`. The event lands in
       state_data which is still inside its mutation window —
       safe.
     - Idempotency:
       - Pending: `if slug in {e["slug"] for e in qdata["queue"]}`:
         print "already queued: <slug> (position N)", return OK
         (no event, no append).
       - Running: check registry. If slug has a registered state
         AND `state.current_claim` is truthy, treat as running →
         OK no-op (same as pending).
       - Done: `if slug in {e["slug"] for e in qdata["history"]}`:
         return `_die(STATUS_TRANSITION, "<slug> already ran")`.
   - Missing-plan-file: extend the existing pre-lock check to also
     emit `EVENT_QUEUE_REJECTED` BEFORE returning UNKNOWN_TASK. This
     requires opening the state file briefly (we haven't entered the
     state lock yet at the existence-check site). Two options:
     - Move the plan-file existence check INSIDE the state lock
       (post-`assert_claim_match`), so the rejection event lands
       in the same state mutation. **Prefer this** — keeps event +
       rejection atomic.
     - Or: open a separate `st.mutate(source_state_path)` just for
       the rejection event. Don't do this; it forces a second lock
       acquisition for a refusal case.
   - Cap config read: `state_data["config"].get(
     "max_queue_adds_per_phase", st.DEFAULT_MAX_QUEUE_ADDS_PER_PHASE)`.
     Lives on the source plan's state, mirroring `max_spawns_per_phase`.

3. **Acceptance.**
   - 8 new tests green.
   - Phases `foundation` / `cli` / `dispatch` tests still green.
   - Full suite green.

4. **Commit + complete.**
   - Title: `queue-worker-callback: phase gates — cap + idempotency + rejected events (#17)`
   - Stage: `end_of_line/cli.py`, `tests/test_queue_worker_gates.py`.
   - `clu complete --plan queue-worker-callback --phase gates --token <T>`

## Failure modes to watch

- **Cap derivation off-by-one** — count must include the to-be-added
  entry's *predecessors only*. If cap=3, the 4th add fails. Check
  the assertion direction explicitly (`existing >= cap`, not `> cap`).
- **Operator entries leaking into worker's cap count** — they have
  `source_phase: None`, which won't match a worker's source_phase
  string. Verify the filter is `source_phase == args.source_phase`,
  not just `source_plan == args.source_plan`.
- **Pending-noop vs done-error race** — `foo` in `queue` AND in
  `history` (a re-add after pop+history that itself got re-queued
  manually). Per design, pending wins because it's the active
  intent. Check the pending list FIRST, then history.
- **Running-slug check needs registry access** — registry imports
  may already be in scope; if not, import lazily inside the helper
  to keep CLI startup time low. Verify by reading current imports.
