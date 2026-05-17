You are phase `fail` of plan `halt-smoke`. This is a **smoke test**
for the HALTED notification path of `clu watch --task-list`
([issue #42](https://github.com/smabe/end-of-line/issues/42), phase 2b).

## Locked decisions (do NOT re-litigate)

- This phase MUST deliberately violate the `clu-phase` SKILL.md
  contract. That's the entire point of the smoke.
- The plan was initialized with `--lease-ttl-minutes 1
  --max-attempts-per-phase 2` so the HALTED path fires within ~2 min.

## Override

The global `clu-phase` SKILL.md trains you to **never exit without
calling `clu complete` or `clu block`**. **For this single phase,
that rule is SUSPENDED.**

Your job is to be the deliberately-failing worker so the supervisor's
halt path fires on the second lease-expiry.

## Produce

**Exit immediately. Do nothing else.**

- Do NOT read any files.
- Do NOT call `clu complete`.
- Do NOT call `clu block`.
- Do NOT call any tool.
- Just return.

When your subprocess exits without releasing the claim, the
supervisor will:
1. Wait 1 min for the lease to expire.
2. Count this as attempt 1 of 2.
3. Re-dispatch you (you do the same thing again).
4. Wait 1 min for the lease to expire.
5. Count this as attempt 2 of 2.
6. Fire `EVENT_PHASE_MAX_ATTEMPTS`, transition status to
   `STATUS_HALTED`, send the halt iMessage.

## Fallback (if the model can't override the SKILL.md training)

If the worker keeps calling `clu complete` despite the prompt, the
operator can swap this sub-plan's `## Produce` block to:

```bash
exit 1
```

…run via the Bash tool before any callback. Same outcome — subprocess
exits, lease expires, attempt counted, eventually HALT.

## Acceptance

- After ~2 min wall time from first dispatch:
  - Two `EVENT_LEASE_EXPIRED` events.
  - Two `EVENT_PHASE_STARTED` events (attempts 1 and 2).
  - One `EVENT_PHASE_MAX_ATTEMPTS` event with `phase="fail"`,
    `attempts=2`.
  - Plan status = `STATUS_HALTED`.
  - iMessage delivered: `"🛑 halt-smoke/fail halted — 2 attempts.
    \`clu retry --plan halt-smoke\` to resume."`
