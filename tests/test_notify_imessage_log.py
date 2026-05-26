"""`_osascript_send` captures AppleScript stderr to a log file (#49).

Previously stderr was DEVNULL — Automation permission denials and
buddy-lookup failures vanished silently, so a missed notification was
undebuggable. The fix replaces DEVNULL with an append-mode file handle
at `$XDG_CONFIG_HOME/clu/imessage.log` (default `~/.config/clu/imessage.log`),
keeping the fire-and-forget Popen shape (no synchronous wait).
"""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from end_of_line import notify_imessage
from tests import CluTestCase


class IMessageLogPathTestCase(CluTestCase):
    """The log path resolves through XDG_CONFIG_HOME."""

    def test_log_path_under_xdg_config_home(self) -> None:
        self.assertEqual(
            notify_imessage.imessage_log_path(),
            self.tmp_path / "clu" / "imessage.log",
        )


class OsascriptSendLogTestCase(CluTestCase):
    """`_osascript_send` no longer discards stderr."""

    def test_popen_called_with_stderr_pointing_at_log_file(self) -> None:
        captured = {}

        class _FakeHandle:
            pass

        def _fake_popen(*args, **kwargs):
            # Snapshot kwargs so the test can inspect what would have
            # gone to the real subprocess.
            captured["args"] = args
            captured["kwargs"] = kwargs
            # Simulate osascript writing to stderr — proves the fd is
            # writable and pointed at the log file.
            kwargs["stderr"].write(b"simulated osascript failure\n")
            kwargs["stderr"].flush()
            return _FakeHandle()

        with mock.patch.object(subprocess, "Popen", side_effect=_fake_popen):
            notify_imessage._osascript_send("you@example.com", "hi")

        # stderr argument must be a writable file, NOT subprocess.DEVNULL.
        self.assertNotEqual(captured["kwargs"]["stderr"], subprocess.DEVNULL)
        # stdout stays DEVNULL — we don't capture happy-path osascript noise.
        self.assertEqual(captured["kwargs"]["stdout"], subprocess.DEVNULL)
        # start_new_session preserved so the cron tick stays detached.
        self.assertTrue(captured["kwargs"]["start_new_session"])
        # The simulated error landed in the log file.
        log_path = notify_imessage.imessage_log_path()
        self.assertIn(
            b"simulated osascript failure",
            log_path.read_bytes(),
        )

    def test_log_file_parent_dir_is_created_lazily(self) -> None:
        # Fresh XDG dir — clu directory doesn't exist yet.
        clu_dir = self.tmp_path / "clu"
        self.assertFalse(clu_dir.exists())

        def _fake_popen(*args, **kwargs):
            kwargs["stderr"].close()  # release our test's grasp politely

            class _FakeHandle:
                pass

            return _FakeHandle()

        with mock.patch.object(subprocess, "Popen", side_effect=_fake_popen):
            notify_imessage._osascript_send("you@example.com", "hi")

        self.assertTrue(clu_dir.exists())
        self.assertTrue((clu_dir / "imessage.log").exists())

    def test_appends_across_calls(self) -> None:
        def _fake_popen(*args, **kwargs):
            kwargs["stderr"].write(b"call\n")
            kwargs["stderr"].flush()

            class _FakeHandle:
                pass

            return _FakeHandle()

        with mock.patch.object(subprocess, "Popen", side_effect=_fake_popen):
            notify_imessage._osascript_send("you@example.com", "one")
            notify_imessage._osascript_send("you@example.com", "two")

        log_path = notify_imessage.imessage_log_path()
        self.assertEqual(log_path.read_bytes().count(b"call\n"), 2)

    def test_does_not_synchronously_wait(self) -> None:
        """Happy-path Popen kwargs must not include a `wait` or `communicate`
        call from `_osascript_send` itself — fire-and-forget semantics are
        load-bearing for cron-tick latency."""
        seen = []

        class _FakeHandle:
            def wait(self, *a, **kw):
                seen.append("wait")

            def communicate(self, *a, **kw):
                seen.append("communicate")

        def _fake_popen(*args, **kwargs):
            return _FakeHandle()

        with mock.patch.object(subprocess, "Popen", side_effect=_fake_popen):
            notify_imessage._osascript_send("you@example.com", "hi")

        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
