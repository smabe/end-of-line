# clu-queue-pop — supervisor post-loop queue advancement in `cmd_tick_all`

You are phase `pop` of the `clu-queue` plan. Phases primitive/add/list
have shipped: `queue.py` exists, all operator CLI commands work, queue
files are populated by real `clu queue add` invocations. Your job: add
the per-project post-loop pop step to `cmd_tick_all` so cron drains
the queue without operator intervention.

Design pass is done. Full context in
`.claude/plans/plan-queue-master.md` and `plans/clu-queue.md`. Do not
redesign.

## Locked decisions (do NOT re-litigate)

- **Per-plan `tick()` is unchanged.** The 8-rule priority chain
  inside `tick()` stays exactly as it is.
- **New step is post-loop in `cmd_tick_all`** (cli.py:444): after the
  per-plan loop, iterate distinct `project_roots` from
  `registry.entries()`. At most one queue-pop per project per tick.
- **Per-project busy gate**: skip a project's pop if any plan in
  that project has `current_claim is not None`.
- **Head-only freeze**: if queue head's slug is already registered
  with status in `{HALTED, HALTED_REPLAN, PAUSED}`, don't pop, don't
  advance. (`clu queue list` already renders the freeze marker from
  phase `list`.)
- **Absorb cases** (pop without re-init, history outcome=`absorbed`):
  head's slug is registered AND status is `DONE`, OR status is
  `RUNNING` with state.json already created (likely the manual
  `clu init` collision case).
- **Abandon case**: head's plan file `<plan_dir>/<slug>.md` doesn't
  exist. Pop, history outcome=`abandoned`, fire `KIND_QUEUE_SKIPPED`
  (defers in quiet hours).
- **Normal pop sequence (state → registry → queue-pop, all under
  queue lock, mirrors `cmd_init` at cli.py:340-348):**
  1. Acquire queue lock.
  2. Read head; check freeze / absorb / abandon as above.
  3. Acquire state lock; create state.json with `EVENT_QUEUE_POPPED`
     as first event (provenance: slug, added_at, position).
  4. `registry.register(project_root, slug)` (idempotent).
  5. Pop head from queue.
  6. Release locks.
  7. Dispatch worker via `dispatch_for_tick` outside any lock.
- **`EVENT_QUEUE_POPPED`** — new constant in state.py. Fields:
  `slug`, `added_at`, `added_by`, `position`. Written as the first
  event in the new plan's state.json before dispatch.
- **No `KIND_QUEUE_ADVANCED` notification.** Successful pops are silent.
- **`KIND_QUEUE_SKIPPED`** — new constant in notify.py, defers in
  quiet hours.
- **Crash recovery falls out** of existing idempotency: state.create
  checks `state_path.exists()` (mirrors `cmd_init` cli.py:342);
  `registry.register` returns False on dup; `tick()` at
  supervisor.py:93 handles "registered + no state.json" as idle.
  Tests must simulate the crash and confirm recovery.

## Read first

- `end_of_line/cli.py` `cmd_tick_all` (~line 523). The per-plan
  loop is your insertion point's predecessor.
- `end_of_line/cli.py` `cmd_init` (~line 338). Pop sequence mirrors
  this; especially the order state.create → registry.register and
  the existence check at cli.py:342.
- `end_of_line/supervisor.py` `tick()` (~line 92) — confirm
  unchanged. Especially supervisor.py:93's "no state file → idle"
  branch; that's load-bearing for crash recovery.
