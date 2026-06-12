# quota-pause-classify — classify quota deaths, forgive attempts, write the pause

You are phase `classify` of the `quota-pause` plan. You wire `quota.classify_quota` into all three worker-death paths, make quota deaths burn no attempt, and write the project-level pause file. After this phase: quota deaths are classified and forgiven, the pause file exists — but nothing gates dispatch on it yet (that's phase `gate`).

## Locked decisions (do NOT re-litigate)

See `plans/quota-pause.md`. Summary:

- New event constants in `state.py`: `EVENT_QUOTA_DEATH = "quota_death"` (kwargs: token, phase, signature, line), `EVENT_QUOTA_PAUSED = "quota_paused"` (kwargs: paused_until-or-None, signature), `EVENT_QUOTA_RESUMED = "quota_resumed"` (emitted in phase `gate`; define the constant now so contract docs/events stay one family).
- `attempts_for_phase` (`state.py:1191-1223`): the subtraction-token set widens from `EVENT_SYSTEMIC_FAILURE` only to `{EVENT_SYSTEMIC_FAILURE, EVENT_QUOTA_DEATH}`. The event MUST carry `phase` and `token` keys to satisfy the existing membership logic at `state.py:1210-1216`.
- Three classification sites, all tailing the last 50 lines of `claim["log_path"]` (stamped at `dispatch.py:392`):
  1. Supervisor dead-PID block (`supervisor.py:662-699`) — classify BEFORE `release_claim_and_emit`.
  2. Supervisor lease-expiry block (ends `supervisor.py:648`) — same, before release.
  3. Dispatch fast-fail (`dispatch.py:365-380`) — quota check BEFORE `_match_systemic_signature`.
- Pause file `plans/.orchestrator/quota.json` via `st.locked_json(path, expected_version=1, empty=...)` (`state.py:543-565`). Schema: `{"schema_version": 1, "paused_until": iso-or-null, "signature": str, "line": str, "canary_plan": null, "canary_deadline": null, "created_at": iso}`. Writing always clears canary fields (a re-pause during canary is exactly the canary-failed case).
- `paused_until = parse_reset(...) + PAUSE_BUFFER_SEC (120s)`; unparseable reset → `paused_until: null` (stuck pause).
- **Plan status never flips** — do NOT mirror `_pause_and_halt`'s `STATUS_PAUSED` write. `EVENT_QUOTA_PAUSED` is appended to the triggering plan's event log.
- Quota-classified supervisor deaths suppress `render_worker_dead` in the returned `TickResult.notify_body` (set it None / use a quota detail string); proper KIND_QUOTA_* notifications land in phase `notify-docs`.

## Read first

- `plans/quota-pause.md` `## Findings log` — phase `matcher` may have logged regex/parse gotchas.
- `end_of_line/quota.py` — the module phase `matcher` shipped (classify_quota, parse_reset, PAUSE_BUFFER_SEC, QUOTA_FILE_NAME).
- `end_of_line/supervisor.py:600-710` — lease-expiry + dead-PID blocks; note mutate-window structure and the "durable state first, best-effort reap last" ordering comment.
- `end_of_line/dispatch.py:161-182, 361-404, 624-712` — log-tail helper, fast-fail window, `_pause_and_halt` family (the shape you are deliberately NOT reusing for status).
- `end_of_line/state.py:1191-1223` — `attempts_for_phase` subtraction logic.
- `end_of_line/config.py` — `ORCHESTRATOR_DIR` / how `plans/.orchestrator/` paths are built (queue.json precedent).
- `tests/test_systemic_failure.py:210-340` — the test pattern for "write a log, kill a worker, assert classification" (mirror it).

## Produce

1. **Failing tests first.**
   - `tests/test_quota.py` additions (`QuotaPauseFileTests`): pause-file write happy path (parseable reset → paused_until = reset+120s), stuck path (unparseable → null), re-pause clears canary fields.
   - `tests/test_supervisor.py` additions: dead-PID worker with a quota line in its log → `EVENT_QUOTA_DEATH` appended, `attempts_for_phase` does NOT count that dispatch, quota.json written, notify_body is not the worker-dead render; same for lease-expiry path; regression: dead-PID worker with a non-quota log behaves exactly as today (event + attempt counted).
   - `tests/test_systemic_failure.py` additions: fast-fail rc≠0 with quota line → quota path taken (no `EVENT_SYSTEMIC_FAILURE`, no STATUS_PAUSED flip, quota.json written); fast-fail with `rate limit` line still takes the systemic path (ordering guard).
   - Use `tests.isolate_registry(self, tmp_path)` in any setUp touching registry/init.

2. **Implementation.**
   - `end_of_line/quota.py`: add `record_quota_pause(project_root, match, now) -> datetime | None` (computes paused_until, writes quota.json under locked_json, returns paused_until) and a small `read_log_tail(log_path, lines=50) -> str` if you don't reuse dispatch's inline pattern — prefer ONE shared tail helper; if you extract it, move `_match_systemic_signature`'s file-read onto it too (3 call sites = extract decisively).
   - `end_of_line/state.py`: the three EVENT_ constants + the `attempts_for_phase` subtraction-set widening.
   - `end_of_line/supervisor.py`: in both death blocks, read the tail, `classify_quota`, and on match append `EVENT_QUOTA_DEATH` + `EVENT_QUOTA_PAUSED`, call `record_quota_pause`, suppress the worker-dead notify body. Non-match → exactly today's behavior.
   - `end_of_line/dispatch.py`: in the fast-fail branch, quota check first; on match append `EVENT_QUOTA_DEATH`, release the claim (mirror `_release_with_failure`'s release, without the dispatch_failed event), write the pause, return False.

3. **Acceptance.**
   - Full suite green.
   - Simulated quota death (test) burns zero attempts across 3 consecutive dead dispatches — `attempts_for_phase` stays 0.
   - Non-quota deaths byte-for-byte regression-safe (existing supervisor/systemic tests untouched and green).
   - quota.json contents match the locked schema for both parseable and stuck cases.

4. **Commit + attest + complete.**
   - Log cross-phase findings (e.g. tail-helper extraction, event-kwarg shape) in the master's `## Findings log`.
   - Structured commit: `quota-pause: phase classify — quota deaths forgiven + project pause file (#94)`.
   - Stage explicit paths: `end_of_line/quota.py`, `end_of_line/state.py`, `end_of_line/supervisor.py`, `end_of_line/dispatch.py`, `tests/test_quota.py`, `tests/test_supervisor.py`, `tests/test_systemic_failure.py` (+ master if findings logged).
   - After the commit: `clu verify --plan quota-pause --phase classify --token <T>`, `clu attest --simplify --plan quota-pause --phase classify --token <T>`.
   - `clu complete --plan quota-pause --phase classify --token <T>`.

## Failure modes to watch

- **Classifying after release** — the claim's `log_path` is gone once `release_claim_and_emit` runs. Read the tail INSIDE the mutate window, before release, matching the "durable state first" ordering comment at `supervisor.py:666-669`.
- **Event-kwarg mismatch breaking forgiveness** — `attempts_for_phase` matches on `evt.get("phase")` and `evt.get("token")`; `append_event` kwarg names must be exactly `phase=` and `token=`. A typo silently re-enables attempt burn (the EVENT_* CLAUDE.md warning, same family).
- **locked_json schema_version** — `load()` validates `schema_version`; the empty factory must include it or first read after first write fails.
- **Fast-fail ordering** — quota before systemic. The regexes don't overlap today (verified at plan time), but order is the contract if a future message contains both wordings.
