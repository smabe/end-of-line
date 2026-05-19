# plan-locator-cleanup — delete dups + grep verify + simplify

You are phase `cleanup` of the `plan-locator` plan. Confirm the
single-walk invariant, delete now-unused helpers, and `/simplify`
over the cumulative diff.

## Locked decisions (do NOT re-litigate)

See `plans/plan-locator.md`. Summary:

- Exactly one registry walk in the codebase post-cleanup
  (inside `state_locator`).
- All duplicate hydration helpers in callers are deleted.
- `/simplify` is mandatory.

## Read first

- `end_of_line/state_locator.py` — the canonical walk.
- `end_of_line/notify_imessage_inbound.py` and
  `end_of_line/notify_discord_inbound.py` — confirm post-migrate
  they're thin callers (target ~10-15 lines for the dispatch loop).
- `end_of_line/cli.py:cmd_answer` — confirm it no longer walks the
  registry itself.

## Produce

1. **Grep-verify invariants.** Run these checks and assert the
   expected counts in your commit body:
   - `git grep -n "for entry in registry.entries" end_of_line/` →
     1 match (in `state_locator.py`).
   - `git grep -n "_find_or_halt_blocker\|hydrate_open_blockers\|open_blockers_for_host" end_of_line/`
     → 0 matches (or only inside `state_locator.py` as internal
     helpers).
   - `git grep -n "for entry in registry" end_of_line/` → 1 match.

2. **Delete dead helpers.**
   - `end_of_line/notify_imessage_inbound.py`: remove
     `open_blockers_for_host` (or any post-migrate residual) if
     nothing imports it.
   - `end_of_line/cli.py`: remove `_find_or_halt_blocker` and any
     similar inline walk.
   - Update imports.

3. **`/simplify` pass.** Run over the cumulative diff:
   `git diff main...HEAD -- end_of_line/state_locator.py
   end_of_line/notify_imessage_inbound.py
   end_of_line/notify_discord_inbound.py end_of_line/cli.py`. Apply
   suggestions that remove duplication; decline suggestions that
   fight the locator architecture.

4. **Acceptance.**
   - All three grep counts match.
   - Full suite green at the post-extract count + 0.
   - `/simplify` output recorded in commit body's "Under the hood"
     section.

5. **Commit + complete.**
   - Title: `plan-locator: phase cleanup — dedupe walks, /simplify,
     single-walk invariant`
   - Stage: `end_of_line/notify_imessage_inbound.py`,
     `end_of_line/cli.py`, plus any files `/simplify` touches.
   - `clu complete --plan plan-locator --phase cleanup --token <T>`.

## Failure modes to watch

- **External imports of deleted helpers.** Grep the whole repo (not
  just `end_of_line/`) before deleting — tests, examples, skills
  may all import them. Fix the import sites before deletion.
- **`/simplify` proposing re-inlining.** If `/simplify` suggests
  moving the walk back into a caller "because it's only used once
  now," decline — the locator is the architectural point. The fact
  that today only the iMessage and CLI callers exist doesn't change
  that.
- **Stale docstrings.** `notify_imessage_inbound.py` and `cli.py`
  may have docstrings claiming they walk the registry. Update them
  to point at `state_locator`.
- **Discord Reply-UI path.** Don't delete the
  `message_reference.message_id` direct-correlation path; it's not
  a registry walk and it shouldn't go through the locator.
