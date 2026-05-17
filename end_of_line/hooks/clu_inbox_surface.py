"""UserPromptSubmit hook: surface unprocessed clu inbox events.

Reads stdin (the hook payload — we don't need its contents but consume
it to be a well-behaved hook), filters `~/.config/clu/inbox/` to events
for the current project, emits `hookSpecificOutput` JSON on stdout, and
marks each surfaced event processed.

Failure semantics: exits 0 on any exception. A noisy crash would surface
on the operator's screen as a stderr line, which is louder than the
inbox event itself. The exception is logged to `~/.config/clu/inbox_hook.log`
for diagnosis.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# Absolute import (not `from .. import`) so the script works when launched
# directly via `python /path/to/clu_inbox_surface.py` — Claude Code's
# UserPromptSubmit hook invokes it that way, with no package context. The
# pipx venv that installed end_of_line guarantees the absolute import resolves.
from end_of_line import inbox

MAX_EVENTS = 20
# Buffer under the documented 10K additionalContext cap; leaves room for
# the truncation footer and any inflation Claude Code may apply.
MAX_CONTEXT_CHARS = 9500


def _log_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "clu" / "inbox_hook.log"


def _resolve_project_root() -> str:
    """`git rev-parse --show-toplevel`, falling back to CWD on failure.

    `cwd=None` so the subprocess inherits this process's CWD (which is
    Claude Code's CWD at the moment of the user prompt). Timeout caps
    the latency floor at 2s even on a pathological filesystem.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return os.getcwd()


def _format_event(e: dict) -> str:
    ts = e.get("timestamp", "?")
    slug = e.get("plan_slug", "?")
    kind = e.get("type", "?")
    summary = e.get("summary", "")
    return f"  [{ts}] {slug} / {kind}: {summary}"


def _build_context(events: Iterable[dict]) -> str:
    events = list(events)
    if not events:
        return ""
    # Show the most recent MAX_EVENTS; summarize the rest in a footer.
    if len(events) > MAX_EVENTS:
        truncated_count = len(events) - MAX_EVENTS
        capped = events[-MAX_EVENTS:]
    else:
        truncated_count = 0
        capped = events
    lines = ["clu inbox (unprocessed):"]
    lines.extend(_format_event(e) for e in capped)
    if truncated_count > 0:
        lines.append(
            f"  (+ {truncated_count} older events — run `clu inbox` "
            "to see all)"
        )
    out = "\n".join(lines)
    if len(out) > MAX_CONTEXT_CHARS:
        footer = "\n  (truncated)"
        out = out[: MAX_CONTEXT_CHARS - len(footer)] + footer
    return out


def main() -> int:
    # Guard against shell-env inheritance of CLU_TEST_MODE — a hook invoked
    # from a test-mode shell must not false-trip the XDG guard.
    os.environ.pop("CLU_TEST_MODE", None)
    try:
        # Consume stdin so Claude Code doesn't see a broken-pipe on its
        # write side. We don't actually need the payload.
        _ = sys.stdin.read()
        project_root = _resolve_project_root()
        events = inbox.list_for_project(project_root)
        context = _build_context(events)
        if not context:
            return 0
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
        sys.stdout.write(json.dumps(payload))
        # Mark only the events we actually surfaced (the capped set).
        for e in events[-MAX_EVENTS:]:
            inbox.mark_processed(e["id"])
        return 0
    except Exception as exc:  # graceful — never alarm the operator
        try:
            log = _log_path()
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as f:
                f.write(f"{exc!r}\n")
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
