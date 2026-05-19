# plan-locator-extract — state_locator module

You are phase `extract` of the `plan-locator` plan. Create
`end_of_line/state_locator.py` as the one place that walks the
registry, loads each plan's state file with schema tolerance, and
resolves a reply text to a `(state_path, blocker_id, answer_index)`
result.

## Locked decisions (do NOT re-litigate)

See `plans/plan-locator.md`. Summary:

- Module path: `end_of_line/state_locator.py`.
- `LocatorResult` is an enum-variant dataclass with `FOUND |
  AMBIGUOUS | NOT_FOUND | STATE_UNREADABLE` variants.
- Unreadable state files are logged + skipped; the walk continues.
- Pure read — no locks taken.

## Read first

- `end_of_line/notify_imessage_inbound.py` — the current walk:
  `poll_once`, `open_blockers_for_host`, the hydration of
  `OpenBlocker` dataclasses.
- `end_of_line/notify_base.py` — `route_reply`, `OpenBlocker`,
  `Reply`. Re-use these dataclasses where they apply.
- `end_of_line/registry.py` — `entries()` shape (slug + plan_dir +
  state_path).
- `end_of_line/cli.py:cmd_answer` — the operator-side path; note
  how it differs in ambiguity tolerance.
- `end_of_line/state.py` — `load`, `SchemaVersionMismatch`,
  blocker dict shape.

## Produce

1. **Failing tests first** in `tests/test_state_locator.py`. ~12
   tests:

   - `test_no_registered_plans_returns_not_found`.
   - `test_one_open_blocker_bare_digit_returns_found` — single plan
     with `q-0`, reply `"1"` → FOUND with answer_index=1.
   - `test_two_open_blockers_bare_digit_returns_ambiguous` — two
     plans each with an open blocker, reply `"1"` → AMBIGUOUS with
     both candidates.
   - `test_slug_qualified_reply_returns_found` — reply
     `"my-plan 1"` → FOUND for `my-plan`, ignores the other plan.
   - `test_unknown_slug_returns_not_found` — reply
     `"nonexistent 1"` → NOT_FOUND.
   - `test_unrelated_text_returns_not_found` — reply
     `"hello world"` → NOT_FOUND.
   - `test_unreadable_state_file_logs_and_skips` — one of three
     plans has corrupt JSON; the other two resolve normally; warning
     is logged.
   - `test_schema_mismatch_skipped` — one plan's state has the wrong
     schema_version; skipped.
   - `test_missing_state_file_skipped` — registry entry points at a
     deleted path; skipped.
   - `test_no_open_blockers_returns_not_found` — all blockers are
     consumed; reply `"1"` → NOT_FOUND.
   - `test_answered_blocker_not_open` — answered-but-not-consumed
     blockers should NOT match (locator is for *open* blockers, i.e.
     awaiting an answer).
   - `test_returns_state_path_for_writer` — FOUND result includes
     the absolute state_path so the caller can shell `clu answer`.

2. **Implementation** in `end_of_line/state_locator.py`:

   ```python
   from __future__ import annotations

   import logging
   from dataclasses import dataclass, field
   from pathlib import Path
   from typing import Literal

   from end_of_line import state as st
   from end_of_line.notify_base import OpenBlocker, route_reply
   from end_of_line.registry import RegistryEntry

   log = logging.getLogger(__name__)

   Variant = Literal["FOUND", "AMBIGUOUS", "NOT_FOUND", "STATE_UNREADABLE"]


   @dataclass
   class LocatorResult:
       variant: Variant
       state_path: Path | None = None
       blocker_id: str | None = None
       answer_index: int | None = None
       candidates: list[OpenBlocker] = field(default_factory=list)


   def find_blocker_for_reply(
       entries: list[RegistryEntry],
       reply_text: str,
   ) -> LocatorResult:
       all_open: list[tuple[Path, OpenBlocker]] = []
       for entry in entries:
           blockers = _load_open_blockers(entry.state_path, entry.slug)
           if blockers is None:
               continue
           for b in blockers:
               all_open.append((entry.state_path, b))

       resolved = route_reply(reply_text, [b for _, b in all_open])
       # …translate route_reply's return into LocatorResult variants


   def _load_open_blockers(state_path: Path, slug: str) -> list[OpenBlocker] | None:
       try:
           data = st.load(state_path)
       except (FileNotFoundError, st.SchemaVersionMismatch,
               json.JSONDecodeError, OSError) as exc:
           log.warning("state_locator: skipping %s — %s", slug, exc)
           return None
       return _hydrate_open_blockers(data, slug)
   ```

3. **Acceptance.**
   - 12 new tests green.
   - Full suite green.
   - `grep -rn "for entry in registry" end_of_line/` returns 2
     matches: the locator itself + at-least-one existing caller
     (those move in migrate). NOT 3 — anything more means a new
     duplicate snuck in.

4. **Commit + complete.**
   - Title: `plan-locator: phase extract — state_locator module +
     LocatorResult`
   - Stage: `end_of_line/state_locator.py`,
     `tests/test_state_locator.py`.
   - `clu complete --plan plan-locator --phase extract --token <T>`.

## Failure modes to watch

- **`route_reply` semantics.** `route_reply` already implements
  bare-digit + slug-qualified parsing — call it; don't reinvent it.
  The locator's job is the WALK and the schema-tolerant LOAD; the
  matching is `route_reply`'s.
- **Import cycle.** `state_locator` imports from `state`, `registry`,
  `notify_base`. None of those may import back from
  `state_locator`. If a cycle appears, move the dataclass.
- **Open vs answered blockers.** "Open" means `answer is None AND
  consumed is False`. Answered-but-not-yet-consumed blockers are NOT
  open — locator must not match them.
- **Logging discipline.** Use `logging` module, don't `print`.
  Inbound poller logs already use the standard pattern.
- **Don't hold locks.** The locator only reads; it must not take
  `state.locked` or `state.mutate` anywhere.
