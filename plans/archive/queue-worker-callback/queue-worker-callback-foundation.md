# queue-worker-callback-foundation — exit code, events, schema, config

You are phase `foundation` of `queue-worker-callback`. Lay the
type-and-constant groundwork: new `ExitCode.QUEUE_CAP`, two new
`EVENT_*` constants, four new queue-entry fields, one new config
default. Pure additive — no behavior changes, no CLI changes.

## Locked decisions (do NOT re-litigate)

See `plans/queue-worker-callback.md` § Phase 1. Summary:
- `QUEUE_CAP = 11` (verified next free in `ExitCode` IntEnum).
- `EVENT_QUEUE_APPENDED = "queue_appended"`,
  `EVENT_QUEUE_REJECTED = "queue_rejected"`.
- Queue entry gains nullable `source_plan` / `source_phase` /
  `source_token_fp` / `reason`. Operator path leaves them `None`.
- `DEFAULT_MAX_QUEUE_ADDS_PER_PHASE = 3` in `state.py`; new key in
  `empty_state()` config block.
- Cap-count derivation (no new state field) — phase `gates` owns
  the derivation logic; this phase just defines the constants.

## Read first

- `end_of_line/cli.py:153-171` — `ExitCode` IntEnum (add member here).
- `end_of_line/state.py:78-135` — `EVENT_*` constant block (add the
  two new ones here, near `EVENT_QUEUE_POPPED` around line 100).
- `end_of_line/state.py:52-53,163-181` — `DEFAULT_MAX_*` constants and
  `empty_state()` config block (mirror `max_spawns_per_phase`).
- `end_of_line/cli.py:1785-1793` — current operator-side entry shape
  in `cmd_queue_add` (we extend, not replace).
- `tests/test_queue_primitive.py` — round-trip pattern for queue
  entries.

## Produce

1. **Failing tests first** (`tests/test_queue_worker_schema.py`,
   new file):
   - `test_exit_code_queue_cap_value` — assert
     `ExitCode.QUEUE_CAP == 11` and that no other ExitCode member
     shares the value.
   - `test_event_constants_present` — assert
     `state.EVENT_QUEUE_APPENDED == "queue_appended"` and
     `state.EVENT_QUEUE_REJECTED == "queue_rejected"`.
   - `test_empty_state_includes_queue_adds_cap` — assert
     `empty_state(...)["config"]["max_queue_adds_per_phase"] == 3`
     (the default).
   - `test_default_constant_exposed` — assert
     `state.DEFAULT_MAX_QUEUE_ADDS_PER_PHASE == 3`.
   - `test_queue_entry_extra_fields_roundtrip` — build an entry dict
     with all four lineage fields populated, call
     `queue.save_atomic` + `queue.load`, assert the loaded entry
     preserves all four fields verbatim.
   - `test_operator_entry_fields_none_after_load` — load a v1-shaped
     entry (only `slug`, `added_at`, `added_by`, `position_at_add`)
     and assert `.get("source_plan") is None` etc. (forward-compat:
     old entries must still load).

2. **Implementation.**
   - `end_of_line/cli.py:171` — add `QUEUE_CAP = 11` after
     `WORKTREE_SETUP_FAILED = 10`.
   - `end_of_line/state.py` (in the EVENT_* block, near
     `EVENT_QUEUE_POPPED`) — add `EVENT_QUEUE_APPENDED` and
     `EVENT_QUEUE_REJECTED`.
   - `end_of_line/state.py:53` (after `DEFAULT_MAX_SPAWNS_PER_PHASE`) —
     add `DEFAULT_MAX_QUEUE_ADDS_PER_PHASE = 3`.
   - `end_of_line/state.py:176` (in `empty_state()` config) — add
     `"max_queue_adds_per_phase": DEFAULT_MAX_QUEUE_ADDS_PER_PHASE,`.
   - `end_of_line/queue.py` — no code change. Schema is implicit
     (dict shape, not a dataclass); v1's `SCHEMA_VERSION = 1` stays.
     Forward-compat is free because new fields are nullable.

3. **Acceptance.**
   - 6 new tests green.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - `grep -n "EVENT_QUEUE_APPENDED\|EVENT_QUEUE_REJECTED\|QUEUE_CAP\|DEFAULT_MAX_QUEUE_ADDS" end_of_line/`
     shows the four new constants.

4. **Commit + complete.**
   - Title: `queue-worker-callback: phase foundation — constants + schema fields (#17)`
   - Stage: `end_of_line/cli.py`, `end_of_line/state.py`,
     `tests/test_queue_worker_schema.py`.
   - `clu complete --plan queue-worker-callback --phase foundation --token <T>`

## Failure modes to watch

- **Schema-version bump temptation** — DON'T bump
  `queue.SCHEMA_VERSION`. New fields are nullable; existing
  `queue.json` files load unchanged. A version bump would force a
  migration nobody needs.
- **Field naming conflict** — v1 already has `added_by` (not
  `enqueued_by`). Use `added_by`, not a new field; design doc's
  `enqueued_by` is a naming variant. Worker entries set
  `added_by: "worker"`.
