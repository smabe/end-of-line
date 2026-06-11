# line-buffer-worker-output — PTY shim so worker logs survive wedges (v2)

Worker stdout reaches the log only at process exit: `claude --print`
block-buffers ~4-8KB when stdout is a pipe/file (anthropics/claude-code#25670;
libuv ignores `stdbuf`, nodejs/node#6379), so a wedged worker leaves a 0-byte
log exactly when the post-mortem needs it (2026-05-26 incident). A PTY flips
Node's isTTY path and output streams in real time.

Re-authored 2026-06-10 from the parked 2026-05-26 plan (diagnosis record:
`plans/archive/line-buffer-worker-output/v1-2026-05-26-diagnosis.md`). The v1
empirical results stand and were re-validated this session:

- **Fresh spike (claude 2.1.170 under `os.openpty`)**: `--print` stays
  print-mode and exits clean under a PTY, but emits 17 ANSI terminal-control
  sequences and CRLF line endings — the drain must normalize.
- **`script(1)` stays rejected** (v1 spike: BSD/util-linux flag drift,
  socket-stdin `tcgetattr` failure, `^D` artifacts).
- **A long-lived shim process is required** — an in-process PTY drain dies
  with the cron-tick supervisor (PTY-constraint memory; v1 parking reason).
- **The v1 cross-plan blocker is confirmed in shipped code**:
  `_emit_worker_idle` samples `%cpu` on `claim.pid` alone (supervisor.py:486)
  — a shim at claim.pid would read ~0% and false-fire WORKER_IDLE. Phase 1
  fixes that first.
- **Python pty contract** (docs.python.org 3.11 + CPython source, cited in
  the pty-shim sub-plan): EOF is `b""` on macOS but `OSError` (EIO) on Linux
  — handle both (CPython's own `pty._copy` does); parent must close its slave
  copy or the master never sees EOF; drain continuously (macOS can discard
  pending PTY bytes at child exit); signal deaths propagate by re-raising on
  self, never `sys.exit(-N)`; fresh ptys have 0x0 winsize — initialize.

## Locked design decisions

### Phase 1 — idle-treewalk (prerequisite; correct on its own)
- Fix `_emit_worker_idle` (supervisor.py:455-523): collect descendant pids
  via `walk_worker_tree` (supervisor.py:135-167, already proven in
  `_emit_stuck_tool` at :403), then ONE `ps -p <pid,pid,...> -o %cpu=` and
  SUM. Instantaneous %cpu stays the metric — `append_cpu_sample` and
  `worker_idle_window_satisfied` (state.py:989-1012) are untouched. Note:
  tree `Descendant` carries cumulative `cpu_seconds`, NOT %cpu — don't
  conflate; the tree is only the pid source.
- Independently correct today: worker children's CPU is currently invisible
  to the idle check.

### Phase 2 — pty-shim
- New `end_of_line/_pty_spawn_shim.py`; dispatch invokes
  `[sys.executable, "-m", "end_of_line._pty_spawn_shim", "--", <cmd string>]`
  (outer Popen drops `shell=True`; the shim runs the operator's command
  string through `sh -c` itself, preserving template semantics). The cmd
  string in the shim argv keeps the plan-slug cmdline marker satisfied.
- Shim contract: `os.openpty()`; TIOCSWINSZ 80x24; ONLCR cleared on slave
  termios (kills CRLF at source); parent closes slave copy post-spawn;
  `select` + `os.read` drain treating BOTH `b""` and `OSError` as EOF;
  ANSI escape sequences stripped before write (spike evidence; keeps
  `_match_systemic_signature` — dispatch.py:80-88, plain-text unanchored —
  and humans reading clean logs); writes to fd 1 unbuffered.
- Exit codes: `os.waitstatus_to_exitcode`; normal → `sys.exit(n)`; signal
  death → re-raise on self (`SIG_DFL` + `os.kill(getpid(), sig)`) so the
  dispatcher's `Popen.returncode` shows POSIX `-N`. Fast-fail window (0.5s,
  dispatch.py:32,342) tolerates ~100ms shim overhead — verified live in
  acceptance.
- Fallback INSIDE the shim: `openpty` failure (PTY exhaustion) → exec the
  command directly + one stderr warning line. Degraded to today's behavior,
  never a dead dispatch.
- claim.pid = shim pid: all eight consumers audited 2026-06-10 and
  compatible once phase 1 lands — `os.kill` liveness ✓, cmdline marker ✓,
  stuck-tool tree walk ✓ (worker is a descendant), pgid=pid via existing
  `start_new_session` ✓ (`_stamp_pid` dispatch.py:719-743),
  heartbeat-daemon `--worker-pid` default ✓ (shim exits when worker exits),
  both reap paths ✓.
- Live smoke in-phase (scratch project, branch code, #90-smoke style): real
  `claude --print` through the shim; log content appears BEFORE exit,
  ANSI-free, LF-only.

## Non-goals
- **Repair workers stay unwrapped** — synchronous, short-lived, no
  claim/pid stamping (dispatch.py:426-435); the wedge-diagnosis problem
  doesn't exist there.
- **No config knob** — always-on with the in-shim fallback; a toggle is
  speculative generality.
- **No platform branches beyond dual EOF handling** — stdlib-only shim.
- **top/serve untouched** — they read transcripts, not these logs.

## Files touched
- `end_of_line/supervisor.py` — P1 — `_emit_worker_idle` descendant-CPU sum
- `tests/test_supervisor_worker_idle.py` — P1 — descendant cases on the
  existing `ps_output` seam
- `end_of_line/_pty_spawn_shim.py` — P2 NEW — the shim (~80 LOC; docstring
  carries the platform contract + citations)
- `end_of_line/dispatch.py` — P2 — wrap at the phase-worker Popen
  (dispatch.py:312-318); repair site untouched. API hotspot: outer Popen
  loses `shell=True` for phase dispatch.
- `tests/test_pty_spawn_shim.py` NEW, `tests/test_dispatch.py` — P2
- `docs/reference.md` — P1, P2; `docs/architecture.md` process-model note — P2

## Per-phase done checklist
- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests` (judge by
  `clu verify` — the in-sandbox run has ~30 known environment failures).
- Structured commit format; stage explicit paths.
- **Post-commit attestations:** `clu verify` then `clu attest --simplify`
  (each with `--plan line-buffer-worker-output --phase <id> --token <T>`).
- `clu complete --plan line-buffer-worker-output --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| idle-treewalk | `line-buffer-worker-output-idle-treewalk.md` | `_emit_worker_idle` sums descendant %cpu | 1h |
| pty-shim | `line-buffer-worker-output-pty-shim.md` | shim module + dispatch wrap + live smoke | 2.5h |

## Findings log

_Empty at plan time. Workers append one dated bullet per cross-phase finding
with file:line. The v1 empirical record lives in
`plans/archive/line-buffer-worker-output/v1-2026-05-26-diagnosis.md`._
