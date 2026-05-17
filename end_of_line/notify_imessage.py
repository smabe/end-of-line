"""iMessage outbound notification backend.

Implements Notifier via osascript. Uses argv form so user-controlled text in
the body never touches the AppleScript source.
"""
from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ChannelSpec

# osascript-friendly AppleScript: argv carries the handle + body so we
# don't have to escape user-controlled text into the script source.
_APPLESCRIPT = """
on run argv
    set toHandle to item 1 of argv
    set body to item 2 of argv
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy toHandle of targetService
        send body to targetBuddy
    end tell
end run
""".strip()


def _osascript_send(to: str, body: str) -> None:
    """Fire-and-forget — don't block the cron tick on a hung Messages.app."""
    subprocess.Popen(
        ["osascript", "-e", _APPLESCRIPT, "--", to, body],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class IMessageNotifier:
    kind_name = "imessage"

    def __init__(self, to: str) -> None:
        self.to = to

    @classmethod
    def from_spec(cls, channel: "ChannelSpec") -> "IMessageNotifier":
        return cls(channel.params["to"])

    def send(
        self,
        kind: str,
        body: str,
        *,
        plan_slug: str,
        blocker_id: str | None = None,
    ) -> str | None:
        _osascript_send(self.to, body)
        return None
