# lease-reliability-reap-core — orphan-PID reap helper (#57 part 1/2)

You are phase `reap-core` of the `lease-reliability` plan. Ship the
`state.reap_orphan_pid` helper + `EVENT_PHASE_ORPHAN_REAPED` event
constant. No supervisor wiring in this phase — that's `supervisor-wire`.

## Locked decisions (do NOT re-litigate)

See `plans/lease-reliability.md`. Summary:

- New helper signature: `reap_orphan_pid(pid: int, cmdline_match: str | None = None) -> ReapResult`.
- Signal sequence: `SIGTERM` → poll `os.kill(pid, 0)` every 250ms up to 5s → `SIGKILL` if still alive.
- Do NOT use `os.waitpid(WNOHANG)` — we never forked the PID; returns `ECHILD`.
- PID-reuse guard: when `cmdline_match` given, shell out to `ps -p <pid> -o command=` and require substring present. On mismatch, signal nothing; return `cmdline_mismatch=True`.
- Platform: macOS + Linux. No Windows.
- Event constant `EVENT_PHASE_ORPHAN_REAPED = "phase_orphan_reaped"` alongside existing event constants.

## Read first

- `end_of_line/state.py:78` — `DEFAULT_LEASE_TTL_MIN` (constants region).
- `end_of_line/state.py:107-110` — existing `EVENT_*` constants; add yours here.
- `end_of_line/state.py:334-359` — `append_event` and `release_if_expired` for the pattern your reap event will mirror.
- `tests/test_release_claim.py` — existing test layout for state-level helpers; mirror the AAA shape.
- `tests/test_stalled_claim.py` — alternative pattern reference.

## Produce

1. **Failing tests first.** New file `tests/test_reap_orphan.py`. Tests:
   - `test_reap_terminates_live_subprocess`: spawn `subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])`, call `reap_orphan_pid(p.pid)`, assert process exits within 6s, assert `result.signaled == "SIGTERM"`, `result.escalated_kill == False`.
   - `test_reap_already_dead_pid_is_noop`: pick a clearly-dead PID (e.g. spawn + immediately wait), call `reap_orphan_pid(p.pid)`, assert no exception, assert `result.signaled is None` or a documented "already dead" signal value.
   - `test_reap_cmdline_mismatch_signals_nothing`: spawn `sleep 30`, call `reap_orphan_pid(p.pid, cmdline_match="this-string-not-in-cmdline-xyz")`, assert process is still alive 1s later, assert `result.cmdline_mismatch == True`. Clean up: `p.terminate(); p.wait()`.
   - `test_reap_cmdline_match_substring_present`: spawn a subprocess whose cmdline includes a known marker; call with `cmdline_match=marker`; assert reap proceeds.
   - Optional: `test_reap_escalates_to_sigkill` — only if you can reliably make a subprocess ignore SIGTERM in a few lines. If not, skip rather than write a flaky test.

2. **Implementation in `end_of_line/state.py`:**
   - Add event constant alongside existing `EVENT_*` block (line ~110): `EVENT_PHASE_ORPHAN_REAPED = "phase_orphan_reaped"`.
   - Add `ReapResult` dataclass (use `@dataclass` from `dataclasses`):
     ```python
     @dataclass
     class ReapResult:
         signaled: str | None  # "SIGTERM", "SIGTERM+SIGKILL", or None
         escalated_kill: bool
         cmdline_mismatch: bool
     ```
   - Implement `reap_orphan_pid(pid: int, cmdline_match: str | None = None) -> ReapResult`:
     - If `cmdline_match` is set, run `subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=2)`. If returncode != 0 → PID gone → return `ReapResult(None, False, False)`. If `cmdline_match` not in stdout → return `ReapResult(None, False, True)`.
     - `try: os.kill(pid, signal.SIGTERM)` — on `ProcessLookupError` return `ReapResult(None, False, False)`.
     - Poll: `for _ in range(20): time.sleep(0.25); try: os.kill(pid, 0); except ProcessLookupError: return ReapResult("SIGTERM", False, False)`.
     - Still alive after 5s: `os.kill(pid, signal.SIGKILL)` (suppress `ProcessLookupError`), return `ReapResult("SIGTERM+SIGKILL", True, False)`.
   - Use `signal`, `time`, `subprocess`, `os` from stdlib. No new top-level imports beyond what's needed.

3. **Acceptance.**
   - All 4 (or 5) new tests in `tests/test_reap_orphan.py` pass.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - `grep -n EVENT_PHASE_ORPHAN_REAPED end_of_line/state.py` shows the new constant.
   - `python3 -c "from end_of_line.state import reap_orphan_pid, ReapResult; print('ok')"` succeeds.

4. **Commit + complete.**
   - Structured commit: `lease-reliability: phase reap-core — orphan-PID reap helper (#57)`.
   - Stage explicit paths: `end_of_line/state.py`, `tests/test_reap_orphan.py`.
   - `clu verify --plan lease-reliability --phase reap-core --token <T>` to stamp tests.
   - `clu attest --simplify --plan lease-reliability --phase reap-core --token <T>` (skip if diff is single-file ≤30 lines; otherwise run `/simplify` then attest).
   - `clu complete --plan lease-reliability --phase reap-core --token <T>`.

## Failure modes to watch

- **Test flakiness from sleep-based polling.** The "subprocess exits within 6s" assertion needs slack — use `subprocess.wait(timeout=10)` and trust SIGTERM is fast in practice. Don't tighten the budget.
- **`ps` not in PATH on stripped Linux containers.** Unlikely on a dev machine but if it bites: degrade to "no cmdline check available, proceed without match" — log a warning event rather than fail the reap. Add this only if a real test environment trips it; otherwise leave the simple shell-out.
- **Zombie process from `subprocess.Popen` not being waited on.** After SIGTERM, the test must call `p.wait()` to reap the zombie or it'll show up as a leaked subprocess in CI. Use `try: p.wait(timeout=10); finally: pass`.
- **PID 0/1 or other special PIDs.** Don't add explicit guards — `os.kill(0, ...)` and `os.kill(1, ...)` will fail with permissions; let the natural error propagate. The caller (supervisor) only ever passes worker PIDs that the OS launched.
