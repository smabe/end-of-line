"""PTY spawn shim — stream worker logs in real time so wedges leave a trail.

Why this exists
---------------
`claude --print` (a Node binary) block-buffers stdout (~4–8 KB) whenever
``isatty(1)`` is false — i.e. whenever stdout is a pipe or a regular file
(anthropics/claude-code#25670; libuv ignores ``stdbuf`` and nodejs/node#6379
means the usual line-buffering coercions don't apply). clu's dispatcher
points the worker's stdout at a per-token log file, so a worker that wedges
mid-stream leaves a 0-byte log — empty exactly when the post-mortem needs it
(the 2026-05-26 wedge incident). Allocating a pseudo-terminal flips Node's
isTTY path, so output streams line-by-line instead.

The drain must live in a long-lived process: an in-supervisor PTY drain dies
with the cron tick (~seconds), after which the master closes and the worker's
writes either fail or are silently discarded — see the v1 parking note in
``plans/archive/line-buffer-worker-output/v1-2026-05-26-diagnosis.md``. This
module IS that long-lived intermediary. The dispatcher launches it as the
phase worker (so ``claim.pid`` is the shim's pid) via::

    [sys.executable, "<abs path to this file>", "--", <cmd>]

It is invoked by absolute file path, NOT ``-m end_of_line._pty_spawn_shim``:
the worker's cwd is the plan worktree, where the package isn't importable
unless clu happens to be pip-installed into ``sys.executable``. This module is
stdlib-only and self-contained, so a path invocation is cwd-independent and
equivalent (the slug-bearing cmd still rides in argv for the cmdline marker).
``<cmd>`` is the rendered operator command STRING as a single argv element.
The shim runs ``sh -c <cmd>`` with the PTY slave as the child's
stdout/stderr, drains the master continuously, normalizes the bytes (ANSI
control sequences stripped, CRLF folded to LF), and writes them to fd 1 —
which the dispatcher has redirected to the log file.

Platform contract
-----------------
Verified against docs.python.org/3.11 (pty/os/subprocess/termios), CPython
``Lib/pty.py`` (the ``_copy`` drain loop), Apple Developer Forums thread
663632, and a live spike against claude 2.1.170 on macOS (2026-06-10):

- **EOF differs by platform.** Reading the master after the child exits
  returns ``b""`` on macOS but raises ``OSError`` (EIO) on Linux. CPython's
  own ``pty._copy`` treats both as EOF; so do we.
- **The parent must close its slave copy** after spawning the child, or the
  master never sees EOF (the kernel keeps the slave side open).
- **Drain continuously with ``select``; never sleep-then-read.** macOS can
  discard pending PTY bytes once the child exits, so a poll that races the
  child's exit loses the tail — the exact bug this fixes.
- **A fresh PTY has a 0x0 winsize.** Initialize it (TIOCSWINSZ 80x24) so
  isatty-gated probes see a sane terminal.
- **Clearing ONLCR on the slave termios** stops the line discipline from
  translating ``\\n`` → ``\\r\\n`` — kills CRLF at the source, cheaper than
  folding every newline in the drain (we still fold defensively).
- **Signal deaths must propagate as the POSIX negative rc** the dispatcher's
  fast-fail branch expects. Restore ``SIG_DFL`` and re-raise the signal on
  self (``os.kill(getpid(), sig)``) rather than ``sys.exit(-N)``, which is
  undefined and truncates to ``& 0xFF``.

ANSI stripping operates per ``os.read`` chunk. In practice a worker's writes
land whole (PTY output is not line-buffered by the discipline, but a single
``write()`` of a coloured line fits one kernel PTY buffer), so escape
sequences arrive intact; a rare cross-chunk split leaks a few cosmetic bytes,
never corruption, and the plain-text systemic-failure signatures in
``dispatch.py`` match on alphabetic keywords unaffected by stray escape bytes.

Stdlib only — no platform branches beyond the dual EOF handling above.
"""

from __future__ import annotations

import fcntl
import os
import re
import select
import signal
import struct
import subprocess
import sys
import termios

# CSI / OSC / Fe / Fp / Fs / nF escape sequences. Matched on the actual ESC
# byte (0x1b), so the ASCII word "ESC" in normal text is never touched.
# Ordering matters: CSI and OSC come before the single-/intermediate-final
# branch so `ESC [` and `ESC ]` route to the right alternative.
_ANSI_RE = re.compile(
    rb"\x1b(?:"
    rb"\[[0-?]*[ -/]*[@-~]"  # CSI: ESC [ params intermediates final
    rb"|\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: ESC ] ... (BEL | ST)
    rb"|[ -/]*[0-~]"  # nF/Fp/Fe/Fs: ESC [intermediates] final
    rb")"
)

_READ_SIZE = 65536

