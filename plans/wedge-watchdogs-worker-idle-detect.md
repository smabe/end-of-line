# wedge-watchdogs-worker-idle-detect — supervisor PID-alive-but-idle detection

You are phase `worker-idle-detect` of the `wedge-watchdogs` plan. Add a 4th gap-fill `_emit_worker_idle` to the supervisor's side-effect band. Fires when worker PID is alive but the process is doing nothing — no active Bash tool, ≤1% CPU for ≥10min, no open Anthropic API socket. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/wedge-watchdogs.md`. Summary:

- Detection only, no auto-kill. Operator-approval mandate from user-CLAUDE.md applies — never auto-`clu force-complete` or `clu release-claim`.
- 10-min low-CPU window with ≥5 samples, each ≤1.0%. Samples cached in `current_claim.cpu_samples` (list of `{ts, cpu}` dicts, trimmed to last 20 via `WORKER_IDLE_SAMPLE_CAP`).
- API-socket heuristic suppression: `lsof -p $PID -i 2>/dev/null` with 1s timeout, skip if `anthropic` substring matches. On lsof error/timeout, emit anyway.
- Idempotent via `worker_idle_notified=True` claim flag. Cleared on claim release so a re-claim of the same phase can re-fire.
- New event `EVENT_WORKER_IDLE` in state.py; new kind `KIND_WORKER_IDLE` in notify.py.

## Read first

- `end_of_line/supervisor.py:358-446` — `_emit_stuck_tool` (the canonical pattern to mirror: cap detection, idempotency stamp, side-notify list, inbox write, `ps_output` test seam).
- `end_of_line/supervisor.py:474-479` — side-effect band where the new gap-fill wires in (after `_emit_stuck_tool`).
- `end_of_line/state.py:259` — `claim_worker_alive` (returns bool; `cmdline_match` arg is optional).
- `end_of_line/state.py:761-775` — `tool_stuck_already_emitted` + `mark_tool_stuck_emitted` (model `worker_idle_already_emitted` / `mark_worker_idle_emitted` exactly on these).
- `end_of_line/state.py:163, 231` — `EVENT_*` constants location.
- `end_of_line/watch.py:63, 78` — `EVENT_STALLED_CLAIM_NOTIFIED` operator-filter membership (model `EVENT_WORKER_IDLE` the same way).
- `tests/test_supervisor.py` — `_emit_stuck_tool` tests (copy the `ps_output` test-seam pattern; add `lsof_output` analog).
- Project CLAUDE.md `Conventions (mandatory)` — `EVENT_*` constants not raw strings, `with st.mutate(...) as data:`, supervisor one-action-per-tick rule (gap-fills are side effects, not actions).

## Produce

1. **Failing tests first.** Add to `tests/test_supervisor.py`:
   - `test_emit_worker_idle_fires_after_threshold_window` — 6 samples spanning 12min, all 0.5% CPU, no `active_tool_started_at`, no anthropic socket → event + inbox + side-notify.
   - `test_emit_worker_idle_idempotent_within_same_claim` — fire twice, get one event.
   - `test_emit_worker_idle_suppressed_when_anthropic_socket_open` — inject `lsof_output="... TCP ->api.anthropic.com:443 ..."` test seam → no event.
   - `test_emit_worker_idle_suppressed_when_active_tool_present` — set `claim["active_tool_started_at"]` → return early.
   - `test_emit_worker_idle_suppressed_when_high_cpu` — one sample at 30% in the window → not satisfied → no event.
   - `test_emit_worker_idle_too_few_samples` — 3 samples spanning 8min, all 0% → not satisfied → no event.
   - `test_worker_idle_notified_cleared_on_release` — fire, release claim, re-claim same phase, fire again succeeds.
   - Plus unit tests in `tests/test_state.py` for `append_cpu_sample` (trim-to-`WORKER_IDLE_SAMPLE_CAP` behavior) and `worker_idle_window_satisfied` (sample-count, span, threshold boundaries).

