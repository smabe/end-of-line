# clu-queue-repair — auto-repair worker with hard slug-preservation

You are phase `repair` of the `clu-queue` plan. Phases primitive/add/
list/pop have shipped: the queue is operational and corruption today
is logged-and-skipped (per `_advance_queue_for_project`'s try/except).
Your job: replace that skip with a real auto-repair pipeline that
spawns a headless Claude worker, validates its output, and reverts on
any destructive change.

The user's explicit constraint: **clu must not delete the queue.**
The safety boundary is clu's Python validation, NOT the worker's
prompt. Tests must prove the validation rejects a worker that drops
or empties pending entries.

Design pass is done. Full context in
`.claude/plans/plan-queue-master.md` (especially the "Auto-repair
worker design" section) and `plans/clu-queue.md`. Do not redesign.

## Locked decisions (do NOT re-litigate)

- **Trigger**: in `_advance_queue_for_project` (from phase pop), when
  `queue.load(path)` raises (`JSONDecodeError`, `SchemaVersionMismatch`,
  unexpected `KeyError`, `OSError`), route to repair pipeline.
- **Backup-first**: before any repair attempt, write
  `<queue_path>.corrupt-<utc-ts>` with the original bytes. Always.
  Even when `repair_command` is unset (operator can recover manually
  from the backup).
- **Throttle**: `<queue_path>.repair-attempts` JSON file storing
  `{"attempts": int, "last_at": iso, "diagnosis_hash": str}`. After
  3 failed attempts on the same diagnosis_hash, fall back to plain
  `KIND_QUEUE_CORRUPT` notification (no dispatch). Reset on success.
- **`ProjectConfig.dispatch.repair_command`** — new optional field.
  Template variables: `{corrupt_path}`, `{backup_path}`,
  `{diagnosis}`, `{schema_json}`, `{log_path}`. If unset, skip
  dispatch and fire `KIND_QUEUE_CORRUPT` directly (throttle still
  increments).
- **Worker dispatch**: extracted helper
  `dispatch.dispatch_repair_worker(cfg, corrupt_path, backup_path,
  diagnosis, log_path)` shares the spawn-and-log mechanics with the
  existing per-phase dispatch (subprocess.Popen + fast-fail check +
  per-token-equivalent log file). Fire-and-wait (this is synchronous
  — cron tick blocks until repair finishes or times out at ~60s).
- **Validation** in `queue.validate_repair(backup_bytes, repaired_path)`:
  1. Re-load: `queue.load(repaired_path)` must not raise. Else
     `Failure(reason="still unparseable: ...")`.
  2. Slug preservation: `best_effort_extract_slugs(backup_bytes)` →
     set; `{e["slug"] for e in repaired["queue"]}` → set; the
     repaired set MUST be a superset. Else
     `Failure(reason=f"would drop slugs: {missing}")`.
  3. History append-only: every slug present in the backup's
     `history` must remain in the repaired history. Else
     `Failure(reason="history entries removed")`.
- **`best_effort_extract_slugs(bytes)`**: regex over
  `b'"slug"\s*:\s*"([^"]+)"'` (case-sensitive, double-quotes only).
  Imperfect but enough to detect catastrophic loss. Document that
  it's best-effort.
- **Revert on validation failure**: `corrupt_path.write_bytes(backup_bytes)`.
  Fire `KIND_QUEUE_REPAIR_FAILED` (halt-bypass).
- **New kinds in notify.py**: `KIND_QUEUE_REPAIRED` (defer),
  `KIND_QUEUE_REPAIR_FAILED` (halt-bypass), `KIND_QUEUE_CORRUPT`
  (halt-bypass). Add the last two to `QUIET_HOURS_BYPASS_KINDS`.
- **New ExitCode**: `REPAIR_DECLINED = 9`. The worker uses this
  when it refuses destructive repair (clu treats any non-zero as
  failure, but documenting 9 makes the worker's intent legible in
  logs).
- **Worker prompt** is shipped as a template that the project's
  `dispatch.repair_command` formats. The prompt MUST include the
  hard rules (preserve slugs, don't empty queue, don't remove
  history, atomic write, exit 9 if can't repair safely). The
  validation in clu enforces these regardless of what the prompt
  says.
- **No `clu queue migrate` subcommand.** Schema bumps in the future
  go through the same repair pipeline. The worker's prompt informs
  it of the expected schema; the worker writes the migrated form.

## Read first

- `end_of_line/queue.py` (phase primitive).
- `end_of_line/cli.py` `_advance_queue_for_project` (phase pop).
  This is where the trigger lives. You replace the existing
  try/except's "log and skip" branch.
- `end_of_line/dispatch.py` — full file. Especially
  `dispatch_for_tick` (line 75) and the fast-fail pattern around
  line 119. The repair worker dispatch shares this scaffolding.
  Extract a shared spawn helper if duplication crosses ~30 lines.
- `end_of_line/notify.py` — `KIND_*` constants, `notify(spec, kind,
  body)` signature, `QUIET_HOURS_BYPASS_KINDS`.
- `end_of_line/config.py` — `ProjectConfig.dispatch` structure. You
  add `repair_command: str | None = None` (optional, defaults to
  None for safe rollout).
- `end_of_line/state.py` `ExitCode` IntEnum. Add `REPAIR_DECLINED = 9`.
- `docs/contract.md` — note that you'll update this in phase `docs`,
  but read it now for the dispatch contract context.
- `CLAUDE.md` — re-read the dispatch-command conventions if any.

## Produce

1. **TDD: failing tests first.** Add `tests/test_queue_repair.py`:

   - `test_repair_disabled_when_repair_command_unset` — corrupt
     queue.json + `dispatch.repair_command = None`. Trigger
     `_advance_queue_for_project`. Result: backup written,
     `KIND_QUEUE_CORRUPT` fired, throttle incremented, no
     subprocess spawn.
   - `test_repair_success_validates_and_clears_throttle` — corrupt
     queue.json + repair_command set + (mocked) worker writes a
     valid queue.json preserving all slugs. Result: backup written,
     validation passes, `KIND_QUEUE_REPAIRED` fired, throttle file
     deleted or reset to 0.
   - `test_repair_reverts_on_dropped_slug` — corrupt input has 3
     slugs by regex extraction; mocked worker writes a queue with
     only 2. Result: backup preserved, corrupt_path reverted to
     backup bytes, `KIND_QUEUE_REPAIR_FAILED` fired with reason
     "would drop slugs: {'foo'}", throttle incremented.
   - `test_repair_reverts_on_empty_queue_when_original_nonempty` —
     mocked worker writes `{"queue": [], "history": [...]}` but
     original had entries. Result: revert + REPAIR_FAILED.
   - `test_repair_reverts_on_history_removal` — mocked worker
     writes a queue with empty `history` but original had history.
     Result: revert + REPAIR_FAILED.
   - `test_repair_reverts_on_still_unparseable` — mocked worker
     writes more garbage. Result: revert + REPAIR_FAILED with
     reason "still unparseable: ...".
   - `test_repair_handles_worker_exit_9` — mocked worker exits 9
     (REPAIR_DECLINED) without modifying the file. Result: no
     revert needed (backup matches current); REPAIR_FAILED fires
     anyway because we didn't get a successful repair; throttle
     increments.
   - `test_repair_handles_worker_timeout` — mocked worker hangs
     past the timeout. Result: process killed, file unchanged from
     backup (revert is a no-op match), REPAIR_FAILED fires,
     throttle increments.
   - `test_repair_throttle_blocks_fourth_attempt` — throttle file
     shows 3 attempts on hash X; corruption with same hash on the
     fourth tick. Result: NO dispatch, NO subprocess spawn;
     `KIND_QUEUE_CORRUPT` fires with "auto-repair gave up after 3
     attempts" body.
   - `test_repair_throttle_resets_on_success` — throttle shows 2
     attempts; successful repair; throttle file removed or attempts
     reset to 0.
   - `test_repair_throttle_different_diagnosis_resets` — throttle
     shows 3 attempts on hash X; new corruption has hash Y. Result:
     dispatch attempts (throttle is per-hash).
   - `test_repair_backup_always_written_before_dispatch` — regardless
     of repair outcome (success/failure/timeout/disabled), the
     corresponding backup file exists with the original bytes.
   - `test_repair_log_path_created` — `.orchestrator/logs/repair-queue-<ts>.log`
     exists after a dispatch attempt.
   - `test_repair_does_not_block_other_projects` — project A has
     corrupt queue + repair pipeline runs; project B has a clean
     queue. Tick. Both projects progress; A's repair doesn't crash
     or block B's pop.
   - `test_best_effort_extract_slugs_finds_all` — input bytes with
     several `"slug": "..."` patterns; returns the right set.
   - `test_best_effort_extract_slugs_robust_to_corruption` — input
     bytes that are valid JSON for the first half then garbage;
     extracts slugs from the valid section.

   Mock the subprocess.Popen call (or extract a thin spawn helper
   you can replace with a fake). For the validation tests, you can
   bypass dispatch entirely and call `validate_repair` directly
   with handcrafted before/after pairs. Run suite — all new tests
   must FAIL.

2. **Add new constants:**
   - `state.py`: `ExitCode.REPAIR_DECLINED = 9`.
   - `notify.py`:
     ```python
     KIND_QUEUE_REPAIRED = "queue_repaired"
     KIND_QUEUE_REPAIR_FAILED = "queue_repair_failed"
     KIND_QUEUE_CORRUPT = "queue_corrupt"
     QUIET_HOURS_BYPASS_KINDS = frozenset({
         KIND_HALTED,
         KIND_QUEUE_REPAIR_FAILED,
         KIND_QUEUE_CORRUPT,
     })
     ```
   - `notify.py`: render functions
     `render_queue_repaired(slug_count, backup_path)`,
     `render_queue_repair_failed(reason, backup_path)`,
     `render_queue_corrupt(diagnosis, backup_path)`.

3. **Add `ProjectConfig.dispatch.repair_command: str | None = None`**
   in `config.py`. Plumb it through the existing dispatch config
   parsing (the field is optional; default None preserves
   pre-this-phase behavior on existing projects).

4. **Implement `queue.best_effort_extract_slugs(data: bytes) ->
   set[str]`** in queue.py. Document the regex limitation.

   Also `queue.best_effort_extract_history_slugs(data: bytes) ->
   set[str]` — regex on `"history"\s*:\s*\[...\]` then extract slugs
   from that substring. (If the regex is too lossy, accept it; the
   primary safety check is the pending queue's slugs.)

5. **Implement `queue.validate_repair(backup_bytes, repaired_path)
   -> ValidationResult`** in queue.py. Returns a dataclass with
   `.ok: bool` and `.reason: str | None`.

6. **Implement throttle helpers** in queue.py:
   - `queue.read_throttle(throttle_path, diagnosis_hash) -> int`
     (returns 0 if file missing, parse error, or different hash).
   - `queue.increment_throttle(throttle_path, diagnosis_hash) -> None`.
   - `queue.reset_throttle(throttle_path) -> None` (unlinks file).

7. **Implement `dispatch.dispatch_repair_worker(cfg, corrupt_path,
   backup_path, diagnosis, log_path) -> int`** in dispatch.py.
   Uses the same `subprocess.Popen` shape as `dispatch_for_tick`.
   Substitutes `{corrupt_path}`, `{backup_path}`, `{diagnosis}`,
   `{schema_json}`, `{log_path}` into `cfg.dispatch.repair_command`.
   Synchronous: `proc.wait(timeout=DEFAULT_REPAIR_TIMEOUT_SEC)` and
   return rc. On timeout, kill and return a sentinel rc (e.g. -1).

   Extract a shared spawn helper if `_spawn_and_stream(cmd,
   log_path, env)` would dedupe ~30 lines between `dispatch_for_tick`
   and `dispatch_repair_worker`. Otherwise inline.

   Bundle a recommended `repair_command` template **in
   `docs/operations.md`** (phase `docs` will write that section).
   The project_default config does NOT auto-set repair_command; it
   stays opt-in.

8. **Implement `handle_corrupt_queue(cfg, exception, queue_path,
   backup_paths_dir)`** in cli.py near
   `_advance_queue_for_project`. This is the orchestrator that
   the catch-block in `_advance_queue_for_project` calls. Skeleton:

   ```python
   def _handle_corrupt_queue(cfg, exception, queue_path):
       diagnosis = f"{type(exception).__name__}: {exception}"
       diagnosis_hash = hashlib.sha256(diagnosis.encode()).hexdigest()[:8]
       throttle_path = queue_path.with_suffix(".json.repair-attempts")

       attempts = queue.read_throttle(throttle_path, diagnosis_hash)
       backup_path = queue_path.with_name(
           f"{queue_path.name}.corrupt-{st.utcnow_compact()}"
       )
       backup_path.write_bytes(queue_path.read_bytes())

       if attempts >= 3:
           notify.notify(cfg.notify, KIND_QUEUE_CORRUPT,
               render_queue_corrupt(diagnosis, backup_path) +
               " (auto-repair gave up after 3 attempts)")
           return

       if not cfg.dispatch.repair_command:
           notify.notify(cfg.notify, KIND_QUEUE_CORRUPT,
               render_queue_corrupt(diagnosis, backup_path))
           queue.increment_throttle(throttle_path, diagnosis_hash)
           return

       log_path = cfg.log_dir() / f"repair-queue-{st.utcnow_compact()}.log"
       log_path.parent.mkdir(parents=True, exist_ok=True)

       backup_bytes = backup_path.read_bytes()
       rc = dispatch.dispatch_repair_worker(
           cfg, queue_path, backup_path, diagnosis, log_path
       )

       result = queue.validate_repair(backup_bytes, queue_path)
       if not result.ok:
           queue_path.write_bytes(backup_bytes)  # revert
           notify.notify(cfg.notify, KIND_QUEUE_REPAIR_FAILED,
               render_queue_repair_failed(result.reason, backup_path))
           queue.increment_throttle(throttle_path, diagnosis_hash)
           return

       repaired = queue.load(queue_path)
       notify.notify(cfg.notify, KIND_QUEUE_REPAIRED,
           render_queue_repaired(len(repaired["queue"]), backup_path))
       queue.reset_throttle(throttle_path)
   ```

9. **Wire `_handle_corrupt_queue` into `_advance_queue_for_project`.**
   Replace the existing `try/except: print(...) ; return` with:
   ```python
   try:
       queue_data = queue.load(queue_path)
   except (json.JSONDecodeError, st.SchemaVersionMismatch, KeyError) as e:
       _handle_corrupt_queue(cfg, e, queue_path)
       return
   ```

10. **Document the worker prompt template.** Add a section to
    `docs/operations.md` (which phase `docs` will also touch — leave
    a stub here and the docs phase fills in fully):
    ```
    Recommended repair_command for ~/.orchestrator.json:
    "claude --print 'Repair queue.json at {corrupt_path}. Original at {backup_path}. Diagnosis: {diagnosis}. ...'"
    ```
    Include the hard-rules paragraph from the master plan. Keep
    phase `docs` as the canonical docs landing site; this is just a
    stub so users can opt in.

11. **Run the full suite.** All new tests pass. Existing tests
    unchanged. Count grows by ~16.

12. **`/simplify`.** Multi-file change across queue.py, dispatch.py,
    cli.py, notify.py, config.py, state.py. /simplify mandatory.

13. **Commit.** Structured:
    - Title: `clu-queue phase repair: auto-repair worker with hard slug-preservation`
    - Why: corruption today crashes the queue advancement for the
      affected project; an unattended Mac shouldn't lose pending
      operator entries. Repair is automatic but clu's validation
      (not the worker's prompt) is the safety boundary.
    - What's new: `dispatch.dispatch_repair_worker`, `queue.validate_repair`,
      `queue.best_effort_extract_slugs`, throttle helpers,
      `_handle_corrupt_queue`, three new `KIND_QUEUE_*` constants,
      `ExitCode.REPAIR_DECLINED = 9`, `ProjectConfig.dispatch.repair_command`
      optional field.
    - Under the hood: backup always written first; opt-in dispatch
      (repair_command unset → notification only); per-diagnosis-hash
      throttle caps at 3 attempts; validation reverts on slug-loss,
      empty-queue, history-removal, or unparseable output.
    - Tests: ~16 new tests covering disabled mode, success, all
      five revert paths, timeout, throttle behavior, multi-project
      independence.
    - Co-Authored-By trailer.

14. **`clu complete --token <token>` with SHA + count summary.**

## Failure modes to watch for

- **Worker is the safety boundary trap.** A common bug is to trust
  the prompt: "I told it not to drop slugs." Don't. The validation
  in clu MUST run on every repair, regardless of how persuasive the
  prompt is. Tests with mocked malicious workers (drops a slug,
  writes empty queue, removes history) MUST pass with a revert.
- **Backup naming collisions.** Multiple corruptions in the same
  second would overwrite the same `corrupt-<ts>` file. Use UTC
  timestamp with at least seconds-precision plus a short hash if
  collisions are theoretically possible. (Realistically: cron runs
  once a minute; the second case is academic but the test for it
  is cheap.)
- **`best_effort_extract_slugs` false negatives.** Regex extraction
  on garbage bytes is fragile. A queue whose JSON is broken in a
  way that hides slug keys (e.g. truncated mid-string) might miss
  slugs. Accept this — the goal is "catch catastrophic loss," not
  "perfect recovery." A truncated original is unrecoverable anyway.
- **`best_effort_extract_history_slugs` regex.** History is a
  nested array; locating "the history array" inside garbage bytes
  is hard. If too fragile, accept "history check skipped on
  un-locateable original" — log it as a soft warning. The pending
  queue check is the load-bearing one.
- **Subprocess timeout.** Set a reasonable timeout
  (`DEFAULT_REPAIR_TIMEOUT_SEC = 60`?). Cron tick budgets matter:
  if repair takes 60s, the next tick fires before this one
  finishes. The existing dispatch is async (Popen + return); repair
  is synchronous (wait + validate + notify). Document that the
  one-minute tick may overlap during repair — and that's OK because
  the repair holds the queue lock briefly (only the revert + repair
  worker writes touch the file).
- **Throttle file as a separate file = lock-ordering question.**
  Throttle reads/writes are unlocked best-effort (it's a counter,
  not state). Document that. If the throttle file gets corrupted,
  treat as 0 attempts and continue.
- **`os.replace` atomicity for the worker.** If the worker writes
  non-atomically and crashes mid-write, we see a half-written file.
  `queue.load` raises; the validation re-runs; we revert. So the
  invariant survives a non-atomic worker, but it costs an extra
  revert. The prompt should specify atomic write; the test for
  "still unparseable" catches the worst case.
- **Notification spam on rapid corruption-fix-corruption-fix
  cycles.** The throttle is per-hash, so the same diagnosis hash
  resets on success but a NEW diagnosis fires fresh. If the
  operator manages to break the queue 3 different ways in an hour,
  they'd get 3 pings — that's correct behavior; they're actively
  doing something weird.
- **`notify.notify` defer machinery.** Verify `KIND_QUEUE_REPAIRED`
  defers in quiet hours (NOT in QUIET_HOURS_BYPASS_KINDS) and the
  other two bypass.

## Done criteria for this phase

- Corruption in queue.json no longer leaves the queue stuck.
- Backup file always written before any repair attempt.
- `repair_command` unset → plain notification, no dispatch, throttle
  increments.
- `repair_command` set + worker succeeds + validation passes →
  `KIND_QUEUE_REPAIRED` fires, queue parseable next tick, throttle
  reset.
- Validation reverts on dropped slug / empty queue / removed history
  / unparseable / worker exit 9 / timeout — all five paths fire
  `KIND_QUEUE_REPAIR_FAILED`, throttle increments.
- 4th corruption on same diagnosis hash → no dispatch, plain
  `KIND_QUEUE_CORRUPT`.
- Multi-project: A's corruption doesn't block B.
- ~16 new tests pass; full suite green.
- One commit, structured message, no `Fixes` trailer.
- `clu complete` with token + SHA + count summary.
