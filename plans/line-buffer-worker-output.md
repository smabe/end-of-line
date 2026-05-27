# line-buffer-worker-output

## Goal
Wrap the dispatched worker's command so its stdout/stderr line-buffers into the log file as output is produced, not at process exit. Surviving log breadcrumbs let post-mortems on wedges (like the 2026-05-26 incident, log file = 0 bytes) see what the worker was doing before it hung. Sister to wedge-watchdogs (which adds detection); this is the diagnostic half.

## Diagnosis
- **Hypothesis:** `claude --print` (Node.js binary) flushes stdout only when `process.stdout.isTTY` is true. The current `subprocess.Popen(cmd, stdout=log_fh)` invocation gives Node a regular file, not a TTY, so libuv buffers stdout until full-buffer-flush or process-exit. When the worker hung mid-API-call without ever exiting cleanly, all buffered output died with the process. Wrapping the worker in `script` allocates a PTY, making the worker's stdout look like a TTY and triggering line-buffered flushes that land in the log file in real time.
- **Falsifiable test:** spawn a 5-tick synthetic worker — `bash -c 'for i in 1 2 3 4 5; do echo "line $i"; sleep 2; done'` — through the existing direct-Popen path while `tail -f` follows the log file. If the hypothesis is wrong (Node-style buffering isn't the cause), lines appear in the log within ~1s of each echo. If the hypothesis is right, the log stays empty (or sparse) until after the process exits at ~10s. Repeat with the script wrapper — lines should appear at 0s/2s/4s/6s/8s.
- **Test result (2026-05-26):**
  - **Hypothesis CONFIRMED.** Direct Popen of `python3 -c '<print + sleep loop>'` produced 0 bytes in the log at +0.3s, +1.3s, +3.3s, then 35 bytes only after process exit at +5s. Same buffering pattern claude --print would show.
  - **Script-wrapper DESIGN broken on macOS BSD.** `script -q /dev/null bash -c '<cmd>'` with the Bash tool's socket stdin failed: `tcgetattr/ioctl: Operation not supported on socket`. With `< /dev/null` explicit redirect it works (rc=0, 18 bytes) but leaves a `^D` (EOT, 0x04) artifact in the log from the tty close.
  - **`script -qc` syntax (Linux util-linux) is not on macOS** — `illegal option -- c`. Forces platform branching even with the workaround.
  - **`pty.openpty()` works clean.** Stdlib-only, no external binary, no `^D` artifact, output landed correctly. ~20 LOC including a drain thread.
  - **Decision: pivot to pty.openpty().** The plan's "Non-goals" rationale ("script wrapper is simpler for 1 phase") doesn't survive empirical contact — script needs `< /dev/null` + platform branching + `^D` cleanup just to match the baseline that pty.openpty gives natively.

## Non-goals
- Not adding a `dispatch.line_buffer` config knob. Always-on. If the platform's `script` binary is missing (`shutil.which("script") is None`), fall back to direct Popen with a one-line stderr warning.
- Not changing `.orchestrator.json` schema. Wrapper applies inside dispatch.py; the operator's command stays untouched.
- Not the detection fix. wedge-watchdogs covers detection (catches the wedge, fires notify); this plan covers diagnostic survivability (gives the operator a log file with content after the catch). They compose.
- Not unifying the two `subprocess.Popen` sites (line 226 main dispatch, line 322 repair-worker) under one helper yet. Repair-worker is short-lived + synchronous; the wedge-prone path is the main dispatcher. Rule of three not hit yet — repair stays unwrapped.
- Not refactoring the `log_fh = open(log_path, "ab")` block-scoped pattern. The script wrapper preserves it — the wrapped cmd writes to log_fh from the script subprocess just like the worker did before.

## Files to touch
- `end_of_line/dispatch.py` — wrap the configured `cmd` (built at line ~168) with `script -q /dev/null bash -c '<escaped>'` (macOS) or `script -qc '<cmd>' /dev/null` (Linux) before the `Popen` call at line ~226. Detect platform via `sys.platform.startswith("darwin")`. If `shutil.which("script")` returns None, skip the wrap, emit a one-line stderr warning, Popen directly.
- `tests/test_dispatch_*.py` — find the existing dispatch test file by inspection; add:
  1. `test_cmd_wrapped_with_script_on_darwin` — patch `sys.platform="darwin"` + `shutil.which("script")` → path; assert Popen received a cmd containing `script -q /dev/null bash -c`.
  2. `test_cmd_wrapped_with_script_on_linux` — patch `sys.platform="linux"` + `shutil.which("script")` → path; assert Popen received `script -qc ... /dev/null`.
  3. `test_cmd_unwrapped_when_script_missing` — patch `shutil.which("script")` → None; assert Popen received the raw cmd and stderr got a one-line warning.
  4. Optional integration: `test_synthetic_worker_log_lands_incrementally` — spawn `bash -c 'echo X; sleep 0.5; echo Y'` through dispatch; after 0.3s assert log contains "X"; after 1.5s assert "Y". Skip if CI timing is flaky.

## Failure modes to anticipate
- **macOS vs Linux `script` flag drift.** BSD `script` (macOS) is `script [-q] file command...` — file is positional, command follows. util-linux `script` (Linux) is `script [-q] [-c command] [file]` — command goes after `-c`, file optional. Wrong syntax → script fails immediately → dispatch fast-fails (`_FAST_FAIL_WAIT_SEC` branch) → plan halts. Test both paths explicitly.
- **Shell escaping of the wrapped cmd.** The current `cmd` string is shell-evaluated (`shell=True`). With the wrapper, the inner cmd becomes an argument to `bash -c '<inner>'`. Quotes in the inner cmd (e.g. `'/clu-phase {plan_slug}'` from the template) need re-escaping. Use `shlex.quote(cmd)` for the inner.
- **TTY behavior changes for the worker.** Once the worker thinks it's running in a TTY (because script allocates one), it may turn on ANSI color codes or progress bars. The log will contain raw escape sequences. Side effect: log readability slightly worse, but content > prettiness. Document in commit's "Under the hood."
- **`script` exit code propagation.** macOS BSD `script` exits with the wrapped command's exit code. util-linux `script` historically did NOT (returned 0 on success of script itself), `--return`/`-e` flag added in newer versions. If exit code doesn't propagate, the dispatch fast-fail logic at line 255-283 misreads success/failure. Phase 1 should verify by running `script -q /dev/null bash -c 'exit 42'; echo $?` on the target system, then add `-e` if needed on Linux.
- **PTY allocation failures.** Resource-starved systems can run out of PTYs (rare on macOS, possible on CI runners). script will fail at startup. The shutil.which guard catches missing binary but not PTY exhaustion. Falls into "fast-fail rc != 0" branch which already releases the claim with a reason string — degraded but acceptable. Add a comment in the wrapper-skip path explaining the failure pattern.
- **Side-effect on `_match_systemic_signature`.** The systemic-failure matcher (line 259) greps the log file for known crash signatures. With script wrapper, the log gets a `Script started` header line (macOS) + possibly a trailing `Script done` line. Make sure existing regex anchors don't false-positive on the wrapper headers. `grep -r _match_systemic_signature` to find patterns + spot-check.

## Done criteria
- `python3 -m unittest discover -s tests` green; count delta matches new tests added (1391 + 2-3 new = 1393-1394).
- Falsifiable-test result from Diagnosis recorded in this plan file (replace the TODO in the Test result line — keeps empirical evidence with the plan).
- Manual smoke: dispatch any plan (wedge-watchdogs in flight is a candidate); `tail -f` the worker log file; observe lines arriving within a few seconds of being produced, not at process exit. Document the result in the commit message's "Tests" section.
- One commit on main, structured format (Title / Why / What's new / Under the hood / Tests / Co-Authored-By), pushed.
- `git diff origin/main..HEAD --stat` touches only `end_of_line/dispatch.py` and a test file. Anything else is scope creep.

## Parking lot

**DEFERRED 2026-05-26** pending wedge-watchdogs completion. Reason: the empirical pivot exposed cross-plan coupling that's unsafe to apply while wedge-watchdogs is mid-execution.

### Design constraint discovered (2026-05-26)

In-process `pty.openpty()` + drain thread inside the supervisor **does not survive supervisor exit**. The supervisor is short-lived (cron-spawned, ~few-second tick, exits). Once supervisor process exits, the drain thread dies, `master_fd` closes, and the worker's writes to slave_fd either fail (EIO → worker dies) or silently discard (worker survives but log goes empty after the first 0.5s — the wedge case is minutes in, so log is empty exactly when we need it).

**Any working solution requires a long-lived intermediary process** between supervisor and worker that owns the PTY:

- **Python shim module** (~50 LOC new `end_of_line/_pty_spawn_shim.py` + ~10 LOC dispatch wrap). Stdlib-only, no platform branch, clean output. Most-correct.
- **`script(1)` binary** (~10 LOC dispatch wrap). External binary, BSD vs util-linux flag drift, `^D` byte artifact in log, requires explicit `stdin=DEVNULL` on macOS. Smaller diff.

### Cross-plan implication

Either intermediary becomes the new `claim.pid` (worker is the intermediary's child, not Popen's direct child). Implications for the surrounding watchdog stack:

- `claim_worker_alive(claim, cmdline_match="...")` — ✅ still works (intermediary's cmdline includes the wrapped cmd, which contains `/clu-phase`).
- `_emit_stuck_tool` — ✅ already walks process tree from claim.pid, finds claude as a descendant.
- **`_emit_worker_idle` (wedge-watchdogs phase 2, planned design)** — ❌ samples `ps -p $PID -o %cpu=` on claim.pid directly. Would see intermediary's near-zero CPU and false-positive forever. **Must be updated to walk the process tree and sum %cpu of all descendants of claim.pid** before this plan can ship safely.

### Pickup order (when resuming)

1. Wait for wedge-watchdogs to fully ship (both phases merged to main + archived).
2. Before implementing this plan, edit `_emit_worker_idle` to walk the process tree for %cpu (mirror the existing `_parse_ps_output` / `Descendant` tree-walk pattern in `supervisor.py:56-160`).
3. Then implement this plan as Python shim (preferred) — `end_of_line/_pty_spawn_shim.py` that pty.openpty's, forks the worker as a child, drains master → its own stdout (which dispatch.py's Popen has redirected to log_fh).
4. Update wedge-watchdogs `_emit_worker_idle` tests to verify CPU-tree-walk behavior with the shim in place.
5. End-to-end smoke: spawn a real `claude --print` worker via the shim, `tail -f` the log, observe lines arriving in real time (not at exit).
