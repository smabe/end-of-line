"""Tests for end_of_line._xdg_guard — runtime XDG safety net.

These tests deliberately do NOT inherit CluTestCase — they control the
env manually so they can exercise both the "guard fires" and "guard is
silent" branches.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import inbox, monitor, registry


class XdgGuardRaisesTestCase(unittest.TestCase):
    """Guard raises RuntimeError on real XDG paths when CLU_TEST_MODE=1."""

    def _real_xdg_test_mode(self) -> dict:
        return {
            "CLU_TEST_MODE": "1",
            "XDG_CONFIG_HOME": str(Path.home() / ".config"),
        }

    def test_guard_raises_on_inbox_in_test_mode(self):
        with mock.patch.dict(os.environ, self._real_xdg_test_mode()):
            with self.assertRaises(RuntimeError) as ctx:
                inbox.write_event(
                    type="test",
                    plan_slug="test-plan",
                    project_root="/tmp",
                    summary="guard test",
                )
            self.assertIn("CluTestCase", str(ctx.exception))

    def test_guard_raises_on_registry_in_test_mode(self):
        with mock.patch.dict(os.environ, self._real_xdg_test_mode()):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(RuntimeError) as ctx:
                    registry.register(Path(d), "test-plan")
                self.assertIn("CluTestCase", str(ctx.exception))

    def test_guard_raises_on_monitor_in_test_mode(self):
        with mock.patch.dict(os.environ, self._real_xdg_test_mode()):
            with self.assertRaises(RuntimeError) as ctx:
                monitor.marker_path()
            self.assertIn("CluTestCase", str(ctx.exception))


class XdgGuardSilentTestCase(unittest.TestCase):
    """Guard is silent when the path is isolated or CLU_TEST_MODE is unset."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def test_guard_silent_on_isolated_xdg_in_test_mode(self):
        with mock.patch.dict(os.environ, {
            "CLU_TEST_MODE": "1",
            "XDG_CONFIG_HOME": str(self.tmp_path),
        }):
            # Path is not under home — guard is silent, write proceeds.
            inbox.write_event(
                type="test",
                plan_slug="test-plan",
                project_root="/tmp",
                summary="guard test",
            )

    def test_guard_silent_outside_test_mode(self):
        real_xdg = str(Path.home() / ".config")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": real_xdg}):
            # Remove CLU_TEST_MODE — mock.patch.dict restores it on exit.
            os.environ.pop("CLU_TEST_MODE", None)
            # Path producers do no I/O; guard returns early (no test mode).
            inbox.inbox_root()
            registry.registry_path()
            monitor.marker_path()
