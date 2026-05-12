# bundle-inbound-routing — last-pinged wins for bare-digit replies

You are phase `routing` of the `bundle-inbound` plan. Implement GH
issue #3: when an iMessage reply is just a bare digit ("0", "1") and
2+ registered plans have open blockers, route to whichever plan was
most-recently pinged. Slug-prefixed replies ("halt-bypass 1") still
take precedence.

## Locked decisions (do NOT re-litigate)

See `plans/bundle-inbound.md`. Summary:

- **Source of truth:** derive `last_blocker_notified_at` from each
  plan's existing `EVENT_PHASE_BLOCKED` events. **No new state field,
  no new registry mutation, no migration.**
- Slug-prefix routing wins over last-pinged. Explicit beats inferred.
- If the last-pinged plan has no open blocker matching the bare-digit
  index, fall through to the next-most-recent-pinged plan with one.
- Theoretical tie (every plan pinged at the same millisecond) →
  refuse with a clear error message.

## Read first

- GH issue #3 body + the existing triage comment.
- `end_of_line/notify_inbound.py`:
  - `OpenBlocker` (presumably a dataclass / NamedTuple) — the row
    type that `open_blockers_for_host` returns. You'll likely extend
    it with a `last_notified_at` field derived from the plan's
    most-recent `EVENT_PHASE_BLOCKED`.
  - `open_blockers_for_host` at line 50 — walks the registry, loads
    each plan's state.json. Already reads the events; just keep the
    timestamp.
  - `route_reply` at line 73 — currently handles single-plan-blocker
    case + slug-prefix case + refuses on multi-plan ambiguity. The
    ambiguity branch is what changes.
- `end_of_line/state.py` — `EVENT_PHASE_BLOCKED` constant at line 73;
  the event payload's `ts` field (ISO timestamp) is the timestamp
  source.
- `end_of_line/registry.py` — `entries()` returns plan rows; lean on
  this to know what "all plans on the host" means.

## Produce

1. **Failing tests first.** Extend `tests/test_notify_inbound.py`
   with these cases:
   - **Single plan open blocker, bare digit** → routes to that plan
     (regression — current behavior preserved).
   - **Two plans open blockers, bare digit** → routes to the one
     whose most recent `EVENT_PHASE_BLOCKED` is later. Construct two
     plans with explicit `ts` strings so the order is deterministic.
   - **Three plans, bare digit** → most-recent wins; second-most-
     recent doesn't fire.
   - **Slug-prefix override** → `"halt-bypass 1"` routes to
     `halt-bypass` even when another plan was pinged more recently.
   - **Last-pinged plan has no matching blocker (out-of-range index)**
     → falls through to the next-most-recently-pinged plan that has
     one.
   - **Tie on timestamp** (two plans, same `EVENT_PHASE_BLOCKED` ts) →
     refuses with a clear error. Don't silently pick one.
   - **No bare-digit reply** (plain text answer) → unchanged behavior;
     this routing change only affects digit-only replies.
   Use plan-state fixtures that you write directly to disk (mirror
   what `test_notify_inbound.py` already does for blocker fixtures).

2. **Implementation.**
   - **`OpenBlocker`** (or whatever the row type is): add a
     `last_notified_at: str` field. Populate it from the plan's most
     recent `EVENT_PHASE_BLOCKED.ts` while you're already iterating
     the state file inside `open_blockers_for_host`.
   - **`route_reply`**: when the reply is bare-digit AND multiple
     plans have open blockers, sort by `last_notified_at` descending,
     pick the first plan whose blocker list contains a valid index
     for the digit. If two top entries tie on timestamp, return the
     ambiguity-refusal path.
   - **Don't add a new public function**. The routing logic stays
     inside `route_reply`; just split out a private `_pick_by_last_pinged`
     helper if the branch grows past ~10 lines.

3. **`/simplify`** if the diff spans >1 file or ~30 lines.

4. **Full suite green:** `python3 -m unittest discover -s tests`.

5. **Commit** (structured format). Stage explicit paths:
   `git add end_of_line/notify_inbound.py tests/test_notify_inbound.py`.

6. **Close GH #3:** PATH-defensive `gh` close from
   `plans/bundle-recovery.md`'s snippet.

## Constraints

- **No new state field.** Derive everything from existing
  `EVENT_PHASE_BLOCKED` events.
- **No registry mutation.** The registry stays the host config layer;
  per-plan timing data does not go there.
- **No new event type.** `EVENT_PHASE_BLOCKED` already carries the
  timestamp; that's the source.
- **Slug-prefix routing must keep working unchanged.** The current
  test coverage for it must stay green.
- **The ambiguity-refused path stays in place for the tie case.**
  Don't silently pick "first by alphabetical plan slug" — that's
  worse than refusing.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan bundle-inbound --phase routing \
    --token <token> --commit <sha>
```

## Escape hatch

`clu block` if:
- The state files in the test fixtures are written through a helper
  that doesn't expose `EVENT_PHASE_BLOCKED.ts` directly, and writing
  one through the public API would require dispatching a real
  blocker. Surface — there's likely a `tests/__init__.py` helper or
  a direct dict-write pattern; if not, ask.
- The `OpenBlocker` type is consumed elsewhere with a fixed shape
  (e.g. tuple unpacking by index) and adding a field would silently
  break consumers. Surface the consumers.
