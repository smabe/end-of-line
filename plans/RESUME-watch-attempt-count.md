# Resume — watch-attempt-count (briefed 2026-08-20, after sqlite-migration)

```
sqlite-migration is shipped — commits e95dd60..4f01efa on main, pushed 2026-08-20.
Cleanup done: plan + 8 shards archived to plans/shipped/, memory rewritten as a
shipped record, follow-ups filed as #109-#114.

clu's storage is now two SQLite databases. No code path opens a state file or
takes a flock. The tick holds nothing while it thinks and applies its decision in
one transaction guarded by per-decision preconditions. 2333 tests green.

Next steps available (pick one or propose your own):
- #109: clu watch reports every attempt as "attempt 1" — the phase_started event
  never carried the count; the formatter falls back to 1. Frozen during the
  migration so p1's golden stayed a valid baseline; that constraint has expired.
- #110: worker-death dedup marker has no reader — one death pings you twice.
  Needs a design call: tick-side suppression vs dropping the daemon's report.
- #111: the inbox hook says "run `clu inbox`" and no such command exists.
  Scope call: reword the footer, or add the command.
- #112: two divergent attempt counters — clu top shows the raw count while
  dispatch acts on the quota-forgiven one. Needs your call on which to display.
- #113: evaluate synchronous=NORMAL under WAL. Measurement task; FULL shipped
  for parity and was never benchmarked.
- #114: docs/_outline.md's LOC column is stale by ~13x. Probably just delete it.

Recommended next pickup: #109. Smallest of the six, no open decision, and it
retires two now-obsolete Done criteria left in the shipped p4/p6 shards. The fix
is putting `attempts` on the event where the claim is minted (plan_store's claim
op and apply_tick_delta) and regenerating tests/goldens/watch-demo.txt in the
same commit — that golden change is intended now, not a regression, and the
commit message should say so.

Read first if continuing from this work:
- ~/.claude/projects/-Users-smabe-projects-end-of-line/memory/project_sqlite_migration.md
  (four things not to rediscover — including that st.utcnow() is second-resolution,
  which made a headline test unable to fail)
- gh issue view 109
- plans/shipped/sqlite-migration-p1.md — the Decisions entry explaining why the
  bug was frozen, and what its expiry depends on

Also open, unrelated to this work:
- plans/skill-drift-trigger.md — all four phases SHIPPED and the master says
  complete, but it was never archived. Run `/plan ship skill-drift-trigger`.
- plans/live-worker-channel.md — APPROVED 2026-08-10, NEXT p1, never started.
- plans/RESUME-verify-gate-93.md — still valid; #93 and #95 both open.

Open questions or blockers: none. The registry is empty after the cutover, so
your next `clu init` also re-syncs the three bundled skills clu doctor currently
flags as stale.
```