2. **Implementation.**
   - `end_of_line/state.py`:
     - `EVENT_WORKER_IDLE = "worker_idle"` next to existing `EVENT_*` constants.
     - `WORKER_IDLE_SAMPLE_CAP = 20` constant.
     - `worker_idle_already_emitted(claim) -> bool` — read `claim.get("worker_idle_notified", False)`.
     - `mark_worker_idle_emitted(claim, now) -> None` — stamp `worker_idle_notified=True`; record `worker_idle_notified_at=now.isoformat()` for surfaces.
     - `append_cpu_sample(claim, cpu_pct, now) -> None` — append `{"ts": now.isoformat(), "cpu": cpu_pct}` to `claim.setdefault("cpu_samples", [])`; trim to last `WORKER_IDLE_SAMPLE_CAP`.
     - `worker_idle_window_satisfied(claim, now, *, min_samples=5, window_min=10, cpu_threshold=1.0) -> bool` — check sample list has ≥min_samples, oldest sample within window covers ≥window_min minutes, every sample ≤cpu_threshold.
     - Ensure `release_claim_and_emit` (and its callees) clear `worker_idle_notified`, `worker_idle_notified_at`, AND `cpu_samples`. Grep for `stalled_notified` cleanup site — apply the same treatment.
   - `end_of_line/supervisor.py`:
     - New `_emit_worker_idle(data, config, side_notifies, *, ps_output=None, lsof_output=None)`:
       - Return if `current_claim` missing OR `active_tool_started_at` set OR PID missing.
       - Sample CPU via `ps -p $PID -o %cpu=` (use `ps_output` seam to inject in tests); parse the float; `append_cpu_sample(claim, cpu, now)`.
       - If `not worker_idle_window_satisfied(claim, now)`: return.
       - If `worker_idle_already_emitted(claim)`: return.
       - lsof check: `subprocess.run(["lsof", "-p", str(pid), "-i"], capture_output=True, timeout=1)`; if `"anthropic"` in stdout.decode(): return. On `TimeoutExpired` / `CalledProcessError` / `OSError`: emit anyway (don't suppress).
       - `mark_worker_idle_emitted(claim, now)`, `st.append_event(data, EVENT_WORKER_IDLE, phase=..., pid=..., low_cpu_minutes=...)`, `inbox.write_event(type="worker_idle", ...)`, `side_notifies.append((notify.KIND_WORKER_IDLE, notify.render_worker_idle(plan_slug, phase_id, pid, low_cpu_min)))`.
     - Wire into `tick` side-effect band: after line 479's `_emit_stuck_tool(data, config)` call, add `_emit_worker_idle(data, config, side_notifies)`.
   - `end_of_line/notify.py`: `KIND_WORKER_IDLE = "worker_idle"` + `render_worker_idle(plan_slug, phase_id, pid, low_cpu_minutes) -> str`. Body should name the diagnostic check (no Bash, no API socket, low CPU for N min) so the operator can act.
   - `end_of_line/watch.py`: renderer entry for `EVENT_WORKER_IDLE`; add to the `--operator` event set (the wedge-event list alongside `EVENT_STALLED_CLAIM_NOTIFIED`, `EVENT_TOOL_STUCK`, `EVENT_ATTESTATION_REFUSED`, `EVENT_PHASE_BLOCKED`).
   - `end_of_line/cli.py`: doctor surface — if recent `EVENT_WORKER_IDLE` event in the log, print a one-line summary (timestamp + phase + pid).

3. **Acceptance.**
   - All new tests green (7 supervisor + 2-3 state).
   - Full suite green; report pre/post count delta.
   - `clu watch --all --operator` includes `worker_idle` events (confirm via grep against watch.py's operator-filter set).
   - `clu doctor` (run locally if a real state.json with a recent claim exists) doesn't crash on the new event.
   - Manual smoke (optional, requires real worker): `kill -STOP <worker-pid>`; wait ~12min while ticks run; verify event + inbox + notify. Then `kill -CONT <worker-pid>` to resume.

4. **Commit + attest + complete.**
   - Title: `wedge-watchdogs: phase worker-idle-detect — supervisor catches alive-but-idle workers`
   - Stage: `end_of_line/state.py`, `end_of_line/supervisor.py`, `end_of_line/notify.py`, `end_of_line/watch.py`, `end_of_line/cli.py`, `tests/test_supervisor.py`, `tests/test_state.py`.
   - `/code-review` after staging — diff >1 file qualifies.
   - **Post-commit** (HEAD must be the SHA being attested):
     - `clu verify --plan wedge-watchdogs --phase worker-idle-detect --token <T>`
     - `clu attest --simplify --plan wedge-watchdogs --phase worker-idle-detect --token <T>`
   - `clu complete --plan wedge-watchdogs --phase worker-idle-detect --token <T>`.

## Failure modes to watch

- **CPU sampling under cron tick cadence.** Default tick is 30s (per #52 ship). 10-min window collects ~20 samples max. Gate is `≥5 samples AND ≥10min span`, not `=N samples` — under slow/skipped ticks fewer samples still gate correctly.
- **lsof on macOS is slow.** `lsof -i` on busy systems hits 100ms-1s. The 1s timeout is the budget; on timeout/error, **emit anyway** (false negative > false positive for this watchdog). Don't reduce the timeout below 1s.
- **`active_tool_started_at` staleness handling.** If a Bash tool started but never had `--end-bash` stamped (tool crashed), the stamp persists. For worker-idle, treat ANY non-empty stamp as "tool active" — don't try to detect staleness; that's `_emit_stuck_tool`'s job.
- **`cpu_samples` unbounded growth.** Trim to `WORKER_IDLE_SAMPLE_CAP=20` on every append. Without trimming, state.json grows unbounded over a multi-hour phase and lock contention rises. Test the trim explicitly.
- **`ps -p $PID -o %cpu=` output format.** Returns a leading-space-padded float. Strip + parse with `float(...)`; handle ValueError on edge cases (pid vanished between `claim_worker_alive` and the ps call) by skipping the sample for this tick — do NOT crash the supervisor.
- **lsof binary missing on the system.** `OSError: [Errno 2] No such file or directory: 'lsof'` should be treated as "couldn't check, emit anyway" — log once, proceed. Avoid crashing the supervisor tick.
- **Test seam parity.** The existing `_emit_stuck_tool` accepts `ps_output` as a test seam. New `_emit_worker_idle` accepts BOTH `ps_output` AND `lsof_output`. Be consistent with the seam-injection signature so the supervisor's tick site doesn't have to special-case the new gap-fill.
- **Concurrent state.py edit risk.** Sister tuning (cap stalled threshold ceiling at 25 min) is being executed in-session on main, touching `state.py:524-535` (`stalled_threshold_for_phase`). This phase touches different lines (new `EVENT_*` + helpers at the bottom of the constants block + new functions). Rebase should be conflict-free; if `git rebase origin/main` does conflict, the resolution is mechanical (both halves land additively).
