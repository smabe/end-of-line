You are phase `blocker` of plan `blocker-smoke`. This is a **smoke
test** for the BLOCKED notification path of `clu watch --task-list`
([issue #42](https://github.com/smabe/end-of-line/issues/42), phase 2a).

## Locked decisions (do NOT re-litigate)

- This phase intentionally calls `clu block` instead of solving any
  real task. That's the point of the smoke.
- The question is a contrived true/false to keep the cycle short.
- After the operator answers, you record the answer in a facts file
  and call `clu complete` normally.

## Produce

### Step 1 — block

Call (the supervisor sets `$T` for you as `--token <T>`):

```bash
clu block \
  --plan blocker-smoke \
  --phase blocker \
  --token "$T" \
  --question "Is the project's primary stack Python 3.11+?" \
  --options "yes|no"
```

Then **exit immediately** — your subprocess returns, the supervisor
keeps the claim alive while waiting for the operator to answer. When
the operator answers via iMessage reply or `clu answer`, the
supervisor's next tick consumes the blocker and re-dispatches you
with the answer available via `clu prior-blocker`.

### Step 2 — record + complete

On resume, call:

```bash
clu prior-blocker --plan blocker-smoke --phase blocker --token "$T"
```

That returns the answer as JSON. Write a single-line `blocker-smoke.facts`
file in the worktree root with the answer text. Stage + commit:

```bash
git add blocker-smoke.facts
git commit -m "blocker-smoke: record operator answer (#42 phase 2a)"
```

Then call:

```bash
clu complete \
  --plan blocker-smoke \
  --phase blocker \
  --token "$T" \
  --commit-sha "$(git rev-parse HEAD)"
```

## Acceptance

- `EVENT_PHASE_BLOCKED` appended to state file with `blocker_id` +
  `question` + `options`.
- After operator answers, `EVENT_BLOCKER_CONSUMED` + re-dispatch
  with `EVENT_PHASE_STARTED` (attempt 2).
- `blocker-smoke.facts` exists with the answer.
- `EVENT_PHASE_COMPLETED` + `EVENT_PLAN_COMPLETED`.
