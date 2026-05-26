"""Protocol-extraction tests — Notifier, InboundPoller, route_reply in notify_base."""

from __future__ import annotations

import unittest
from pathlib import Path

from end_of_line.notify_base import (
    InboundPoller,
    Notifier,
    OpenBlocker,
    Reply,
    route_reply,
)
from end_of_line.notify_imessage import IMessageNotifier
from end_of_line.notify_imessage_inbound import IMessageInboundPoller
from end_of_line import notify


class NotifierProtocolTestCase(unittest.TestCase):
    def test_notifier_is_runtime_checkable_protocol(self) -> None:
        notifier = IMessageNotifier(to="+15551234567")
        self.assertIsInstance(notifier, Notifier)

    def test_imessage_notifier_kind_name_is_imessage(self) -> None:
        self.assertEqual(IMessageNotifier(to="+15551234567").kind_name, "imessage")


class InboundPollerProtocolTestCase(unittest.TestCase):
    def test_inbound_poller_is_runtime_checkable_protocol(self) -> None:
        poller = IMessageInboundPoller()
        self.assertIsInstance(poller, InboundPoller)


class RouteReplyBaseTestCase(unittest.TestCase):
    def test_route_reply_lives_in_notify_base(self) -> None:
        ob = OpenBlocker(
            project_root=Path("/p"),
            plan_slug="plan-slug",
            blocker_id="q-1",
            options_count=2,
            last_notified_at="",
        )
        result = route_reply("plan-slug 1", [ob])
        self.assertIsInstance(result, Reply)
        self.assertEqual(result.target, ob)
        self.assertEqual(result.answer, "1")


class NotifierRegistryTestCase(unittest.TestCase):
    def test_notify_registry_contains_imessage(self) -> None:
        self.assertIs(notify._NOTIFIER_REGISTRY["imessage"], IMessageNotifier)


if __name__ == "__main__":
    unittest.main()
