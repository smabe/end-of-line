"""Tests for the PTY spawn shim (line-buffer-worker-output / pty-shim).

Two layers:

- `strip_ansi` is a pure byte->byte function; unit-tested against a sample
  modelled on the 2026-06-10 spike (CSI colour + private sequences, OSC
  with both BEL and ST terminators, Fe/Fp single-char escapes, CRLF).
- The shim's process behaviour (real os.openpty, rc propagation, signal
  re-raise, incremental delivery) is exercised by spawning the module as a
  subprocess and observing its stdout pipe + returncode — the same surface
  the dispatcher's outer Popen sees.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from end_of_line._pty_spawn_shim import strip_ansi

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Invoke the shim by file path (as dispatch does) — stdlib-only + standalone,
# so it needs no package on sys.path and no particular cwd.
_SHIM = str(_REPO_ROOT / "end_of_line" / "_pty_spawn_shim.py")


def _pty_available() -> bool:
    """True iff os.openpty() works here.

    Seatbelt sandboxes (e.g. the in-session Bash sandbox) deny PTY
    allocation with EPERM, so the shim's openpty-failure fallback runs
    instead of the real pty path. The pty-integration tests below are
    meaningless without a real pty, so they skip there. `clu verify` runs
    sandbox-exempt, so the pty path IS exercised under the authoritative run.
    """
    try:
        master, slave = os.openpty()
    except OSError:
        return False
    os.close(master)
    os.close(slave)
    return True


_PTY_AVAILABLE = _pty_available()


class StripAnsiTestCase(unittest.TestCase):
    """Pure-function coverage — no I/O, no pty."""

    def test_strips_csi_osc_singlechar_and_folds_crlf(self) -> None:
        sample = (
            b"\x1b[31mRED\x1b[0m\r\n"  # CSI colour around text + CRLF
            b"\x1b[?25lhidden\x1b[?25h\r\n"  # CSI private (hide/show cursor)
            b"\x1b]0;window title\x07kept\r\n"  # OSC, BEL-terminated
            b"\x1b]0;st-title\x1b\\also\r\n"  # OSC, ST-terminated (ESC \\)
            b"\x1bMrev\x1b=app\x1b>norm\r\n"  # Fe single-char + Fp DECKPAM/DECKPNM
            b"plain line\n"  # already clean LF — untouched
        )
        expected = (
            b"RED\n"
            b"hidden\n"
            b"kept\n"
            b"also\n"
            b"revappnorm\n"
            b"plain line\n"
        )
        self.assertEqual(strip_ansi(sample), expected)

    def test_leaves_literal_ESC_word_alone(self) -> None:
        # The word "ESC" (three ASCII letters) is not the ESC byte (0x1b);
        # over-stripping arbitrary text is the failure mode to avoid.
        text = b"press ESC to cancel; the rate limit is 5"
        self.assertEqual(strip_ansi(text), text)

    def test_empty_and_plain_passthrough(self) -> None:
        self.assertEqual(strip_ansi(b""), b"")
        self.assertEqual(strip_ansi(b"no escapes here\n"), b"no escapes here\n")


class ShimProcessTestCase(unittest.TestCase):
    """Spawn the shim as the dispatcher would and observe its real output."""

    def _run_shim(
        self, cmd: str, *, timeout: float = 10.0
    ) -> tuple[int | None, bytes]:
        proc = subprocess.Popen(
            [sys.executable, _SHIM, "--", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out

    def test_rc_zero_propagates(self) -> None:
        rc, _ = self._run_shim("exit 0")
        self.assertEqual(rc, 0)

    def test_rc_nonzero_propagates(self) -> None:
        rc, _ = self._run_shim("exit 7")
        self.assertEqual(rc, 7)

    def test_signal_death_propagates_as_negative_rc(self) -> None:
        # Child SIGTERMs itself; the shim must re-raise the signal on itself
        # so the OUTER returncode reads the POSIX -15 the fast-fail branch
        # expects (never sys.exit(-15), which truncates to & 0xFF).
        rc, _ = self._run_shim("kill -TERM $$")
        self.assertEqual(rc, -15)

    @unittest.skipUnless(_PTY_AVAILABLE, "os.openpty not permitted (sandbox)")
    def test_end_to_end_crlf_folded(self) -> None:
        # ONLCR cleared on the slave + CRLF fold in the drain => clean LF.
        # Requires a real pty: the fallback (execvp) path emits raw bytes.
        rc, out = self._run_shim(r"printf 'a\r\nb\r\n'")
        self.assertEqual(rc, 0)
        self.assertEqual(out, b"a\nb\n")

    @unittest.skipUnless(_PTY_AVAILABLE, "os.openpty not permitted (sandbox)")
    def test_incremental_delivery_before_exit(self) -> None:
        # The whole point: a line must reach the log BEFORE the worker exits.
        # If output were block-buffered until exit, both lines would arrive
        # together; the ~0.4s gap proves X flushed before Y was produced.
        proc = subprocess.Popen(
            [sys.executable, _SHIM, "--", "echo X; sleep 0.4; echo Y"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert proc.stdout is not None
            line1 = proc.stdout.readline()
            t1 = time.monotonic()
            line2 = proc.stdout.readline()
            t2 = time.monotonic()
        finally:
            proc.wait(timeout=5)
        self.assertEqual(line1, b"X\n")
        self.assertEqual(line2, b"Y\n")
        self.assertGreaterEqual(t2 - t1, 0.3)


if __name__ == "__main__":
    unittest.main()
