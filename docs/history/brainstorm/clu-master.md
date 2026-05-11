# clu — Master Plan (Brainstorm Consolidation)

Synthesizes four expert reviews of the v0.1 scaffold (commit `1f2da6c`):
- `clu-dist-sys.md` — Distributed Systems / SRE
- `clu-red-team.md` — Adversarial / security
- `clu-notifications.md` — Channel bake-off
- `clu-end-user.md` — Day-in-the-life UX

## Context

clu v0.1 ships a correct-shaped core: cron-driven supervisor, file state with atomic writes under flock, append-only event log, cold-context workers, /simplify follow-through via spawned tasks. The 23 unit tests cover the happy paths.

What the reviews uniformly land on: **the engine is right, but the surface assumes the user is sitting at the terminal**, and the worker contract assumes good-faith callers. Both assumptions are wrong in practice. Solo-developer reality: the user is on their phone half the time, and the worker is an LLM that hallucinates.

## Verdict

> **Will it work?** Yes — the architecture holds up under crash-safety, concurrency, and projection-correctness scrutiny, modulo two architectural debts called out below. **Is it daily-usable today?** No. There's a tight set of fixes (one weekend's work) that turns it from "promising scaffold" into "the thing I described when I started building it."

Three reviewers converged on the same root cause from different angles:

| Reviewer | Symptom | Root cause |
|---|---|---|
| Dist-sys | "events are source of truth" claim doesn't hold | Status/claim/consumed mutated next to events, never derived |
| Red team | Anyone can call `clu complete` and the plan advances | Worker callbacks don't validate token (C1) or commit SHA (C2) |
| End user | "Silent-stuck" — happily running and dead look identical | No heartbeat; no halt reason in status |

All three are the same shape: **the supervisor trusts what it's told without verifying.** Fix that bias and most of the critical list collapses.

## Critical fixes (block daily use)

Numbered for tracking. Each entry: what / why / where / size.

### 1. Token validation on worker callbacks 🔒
**What:** `cmd_complete`, `cmd_block`, `cmd_spawn` all accept identity-free input. Stale workers can mark phases done after a new worker has taken the lease (red team C1/H2; dist-sys finding 8).
**Fix:** Require `--token` on all three commands; pass it through to `state.release_claim(expected_token=...)`. If token mismatches the current claim, exit non-zero. Worker dispatch template already has `{token}` — just plumb it through.
**Files:** `cli.py:205-231`, `state.py:204` (already supports `expected_token`).
**Size:** S — half a day. Also: bump `_TOKEN_LEN` from 8 to 16 hex chars while we're here (red team L1).

### 2. Quality gate in `cmd_complete` 🔒
**What:** Worker says "done with SHA X" → supervisor believes it without checking. An LLM that wrote `git commit --allow-empty -m fake` and called `clu complete` would advance the plan (red team C2).
**Fix:** In `cmd_complete`, run `git -C <project> cat-file -e <sha>` for each `--commit`. Reject unknown SHAs. Optional: also verify each commit's parent chain reaches the dispatch-time HEAD (catches `--commit` from a different branch).
**Files:** `cli.py:205`.
**Size:** S — one subprocess call + an error path.

### 3. Path-traversal guards 🔒
**What:** `--plan` flows into `state_path()` without sanitization. `--plan ../../../tmp/pwn` writes outside the project root. `phase_id` parsed from markdown has the same issue (red team C4/C5).
**Fix:** Define `_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")` once. Validate `args.plan` in `main()` before any path construction. Validate `phase_id` in `plan_parser.parse_sessions_index` before returning. Also: `state_path.resolve().relative_to(project_root.resolve())` assertion before any write.
**Files:** `cli.py:90`, `plan_parser.py:60`, `config.py:25`.
**Size:** S.

