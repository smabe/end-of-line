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


def isolate_queue(testcase: unittest.TestCase, tmp_path: Path) -> None:
    """Isolate registry + queue file paths for a queue test.

    queue.json lives under each project's `.orchestrator/` — so per-test
    isolation falls out naturally as long as the project root is itself
    tmp-scoped. The only shared sink that needs patching is the host
    registry, since `clu queue add`'s bootstrap check reads it.
    """
    isolate_registry(testcase, tmp_path)


def isolate_monitor_marker(testcase: unittest.TestCase, tmp_path: Path) -> None:
    """Point clu's monitor marker file at a per-test XDG dir.

    `monitor.marker_path()` resolves through `XDG_CONFIG_HOME`, so this
    is the same monkeypatch as `isolate_registry`. Named separately so a
    monitor test that doesn't touch registry doesn't read as registry-coupled.
    """
    isolate_registry(testcase, tmp_path)
