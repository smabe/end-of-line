"""`clu notify-test` smoke command — fires test notifications through configured
channels and reports per-channel status.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import notify
from end_of_line.cli import main
from end_of_line.config import CONFIG_FILENAME
from tests import isolate_registry


class NotifyTestCommandTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        isolate_registry(self, Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _notify_test(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "notify-test",
                    "--project",
                    str(self.project),
                    *extra,
                ]
            )
        return rc, out.getvalue(), err.getvalue()

    def _write_config(self, data: dict) -> None:
        (self.project / CONFIG_FILENAME).write_text(json.dumps(data))

    def _mock_notifier(self, kind: str, *, send_return=None, send_side_effect=None):
        mock_notifier = mock.MagicMock()
        if send_side_effect is not None:
            mock_notifier.send.side_effect = send_side_effect
        else:
            mock_notifier.send.return_value = send_return
        mock_cls = mock.MagicMock()
        mock_cls.from_spec.return_value = mock_notifier
        return mock_cls, mock_notifier

    # --- no channels -------------------------------------------------------

    def test_notify_test_no_channels_configured(self) -> None:
        # No .orchestrator.json → no channels.
        rc, _out, err = self._notify_test()
        self.assertNotEqual(rc, 0)
        self.assertIn("No channels configured", err)

    # --- single-channel filter ---------------------------------------------

    def test_notify_test_fires_one_channel_by_kind(self) -> None:
        self._write_config(
            {
                "notify": {
                    "channels": [
                        {"kind": "imessage", "to": "+15550001234"},
                    ]
                }
            }
        )
        mock_cls, mock_notifier = self._mock_notifier("imessage", send_return="msg-1")
        with mock.patch.dict(notify._NOTIFIER_REGISTRY, {"imessage": mock_cls}):
            rc, out, _err = self._notify_test("--channel", "imessage")
        self.assertEqual(rc, 0)
        mock_notifier.send.assert_called_once()
        self.assertIn("imessage: OK", out)

    # --- all channels without filter ---------------------------------------

    def test_notify_test_fires_all_channels_when_no_filter(self) -> None:
        self._write_config(
            {
                "notify": {
                    "channels": [
                        {"kind": "imessage", "to": "+1"},
                        {"kind": "discord", "bot_token": "tok", "user_id": "uid"},
                    ]
                }
            }
        )
        im_cls, im_notifier = self._mock_notifier("imessage")
        dc_cls, dc_notifier = self._mock_notifier("discord")
        with mock.patch.dict(
            notify._NOTIFIER_REGISTRY,
            {
                "imessage": im_cls,
                "discord": dc_cls,
            },
        ):
            rc, _out, _err = self._notify_test()
        self.assertEqual(rc, 0)
        im_notifier.send.assert_called_once()
        dc_notifier.send.assert_called_once()

    # --- disabled channels -------------------------------------------------

    def test_notify_test_skips_disabled_channels(self) -> None:
        self._write_config(
            {
                "notify": {
                    "channels": [
                        {"kind": "imessage", "to": "+1", "enabled": True},
                        {"kind": "discord", "bot_token": "tok", "user_id": "uid", "enabled": False},
                    ]
                }
            }
        )
        im_cls, im_notifier = self._mock_notifier("imessage")
        # Discord mock shouldn't be called, so no need to register it.
        with mock.patch.dict(notify._NOTIFIER_REGISTRY, {"imessage": im_cls}):
            rc, out, _err = self._notify_test()
        self.assertEqual(rc, 0)
        im_notifier.send.assert_called_once()
        self.assertIn("discord: SKIPPED (disabled)", out)

    # --- per-channel status reporting -------------------------------------

    def test_notify_test_reports_per_channel_status(self) -> None:
        self._write_config(
            {
                "notify": {
                    "channels": [
                        {"kind": "imessage", "to": "+1"},
                        {"kind": "discord", "bot_token": "tok", "user_id": "uid"},
                    ]
                }
            }
        )
        im_cls, _im_notifier = self._mock_notifier("imessage", send_return=None)
        dc_cls, _dc_notifier = self._mock_notifier(
            "discord",
            send_side_effect=Exception("HTTPError 401"),
        )
        with mock.patch.dict(
            notify._NOTIFIER_REGISTRY,
            {
                "imessage": im_cls,
                "discord": dc_cls,
            },
        ):
            rc, out, _err = self._notify_test()
        # notify-test doesn't exit non-zero on channel failure — it reports
        # per-channel and continues.
        self.assertEqual(rc, 0)
        self.assertIn("imessage: OK", out)
        self.assertIn("discord: FAILED", out)
