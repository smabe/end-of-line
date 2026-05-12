# smoke-queue-a — clu-queue dogfood sentinel (1 of 3)

Trivial single-phase plan dispatched by the `clu-queue` smoke
phase. Writes a sentinel file proving the cron-driven queue
drain reached this plan, commits it with a structured message,
and calls `clu complete`.

No real code changes. After the smoke succeeds, this plan file
plus the sub-plan and the sentinel are cleaned up by the
clu-queue smoke phase's archive commit.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| touch | `smoke-queue-a-touch.md` | Write `tmp/smoke/smoke-queue-a.touched`, commit, `clu complete`. | <1m |
