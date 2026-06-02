"""Refuse XDG writes to real ~/.config/clu/ when CLU_TEST_MODE=1.

Pairs with CluTestCase in tests/__init__.py. Defense-in-depth so a test
class that forgets to subclass CluTestCase hard-fails instead of silently
leaking ghost state into the operator's real inbox / registry / monitor
marker.
"""

from __future__ import annotations

import os
from pathlib import Path

_SENTINEL = "CLU_TEST_MODE"


def clu_config_dir() -> Path:
    """clu's config directory: `$XDG_CONFIG_HOME/clu` (default `~/.config/clu`).

    Single source for the XDG-base resolution previously copy-pasted across
    registry, monitor, inbox, notify, the session/inbox hooks, and the global
    config loader. Returns the directory only and does NOT call
    `assert_xdg_safe` — callers append their filename and assert the final path
    themselves where appropriate (the hooks intentionally skip the assert,
    matching prior behavior).
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "clu"


def assert_xdg_safe(path: Path) -> None:
    if not os.environ.get(_SENTINEL):
        return
    try:
        resolved = path.resolve()
        home = Path.home().resolve()
    except OSError:
        return  # path doesn't exist yet — let the caller fail naturally
    try:
        resolved.relative_to(home)
    except ValueError:
        return  # not under home, safe
    raise RuntimeError(
        f"refusing XDG write to {resolved!s} while CLU_TEST_MODE=1 — "
        f"test class likely missing CluTestCase isolation (see "
        f"tests/__init__.py:CluTestCase)"
    )
