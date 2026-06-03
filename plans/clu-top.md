# clu top — live worker activity view

## Goal
A read-only `top`-like terminal command the operator runs in a side window
to watch what every active clu worker is doing right now: phase start time
(elapsed since dispatch), current/last Bash command, last file written, last
activity time, last assistant output, plus heartbeat age and PID liveness.
Sources are harness- and OS-written (the worker LLM can't forge them), giving
an independent check that workers are actually producing work.

## Non-goals
- **No following sidechain/subagent transcripts in v1** — `clu top` follows
  each worker's MAIN session file only. *Why this exclusion is safe:* the main
  transcript still advances when a worker spawns subagents (the `Agent`
  tool_use and its result land in the main file), so liveness/progress
  detection never depends on reading sidechains — only the fine-grained "which
  bash command inside a subagent" is lost. Following N sidechain files is the
  first parking-lot enhancement, not a correctness gap.
- No interactivity beyond `q` to quit — no scrolling, filtering, or drill-down.
- No killing / releasing / force-completing workers from the view. Read-only;
  destructive actions stay manual per the operator-approval rule.
- No token-cost aggregation dashboard (coolant's lane) — last-turn token count
  is at most one column, not a roll-up.
- No commit-validation cross-check (verifying `clu complete --commits` against
  real git history) — that's a separate trust upgrade; parking-lot note only.
- No change to notifications / quiet-hours / worker sandbox (locked config).

## Files to touch
- **`end_of_line/top.py`** (new) — the module. Pure-stdlib. Contains:
  transcript locator (forward-encode cwd → glob `~/.claude/projects/<enc>/*.jsonl`
  → keep files whose in-file `cwd` matches and `isSidechain` is not true →
  newest mtime); seek-from-end tail reader (partial-final-line safe); defensive
  JSONL field extractor (last Bash command, last Edit/Write path+ts, last
  assistant text, last entry ts, last `message.usage`); per-worker row assembly
  joining transcript fields with state — including elapsed-since-`started_at`
  (`current_claim["started_at"]`, `state.py:709`) rendered as a START/RAN
  column (e.g. "running 12m"), absolute start time available; curses renderer +
  plain-text `--once` fallback. Structurally parallel to `watch.py`; shared enumeration already
  lives in `registry.entries()` / `cfg.state_path()`.
- **`end_of_line/cli.py`** — `p_top = sub.add_parser("top", ...)` near `:1168`;
  dispatcher `if args.cmd == "top": return cmd_top(args)` near `:1295`;
  `def cmd_top(args) -> int:` host-level handler after `cmd_watch` (~`:3900`).
  Flags: `--project` (scope to one), `--once` (snapshot + exit, non-curses),
  `--interval` (default ~1.5s).
- **`end_of_line/dispatch.py`** *(phase 3)* — generate a UUID per dispatch; add
  `session_id` to the `cmd_tmpl.format(...)` placeholders at `:170`; stamp
  `claim["session_id"]` in `_stamp_pid` (`:267-279`).
- **`examples/HealthData.orchestrator.json`** *(phase 3)* — add
  `--session-id {session_id}` to the example `command` (opt-in placeholder).
- **`docs/operations.md`** *(phase 3)* — document `clu top` + the `{session_id}`
  placeholder and what it buys (deterministic transcript lookup).
- **`tests/test_top.py`** (new) — locator (cwd-collision decoy + sidechain
  fixtures), tail reader (truncated final line), extractor (string-vs-array
  `content`, unknown `type`), row assembly, `--once` plain output, dead-PID flag.
- **`tests/test_dispatch.py`** *(phase 3)* — `{session_id}` placeholder
  substitution + `claim["session_id"]` stamping.

## Failure modes to anticipate
- **Lossy, colliding cwd encoding** — non-`/` chars also map to `-`, and the
  transform is non-reversible (CC issue #19972), so two projects can encode to
  the same dir. Mitigation: never reverse-map; confirm each candidate by
  reading the in-file `cwd` field. This is the load-bearing correctness check,
  tested with a decoy fixture.
- **Sidechain transcripts** — `/plan`/`/clu-phase` workers spawn subagents whose
  Bash/Edit activity is in separate `agent-*.jsonl` / `isSidechain:true` files.
  v1 follows the main session (see Non-goals); filter sidechains in the locator
  so the view doesn't accidentally latch onto one.
- **Partial final line** — the worker may be mid-append; the last JSON line is
  truncated. `try/json.loads/except JSONDecodeError: continue`, re-read next poll.
- **JSONL schema drift across CC versions** — `type` values grow without notice
  and `message.content` may be a string OR an array (CC issue #53516). Parse
  defensively: switch on `type`, ignore unknowns, normalize content shape.
- **Stale transcript read as false "idle"** — a dead worker stops appending, so
  "last activity" silently ages. Cross with `claim_worker_alive` (PID probe) so
  a dead worker is flagged, never shown as quietly working.
- **No transcript yet / cwd-match fails** — worker just launched, or operator
  hasn't adopted `{session_id}`. Render the row from state-only data (phase,
  heartbeat) with `—` for transcript fields; never crash or drop the worker.
- **Non-tty / piped stdout** — `curses.initscr()` raises when stdout isn't a
  tty. Guard with `sys.stdout.isatty()`; route to the `--once` plain path
  (also what tests exercise).
- **Narrow terminal** — `addstr` past the edge raises `curses.error`. Clamp
  every field to width; wrap writes in try/except.

## Done criteria
- `clu top` runs in a terminal, refreshes ~1.5s, shows one row per active claim
  across all registered plans: plan/phase · start (elapsed since `started_at`) ·
  last-activity age · heartbeat age · PID-alive · current/last command · last
  file write · last assistant line.
- Transcript locator forward-encodes the worktree (or project) cwd, confirms via
  in-file `cwd`, filters sidechains, picks newest — unit-tested against a
  fixture dir with a matching main file, a sidechain file, and a colliding-cwd
  decoy.
- Tail reader survives a truncated final line; extractor survives unknown `type`
  and string-vs-array `content` — unit-tested.
- `--once` emits a plain-text snapshot (pipeable, test-friendly); curses path is
  `isatty`-guarded.
- A worker whose PID is gone is visibly flagged, not shown as idle.
- *(Phase 3)* dispatch passes `--session-id {session_id}` when the operator adds
  the placeholder, stamps `claim["session_id"]`; `clu top` uses it for an exact
  filename when present, falls back to cwd-confirmation otherwise. Example
  config + docs updated.
- Full `unittest` suite green; report the count (current baseline 1490).

## Parking lot
(empty at start)
