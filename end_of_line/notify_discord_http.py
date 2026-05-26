"""Shared HTTP client for the Discord REST API.

Used by both `notify_discord` (outbound webhook/DM send) and
`notify_discord_inbound` (operator-DM polling). Owns the bot-token
auth header, JSON request/response, and the 429 retry-once-then-drop
pattern with Retry-After parsing (header first, body fallback).

stdlib only — urllib.request + json.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "clu/1.0 (https://github.com/smabe/end-of-line)"


def parse_retry_after(exc: urllib.error.HTTPError) -> float:
    """Read Retry-After from response header, falling back to JSON body."""
    header_val = exc.headers.get("Retry-After")
    if header_val is not None:
        try:
            return float(header_val)
        except (ValueError, TypeError):
            pass
    try:
        body_data = json.loads(exc.read())
        return float(body_data.get("retry_after", 1.0))
    except Exception:
        return 1.0


def request(
    bot_token: str,
    method: str,
    path: str,
    body: dict | None = None,
    *,
    log_prefix: str,
    empty_on_double_429: Callable[[str], Any],
    _retried: bool = False,
) -> Any:
    """Discord REST call: bot-token auth, JSON body, retry once on 429.

    On a second 429, prints a `<log_prefix>: rate limited twice ...`
    line to stderr and returns the value produced by
    `empty_on_double_429(method)` — outbound uses `{}`, inbound uses
    `[]` for GETs and `{}` otherwise.
    """
    req = urllib.request.Request(
        API_BASE + path,
        method=method,
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        data=json.dumps(body).encode() if body else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and not _retried:
            time.sleep(parse_retry_after(exc))
            return request(
                bot_token,
                method,
                path,
                body,
                log_prefix=log_prefix,
                empty_on_double_429=empty_on_double_429,
                _retried=True,
            )
        if exc.code == 429 and _retried:
            print(
                f"{log_prefix}: rate limited twice on {method} {path}, dropping",
                file=sys.stderr,
            )
            return empty_on_double_429(method)
        raise
