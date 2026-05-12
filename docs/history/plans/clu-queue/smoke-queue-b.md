# smoke-queue-b — clu-queue dogfood sentinel (2 of 3)

Trivial single-phase plan dispatched by the `clu-queue` smoke
phase. Mirror of `smoke-queue-a` — writes a sentinel proving the
queue advanced past plan A to plan B.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| touch | `smoke-queue-b-touch.md` | Write `tmp/smoke/smoke-queue-b.touched`, commit, `clu complete`. | <1m |
