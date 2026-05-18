"""Inbound iMessage poller — thin shim + __main__ entry point.

All logic lives in notify_imessage_inbound. This module re-exports the
public surface so existing imports continue to work, then provides main().

Reply grammar (locked, see render_blocker for the user-facing prompt):

    ^\\s*(?:<plan-slug>\\s+)?[0-9]\\s*$

A bare digit is only honored when exactly one plan on the host has an
open blocker; with more than one we refuse to guess and force the user
to disambiguate with the slug prefix. The render_blocker hint already
nudges them toward `<plan-slug> <number>`, so this is the lower-surprise
default.
"""
from __future__ import annotations

import sys
import time

# Re-exports — keep old import paths working.
from .notify_base import REPLY_RE, OpenBlocker, Reply, route_reply  # noqa: F401
from .notify_imessage_inbound import (  # noqa: F401
    APPLE_EPOCH_OFFSET_SECONDS,
    DEFAULT_CHAT_DB,
    DEFAULT_POLL_SECONDS,
    INBOUND_STATE_SCHEMA_VERSION,
    LEGACY_SEEN_PATH,
    OUTBOUND_MARK_SANITY_TIMEOUT_SECONDS,
    OUTBOUND_PENDING_SCHEMA_VERSION,
    POLL_BATCH_LIMIT,
    Dispatcher,
    IMessageInboundPoller,
    OpenBlockersFn,
    SelfChatLookupError,
    ShellAnswerFn,
    TickSpawner,
    _auto_tick_enabled,
    _cli_dispatch,
    _resolve_self_chat_id,
    _shell_clu_answer,
    _spawn_tick,
    append_outbound_mark,
    drain_outbound_marks,
    inbound_state_path,
    open_chat_db,
    outbound_pending_path,
    poll_once,
    read_inbound_state,
    unix_to_chatdb_ns,
    write_inbound_state,
)


def main(argv: list[str] | None = None) -> int:
    if not DEFAULT_CHAT_DB.exists():
        print(f"notify_inbound: chat.db not found at {DEFAULT_CHAT_DB}", file=sys.stderr)
        return 1
    poller = IMessageInboundPoller()
    while True:
        try:
            poller.poll()
        except Exception as exc:
            print(f"notify_inbound: poll error: {exc}", file=sys.stderr)
        time.sleep(DEFAULT_POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main() or 0)
