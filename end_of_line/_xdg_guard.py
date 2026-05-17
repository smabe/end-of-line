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
