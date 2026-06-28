# session-activity-refactor — extract a shared row-builder

You are phase `refactor` of the `session-activity` plan. It delivers one commit: a shared base-row helper that `assemble_row` and `assemble_blocked_row` both build from, so the upcoming `assemble_session_row` is the third caller of one source of truth — not a third hand-copied D10 schema. Pure refactor, zero behavior change.

## Locked decisions (do NOT re-litigate)
See master `plans/session-activity.md`. Binding here:
- Extract is the right call NOW: `assemble_row` (top.py:284 @7dbe001) and `assemble_blocked_row` (top.py:313) already duplicate the ~14-key D10 row body; the session row is the rule-of-three trigger. (Reuse specialist recommended Phase-0 refactor; operator approves the plan as drafted.)
- The D10 row dict stays a frozen append-only wire contract — this phase changes *how* the dict is built, never its keys or values.

## Work
- `end_of_line/top.py` — introduce a private base builder and route both existing assemblers through it.
  - The two assemblers share these activity/identity keys: `last_command`, `command_running`, `last_write`, `last_write_seconds`, `last_text`, `last_activity_seconds`, `tokens` — plus the always-present `phase_id`, `ran_seconds`, `heartbeat_age_seconds`, `alive`, `attempts`, `lease_remaining_seconds`, `stuck`. `assemble_row` fills them from live activity; `assemble_blocked_row` sets the claim-only ones to `None`.
  - Shape (the base takes an already-reduced `activity` dict + `now`, returns the activity-derived keys; each caller `.update()`s its own discriminator/claim keys):
    ```python
    def _base_row(activity: dict, now=None) -> dict:
        return {
            "last_command": activity.get("last_command"),
            "command_running": activity.get("command_running", False),
            "last_write": activity.get("last_write"),
            "last_write_seconds": _age_seconds(activity.get("last_write_ts"), now),
            "last_text": activity.get("last_text"),
            "last_activity_seconds": _age_seconds(activity.get("last_activity_ts"), now),
            "tokens": activity.get("tokens"),
        }
    ```
  - `assemble_row` = `_base_row(activity, now)` + the claim keys (`phase_id`, `ran_seconds`, `heartbeat_age_seconds`, `alive`, `attempts`, `lease_remaining_seconds`, `stuck`).
  - `assemble_blocked_row` = `_base_row({}, now)` (empty activity → all `None`/default) + its claim-None keys + the three `blocked`/`blocker_question`/`blocked_seconds` discriminators. Confirm the empty-activity path yields byte-identical values to today's explicit `None`s (it does: `.get()` → `None`, `command_running` default `False`).
  - Keep `assemble_session_row` OUT of this phase — it lands in `discover`.

## Decisions & findings
### Decision: base helper takes a reduced `activity` dict, not a claim  *(status: active)*
- **Rationale:** the shared surface is the activity-derived keys; claim-derived keys (`alive`, `lease`, `attempts`) differ per caller and a blocked row has none. Threading the claim into the base would force an `isBlocked` branch — two functions in a trenchcoat. A plain `activity` dict (already what `extract_activity` returns) keeps the base branch-free; the session caller will pass its own `extract_activity` output unchanged.
- **Alternatives considered:** (a) base takes `(claim, activity)` and branches — rejected, reintroduces the mode flag. (b) leave the duplication, copy a third time for sessions — rejected by rule-of-three + the reuse specialist's default.
- **Evidence:** `assemble_row` top.py:284–310, `assemble_blocked_row` top.py:313–344, `extract_activity` return shape top.py:217–225 (all @7dbe001).

### Finding: splice `**_base_row(...)`, never `_base_row(); .update()`  *(from xhigh /code-review)*
- The first cut used `row = _base_row(activity, now); row.update({claim keys})` — which put the 7 activity keys FIRST, reordering the D10 wire-contract JSON (`clu serve`'s `json.dumps(rows)`). Key-addressed consumers don't break, but it violates the append-only D10 invariant and changes `/api/workers` byte order. Fix: a single dict literal with `**_base_row(activity, now)` spliced at the original mid-record position (between `alive` and `attempts`) — preserves exact key order AND drops the mutate-then-return. Verified: `assemble_row`/`assemble_blocked_row` key order is byte-identical to pre-refactor.
- Rule-of-three also fired in tests: `_now()` was the 3rd identical copy → extracted a module-level `_now()` in test_top.py. And added `test_assemble_blocked_row_activity_portion_is_base_defaults` (the blocked row's 7 deleted literals were only transitively covered).

## Failure modes to anticipate
- **Silent value drift** in `assemble_blocked_row`: if `_base_row({})` returns a subtly different default than today's literal `None` (e.g. `command_running` flips), a blocked row mis-renders. Mitigation: the existing blocked-row tests must pass unchanged; assert the empty-activity base equals the prior literal dict.
- **`now` plumbing:** `_age_seconds(None, now)` returns `None` regardless of `now` — confirm passing `now` through the base doesn't change blocked-row output (it can't; both ts are absent).
- **Import/order churn:** `_base_row` must be defined above its callers; a NameError only shows at call time in Python — a test exercising both assemblers catches it.

## Done criteria
- `_base_row` exists; `assemble_row` and `assemble_blocked_row` both build from it; no other call sites changed.
- Existing top.py / webserver row tests pass UNCHANGED (proof of zero behavior drift). A direct test asserts `assemble_blocked_row` output is identical pre/post (or asserts the exact expected dict).
- Full suite green: `python3 -m unittest discover -s tests`.
- `/code-review` run on the diff (multi-hunk single-file refactor — not trivial-exempt).
