"""Shared test helpers."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock


def isolate_registry(testcase: unittest.TestCase, tmp_path: Path) -> None:
    """Point clu's XDG-based registry at a per-test temp dir.

    Without this, `cmd_init` writes to the user's real
    `~/.config/clu/registry.json` during tests. Call from setUp after
    creating tmp_path; the patch auto-restores via addCleanup.
    """
    patcher = mock.patch.dict(
        os.environ, {"XDG_CONFIG_HOME": str(tmp_path)},
    )
    patcher.start()
    testcase.addCleanup(patcher.stop)
