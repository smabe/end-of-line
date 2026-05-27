# wedge-watchdogs — close the heartbeat-pipe + worker-idle gaps from the 2026-05-26 wedge

A 49-min worker wedge slipped past every existing watchdog. Forensic snapshot: worker PID alive at 0.4% CPU, heartbeat-loop subshell alive but `clu heartbeat` silently failing (loop's `>/dev/null 2>&1` swallowed exits), zero TCP connections to api.anthropic.com, no active Bash tool window (so `_emit_stuck_tool` returned early), lease not yet expired (so `_emit_stalled_claim_notify` and `_detect_stalled` both stayed quiet). No notification fired.

Each existing watchdog (worker-pid-liveness #72, worker-watchdog #67, heartbeat-threshold-scales-with-lease) has a precondition the wedge violates. This plan adds two complementary halves that each catch the same wedge independently — defense in depth, not single-point detection. Sister tuning (cap stalled threshold ceiling at ~25 min) ships in-session separately; sister diagnostics (line-buffered worker output) is a separate single-phase plan.

## Locked design decisions

### Phase 1 — heartbeat-self-report
- **Loop tracks consecutive non-zero `clu heartbeat` exits** and tees stderr to `logs/heartbeat-errors.<plan>.<phase>.log` (same convention as `logs/attempt-context.*`). A success resets the counter; on the 3rd consecutive failure (~6min at 120s interval) the loop invokes `clu notify-heartbeat-failure ... --token <T>`.
- **New CLI subcommand `clu notify-heartbeat-failure`** — token-validated against current claim; idempotent via `heartbeat_loop_failing_notified=True` flag on the claim (mirrors `stalled_notified`); appends `EVENT_HEARTBEAT_LOOP_FAILING`; writes inbox event; fires side notify.
- **Why 3 strikes:** transient state.json `LockTimeout` is sub-second; 6 min of consecutive failures is structural (claim drift, schema bump, broken token), not contention.
- **Why worker-side, not supervisor-polling-a-sidecar:** worker owns its own loop health. Supervisor stays single-tick + first-match-wins.

### Phase 2 — worker-idle-detect
- **New gap-fill `_emit_worker_idle`** in `supervisor.py` side-effect band (line 477 area), running after `_emit_stuck_tool`. Fires once per claim when ALL hold: PID alive, no `active_tool_started_at`, worker process CPU ≤1% averaged over ≥10-min span with ≥5 samples.
- **CPU sampling:** `ps -p $PID -o %cpu=` per tick; supervisor stores samples in `current_claim.cpu_samples` (list of `{ts, cpu}` dicts, trimmed to last 20).
- **API-socket suppression (heuristic):** `lsof -p $PID -i 2>/dev/null` with 1s timeout; skip emission if `anthropic` matches. Open Anthropic socket = mid-API-call, not idle. On lsof timeout/error, emit anyway (false negative > false positive). Mirrors the operator's manual diagnosis.
- **Idempotency flag:** `worker_idle_notified=True` on the claim. Cleared on claim release so a re-claim of the same phase can re-fire.
- **New constants:** `EVENT_WORKER_IDLE` in `state.py`, `KIND_WORKER_IDLE` in `notify.py`.

## Non-goals

- **No auto-kill, no auto-`force-complete`.** Detection-only, mirroring `_emit_stuck_tool`. The operator-approval checkpoint from user-CLAUDE.md applies.
- **No retry inside `clu notify-heartbeat-failure`.** If state.json is locked at the moment the worker calls it, the operator just misses THIS heartbeat-failure notify — the next loop iteration handles the retry. Cleaner than blocking the bash loop on contention.
- **No replacement of `_emit_stuck_tool` or `_detect_stalled`.** Both stay untouched. The new gap-fills are additive.
- **No fix for the underlying wedge cause** (Claude Code internal state machine hanging on a dropped API stream). That's upstream from clu. This plan makes the symptom self-reporting.
- **Not the line-buffering fix.** That's a separate plan so post-mortem logs survive next time.

## Files touched

- `end_of_line/skills/clu-phase/SKILL.md` — P1 — heartbeat-loop snippet rewrite. **No API surface; install content.**
- `end_of_line/state.py` — P1, P2 — `EVENT_HEARTBEAT_LOOP_FAILING` (P1), `EVENT_WORKER_IDLE` (P2), `mark_heartbeat_loop_failing_notified`, `mark_worker_idle_emitted`, `append_cpu_sample`, `worker_idle_window_satisfied` helpers. **API hotspot: module-level `EVENT_*` additions — concurrent plans touching state.py constants serialize.**
- `end_of_line/supervisor.py` — P2 — `_emit_worker_idle` added as 4th entry in side-effect band (line 477 area). **API hotspot: tick side-effect band ordering.**
- `end_of_line/cli.py` — P1 — `cmd_notify_heartbeat_failure` + argparse subparser + doctor surface lines for both events.
- `end_of_line/watch.py` — P1, P2 — renderer + `--operator` filter pass-through for both events (per #70 wedge-event pattern).
- `end_of_line/notify.py` — P1, P2 — `KIND_HEARTBEAT_LOOP_FAILING`, `KIND_WORKER_IDLE`, `render_heartbeat_loop_failing`, `render_worker_idle`.
- `tests/test_supervisor.py` — P2 — `_emit_worker_idle` happy path + idempotency + API-socket suppression + CPU-sample threshold + active-tool suppression.
- `tests/test_notify_heartbeat_failure.py` (new) — P1 — `cmd_notify_heartbeat_failure` token validation + idempotency + inbox + state event.
- `tests/test_watch.py` — P1, P2 — both events render through the renderer + appear under `--operator`.
- `tests/test_state.py` — P2 — `append_cpu_sample` + `worker_idle_window_satisfied` unit tests.

## Per-phase done checklist

- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines (both phases qualify).
- Full suite green: `python3 -m unittest discover -s tests` — report count delta.
- Structured commit format (Title / Why / What's new / Under the hood / Tests / `Co-Authored-By:` trailer); stage explicit paths.
- **Post-commit attestations:** `clu verify --plan wedge-watchdogs --phase <id> --token <T>` then `clu attest --simplify --plan wedge-watchdogs --phase <id> --token <T>`.
- Call `clu complete --plan wedge-watchdogs --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| heartbeat-self-report | `wedge-watchdogs-heartbeat-self-report.md` | Loop counts consecutive failures + new `clu notify-heartbeat-failure` CLI + state/notify/watch wiring | 1.5h |
| worker-idle-detect | `wedge-watchdogs-worker-idle-detect.md` | `_emit_worker_idle` gap-fill + CPU sampling + API-socket suppression + state/notify/watch wiring | 2h |