- `end_of_line/state.py` `STATUS_*` constants, `EVENT_*` constants
  (you're adding `EVENT_QUEUE_POPPED`), `empty_state(...)`,
  `append_event`, `claim_phase`, `current_claim` access.
- `end_of_line/registry.py` `entries()` and `load_entry_state()`.
- `end_of_line/notify.py` — `KIND_*` constants, `notify(kind, ...)`
  signature, `QUIET_HOURS_BYPASS_KINDS` set. You're adding
  `KIND_QUEUE_SKIPPED` (NOT in halt-bypass set; defers).
- `end_of_line/dispatch.py` `dispatch_for_tick` — the existing
  dispatch entry point; you reuse it.
- `CLAUDE.md` — "One tick = one action" still holds per-plan;
  queue-pop is one host-level action on top of the loop.

## Produce

1. **TDD: failing tests first.** Add `tests/test_queue_pop.py`:

   - `test_pop_dispatches_idle_project_with_pending_queue` —
     project P, no live claims, queue has [foo], `plans/foo.md`
     exists. Run `cmd_tick_all`. Result: registry has (P, foo),
     `foo.state.json` exists with `EVENT_QUEUE_POPPED` as first
     event, queue is empty, dispatch was called.
   - `test_pop_skipped_when_project_has_active_claim` — project P
     has plan-A with current_claim set. Queue has [foo]. Tick runs.
     Result: queue still [foo], foo never registered, no dispatch.
   - `test_pop_multi_project_independent` — project A is busy
     (has claim), project B is idle with queue [foo]. Tick. Result:
     A's queue (if any) unchanged, B's queue pops foo, A's plan
     ticks normally per-plan.
   - `test_pop_caps_at_one_per_project_per_tick` — project P with
     queue [a, b]. Tick. Result: a is popped + dispatched; b still
     in queue. Tick again (with a still RUNNING + claimed): b NOT
     popped (busy gate). After a's claim clears: next tick pops b.
   - `test_pop_freezes_on_halted_head` — project P. Slug `foo` is
     registered with status HALTED. Queue has [foo, bar].
     Tick. Result: queue unchanged (foo still at head, frozen), no
     dispatch.
   - `test_pop_freezes_on_paused_head` — same with STATUS_PAUSED.
   - `test_pop_freezes_on_halted_replan_head` — same with
     STATUS_HALTED_REPLAN.
   - `test_pop_absorbs_done_head` — queue head's slug is
     registered with status DONE (manual init collision). Tick.
     Result: queue is popped (head removed), history has entry with
     outcome=`absorbed`, no new state.json write, no dispatch (the
     plan is done).
   - `test_pop_absorbs_running_head` — head's slug is already
     registered with status RUNNING (state.json exists, no claim).
     Tick. Result: queue popped (history outcome=`absorbed`),
     existing state.json unchanged, no dispatch (per-plan tick
     handles it).
   - `test_pop_abandons_missing_plan_file` — queue has [foo],
     plans/foo.md doesn't exist at pop time. Tick. Result: queue
     popped, history outcome=`abandoned`, `KIND_QUEUE_SKIPPED`
     notification fired (or marked deferred if in quiet hours).
   - `test_pop_recovers_after_crash_between_state_and_registry` —
     create state.json for foo manually (simulating a partial pop
     crash), leave registry empty, queue still has [foo]. Tick.
     Result: state.create skipped (already exists), registry.register
     fires (idempotent), queue popped, recovers.
   - `test_pop_event_queue_popped_first_event` — after pop +
     dispatch, the new plan's state.json events[] starts with
     `EVENT_QUEUE_POPPED` carrying slug, added_at, added_by,
     position. The phase_started event comes after.
   - `test_pop_skipped_when_bootstrap_project_not_in_registry` —
     project Q has NO registered plans and NO queue file
     (registry.entries() returns nothing for Q). Tick. Result:
     Q is never visited; no pop attempt.
   - `test_pop_does_not_block_other_projects_if_one_queue_corrupt` —
     project A has a corrupt queue.json. Project B has valid queue
     [foo]. Tick. Result: B's foo pops; A's queue logs the corruption
     (this is the phase-repair handoff — for this phase, just
     verify the iteration doesn't crash). Phase `repair` adds the
     full corruption handling.

   Use `isolate_registry` + `isolate_queue` + tmp project roots.
   Mock `dispatch_for_tick` to assert it was called with the right
   args without actually spawning. Run suite — all new tests must
   FAIL.

2. **Add `EVENT_QUEUE_POPPED` constant** to `state.py` near the
   other EVENT_* constants. Document the fields in a comment.

3. **Add `KIND_QUEUE_SKIPPED` constant** to `notify.py` near the
   other KIND_* constants. Do NOT add it to `QUIET_HOURS_BYPASS_KINDS`
   (skips defer during quiet hours).

4. **Add a `render_queue_skipped(slug, reason)` function** to
   notify.py (mirrors `render_halted`, `render_completed`, etc.).

5. **Implement the post-loop step.** Edit `cmd_tick_all` (cli.py:523):

   ```python
   def cmd_tick_all(args) -> int:
       # Existing per-plan loop (unchanged)
       results = []
       for row in registry.entries():
           try:
               cfg = load_project_config(Path(row.project_root))
               state_path = cfg.state_path(row.plan_slug)
               result = _tick_one_plan(row.plan_slug, cfg, state_path, dispatch=True)
               results.append((row, result))
               print(f"tick {row.plan_slug} @ {row.project_root}: {result}")
           except Exception as exc:
               print(
                   f"tick-all: {row.plan_slug} @ {row.project_root}: {type(exc).__name__}: {exc}",
                   file=sys.stderr,
               )

       # NEW: per-project queue advancement
       seen_projects = {
           Path(row.project_root).resolve(): None
           for row in registry.entries()
       }
       for project_root in seen_projects:
           try:
               _advance_queue_for_project(project_root, registry.entries())
           except Exception as exc:
               print(
                   f"tick-all queue @ {project_root}: {type(exc).__name__}: {exc}",
                   file=sys.stderr,
               )

       return ExitCode.OK
   ```

   Implement `_advance_queue_for_project(project_root, all_registry_entries)`:

   ```python
   def _advance_queue_for_project(project_root: Path, all_entries) -> None:
       cfg = load_project_config(project_root)
       queue_path = cfg.queue_path()
       if not queue_path.exists():
           return

       # Per-project busy gate (uses pre-loaded entries)
       project_entries = [
           e for e in all_entries
           if Path(e.project_root).resolve() == project_root
       ]
       for entry in project_entries:
           state = registry.load_entry_state(entry)
           if state and state.get("current_claim"):
               return  # busy

       # Load queue (corruption handling deferred to phase `repair`)
       try:
           queue_data = queue.load(queue_path)
       except Exception as e:
           # Phase `repair` will replace this with the auto-repair pipeline.
           # For phase `pop`, just log and skip — never crash the loop.
           print(f"queue load failed @ {project_root}: {e}", file=sys.stderr)
           return

       if not queue_data["queue"]:
           return

       head = queue_data["queue"][0]
       slug = head["slug"]
       st.validate_slug(slug, kind="plan slug")  # defense in depth

       # Re-check head's registry status (could have changed since
       # the busy gate; cheap and avoids races where pop sees a
       # registered halted plan).
       state_path = cfg.state_path(slug)
       existing_status = None
       if state_path.exists():
           try:
               existing = st.load(state_path)
               existing_status = existing.get("status")
           except Exception:
               pass

       # Freeze check
       if existing_status in {st.STATUS_HALTED, st.STATUS_HALTED_REPLAN, st.STATUS_PAUSED}:
           return  # freeze; don't pop

       # Absorb check
       if existing_status in {st.STATUS_DONE, st.STATUS_RUNNING}:
           with queue.mutate(queue_path) as data:
               # Re-check under lock; head might have shifted from
               # a concurrent operator action.
               if not data["queue"] or data["queue"][0]["slug"] != slug:
                   return
               entry = data["queue"].pop(0)
               data["history"].append({
                   **entry,
                   "ended_at": st.utcnow_iso(),
                   "outcome": "absorbed",
               })
           return  # no dispatch — per-plan tick handles it

       # Abandon check (plan file missing)
       plan_file = cfg.plan_dir / f"{slug}.md"
       if not plan_file.exists():
           with queue.mutate(queue_path) as data:
               if not data["queue"] or data["queue"][0]["slug"] != slug:
                   return
               entry = data["queue"].pop(0)
               data["history"].append({
                   **entry,
                   "ended_at": st.utcnow_iso(),
                   "outcome": "abandoned",
               })
           notify.notify(
               cfg.notify,
               kind=notify.KIND_QUEUE_SKIPPED,
               body=notify.render_queue_skipped(slug, reason="plan file missing"),
           )
           return

       # Normal pop sequence — all under queue lock
       with queue.mutate(queue_path) as data:
           if not data["queue"] or data["queue"][0]["slug"] != slug:
               return  # raced
           # state-create first (idempotent: skip if exists)
           with st.locked(state_path):
               if not state_path.exists():
                   state = st.empty_state(slug, cfg.plan_dir)
                   st.append_event(
                       state, st.EVENT_QUEUE_POPPED,
                       slug=slug,
                       added_at=head.get("added_at"),
                       added_by=head.get("added_by", "operator"),
                       position=1,
                   )
                   st.save_atomic(state_path, state)
           # registry.register second (idempotent)
           registry.register(cfg.project_root, slug)
           # pop queue last
           data["queue"].pop(0)

       # Dispatch outside all locks (matches existing tick pattern)
       result = _tick_one_plan(slug, cfg, state_path, dispatch=True)
       print(f"tick (queue-pop) {slug} @ {cfg.project_root}: {result}")
   ```

   Match the codebase's actual idioms — the snippet is structural.

6. **Run the full suite.** All new tests pass. Existing tests
   unchanged. Count grows by ~14.

7. **`/simplify`.** This is the largest phase by LOC; /simplify is
   mandatory.

8. **Commit.** Structured:
   - Title: `clu-queue phase pop: per-project queue advancement in tick-all`
   - Why: cron must drain queues without operator intervention; this
     is the rule-10 host-level step that closes the Day-5 gap.
   - What's new: post-loop step in `cmd_tick_all` iterating distinct
     project roots; `_advance_queue_for_project` with freeze/absorb/
     abandon/normal-pop branches; `EVENT_QUEUE_POPPED`,
     `KIND_QUEUE_SKIPPED`.
   - Under the hood: state→registry→queue-pop order mirrors cmd_init;
     all under queue lock; dispatch outside locks; crash recovery
     via existing idempotency.
   - Tests: ~14 new tests covering all branches + crash recovery +
     multi-project independence.
   - Co-Authored-By trailer.

9. **`clu complete --token <token>` with SHA + count summary.**

## Failure modes to watch for

- **Iterating `registry.entries()` twice per tick.** Cheap (≤50ms
  per architecture.md budget) but worth knowing. Don't cache between
  the per-plan loop and the queue loop — fresh reads avoid stale
  state from concurrent mutations.
- **Race: queue mutated during pop.** The `data["queue"][0]["slug"]
  != slug` re-check after acquiring `queue.mutate` defends against
  this. Don't rely on the pre-check.
- **`state.empty_state` signature.** Verify it takes plan_slug and
  plan_dir (or equivalent) — match the existing `cmd_init` call at
  cli.py:345.
- **Provenance event before phase_started.** Order: `empty_state`
  → `append_event(EVENT_QUEUE_POPPED)` → `save_atomic`. The
  per-plan tick that follows will write `phase_started` as the
  second event when it claims the first phase. Tests must assert
  this order.
- **Multi-project: queue.json paths.** Each project has its own
  queue.json under its own `.orchestrator/`. Don't accidentally
  resolve to a single global path.
- **Notification deferral.** `KIND_QUEUE_SKIPPED` must defer in
  quiet hours; ensure it's NOT in `QUIET_HOURS_BYPASS_KINDS`.
  Verify with a test that sets quiet hours = 24/7 and confirms the
  notify call enqueues rather than fires.
- **`dispatch_for_tick` invocation.** The existing path uses
  `_tick_one_plan` which internally calls dispatch — match the
  exact call shape so the worker spawn / log / claim machinery
  Just Works.
- **Slug validation defense-in-depth.** Even though slugs are
  validated at `queue add`, re-validate at pop time. A corrupt or
  manually-edited queue.json could smuggle a bad slug past
  load-time.

## Done criteria for this phase

- `cmd_tick_all` runs the per-project post-loop queue advancement.
- A 3-entry queue on an idle project drains across 3 successive
  `tick-all` invocations.
- Multi-project independence verified: A busy doesn't block B.
- Freeze marker at head HALTED/HALTED_REPLAN/PAUSED prevents pop.
- Absorb on DONE/RUNNING head pops without re-init.
- Abandoned head (missing plan file) pops + history + KIND_QUEUE_SKIPPED.
- `EVENT_QUEUE_POPPED` is the new plan's first event with full
  provenance.
- Crash-recovery test simulating partial pop reaches green next tick.
- Corrupt queue.json in one project doesn't crash the loop or block
  other projects (full corruption handling lands in phase `repair`).
- ~14 new tests pass; full suite green.
- One commit, structured message, no `Fixes` trailer.
- `clu complete` with token + SHA + count summary.
