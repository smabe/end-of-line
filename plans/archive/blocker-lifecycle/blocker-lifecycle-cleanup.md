# blocker-lifecycle-cleanup — round-trip test + simplify pass

You are phase `cleanup` of the `blocker-lifecycle` plan. Add an
end-to-end test for the worker → operator → worker blocker
round-trip, delete any now-unused inline helpers in `supervisor.py`,
and run `/simplify` over the cumulative diff.

## Locked decisions (do NOT re-litigate)

See `plans/blocker-lifecycle.md`. Summary:

- End-to-end test exercises the full round-trip: spawn a blocker
  via `clu block`, simulate the operator answer via `clu answer`,
  run 2 ticks (rule 4 then dispatch), assert the worker would see
  the answer.
- `/simplify` is mandatory after migrate (diff >1 file, >30 lines).

## Read first

- `tests/test_worker_callbacks.py` — canonical setUp pattern (git
  init, isolate_registry, init plan, claim_phase).
- `end_of_line/cli.py:cmd_block`, `cmd_answer` — what to invoke
  inside the test.
- `end_of_line/supervisor.py` — confirm post-migrate that no inline
  blocker iteration remains. Anything that's now unused is fair to
  delete.

## Produce

1. **Failing test first** in `tests/test_blocker_round_trip.py`:

   - `test_blocker_round_trip_re_dispatches_with_answer`:
     - Set up plan with phase `foundation`, claim it.
     - Call `main(["block", "--plan", slug, "--phase", "foundation",
       "--token", T, "--question", "go?", "--option", "A", "--option",
       "B"])` → blocker `q-0` recorded, claim cleared.
     - Call `main(["answer", "--project", project, "--plan", slug,
       "q-0", "0"])` → blocker's `answer` field set.
     - Run `supervisor.tick(state_path)` once → rule 4 fires;
       returns `blocker_resumed`; status flips back to RUNNING;
       `consumed=True`.
     - Run `supervisor.tick(state_path)` again → dispatch rule fires;
       new claim on `foundation`; new token minted.
     - Assert: blocker is consumed; new claim exists with phase
       `foundation`; answered blocker is still in state with
       `answer=0, consumed=True` (so the next worker can read it).

2. **Cleanup pass.**
   - `git grep -n "consumed.*False" end_of_line/supervisor.py` — if
     any inline init/check remains from the pre-migrate rule body,
     delete it.
   - `git grep -n "last_repinged_at" end_of_line/supervisor.py` —
     should be only the single stamp site from migrate.
   - Run `/simplify` over the cumulative blocker-lifecycle diff
     (`git diff main...HEAD -- end_of_line/state_blocker.py
     end_of_line/supervisor.py end_of_line/cli.py
     end_of_line/notify.py`).

3. **Acceptance.**
   - The new round-trip test passes.
   - Full suite green at ~+16 tests over the pre-plan baseline
     (extract added ~15, this phase adds 1).
   - `/simplify` output recorded in the commit body's "Under the
     hood" section.
   - `git grep -E "def (render_blocker|render_stalled|render_halted)"
     end_of_line/notify.py` returns 0 (definitions live in
     state_blocker now).

4. **Commit + complete.**
   - Title: `blocker-lifecycle: phase cleanup — round-trip test +
     /simplify pass`
   - Stage: `tests/test_blocker_round_trip.py` plus any files
     `/simplify` touches.
   - `clu complete --plan blocker-lifecycle --phase cleanup --token
     <T>`.

## Failure modes to watch

- **`/simplify` reverting moves.** If `/simplify` proposes
  re-inlining a function back into `supervisor.py`, decline — the
  whole point of the plan is the seam. Accept simplifications that
  remove duplication, decline ones that fight the architecture.
- **Tick count drift.** If rule 4 doesn't fire on the first tick
  after `clu answer`, check the answer write went into the right
  blocker_id and `consumed` is still False.
- **State path normalization.** `cmd_answer` resolves the state
  path via the registry; the test must call `main(["init", ...])`
  first or pre-register the slug. Mirror
  `tests/test_worker_callbacks.py::setUp`.
- **Last-pinged routing.** This phase doesn't exercise iMessage
  inbound routing — the test should call `cmd_answer` directly with
  `--plan <slug>`, not simulate a bare-digit reply.
