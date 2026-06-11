# line-buffer-worker-output-pty-shim — the PTY shim + dispatch wrap

You are phase `pty-shim` of the `line-buffer-worker-output` plan. You
deliver, as one commit: `end_of_line/_pty_spawn_shim.py`, the dispatch wrap
that routes phase workers through it, tests, and a live smoke proving log
lines land BEFORE process exit. Phase idle-treewalk has already made the
idle watchdog tree-aware — the shim may safely become claim.pid.

## Locked decisions (do NOT re-litigate)

See `plans/line-buffer-worker-output.md`. The shim contract, each item
grounded (docs.python.org/3.11/library/pty.html, /os.html, /subprocess.html;
CPython Lib/pty.py + GH-12049; Apple forum thread 663632; spike 2026-06-10):

- Invocation: dispatch builds
  `[sys.executable, "-m", "end_of_line._pty_spawn_shim", "--", cmd]` where
  `cmd` is the rendered operator command STRING; outer Popen drops
  `shell=True` (list argv); `stdout=log_fh, stderr=STDOUT,
  start_new_session=True` unchanged; `_stamp_pid` unchanged (shim pid =
  claim pid = pgid). The shim itself runs `sh -c <cmd>` with the pty slave
  as the child's stdout/stderr and stdin=DEVNULL.
- Inside the shim: `os.openpty()`; `fcntl.ioctl(slave, TIOCSWINSZ,
  struct.pack("HHHH", 24, 80, 0, 0))`; clear ONLCR on the slave termios;
  spawn child; CLOSE the parent's slave fd (or the master never EOFs);
  drain loop = `select` on master + `os.read`, treating `b""` AND `OSError`
  both as EOF (macOS returns b"", Linux raises EIO — CPython's `pty._copy`
  precedent); never sleep-then-read (macOS can discard pending pty bytes at
  child exit); strip ANSI escape sequences (CSI/OSC/single-char escapes —
  one compiled regex, pure function, unit-tested) and write to fd 1 via
  `os.write` (unbuffered).
- Exit propagation: `os.waitpid` → `os.waitstatus_to_exitcode`; n >= 0 →
  `sys.exit(n)`; signal death (negative) → restore `SIG_DFL` for that
  signal and `os.kill(os.getpid(), sig)` so the OUTER `Popen.returncode`
  reads the POSIX `-N` the fast-fail branch expects. Never `sys.exit(-N)`
  (undefined, truncates to & 0xFF).
- Fallback: `os.openpty()` raising → write one warning line to stderr and
  `os.execvp("sh", ["sh", "-c", cmd])` — direct exec preserves pid (=claim
  pid) and rc; behavior degrades to today's block-buffered logging, never a
  dead dispatch.
- Repair workers stay on the direct Popen path (dispatch.py:426-435).

## Read first

- `plans/line-buffer-worker-output.md` `## Findings log` — REQUIRED
  (idle-treewalk may have logged seam changes).
- `plans/archive/line-buffer-worker-output/v1-2026-05-26-diagnosis.md` —
  the v1 empirical record (why script(1) lost; buffering proof).
- `end_of_line/dispatch.py:297-318` (popen_kwargs + Popen), `:341-370`
  (fast-fail + `_match_systemic_signature`), `:719-743` (`_stamp_pid`),
  `:80-88` (signature regexes — your ANSI strip keeps these reliable).
- `tests/test_dispatch.py` — Popen-arg assertion patterns.

## Produce

1. **Failing tests first.**
   - `tests/test_pty_spawn_shim.py` (NEW): ANSI-strip pure function (CSI,
     OSC, bare ESC sequences from the spike's byte sample; CRLF folding);
     rc propagation — child `exit 0`, `exit 7`, killed by SIGTERM (assert
     outer returncode 0 / 7 / -15); incremental delivery — child
     `echo X; sleep 0.4; echo Y`, assert X is readable from the shim's
     stdout pipe before the child exits (real pty integration, macOS).
   - `tests/test_dispatch.py`: phase dispatch Popen receives the shim argv
     (list, no shell=True) with cmd as final arg; repair dispatch is
     UNCHANGED (regression pin).

2. **Implementation**: `_pty_spawn_shim.py` (~80 LOC; module docstring
   carries the platform contract + citations) + the dispatch wrap +
   `docs/reference.md` entry + one process-model paragraph in
   `docs/architecture.md`.

3. **Acceptance.**
   - All new tests green; full suite green; `basedpyright` exit 0.
   - **Live smoke** (scratch project, #90-smoke style, notify masked,
     BRANCH code driving dispatch): one trivial phase through a real
     `claude --print` worker via the shim. Assert: (a) log file gains
     content while the worker is still running (`tail` during, not after);
     (b) log is ANSI-free and LF-only; (c) phase completes, heartbeats
     land, no watchdog misfires; (d) `clu doctor` quiet. Record timings in
     the completion summary.
   - Fast-fail check: scratch dispatch of a command that exits rc=3
     immediately → claim released with rc=3 in the reason (shim overhead
     stays inside the 0.5s window).

4. **Commit + attest + complete.**
   - Findings: log shim/runtime surprises (PTY behavior differences,
     timing) — this is fleet-wide infrastructure.
   - Structured commit: `line-buffer-worker-output: phase pty-shim — PTY
     shim streams worker logs in real time`.
   - Stage explicit paths: `end_of_line/_pty_spawn_shim.py`,
     `end_of_line/dispatch.py`, `tests/test_pty_spawn_shim.py`,
     `tests/test_dispatch.py`, `docs/reference.md`, `docs/architecture.md`
     (+ master if findings logged).
   - After the commit:
     - `clu verify --plan line-buffer-worker-output --phase pty-shim --token <T>`
     - `clu attest --simplify --plan line-buffer-worker-output --phase pty-shim --token <T>`
   - `clu complete --plan line-buffer-worker-output --phase pty-shim --token <T>`.
   - Completion summary MUST note: the shim takes effect for dispatches
     AFTER this plan ships (installed clu tracks main); the first post-ship
     plan's logs are the production proof.

## Failure modes to watch

- **Your own session predates the shim** — your dispatch ran direct Popen;
  only the scratch smoke exercises shim code. Don't claim production proof
  from your own runtime.
- **Drain thread vs signal-re-raise ordering**: finish draining (EOF) and
  flush fd 1 BEFORE re-raising the child's signal on yourself, or tail
  bytes are lost exactly like the bug this plan fixes.
- **shlex/quoting**: the cmd string passes as ONE argv element to the shim
  and then to `sh -c` — no re-quoting needed; do NOT shlex.split it.
- **ANSI regex over-stripping**: strip terminal control sequences, not
  arbitrary text containing literal "ESC" words; unit-test against the
  spike's real byte sample.
- **Sandbox suite caveat**: judge green by `clu verify`.
