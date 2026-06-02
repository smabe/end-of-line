"""iMessage outbound notification backend.

Implements Notifier via osascript. Uses argv form so user-controlled text in
the body never touches the AppleScript source.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ._xdg_guard import assert_xdg_safe, clu_config_dir

if TYPE_CHECKING:
    from .config import ChannelSpec

log = logging.getLogger(__name__)

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


def imessage_log_path() -> Path:
    """Append-mode log file capturing osascript stderr.

    Lives at `$XDG_CONFIG_HOME/clu/imessage.log` (default
    `~/.config/clu/imessage.log`). AppleScript runtime errors
    (Automation permission denied, buddy lookup failed, Messages.app
    not running) land here so a missed iMessage isn't undebuggable.
    """
    path = clu_config_dir() / "imessage.log"
    assert_xdg_safe(path)
    return path


def _osascript_send(to: str, body: str) -> None:
    """Fire-and-forget — don't block the cron tick on a hung Messages.app.

    osascript stderr is appended to `imessage_log_path()` so AppleScript
    failures (Automation permission denied, etc.) are debuggable. Previously
    stderr was DEVNULL and all failures vanished silently (#49).
    """
    log_path = imessage_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Popen dups the underlying fd into the child, so closing our copy
    # after Popen returns is safe — the child still writes to its dup.
    log = open(log_path, "ab")
    try:
        subprocess.Popen(
            ["osascript", "-e", _APPLESCRIPT, "--", to, body],
            stdout=subprocess.DEVNULL,
            stderr=log,
            start_new_session=True,
        )
    finally:
        log.close()


class IMessageNotifier:
    kind_name = "imessage"

    def __init__(self, to: str) -> None:
        self.to = to

    @classmethod
    def from_spec(cls, channel: ChannelSpec) -> IMessageNotifier:
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
        # Local import keeps the outbound module from depending on the
        # inbound module at import time — the dependency is only
        # exercised when an iMessage actually fires.
        from .notify_imessage_inbound import append_outbound_mark

        try:
            append_outbound_mark(self.to, time.time())
        except Exception as exc:
            log.warning("imessage: failed to record outbound mark: %s", exc)
        return None
