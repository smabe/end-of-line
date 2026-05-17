# halt-smoke

Throwaway smoke for [#42](https://github.com/smabe/end-of-line/issues/42)
phase 2b — intentional max-attempts exhaustion to validate Claude's
HALTED reaction (PushNotification) and clu's iMessage emission.
Archive + delete after HALT fires.

Companion: [`blocker-smoke.md`](blocker-smoke.md) — phase 2a for the
BLOCKED path.

## Init flags (shortened lease for fast turnaround)

```bash
clu init \
  --plan halt-smoke \
  --plan-dir plans \
  --lease-ttl-minutes 1 \
  --max-attempts-per-phase 2 \
  --worktree
```

Total wall time to HALTED: ~2 min (tick 1 dispatch → 1 min lease
expiry → tick 2 dispatch → 1 min lease expiry → MAX_ATTEMPTS fires).

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| fail | [`halt-smoke-fail.md`](halt-smoke-fail.md) | exit without callback, twice | 2m |
