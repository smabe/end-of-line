"""Is clu's monitoring hook installed, and when was it installed?

Two questions, two sources, and keeping them apart is the whole point of
this module.

**Is it installed** is DERIVED, every time it is asked, from
`~/.claude/settings.json` — the file Claude Code reads, and therefore the
only thing that decides whether a hook fires. `hook_state` answers it per
SURFACE, because the SessionStart operator dashboard and the opt-in
`--inbox` UserPromptSubmit surface are different hooks with independent
lifecycles, and in THREE states, because a locked or malformed settings
file means "cannot tell" and must never be reported as "not installed".

Matching is by the hook script's BASENAME, never its absolute path: an
operator whose clu moved (a reinstall, a new venv, a second checkout)
still has a working hook, and both reporting it missing and appending a
duplicate entry beside it are wrong answers.

**When it was installed** is the marker: a set of key/value rows in the
`monitor` table of the host database at `$XDG_CONFIG_HOME/clu/clu.db`
(default `~/.config/clu/`). Account-wide, not per-project — one hook
watches every plan on the host. The rows are `hook_installed_at`,
`hook_path`, `settings_json_path`, plus `session_start_hook_path` /
`session_start_installed_at`. This is install METADATA — the install time
is the one fact `settings.json` cannot supply, and `/clu-monitor` reports
it back to the operator. It decides nothing, which is what makes it
genuinely advisory rather than nominally so.

The v1 marker (the broken pre-#20 `/schedule` install) has no equivalent
here and needs none: it was a JSON file and that file is not read any more.

Tolerant by design on both sides: no rows, a locked database, or one
written by a newer clu all surface as `None`; an unreadable settings file
surfaces as `UNREADABLE`. Neither ever raises into clu's startup path.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from . import db
from . import state as st

SCHEMA_VERSION = 2

# Where the marker lived before it moved into the host database. Nothing in
# clu reads or writes this file any more; the path is kept only so the
# quarantine sweep has a name to point at.
LEGACY_MARKER_FILENAME = "monitor.json"


class Surface(Enum):
    """A hook clu can install, and everything that identifies it.

    `event` is the `hooks` key in settings.json; `basename` the bundled
    script under `end_of_line/hooks/`; the two `*_key` fields name the
    marker rows recording this surface's install.
    """

    event: str
    basename: str
    path_key: str
    installed_at_key: str

    SESSION_START = (
        "SessionStart",
        "clu_session_start.py",
        "session_start_hook_path",
        "session_start_installed_at",
    )
    INBOX = (
        "UserPromptSubmit",
        "clu_inbox_surface.py",
        "hook_path",
        "hook_installed_at",
    )

    def __init__(self, event: str, basename: str, path_key: str, installed_at_key: str) -> None:
        self.event = event
        self.basename = basename
        self.path_key = path_key
        self.installed_at_key = installed_at_key

    @property
    def marker_keys(self) -> tuple[str, str]:
        return (self.path_key, self.installed_at_key)


class HookState(Enum):
    """Three states, so "cannot tell" never reads as "not installed"."""

    PRESENT = "present"
    ABSENT = "absent"
    UNREADABLE = "unreadable"


# The marker row that belongs to no single surface: which settings file the
# installer wrote into. It goes when the last surface's rows go.
_SHARED_MARKER_KEYS = ("settings_json_path",)


def default_settings_path() -> Path:
    """The settings.json clu installs into and derives its answer from."""
    return Path.home() / ".claude" / "settings.json"


def entry_command(entry: dict) -> str | None:
    """Pull the `command` string from a settings.json hook entry.

    Both shapes are valid:
      flat:   {"type": "command", "command": "..."}
      nested: {"matcher"?: ..., "hooks": [{"type": "command", "command": "..."}]}
    """
    if "command" in entry:
        return entry.get("command")
    inner = entry.get("hooks")
    if isinstance(inner, list) and inner:
        first = inner[0]
        if isinstance(first, dict):
            return first.get("command")
    return None


def entry_matches(entry: object, surface: Surface) -> bool:
    """True when a settings.json hook entry runs THIS surface's script.

    The one place clu decides "is this entry ours", shared by the predicate
    and by the install/uninstall paths — so a moved clu is recognised the
    same way everywhere. Non-dict junk in the operator's array answers
    False rather than raising.
    """
    if not isinstance(entry, dict):
        return False
    return surface.basename in (entry_command(entry) or "")


def entry_script_path(entry: dict, surface: Surface) -> str | None:
    """The hook script path an entry actually runs, or None.

    Reported back to the operator on a re-install: with basename matching,
    the entry clu recognises may name a path clu would not have chosen, and
    printing clu's own resolved path there would assert something false.
    """
    for token in (entry_command(entry) or "").split():
        if token.endswith(surface.basename):
            return token
    return None


def hook_state(surface: Surface, settings_path: Path | None = None) -> HookState:
    """Is `surface`'s hook registered in settings.json?

    `settings_path` is the injection seam every test must use: the default
    resolves through `Path.home()`, NOT the XDG config dir, so it escapes
    `CLU_TEST_MODE` isolation and would read the developer's real file.

    A MISSING settings.json is `ABSENT`, not `UNREADABLE` — no user
    settings file means Claude Code loads no user hooks, which is a fact
    the absence tells us. Anything we merely failed to parse is
    `UNREADABLE`.
    """
    path = settings_path if settings_path is not None else default_settings_path()
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return HookState.ABSENT
    except (OSError, ValueError):
        return HookState.UNREADABLE
    try:
        data = json.loads(raw)
    except ValueError:
        return HookState.UNREADABLE
    if not isinstance(data, dict):
        return HookState.UNREADABLE
    hooks = data.get("hooks")
    if hooks is None:
        return HookState.ABSENT
    if not isinstance(hooks, dict):
        return HookState.UNREADABLE
    entries = hooks.get(surface.event)
    if entries is None:
        return HookState.ABSENT
    if not isinstance(entries, list):
        return HookState.UNREADABLE
    if any(entry_matches(e, surface) for e in entries):
        return HookState.PRESENT
    return HookState.ABSENT


def load_marker(path: Path | None = None) -> dict | None:
    """Return the install metadata when some is recorded; None otherwise.

    `path` names the host DATABASE, not a marker file — the keyword is the
    same test seam it always was. Rows here answer "when" and "where", never
    "is it installed": ask `hook_state` for that.
    """
    try:
        with db.host_conn(path) as conn:
            rows = conn.execute("SELECT k, v FROM monitor").fetchall()
    except db.DEGRADABLE_ERRORS:
        return None
    if not rows:
        return None
    marker: dict = {"schema_version": SCHEMA_VERSION}
    marker.update(dict(rows))
    return marker


def _stamp(pairs: list[tuple[str, str]], path: Path | None) -> None:
    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        cur.executemany("INSERT OR REPLACE INTO monitor (k, v) VALUES (?, ?)", pairs)


def record_hook_installed(
    hook_path: str,
    settings_json_path: str,
    *,
    path: Path | None = None,
) -> None:
    """Stamp the marker. Per-key upsert, so a `session_start_hook_path`
    recorded by an earlier `--session-start` run survives a re-install.

    `path` parameter is for tests; production uses the default XDG-derived
    host database.
    """
    _stamp(
        [
            ("hook_installed_at", st.utcnow()),
            ("hook_path", hook_path),
            ("settings_json_path", settings_json_path),
        ],
        path,
    )


def record_session_start_installed(
    session_start_hook_path: str,
    settings_json_path: str | None = None,
    *,
    path: Path | None = None,
) -> None:
    """Stamp the SessionStart hook path onto the marker (#70).

    Additive, per-key upsert. `clu install-hook` calls this on every run —
    SessionStart is the default surface — so `settings_json_path` is stamped
    here too; without it a default install would leave the marker without the
    path `/clu-monitor` reports back. Passing it stays optional so callers
    that only want the hook path recorded are unaffected.
    """
    pairs = [("session_start_hook_path", session_start_hook_path)]
    if settings_json_path is not None:
        pairs.append(("settings_json_path", settings_json_path))
    # FIRST observation wins for install-time. `clu install-hook` calls this on
    # every run, including no-op re-runs that write no settings, so stamping
    # unconditionally would report the time of the last CHECK as the install
    # date — which is what `/clu-monitor` prints back to the operator. Absent
    # (a fresh machine, or a marker wiped by a store migration) still stamps.
    existing = load_marker(path) or {}
    if not existing.get("session_start_installed_at"):
        pairs.append(("session_start_installed_at", st.utcnow()))
    _stamp(pairs, path)


def clear_surface_marker(surface: Surface, path: Path | None = None) -> None:
    """Drop the install metadata for ONE surface, leaving the other's alone.

    `clear_marker` deletes every row, so removing one hook used to erase what
    clu knew about the other. `settings_json_path` names no single surface,
    so it goes with the last one out — otherwise a full uninstall leaves a
    marker describing nothing.
    """
    survivors = [k for s in Surface if s is not surface for k in s.marker_keys]
    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        cur.executemany("DELETE FROM monitor WHERE k = ?", [(k,) for k in surface.marker_keys])
        placeholders = ",".join("?" * len(survivors))
        remaining = cur.execute(
            f"SELECT COUNT(*) FROM monitor WHERE k IN ({placeholders})", survivors
        ).fetchone()[0]
        if not remaining:
            cur.executemany("DELETE FROM monitor WHERE k = ?", [(k,) for k in _SHARED_MARKER_KEYS])


def clear_marker(path: Path | None = None) -> None:
    with db.host_conn(path) as conn, db.write_txn(conn) as cur:
        cur.execute("DELETE FROM monitor")
