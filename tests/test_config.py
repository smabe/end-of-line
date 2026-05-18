"""Round-trip tests for end_of_line.config.load_project_config."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from end_of_line.config import (
    CONFIG_FILENAME,
    ConfigError,
    DispatchSpec,
    load_project_config,
)


class _ConfigTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _write(self, raw: dict) -> None:
        (self.root / CONFIG_FILENAME).write_text(json.dumps(raw))


class LoadProjectConfigTests(_ConfigTestBase):

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


class TestCommandFieldTests(_ConfigTestBase):
    def test_test_command_default_none_when_absent(self) -> None:
        cfg = load_project_config(self.root)
        self.assertIsNone(cfg.test_command)

    def test_test_command_field_loaded_from_orchestrator_json(self) -> None:
        self._write({"test_command": "make test"})
        cfg = load_project_config(self.root)
        self.assertEqual(cfg.test_command, "make test")

    def test_test_command_none_when_null_in_json(self) -> None:
        self._write({"test_command": None})
        cfg = load_project_config(self.root)
        self.assertIsNone(cfg.test_command)


class AutoArchiveFieldTests(_ConfigTestBase):
    def test_auto_archive_defaults_to_true_when_absent(self) -> None:
        cfg = load_project_config(self.root)
        self.assertIs(cfg.auto_archive, True)

    def test_auto_archive_false_in_orchestrator_json(self) -> None:
        self._write({"auto_archive": False})
        cfg = load_project_config(self.root)
        self.assertIs(cfg.auto_archive, False)

    def test_auto_archive_true_explicit(self) -> None:
        self._write({"auto_archive": True})
        cfg = load_project_config(self.root)
        self.assertIs(cfg.auto_archive, True)

    def test_auto_archive_non_bool_raises_config_error(self) -> None:
        for bad_value in ("yes", 1, 0, "true", "false"):
            with self.subTest(value=bad_value):
                self._write({"auto_archive": bad_value})
                with self.assertRaises(ConfigError):
                    load_project_config(self.root)


class TickOnActionFieldTests(_ConfigTestBase):
    def test_tick_on_action_defaults_to_true_when_absent(self) -> None:
        cfg = load_project_config(self.root)
        self.assertIs(cfg.tick_on_action, True)

    def test_tick_on_action_false_in_orchestrator_json(self) -> None:
        self._write({"tick_on_action": False})
        cfg = load_project_config(self.root)
        self.assertIs(cfg.tick_on_action, False)

    def test_tick_on_action_true_explicit(self) -> None:
        self._write({"tick_on_action": True})
        cfg = load_project_config(self.root)
        self.assertIs(cfg.tick_on_action, True)

    def test_tick_on_action_non_bool_raises_config_error(self) -> None:
        for bad_value in ("yes", 1, 0, "true", "false"):
            with self.subTest(value=bad_value):
                self._write({"tick_on_action": bad_value})
                with self.assertRaises(ConfigError):
                    load_project_config(self.root)


if __name__ == "__main__":
    unittest.main()