# Once the worker has exited, how long the master may stay quiet before the
# drain gives up waiting for EOF. EOF needs EVERY slave fd closed; a detached
# descendant that inherited the slave (none should — the heartbeat daemon
# dup2's its stdio off it — but we don't depend on that) would otherwise hang
# the drain, and the shim IS claim.pid, so the claim would stall until lease
# expiry. While the worker is still alive this timeout just re-polls — a silent
# worker (long API call) never trips it.
_POST_EXIT_QUIET_SEC = 2.0


def strip_ansi(data: bytes) -> bytes:
    """Remove ANSI terminal-control sequences and fold CRLF → LF.

    Pure: same bytes in → same bytes out, no I/O. Strips escapes first so a
    sequence sitting between a CR and an LF can't survive the fold.
    """
    return _ANSI_RE.sub(b"", data).replace(b"\r\n", b"\n")


def _write_all(data: bytes) -> None:
    """Write every byte to fd 1, tolerating short writes; unbuffered."""
    while data:
        try:
            n = os.write(1, data)
        except OSError:
            return
        data = data[n:]


def _drain(master_fd: int, proc: subprocess.Popen[bytes]) -> None:
    """Copy master → fd 1 until EOF, normalizing each chunk.

    Treats both ``b""`` (macOS) and ``OSError`` (Linux EIO) as EOF, per the
    platform contract — the normal stop. Reads as soon as ``select`` reports
    data (never sleep-then-read) so macOS can't discard the tail at child exit.

    The ``select`` carries a timeout used ONLY as a hang-guard: if the worker
    has already exited (``proc.poll()`` set) and the master has been quiet for
    one interval, a detached descendant must still be holding the slave open —
    stop rather than block forever (the shim is ``claim.pid``; blocking would
    stall the claim until lease expiry). A still-running worker that's merely
    silent re-polls and keeps waiting.
    """
    while True:
        try:
            ready, _, _ = select.select([master_fd], [], [], _POST_EXIT_QUIET_SEC)
        except OSError:
            return
        if ready:
            try:
                chunk = os.read(master_fd, _READ_SIZE)
            except OSError:
                return  # Linux raises EIO at child exit — that's EOF.
            if not chunk:
                return  # macOS returns b"" at EOF.
            out = strip_ansi(chunk)
            if out:
                _write_all(out)
            continue
        # No data this interval: only stop if the worker is already gone.
        if proc.poll() is not None:
            return


def _run_under_pty(master: int, slave: int, cmd: str) -> int:
    """Run ``sh -c cmd`` over an already-open PTY pair; return subprocess rc.

    The returned rc follows ``subprocess.Popen.returncode`` semantics, which
    are identical to ``os.waitstatus_to_exitcode``: ``>= 0`` for a normal
    exit code, ``-N`` for death by signal N. Caller propagates it. The PTY is
    opened by the caller so the openpty-failure fallback can run BEFORE any
    child is spawned — never re-executing a command that already started.
    """
    # Fresh PTYs are 0x0; give isatty-gated probes a sane 80x24 (rows, cols).
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    except OSError:
        pass

    # Clear ONLCR (oflag) so \n is not translated to \r\n on the way out.
    try:
        attrs = termios.tcgetattr(slave)
        attrs[1] &= ~termios.ONLCR
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
    except (termios.error, OSError):
        pass

    proc = subprocess.Popen(
        ["sh", "-c", cmd],
        stdin=subprocess.DEVNULL,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    # The child holds its own slave fds now; the parent MUST drop its copy or
    # the master never EOFs.
    os.close(slave)

    # Drain to completion (EOF == child closed all slave fds == child exited)
    # BEFORE reaping, so the tail is flushed before any signal re-raise.
    _drain(master, proc)
    try:
        os.close(master)
    except OSError:
        pass

    return proc.wait()


def _exit_with(rc: int) -> None:
    """Propagate the child's rc as this process's exit, signals included."""
    if rc < 0:
        sig = -rc
        # Restore default disposition so the re-raise actually terminates us
        # (e.g. SIGINT would otherwise raise KeyboardInterrupt). SIGKILL /
        # SIGSTOP can't be reset — os.kill still delivers the fatal default.
        try:
            signal.signal(sig, signal.SIG_DFL)
        except (OSError, ValueError):
            pass
        os.kill(os.getpid(), sig)
        # Fatal signals don't return; if somehow ignored, fall through.
    sys.exit(rc & 0xFF if rc >= 0 else 1)


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    # Conventionally invoked as `... -- <cmd>`; tolerate a missing separator.
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        sys.stderr.write("clu pty-shim: no command given\n")
        sys.exit(2)
    cmd = args[0]

    try:
        master, slave = os.openpty()
    except OSError as exc:
        # PTY exhaustion: degrade to today's block-buffered logging rather
        # than a dead dispatch. execvp preserves this pid (= claim pid) and
        # the child's rc. Scoped to openpty so a child is never double-run.
        sys.stderr.write(f"clu pty-shim: pty unavailable ({exc}); running without pty\n")
        sys.stderr.flush()
        os.execvp("sh", ["sh", "-c", cmd])  # never returns on success

    _exit_with(_run_under_pty(master, slave, cmd))


if __name__ == "__main__":
    main()
