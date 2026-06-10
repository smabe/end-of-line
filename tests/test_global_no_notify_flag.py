"""`clu --no-notify` global flag — suppresses outbound transport without
affecting inbox writes or clu-watch.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import notify
from end_of_line.cli import main
from end_of_line.config import ChannelSpec, NotifySpec
from tests import capture_inbox_writer, isolate_registry


class GlobalNoNotifyFlagTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        isolate_registry(self, self.tmp_path)
        # Always reset suppress state so tests don't leak into each other.
        notify.set_global_suppress(False)
        self.addCleanup(notify.set_global_suppress, False)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- argparse integration -----------------------------------------------

    def test_global_no_notify_flag_recognized_by_argparse(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--no-notify", "list"])
        # "list" with empty registry returns 0 or 1 — either is fine.
        # The important thing: no "unrecognized arguments" error.
        self.assertNotIn("unrecognized arguments", err.getvalue())
        self.assertNotIn("error: argument", err.getvalue())

    # --- notify module suppress logic ---------------------------------------

    def test_global_no_notify_short_circuits_dispatch(self) -> None:
        spec = NotifySpec(channels=(ChannelSpec(kind="imessage", params={"to": "+1"}),))
        mock_cls = mock.MagicMock()
        mock_notifier = mock.MagicMock()
        mock_cls.from_spec.return_value = mock_notifier

        notify.set_global_suppress(True)
        with mock.patch.dict(notify._NOTIFIER_REGISTRY, {"imessage": mock_cls}):
            result = notify.notify(spec, notify.KIND_BLOCKER, "body")

        self.assertFalse(result)
        mock_cls.from_spec.assert_not_called()
        mock_notifier.send.assert_not_called()

    def test_global_no_notify_does_not_affect_inbox_writes(self) -> None:
        inbox_calls: list[dict] = []
        spec = NotifySpec(channels=(ChannelSpec(kind="imessage", params={"to": "+1"}),))
        mock_cls = mock.MagicMock()
        mock_cls.from_spec.return_value.send.return_value = None

        notify.set_global_suppress(True)
        with mock.patch.dict(notify._NOTIFIER_REGISTRY, {"imessage": mock_cls}):
            result = notify.notify(
                spec,
                notify.KIND_BLOCKER,
                "body",
                plan_slug="my-plan",
                project_root="/tmp/proj",
                inbox_writer=capture_inbox_writer(inbox_calls),
            )

        self.assertFalse(result)
        # Inbox write must still happen even when outbound is suppressed.
        self.assertEqual(len(inbox_calls), 1)
        self.assertEqual(inbox_calls[0]["type"], notify.KIND_BLOCKER)
        # Channel send must NOT happen.
        mock_cls.from_spec.assert_not_called()
