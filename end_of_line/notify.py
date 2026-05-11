"""Notification stubs.

For MVP, notifications print to stderr. Wire real channels (osascript
notification, Pushover, iMessage) by replacing `notify` with a dispatcher
that reads ProjectConfig.notify.
"""
from __future__ import annotations

import sys


def notify(message: str, kind: str = "info") -> None:
    print(f"[notify:{kind}] {message}", file=sys.stderr)
