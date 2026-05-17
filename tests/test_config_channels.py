"""Tests for multi-channel config schema + auto-migration (phase schema)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from end_of_line.config import (
    CONFIG_FILENAME,
    ChannelSpec,
    ConfigError,
    NotifySpec,
    load_project_config,
)


class ChannelsMigrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _write(self, raw: dict) -> None:
        (self.root / CONFIG_FILENAME).write_text(json.dumps(raw))

    def test_load_migrates_flat_imessage_to_channels(self) -> None:
        self._write({"notify": {"imessage": {"to": "+15551234567"}}})
        cfg = load_project_config(self.root)
        self.assertEqual(len(cfg.notify.channels), 1)
        ch = cfg.notify.channels[0]
        self.assertEqual(ch.kind, "imessage")
        self.assertEqual(ch.params["to"], "+15551234567")
        self.assertTrue(ch.enabled)

    def test_load_native_channels_list_unchanged(self) -> None:
        self._write({"notify": {"channels": [
            {"kind": "imessage", "to": "+15550000000"},
        ]}})
        cfg = load_project_config(self.root)
        self.assertEqual(len(cfg.notify.channels), 1)
        ch = cfg.notify.channels[0]
        self.assertEqual(ch.kind, "imessage")
        self.assertEqual(ch.params["to"], "+15550000000")

    def test_load_rejects_unknown_kind(self) -> None:
        self._write({"notify": {"channels": [{"kind": "telegram"}]}})
        with self.assertRaises(ConfigError):
            load_project_config(self.root)

    def test_load_rejects_missing_required_imessage_field(self) -> None:
        self._write({"notify": {"channels": [{"kind": "imessage"}]}})
        with self.assertRaises(ConfigError):
            load_project_config(self.root)

    def test_load_accepts_discord_kind_schema_only(self) -> None:
        self._write({"notify": {"channels": [
            {"kind": "discord", "bot_token": "x", "user_id": "y"},
        ]}})
        cfg = load_project_config(self.root)
        self.assertEqual(len(cfg.notify.channels), 1)
        ch = cfg.notify.channels[0]
        self.assertEqual(ch.kind, "discord")
        self.assertEqual(ch.params["bot_token"], "x")
        self.assertEqual(ch.params["user_id"], "y")

    def test_load_channel_kinds_filter_defaults_to_none(self) -> None:
        self._write({"notify": {"channels": [
            {"kind": "imessage", "to": "+1"},
        ]}})
        cfg = load_project_config(self.root)
        self.assertIsNone(cfg.notify.channels[0].kinds)

    def test_load_channel_enabled_defaults_true(self) -> None:
        self._write({"notify": {"channels": [
            {"kind": "imessage", "to": "+1"},
        ]}})
        cfg = load_project_config(self.root)
        self.assertTrue(cfg.notify.channels[0].enabled)

    def test_load_channel_enabled_false_persists(self) -> None:
        self._write({"notify": {"channels": [
            {"kind": "imessage", "to": "+1", "enabled": False},
        ]}})
        cfg = load_project_config(self.root)
        self.assertFalse(cfg.notify.channels[0].enabled)

    def test_load_migration_preserves_quiet_hours(self) -> None:
        self._write({"notify": {
            "imessage": {"to": "+1"},
            "quiet_hours": ["22:00", "08:00"],
        }})
        cfg = load_project_config(self.root)
        self.assertEqual(len(cfg.notify.channels), 1)
        self.assertEqual(cfg.notify.quiet_hours, ("22:00", "08:00"))


if __name__ == "__main__":
    unittest.main()
