# blocker-lifecycle-migrate — supervisor + cmd_block rewiring

You are phase `migrate` of the `blocker-lifecycle` plan. Rewire
`supervisor.py` rule 4, the stuck-blocker `side_notifies` rule, and
`cli.cmd_block` to call into the new `state_blocker` module. No new
tests; pure callsite rewiring. The full suite must remain green at
the same count as end-of-extract.

## Locked decisions (do NOT re-litigate)

See `plans/blocker-lifecycle.md`. Summary:

- Supervisor calls `state_blocker.process_answered_blockers` inside
  the existing `with st.mutate():` window — does NOT take a new
  lock.
- Side-notify path: call `state_blocker.stuck_blocker_repings`,
  iterate the return, stamp `last_repinged_at` + append to
  `side_notifies` — one mutation window, no fan-out.
- `cli.cmd_block` uses the moved `render_blocker` for its
  immediate `KIND_BLOCKER` send.
- Notify gating (quiet hours, halt-bypass kinds) stays where it is
  — in `notify.notify`. Don't replicate it in `state_blocker`.

## Read first

- `end_of_line/state_blocker.py` — what extract shipped (read the
  function signatures; you'll be calling them).
- `end_of_line/supervisor.py` — locate rule 4 and the
  `side_notifies` block. The `with st.mutate(state_path) as data:`
  window is the seam.
- `end_of_line/cli.py:cmd_block` — find the `notify.notify(...,
  KIND_BLOCKER, ...)` call and the body it passes.
- `end_of_line/notify.py` — confirm `render_*` are now re-exports;
  callers reaching for `notify.render_blocker` still work.

## Produce

1. **No new failing tests.** This phase is a pure rewire; existing
   tests must remain green. If a test breaks during migration, the
   rewire is wrong — fix the rewire, don't change the test.

2. **Implementation.**

   - `end_of_line/supervisor.py` rule 4:
     ```python
     # OLD: inline scan for answered blockers
     # NEW:
     events, target_status = state_blocker.process_answered_blockers(data)
     if events:
         for ev_type, blocker_id in events:
             st.append_event(data, ev_type, blocker_id=blocker_id)
             data["blockers"][blocker_id]["consumed"] = True
         if target_status:
             data["status"] = target_status
         return TickResult(action="blocker_resumed", ...)
     ```

   - `end_of_line/supervisor.py` side-notify path for stuck blockers:
     ```python
     stuck = state_blocker.stuck_blocker_repings(data, now)
     for blocker_id, kind, body in stuck:
         data["blockers"][blocker_id]["last_repinged_at"] = now.isoformat()
         side_notifies.append((kind, body))
     ```

   - `end_of_line/cli.py:cmd_block`:
     ```python
     body = state_blocker.render_blocker(blocker, plan_slug)
     notify.notify(cfg, KIND_BLOCKER, body, plan_slug=plan_slug,
                   blocker_id=blocker_id)
     ```

   - `end_of_line/notify.py`: confirm the three render names still
     resolve (re-exports from extract). If any external caller
     imports them, this keeps working.

3. **Acceptance.**
   - Suite green at the post-extract count (same number, no new
     tests).
   - `git grep -n "for bid.*blocker" end_of_line/supervisor.py`
     returns 0 matches (the inline scan is gone).
   - `git grep -n "last_repinged_at" end_of_line/supervisor.py`
     returns the single rewire site, not the original inline rule.
   - Manual smoke: `python3 -m end_of_line.cli --help` doesn't
     regress.

4. **Commit + complete.**
   - Title: `blocker-lifecycle: phase migrate — supervisor + cmd_block
     call state_blocker`
   - Stage: `end_of_line/supervisor.py`, `end_of_line/cli.py`.
   - `clu complete --plan blocker-lifecycle --phase migrate --token
     <T>`.

## Failure modes to watch

- **Two ticks instead of one.** Process-answered + stuck-blocker
  re-ping are both lifecycle steps but on different paths. The
  rule-4 return short-circuits the priority chain (one action);
  side-notifies fire ALONGSIDE the first match. Don't move
  stuck-blocker into the priority chain — it's a side-notify by
  design (ADR-0002).
- **Double-stamp on `consumed`.** rule 4 sets `consumed=True`
  itself (inside the same mutation window); don't expect
  `state_blocker` to do it. The module returns events; the caller
  applies them.
- **`/simplify` after migration.** Even a "pure rewire" can leave
  vestigial helpers — run `/simplify` over the diff.
- **Lock-ordering preservation.** `state_blocker` functions must
  be called INSIDE the existing `with st.mutate():` window, not
  before/after. They're pure but the data they read must be the
  same snapshot the rule will then write back.
