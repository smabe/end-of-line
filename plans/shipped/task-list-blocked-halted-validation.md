# task-list-blocked-halted-validation

Closes [#42](https://github.com/smabe/end-of-line/issues/42).

## Goal

Validate the BLOCKED / HALTED / SYSTEMIC_FAILURE paths of the
`--task-list` protocol that shipped with #39 but were never exercised
end-to-end. Both directions: protocol-emission correctness (regression
guard in the test suite) AND agent-side reaction correctness (does
Claude actually call PushNotification with appropriate urgency on
BLOCKED/HALTED, and stay quiet on routine TASK_UPDATEs).

## Non-goals

- **Re-litigating the msg shapes themselves.** They're locked in
  `_task_msg_for` (watch.py:216-240); we're freezing them, not
  redesigning. Any drift surfaces as a test failure.
- **Auto-firing PushNotification from clu.** PushNotification is a
  Claude-Code-side tool call. clu's contract is emit-the-protocol;
  reaction lives in `/clu-plan` SKILL.md and the model's behavior.
- **Multi-blocker / multi-halt fixtures.** One BLOCKED, one
  MAX_ATTEMPTS, one SYSTEMIC_FAILURE — enough to lock the shapes.
- **Changing the `clu block` worker-callback shape.** Existing CLI
  + state schema is unchanged.
- **Iterating on agent reaction prompt** if Option A reveals
  Claude under-reacts or over-reacts. That's a SKILL.md revision
  filed as a follow-up; this plan validates current behavior.

## Files to touch

### Phase 1 — Option B regression tests

- `tests/test_watch_task_protocol.py` — add three exact-line
  assertions covering the full TASK_UPDATE shape (task=, parent=,
  status=, msg=) for:
  - `EVENT_PHASE_BLOCKED` with `phase` + `blocker_id` +
    `question` (≤99 chars to dodge `_trunc`) →
    `msg="BLOCKED <id> — <question>"`
  - `EVENT_PHASE_MAX_ATTEMPTS` with `phase` + `attempts` →
    `msg="HALTED (max attempts on <phase>)"`
  - `EVENT_SYSTEMIC_FAILURE` with `signature` + `phase` (the
    optional phase field — present in real production usage) →
    `msg="SYSTEMIC FAILURE — <signature>"` and `task_id` carries
    the phase prefix (NOT plan-scoped).
- `tests/test_watch_task_stream.py` — add one stream-level
  integration test: append BLOCKED to a state file mid-stream
  and assert the projected line contains the expected msg shape
  end-to-end through `stream_loop`. Locks the routing through
  `project_event_task` (not just unit-test of the projector).

### Phase 2 — Option A live smoke plans

Two throwaway plans, each exercising a different halt path:

**2a. `plans/blocker-smoke.md`** — designed to **intentionally** call
`clu block` instead of `clu complete`. Operator answers via iMessage;
phase resumes and completes. Receipt commits live in the plan's
worktree; archive + delete after smoke (same pattern as
`adventure-time-smoke`).
- `plans/blocker-smoke-blocker.md` — single sub-plan that:
  1. Reads a trivial fact (e.g. "what's the project's primary
     stack?").
  2. Calls `clu block --plan blocker-smoke --phase blocker
     --token <T> --question "Is the stack Python 3.11+?"
     --options "yes|no"`.
  3. After resume (via `EVENT_BLOCKER_CONSUMED`), records the
     answer in a facts file, then `clu complete --token`.

**2b. `plans/halt-smoke.md`** — designed to **intentionally exhaust
max attempts** so the supervisor fires `EVENT_PHASE_MAX_ATTEMPTS`.
Init with fast-turnaround flags:
```bash
clu init --plan halt-smoke --lease-ttl-minutes 1 --max-attempts-per-phase 2 --worktree
```
- Total wall time to HALTED: ~2 min after first dispatch.
- `plans/halt-smoke-fail.md` — single sub-plan whose prompt
  **explicitly overrides** the global `clu-phase` SKILL.md rule
  ("never exit without callback"):

  > **This phase is a smoke test for the HALTED notification path.
  > You MUST deliberately violate the clu-phase contract: do NOT
  > call `clu complete` or `clu block`. Exit immediately without
  > any callback. The `clu-phase` SKILL.md tells you "never exit
  > without callback" — that rule is SUSPENDED for this single
  > phase. Your job is to be the deliberately-failing worker so
  > the supervisor's halt path fires.**

- Sequence: tick 1 dispatches phase → worker exits without
  callback → lease expires after 1 min → attempt counted →
  tick 2 dispatches again (attempt 2 of 2) → worker exits
  again → lease expires → attempt 2 of 2 hit → supervisor fires
  `EVENT_PHASE_MAX_ATTEMPTS` → halt iMessage + TASK_UPDATE with
  `msg="HALTED (max attempts on fail)"`.

- **Fallback if the model resists**: if the worker calls
  `clu complete` despite the prompt (clu-phase SKILL.md baking
  beats the override), swap the smoke approach: have the
  sub-plan's prompt call `exit 1` from a Bash tool before any
  callback. Subprocess-exit-1 still leaves the lease unrelease;
  same path to MAX_ATTEMPTS, more deterministic.

The smoke isn't a *code change*; the deliverable is the operator
observing the protocol fire and Claude's reaction in a real Monitor
stream. Receipts captured in the archive commit message (per smoke):
- Did Claude call PushNotification on the BLOCKED / HALTED
  TASK_UPDATE?
- With what urgency?
- Did Claude stay quiet on routine "resumed" / progress TASK_UPDATEs?

## Failure modes to anticipate

- **Test fixtures hard-code msg content** — when we tighten the
  expected msg string, a future change to `_task_msg_for` will
  break these tests. That's the *point* — the tests are the
  freeze. Don't loosen them.
- **Truncation interaction** — questions >100 chars get truncated
  with U+2026 (`…`). The new assertion must use a question that
  fits under 100 chars OR explicitly test the truncation tail.
  Lean to "fits under 100" for clarity; truncation is already
  covered in `MsgTruncationTest`.
- **Phase 2 worker dispatches to a real branch** — `blocker-smoke`
  needs a real `clu init --worktree` so its block-then-resume
  doesn't pollute main. Same isolation pattern as
  adventure-time-smoke.
- **iMessage timing** — operator may not be at terminal when
  BLOCKED fires. Smoke proceeds asynchronously: BLOCKED iMessage
  arrives, operator answers when convenient, supervisor's next
  tick consumes the blocker and resumes the phase. No hard
  deadline.
- **Agent reaction is non-deterministic** — Claude may PushNotify
  on BLOCKED in one run and stay quiet in another (model
  variance). Run smoke multiple times if first attempt is silent;
  if consistent silence, SKILL.md needs sharper instructions
  (filed as separate follow-up issue).
- **clu install-skill state** — Phase 2 requires the operator's
  global `~/.claude/skills/clu-plan/SKILL.md` to be the post-#40
  version (with the `└ ` glyph + parent= parse rules). Verify
  with `clu install-skill --only clu-plan --force` before kicking
  off the smoke; otherwise Claude reacts to a stale protocol.
- **Single-question fixture may pass on bug** — assertions on
  exact strings won't catch the case where the projector emits
  a `parent=` field for a plan-scoped event. Cross-reference
  the negative assertions in `test_paused_uses_parent_task_id`
  et al. — they already cover that side.
- **Worker contract override risk (halt-smoke)** — the global
  `clu-phase` SKILL.md trains the worker to *always* call
  `clu complete`. A "do nothing" prompt may get re-interpreted as
  "call `clu block` with an introspective question." Halt-smoke
  prompt needs explicit override language; fallback (subprocess
  `exit 1`) is documented in Phase 2b if model resists.
- **SYSTEMIC_FAILURE has no easy smoke trigger** — would need
  artificial OOM or matching `match_systemic_signature` patterns.
  Phase 2 stays BLOCKER + HALT only; SYSTEMIC msg-shape coverage
  is phase 1's job alone.

## Done criteria

### Phase 1 (Option B)
- New test methods exist asserting full-line shape for BLOCKED,
  MAX_ATTEMPTS, SYSTEMIC_FAILURE. Each asserts the full
  `TASK_UPDATE task=... parent=... status=... msg="..."` line via
  `assertEqual`, not substring.
- New stream-level test asserts end-to-end emission of BLOCKED
  through `stream_loop`.
- All assertions pass. Full suite green (739 → 743 give-or-take).

### Phase 2 (Option A)
- **2a. blocker-smoke**: plan authored, queued, dispatched. BLOCKED
  iMessage arrived; operator answered; phase resumed and completed
  and archived. Receipt commit records PushNotification observation.
- **2b. halt-smoke**: plan authored with short-lease config, queued,
  dispatched. Worker failed-to-callback twice → MAX_ATTEMPTS fired.
  HALTED iMessage arrived. Receipt commit records PushNotification
  observation (urgency should be higher than BLOCKED since HALTED
  is terminal).
- Routine TASK_UPDATE noise level documented for both smokes
  (silent / over-pinging).
- If either smoke shows under-reaction or over-reaction, follow-up
  issue filed against `/clu-plan` SKILL.md reaction logic.

### Both
- #42 closed with link to phase-1 commit + phase-2 receipts.

## Parking lot

(empty)