### 4. Lockfile `O_NOFOLLOW` + restrictive mode 🔒
**What:** `open(lock_path, "w")` follows symlinks. Pre-seeding the lockfile as a symlink truncates the target (red team H1; dist-sys finding 1).
**Fix:** `os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)` wrapped via `os.fdopen`.
**Files:** `state.py:90-100`.
**Size:** XS.

### 5. Schema-version check on load 🔒
**What:** `state.load()` doesn't enforce `schema_version == SCHEMA_VERSION` despite the docs claiming it halts (red team H5).
**Fix:** Assert in `load()`; raise a typed `SchemaVersionMismatch` exception that the CLI catches with a clear error.
**Files:** `state.py:114`.
**Size:** XS.

### 6. Spawned-task completion path 🔒
**What:** Workers can `clu spawn`, but no command writes `status="done"` on a spawned task. `supervisor.tick` refuses `plan_done` while any task is pending → constructive deadlock (red team H3).
**Fix:** Either (a) dispatch spawned tasks AS phases (supervisor already iterates phases; extend to spawned_tasks with `depends_on_phases` honored), or (b) add `clu task done <id>` that workers call. Recommend (a) — keeps the "one path through the supervisor" model. Also: cap spawns per phase via `max_spawns_per_phase` config (default 10).
**Files:** `supervisor.py:113-131`, `cli.py` (new `task` subcommand if (b)).
**Size:** M — half a day for (a) including a test.

### 7. Dispatch failure visibility 👁
**What:** `claude` not on cron's PATH = silent success-then-halt over 90 minutes with no signal of WHY (dist-sys 9; red team H7).
**Fix:** (a) Pre-flight `shutil.which("claude")` in `dispatch_for_tick`; on miss, append `EVENT_DISPATCH_FAILED` instead of claiming the phase. (b) Pipe worker stderr to `<plan_dir>/.orchestrator/<slug>.<token>.worker.log` instead of `DEVNULL`. (c) Record dispatched `pid` in `current_claim`; surface in `clu status`.
**Files:** `dispatch.py:43-60`, `state.py:172` (claim shape).
**Size:** S.

### 8. Worker heartbeat → "stalled" status 👁
**What:** Lease is 30 min; if the worker dies at minute 2 there's no signal until minute 30 (end-user Cliff 1).
**Fix:** Add `clu heartbeat --token T --phase X`. Worker pings it every ~2 min. Stamp `last_heartbeat_at` on the claim. `supervisor.tick` flags `stalled` if `now - last_heartbeat > 5 * interval`. `clu status` prints `last heartbeat 47s ago` or `STALLED 8 min ago`. Worker session needs the heartbeat shim wired into its `/plan` resume prompt (or a sidecar tmux pane running the loop — design choice for the worker contract doc).
**Files:** `cli.py` (new `heartbeat`), `supervisor.py`, `state.py`.
**Size:** M.

### 9. iMessage notification adapter (with reply round-trip) 📣
**What:** Currently a stderr stub. Means notifications don't reach the user when AFK = the whole orchestrator value prop is invisible (end-user Cliff 2; notifications agent verdict).
**Fix:** Wire `notify.py` to send via `osascript`/Messages.app outbound. Build `notify_inbound.py` as a `launchd` LaunchAgent that polls `~/Library/Messages/chat.db` for replies matching `^[0-9]$` and execs `clu answer`. Code already sketched in `clu-notifications.md`. Pushover at priority 2 as fallback for `halted` and stale-blocker escalations only — `$4.99` one-time.
**Files:** `notify.py` rewrite, new `notify_inbound.py`, new `examples/clu.inbound.plist`.
**Size:** M.

### 10. Fleet view: `clu` with no args 🖥
**What:** Daily usage involves N plans across M projects; `clu status --project P --plan S` is friction. Slug amnesia is real (end-user Cliff 3).
**Fix:** `clu` (no args) → walks a registry (`~/.config/clu/projects.toml` listing project roots) → one line per plan with status, current phase, blocker count, age of last activity. `clu register --project P` adds an entry.
**Files:** `cli.py`, new `registry.py`.
**Size:** S.

