# p2 — Operator-stop blocker type

Give an operator-directed stop a distinct, durable record so it doesn't
masquerade as an ordinary block, and so the console has a state-based ack that
its "stop now" was honored. clu-Python is still not in the message path — this is
the callback the worker itself invokes when it acts on a stop message (p3 wires
the worker to use it).

## Locked decisions

- **New blocker TYPE, not a schema change.** `add_blocker` already carries
  `blocker_type` (`state.py:1082-1089`, default `BLOCKER_INPUT`). Add
  `BLOCKER_OPERATOR_STOP = "blocked_operator_stop"` and thread it — no new field
  on the blocker record, no `schema_version` bump. **Value convention:** existing
  types are `blocked_`-prefixed (`BLOCKER_INPUT = "blocked_input"` at
  `state.py:272`, `blocked_replan`), so the new value is `blocked_operator_stop`
  to match — NOT a bare `operator_stop`.
- **Why a type and not free text.** The change-impact agent flagged that
  `EVENT_PHASE_BLOCKED` records no cause, so an operator-stop is
  indistinguishable from a design-question block in state and in the
  prior-attempt sidecar. A typed marker is checkable; pattern-matching the
  question string is not.
- **Stop = block, not complete.** `clu complete` with no commits enforces the
  verify/simplify gates (`cli.py:4736-4823`) — wrong for a mid-work stop. `block`
  bypasses gates and pauses the plan for the operator's redirect (delivered as
  the blocker answer). This is the user-facing "stop" semantic surfaced at
  approval.

## Work

- `end_of_line/state.py`: add `BLOCKER_OPERATOR_STOP` constant beside the
  existing `BLOCKER_*` constants; accept it as a valid `blocker_type` in
  `add_blocker`. Verify no validation elsewhere rejects an unknown type
  (grep `BLOCKER_INPUT` usage).
- `end_of_line/cli.py`: `clu block` gains `--operator-stop` (a flag that sets
  `blocker_type=BLOCKER_OPERATOR_STOP`; mutually consistent with the existing
  `--question`/options args). Thread through `cmd_block` (`cli.py:6069`) into
  `add_blocker`.
- `end_of_line/cli.py`: `clu blockers show` (`cmd_blockers_show`, `cli.py:6226`)
  renders the type so an operator-stop is visible; if the prior-attempt sidecar
  (`_last_termination_reason` at `dispatch.py:498`, written via
  `_maybe_write_attempt_context` at `dispatch.py:580`) enumerates termination
  reasons, add the operator-stop case there so a resuming worker sees "prior
  attempt was stopped by operator", not a bare block.
- Tests (TDD, AAA, factory helpers): `clu block --operator-stop` records
  `type == "blocked_operator_stop"` in the blocker and leaves the answer/resume path
  identical to a normal block; `blockers show` displays the type; sidecar (if
  touched) names the operator-stop cause. Use `tests.isolate_registry` in setUp.
- Consumes: `none` (state/CLI only).
- Produces: `clu block --operator-stop` CLI surface + `BLOCKER_OPERATOR_STOP`
  state value. Consumed by p3 (worker invokes it) and p4/p5 (console reads it as
  the ack; docs describe it).

## Done criteria

- **Observable:** run `clu block --operator-stop --question "..."` against a test
  plan; inspect the state file — the blocker's `type` is `blocked_operator_stop`;
  `clu blockers show <id>` prints it. A normal `clu block` still records
  `blocked_input`. (State inspection, not just green tests.)
- The answer→resume path (`process_answered_blockers`, `state_blocker.py:39-55`)
  is byte-for-byte unchanged for both types — an operator-stop block resumes
  exactly like any block once answered. Prove with a test.
- Full `unittest` suite green; `clu verify` green.

## Decisions & findings
<!-- sealed at phase commit -->
_pending._
