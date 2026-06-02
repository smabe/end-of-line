"""Tests for the global notify config layer (~/.config/clu/config.json) and the
disabled-channel validation exemption that the per-kind mask stub relies on.

Phase 1 (MaskStubValidationTestCase) covers only `_validate_channel`'s
enabled-exempt behavior and needs no global file, so it uses a plain project
config. Phase 2 (global merge) uses CluTestCase for XDG isolation.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from end_of_line.config import (
    CONFIG_FILENAME,
    ConfigError,
    load_project_config,
)


class MaskStubValidationTestCase(unittest.TestCase):
    """`{kind, enabled:false}` validates without required fields (mask stub)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _write(self, raw: dict) -> None:
        (self.root / CONFIG_FILENAME).write_text(json.dumps(raw))

    def test_disabled_channel_missing_required_fields_validates(self) -> None:
        # A mask stub carries no bot_token/user_id but must NOT raise.
        self._write(
            {"notify": {"channels": [{"kind": "discord", "enabled": False}]}}
        )
        cfg = load_project_config(self.root)
        self.assertEqual(len(cfg.notify.channels), 1)
        ch = cfg.notify.channels[0]
        self.assertEqual(ch.kind, "discord")
        self.assertFalse(ch.enabled)

    def test_enabled_channel_missing_required_still_raises(self) -> None:
        # Regression guard: the exemption is for disabled channels ONLY.
        self._write(
            {"notify": {"channels": [{"kind": "discord", "enabled": True}]}}
        )
        with self.assertRaises(ConfigError):
            load_project_config(self.root)

    def test_default_enabled_channel_missing_required_still_raises(self) -> None:
        # `enabled` defaults True, so an omitted-enabled channel still validates.
        self._write({"notify": {"channels": [{"kind": "discord"}]}})
        with self.assertRaises(ConfigError):
            load_project_config(self.root)

    def test_fully_specified_disabled_channel_validates(self) -> None:
        self._write(
            {
                "notify": {
                    "channels": [
                        {
                            "kind": "discord",
                            "bot_token": "x",
                            "user_id": "y",
                            "enabled": False,
                        }
                    ]
                }
            }
        )
        cfg = load_project_config(self.root)
        ch = cfg.notify.channels[0]
        self.assertFalse(ch.enabled)
        self.assertEqual(ch.params["bot_token"], "x")
        self.assertEqual(ch.params["user_id"], "y")


if __name__ == "__main__":
    unittest.main()
