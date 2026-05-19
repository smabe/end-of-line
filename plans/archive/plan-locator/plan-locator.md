# plan-locator — one module owns "find which plan answers this reply"

When an iMessage reply arrives, `notify_imessage_inbound.poll_once`
walks the Registry, loads every Plan's state file in memory,
hydrates `OpenBlocker` dataclasses, and calls `route_reply`. The
operator-side `cli.cmd_answer` follows a parallel walk with slightly
different ambiguity tolerance. The Discord InboundPoller (post-#11)
adds a third copy of the walk. Each callsite tolerates missing /
stale / schema-mismatched state files independently.

This plan extracts a `state_locator.py` module that owns the walk:
load registry → load each plan's state file (skipping unloadable
ones with a clear reason) → match the reply against open blockers →
return `(state_path, blocker_id, answer_index)` or `None`. The three
callers become thin: load registry → call locator → dispatch.
Independent of plans A and C; can run in parallel.

## Locked design decisions

### Cross-cutting

- **Module name: `end_of_line/state_locator.py`.** **Why:** owns
  resolution across many state files; parallel to `state.py` which
  owns one state file. **How to apply:** import-from-state pattern.

- **Ambiguity rules consolidate.** Today
  `notify_imessage_inbound` drops ambiguous bare-digit replies
  silently; `cmd_answer` errors out on similar ambiguity.
  Reconcile: locator returns a `LocatorResult` enum (`FOUND |
  AMBIGUOUS | NOT_FOUND | STATE_UNREADABLE`) with the matching
  candidates. Callers decide the UX (poller drops silently, CLI
  prints to stderr). **Why:** the matching logic shouldn't differ
  by caller; the response policy should.

- **Stale state files don't fail the whole walk.** A plan whose
  state file is unreadable (schema mismatch, JSON corruption,
  missing) gets logged and skipped; the locator continues with the
  remaining plans. **Why:** today one bad state file crashes the
  poller (operator-reported friction). **How to apply:** wrap each
  load in a try/except logging the slug + reason.

### Phase 1 — extract

- **New module `end_of_line/state_locator.py`:**
  - `@dataclass LocatorResult` with `variant: Literal["FOUND",
    "AMBIGUOUS", "NOT_FOUND", "STATE_UNREADABLE"]`, optional
    `state_path`, optional `blocker_id`, optional `answer_index`,
    and `candidates: list[OpenBlocker]` for the AMBIGUOUS case.
  - `find_blocker_for_reply(registry_entries, reply_text) ->
    LocatorResult` — owns the walk, the bare-digit-needs-uniqueness
    rule, the slug-qualified match, and the schema-tolerant load.
  - Internal helper `_load_open_blockers(state_path) ->
    list[OpenBlocker] | None` (None on unreadable).
- **Tests in `tests/test_state_locator.py`:** ~12 tests covering
  bare digit / slug-qualified / unrelated-text / ambiguous-bare-
  digit / no-open-blockers / unreadable-state-file / multiple-
  matches-one-readable-one-not.
- **Pure refactor:** no behavior change visible to existing tests.

### Phase 2 — migrate

- `notify_imessage_inbound.poll_once` becomes ~10 lines: read
  chat.db rows → for each row, call locator → on `FOUND` shell `clu
  answer`; on `AMBIGUOUS`/`NOT_FOUND` drop silently with a log
  line.
- `cli.cmd_answer` calls locator with the operator-supplied slug
  (if provided, treats it as slug-qualified; otherwise as
  bare-digit). On `AMBIGUOUS` prints to stderr listing the
  candidates and exits non-zero.
- Discord inbound (`notify_discord_inbound.poll`, post-#11) added
  to the callsite list — same shape as iMessage inbound.
- Existing tests for inbound + cmd_answer stay green; any tests
  that mocked the walk get rewritten against the locator interface.

### Phase 3 — cleanup

- Delete the duplicate walk helpers from `notify_imessage_inbound`
  and `cli.py` (e.g. `_find_or_halt_blocker`).
- Verify the Discord InboundPoller is also a thin caller — grep
  for `for entry in registry.entries()` outside `state_locator.py`
  and confirm only one match remains (the locator itself).
- `/simplify` over the diff.
- Suite green at ~+12 new tests over baseline.

## Non-goals

- **Last-pinged routing for ambiguous bare digits.** Deferred (see
  `docs/architecture.md` Day 2.4 note). Locator returns `AMBIGUOUS`
  with all candidates; callers can pick a policy later.
- **Locking discipline change.** No state-file locks taken during
  the walk (the locator is read-only); callers still take locks
  inside `clu answer` for the write.
- **InboundPoller protocol changes.** Per-channel poll signatures
  unchanged.
- **State schema changes.** Locator is read-only over current
  schema.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green.
- Structured commit format.
- Stage explicit paths.
- Call `clu complete --plan plan-locator --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| extract | `plan-locator-extract.md` | New `state_locator.py` with `LocatorResult` + walk + load-tolerance; ~12 tests | 2h |
| migrate | `plan-locator-migrate.md` | Rewire `notify_imessage_inbound`, `cmd_answer`, `notify_discord_inbound` | 1.5h |
| cleanup | `plan-locator-cleanup.md` | Delete duplicate walks, `/simplify`, grep-verify single walk | 1h |
