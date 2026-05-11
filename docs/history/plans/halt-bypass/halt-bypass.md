# halt-bypass — Day 3 sub-plan #3

Decide whether halt notifications should bypass quiet hours. Currently
they're gated (Day 2.9), which means a 3am halt sits silent until 8am.
For a real cron-driven fleet that might be the right call (you can't act
on it anyway) but it might also mean a worker burns the night thrashing.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| Pick policy (blocker) | `halt-bypass-design-block.md` | Add KIND_HALTED to QUIET_HOURS_BYPASS_KINDS? | 15m |
| Implement | `halt-bypass-impl.md` | Implement whichever path the blocker selects | 15m |
