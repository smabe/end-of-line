"""Round-trip tests for end_of_line.config.load_project_config."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from end_of_line.config import (
    CONFIG_FILENAME,
    DispatchSpec,
    load_project_config,
)


class LoadProjectConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _write(self, raw: dict) -> None:
        (self.root / CONFIG_FILENAME).write_text(json.dumps(raw))

    def test_missing_file_returns_defaults(self) -> None:
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.dispatch, DispatchSpec())
        self.assertEqual(cfg.dispatch.path, "")

    def test_dispatch_path_present(self) -> None:
        self._write({"dispatch": {"path": "/opt/homebrew/bin:/usr/bin"}})
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.dispatch.path, "/opt/homebrew/bin:/usr/bin")

    def test_dispatch_path_absent_defaults_to_empty(self) -> None:
        self._write({"dispatch": {"command": "echo hi"}})
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.dispatch.command, "echo hi")
        self.assertEqual(cfg.dispatch.path, "")

    def test_dispatch_path_explicit_empty_string(self) -> None:
        self._write({"dispatch": {"path": ""}})
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.dispatch.path, "")

    def test_dispatch_path_expands_tilde(self) -> None:
        self._write({"dispatch": {"path": "~/.local/bin:/usr/bin"}})
        cfg = load_project_config(self.root)
        expanded = os.path.expanduser("~/.local/bin")
        self.assertEqual(cfg.dispatch.path, f"{expanded}:/usr/bin")

    def test_dispatch_path_absolute_unchanged(self) -> None:
        self._write({"dispatch": {"path": "/foo:/bar"}})
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.dispatch.path, "/foo:/bar")

    def test_dispatch_path_empty_stays_empty(self) -> None:
        self._write({"dispatch": {"path": ""}})
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.dispatch.path, "")
        self.assertFalse(cfg.dispatch.path)

    def test_dispatch_path_mixed_segments(self) -> None:
        self._write({"dispatch": {"path": "~/foo:/bar:~/baz"}})
        cfg = load_project_config(self.root)
        first = os.path.expanduser("~/foo")
        third = os.path.expanduser("~/baz")
        self.assertEqual(cfg.dispatch.path, f"{first}:/bar:{third}")

    def test_dispatch_command_and_path_together(self) -> None:
        self._write({
            "dispatch": {
                "command": "claude --print {plan_slug}",
                "path": "/usr/local/bin:/usr/bin",
            }
        })
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.dispatch.command, "claude --print {plan_slug}")
        self.assertEqual(cfg.dispatch.path, "/usr/local/bin:/usr/bin")


if __name__ == "__main__":
    unittest.main()