### 11. Quiet hours + SLA pauses during quiet 😴
**What:** A 24h SLA that ticks through the user's sleep fires at the worst moment (end-user Cliff 2). Notifications outside waking hours train the user to mute clu.
**Fix:** `.orchestrator.json` adds `"quiet_hours": ["22:00", "08:00"]` (local time). `supervisor.tick`'s stale-question SLA check skips when `now` is inside quiet hours. Outbound notifications gate by quiet-hours unless event is `EVENT_PHASE_MAX_ATTEMPTS` (halt) or Pushover priority 2 (emergency).
**Files:** `supervisor.py:55-68`, `notify.py`.
**Size:** XS.

## Notification design (concrete recommendation)

| Channel | Use | Notes |
|---|---|---|
| **iMessage (osascript outbound + chat.db polling inbound)** | Primary. Blockers, prompts, victory pings. | Zero new infra; leverages existing iMessage MCP context. Mac-on dependency is acceptable for solo. |
| **Pushover priority 1-2** | Fallback. `halted` + SLA-exceeded only. | $4.99 one-time. Emergency-retry handles "AFK + Mac asleep." |
| **`clu` no-args fleet view** | Pull, not push. The "what's happening" question. | The trust loop's bottom rung — no notifications can fully substitute. |

Anti-patterns ruled out: Discord (iOS push reliability), Telegram (extra app for marginal button-tap win over iMessage replies), Twilio SMS (doesn't actually solve "Mac off" — `clu answer` still needs the Mac), web dashboard (overbuilt), self-hosted ntfy (puts the alert escape hatch on the same Mac that hung).

## High-severity backlog (next round, post-MVP)

Not blocking daily use, but bite next month if ignored:

- **Sandbox worker dispatch.** Workers have full shell (red team C3). For solo dev this is a deliberate trade — the LLM needs git, npm, xcodebuild, etc. Document a `sandbox-exec` profile in the README as **operator guidance**: deny outbound network, deny writes outside `project_root`, deny `git push` unless explicitly approved. v0.2: add a `dispatch.sandbox: "strict" | "permissive"` config switch.
- **Events sidecar `.jsonl`.** Move `events[]` out of `state.json` into a sibling append-only `<slug>.events.jsonl` (dist-sys finding 5). Decouples projection source from cache. Real architectural change; defer until corruption actually bites or until v0.2.
- **`clu doctor`.** PATH check, dispatch dry-run, notify ping, state-dir writeable, cron-entry validity. Defer until you onboard a second project.
- **`clu retry --plan X` + `clu pause` + `clu resume`.** Recovery commands. Cheap; pair with critical-fix 8.
- **Halt reason as first-class field.** Add `halt_reason` to state on `STATUS_HALTED` transition; print first in `clu status`.
- **Pass prior-attempt commits to retried workers.** Idempotency seam (dist-sys finding 4): on attempt N>1, dispatch template gets `{prior_commits}` so the worker can inspect partial work.
- **Per-phase spawn cap.** After (6) is done, add `max_spawns_per_phase` (default 10).
- **Victory ping on `plan_completed`.** One iMessage with commit chain + simplify-cleanup count. End-user empath flagged this as cheap-and-essential dopamine.
- **`launchd` plist as primary install path.** macOS-native cron alternative; `StandardOutPath` + `StandardErrorPath` solve half the observability gap for free. Cron stays supported.

## Medium / deferred

- `fsync` parent directory after `os.replace` (dist-sys 2). Two-line POSIX-purity fix; APFS journaling mitigates.
- Pin `requires-python = ">=3.11"` explicitly in `pyproject.toml` (we already require it; just make it loud).
- `resolve_blocker_answer` should reject `idx < 0` and out-of-range explicitly (red team M2).
- `prior_phase` dead code in `state.claim_phase` (red team L2).
- `tempfile` leak on `KeyboardInterrupt` — `except BaseException` (red team M3).
- Tests for: save_atomic interrupted, two-tick race, dispatch fail, worker crash mid-phase, status drift (dist-sys finding 10).
- HMAC chain over event log (red team out-of-scope; only matters when clu is system-of-record).

## Documented downgrades (acceptable as-is)

These came up in review but the right answer is "be honest about it" not "fix":

- **Status is mutated imperatively, not derived from events** (dist-sys 8). Rather than refactor, downgrade the docs: `events` is an audit log; `status`/`current_claim` are state. They MAY drift; `clu doctor` will eventually flag mismatches.
- **Wall-clock everywhere.** Solo-Mac, NTP-synced, fine. Document.
- **fcntl on local FS only.** iCloud Drive sync / NFS unsupported. Document.
- **Worker has full user-level shell.** Solo machine, single user — risk is bounded by who can land code into a plan file. Document threat model in README.

## Open questions for the user

Before shipping the critical-fix round, confirm:

1. **Quiet hours range.** Sketch: 22:00–08:00 local. OK?
2. **iMessage destination handle.** Self-chat, own phone number, or another handle?
3. **Pushover yes/no.** If you already own Pushover, free win. Otherwise, $4.99 worth it? (Question: how often is your Mac actually asleep when a `halted` event would fire? If "never," skip Pushover.)
4. **Free-text blocker answers needed?** If yes, iMessage primary is the right call (Telegram buttons can't do free text). If always option-index, Telegram becomes viable but not better.
5. **Heartbeat frequency.** Sketch: every 2 min. Worker process burns a tick of Claude tokens each heartbeat (or uses a separate process). Trade-off worth discussing.
6. **Sandbox stance for v0.1.** Document-only ("user is responsible for what their worker LLM does"), or actually wire `sandbox-exec` into dispatch from day one?

## Test plan (additions to current 23/23)

- `test_state.test_save_atomic_interrupted` — mock `os.replace` to raise; assert tmp cleaned up.
- `test_state.test_concurrent_tick_serializes` — multiprocessing + Barrier; assert exactly one dispatches.
- `test_state.test_lockfile_symlink_rejected` — pre-seed lockfile as symlink; assert `O_NOFOLLOW` blocks.
- `test_state.test_schema_mismatch_raises` — assert v2 state file raises `SchemaVersionMismatch` on load.
- `test_cli.test_path_traversal_rejected` — `--plan "../foo"` exits non-zero.
- `test_cli.test_complete_requires_token` — `clu complete --phase X` without `--token` exits non-zero.
- `test_cli.test_complete_validates_sha` — `--commit deadbeef` (nonexistent) exits non-zero.
- `test_supervisor.test_dispatch_failure_emits_event` — `dispatch.command = "false"` → `EVENT_DISPATCH_FAILED`.
- `test_supervisor.test_stale_heartbeat_marks_stalled` — claim with old heartbeat → status shows `stalled`.
- `test_notify.test_quiet_hours_suppresses` — blocker at 02:00 with quiet=22-08 → no outbound send.

## Suggested execution order (one weekend of work)

Day 1 (security + correctness):
1. Path-traversal guards (#3)
2. Lockfile O_NOFOLLOW (#4)
3. Schema version check (#5)
4. Token validation on callbacks (#1) + token-length bump
5. SHA quality gate (#6→#2)
6. Spawned-task completion path (#6)

Day 2 (observability + UX):
7. Dispatch failure visibility (#7)
8. Worker heartbeat + stalled (#8)
9. Fleet view `clu` no-args (#10)
10. Quiet hours (#11)
11. iMessage outbound + Pushover fallback (#9 outbound half)

Day 3 (the reply loop):
12. iMessage inbound LaunchAgent (#9 inbound half)
13. Victory ping
14. `clu retry` / `clu pause` / `clu resume`
15. Halt reason in status
16. Tests for everything above

After this round: clu is daily-usable. Architectural debts (events sidecar, sandbox-exec, launchd plist) can land at v0.2 against a backlog of real usage.
