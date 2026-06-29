"""Tests for the global notify config layer (~/.config/clu/config.json) and the
disabled-channel validation exemption that the per-kind mask stub relies on.

Phase 1 (MaskStubValidationTestCase) covers only `_validate_channel`'s
enabled-exempt behavior and needs no global file, so it uses a plain project
config. Phase 2 (global merge) uses CluTestCase for XDG isolation.
"""

from __future__ import annotations

import json
import unittest

from end_of_line.config import (
    CONFIG_FILENAME,
    ConfigError,
    global_config_path,
    load_project_config,
    load_session_dirs,
)
from tests import CluTestCase


class MaskStubValidationTestCase(CluTestCase):
    """`{kind, enabled:false}` validates without required fields (mask stub).

    Extends CluTestCase so `load_project_config` (which now reads the global
    config) resolves to the isolated temp XDG dir — never the operator's real
    ~/.config/clu/config.json — keeping these assertions hermetic.
    """

    def setUp(self) -> None:
        super().setUp()
        self.root = (self.tmp_path / "proj")
        self.root.mkdir()

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


class GlobalMergeTestCase(CluTestCase):
    """Global ~/.config/clu/config.json merges as base under per-project config.

    CluTestCase points XDG_CONFIG_HOME at a temp dir, so `global_config_path()`
    resolves to <tmp>/clu/config.json — written via `_write_global` below.
    """

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "proj"
        self.project.mkdir()

    def _write_project(self, notify: dict | None) -> None:
        raw: dict = {"dispatch": {"command": "echo hi"}}
        if notify is not None:
            raw["notify"] = notify
        (self.project / CONFIG_FILENAME).write_text(json.dumps(raw))

    def _write_global(self, raw: dict) -> None:
        path = global_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw))

    def _kinds(self, cfg) -> set[str]:
        return {c.kind for c in cfg.notify.channels}

    def _channel(self, cfg, kind):
        return next(c for c in cfg.notify.channels if c.kind == kind)

    # -- inheritance --------------------------------------------------------

    def test_global_channel_inherited_when_project_has_none(self) -> None:
        self._write_global(
            {"notify": {"channels": [
                {"kind": "discord", "bot_token": "G", "user_id": "u"}]}}
        )
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}]})
        cfg = load_project_config(self.project)
        self.assertEqual(self._kinds(cfg), {"discord", "imessage"})
        self.assertEqual(self._channel(cfg, "discord").params["bot_token"], "G")

    def test_global_inherited_when_project_has_no_notify_block(self) -> None:
        self._write_global(
            {"notify": {"channels": [
                {"kind": "discord", "bot_token": "G", "user_id": "u"}]}}
        )
        self._write_project(None)
        cfg = load_project_config(self.project)
        self.assertEqual(self._kinds(cfg), {"discord"})

    # -- override / add / mask ---------------------------------------------

    def test_local_same_kind_overrides_global(self) -> None:
        self._write_global(
            {"notify": {"channels": [
                {"kind": "discord", "bot_token": "G", "user_id": "gu"}]}}
        )
        self._write_project(
            {"channels": [{"kind": "discord", "bot_token": "L", "user_id": "lu"}]}
        )
        cfg = load_project_config(self.project)
        self.assertEqual(len(cfg.notify.channels), 1)
        self.assertEqual(self._channel(cfg, "discord").params["bot_token"], "L")

    def test_local_new_kind_adds_alongside_global(self) -> None:
        self._write_global(
            {"notify": {"channels": [
                {"kind": "discord", "bot_token": "G", "user_id": "u"}]}}
        )
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}]})
        cfg = load_project_config(self.project)
        self.assertEqual(self._kinds(cfg), {"discord", "imessage"})

    def test_local_mask_disables_global_kind(self) -> None:
        self._write_global(
            {"notify": {"channels": [
                {"kind": "discord", "bot_token": "G", "user_id": "u"}]}}
        )
        self._write_project({"channels": [{"kind": "discord", "enabled": False}]})
        cfg = load_project_config(self.project)
        # The global discord is shadowed by the disabled local stub; no enabled
        # discord survives.
        enabled_discord = [
            c for c in cfg.notify.channels if c.kind == "discord" and c.enabled
        ]
        self.assertEqual(enabled_discord, [])

    def test_multiple_same_kind_local_not_collapsed(self) -> None:
        self._write_global(
            {"notify": {"channels": [
                {"kind": "discord", "bot_token": "G", "user_id": "u"}]}}
        )
        self._write_project(
            {"channels": [
                {"kind": "imessage", "to": "+1"},
                {"kind": "imessage", "to": "+2"},
            ]}
        )
        cfg = load_project_config(self.project)
        imessage_tos = sorted(
            c.params["to"] for c in cfg.notify.channels if c.kind == "imessage"
        )
        self.assertEqual(imessage_tos, ["+1", "+2"])

    # -- quiet_hours --------------------------------------------------------

    def test_quiet_hours_local_wins(self) -> None:
        self._write_global({"notify": {"channels": [], "quiet_hours": ["22:00", "08:00"]}})
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}],
                             "quiet_hours": ["23:00", "07:00"]})
        cfg = load_project_config(self.project)
        self.assertEqual(cfg.notify.quiet_hours, ("23:00", "07:00"))

    def test_quiet_hours_falls_back_to_global(self) -> None:
        self._write_global({"notify": {"channels": [], "quiet_hours": ["22:00", "08:00"]}})
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}]})
        cfg = load_project_config(self.project)
        self.assertEqual(cfg.notify.quiet_hours, ("22:00", "08:00"))

    # -- legacy + back-compat ----------------------------------------------

    def test_legacy_imessage_still_inherits_global_discord(self) -> None:
        self._write_global(
            {"notify": {"channels": [
                {"kind": "discord", "bot_token": "G", "user_id": "u"}]}}
        )
        self._write_project({"imessage": {"to": "+1"}})
        cfg = load_project_config(self.project)
        self.assertEqual(self._kinds(cfg), {"discord", "imessage"})

    def test_no_global_file_behaves_as_today(self) -> None:
        # No global file written. Legacy form still migrates to one channel.
        self._write_project({"imessage": {"to": "+1"}})
        cfg = load_project_config(self.project)
        self.assertEqual(self._kinds(cfg), {"imessage"})

    def test_empty_local_channels_inherits_global(self) -> None:
        self._write_global(
            {"notify": {"channels": [
                {"kind": "discord", "bot_token": "G", "user_id": "u"}]}}
        )
        self._write_project({"channels": []})
        cfg = load_project_config(self.project)
        self.assertEqual(self._kinds(cfg), {"discord"})

    # -- fail-open ----------------------------------------------------------

    def test_malformed_global_file_fails_open(self) -> None:
        path = global_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json")
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}]})
        cfg = load_project_config(self.project)  # must not raise
        self.assertEqual(self._kinds(cfg), {"imessage"})

    def test_empty_global_file_fails_open(self) -> None:
        path = global_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")  # json.loads("") raises JSONDecodeError
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}]})
        cfg = load_project_config(self.project)  # must not raise
        self.assertEqual(self._kinds(cfg), {"imessage"})

    def test_malformed_global_channel_fails_open(self) -> None:
        # Valid JSON, invalid channel (unknown kind) — must not break every
        # project load; global is ignored, local channels survive.
        self._write_global({"notify": {"channels": [{"kind": "telegram"}]}})
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}]})
        cfg = load_project_config(self.project)  # must not raise
        self.assertEqual(self._kinds(cfg), {"imessage"})

    def test_non_object_global_root_fails_open(self) -> None:
        # Valid JSON but not an object (e.g. a list) — raw.get would AttributeError.
        path = global_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]")
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}]})
        cfg = load_project_config(self.project)  # must not raise
        self.assertEqual(self._kinds(cfg), {"imessage"})

    def test_non_dict_channel_entry_fails_open(self) -> None:
        # channels is a list of strings, not objects — _validate_channel would
        # AttributeError on c.get(); must fail open, not crash.
        self._write_global({"notify": {"channels": ["discord"]}})
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}]})
        cfg = load_project_config(self.project)  # must not raise
        self.assertEqual(self._kinds(cfg), {"imessage"})

    def test_invalid_shape_quiet_hours_ignored(self) -> None:
        # A 2-char string would pass a bare len()==2 check; it must be rejected
        # both for the global value and the local value.
        self._write_global({"notify": {"channels": [], "quiet_hours": "ab"}})
        self._write_project({"channels": [{"kind": "imessage", "to": "+1"}],
                             "quiet_hours": "cd"})
        cfg = load_project_config(self.project)
        self.assertIsNone(cfg.notify.quiet_hours)


class SessionDirsTestCase(CluTestCase):
    """`load_session_dirs` reads the machine-wide `session_dirs` key — the cwds
    whose Claude sessions clu top/serve surface without a registered plan."""

    def _write_global(self, raw: dict) -> None:
        path = global_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw))

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(load_session_dirs(), [])

    def test_absent_key_is_empty(self) -> None:
        self._write_global({"notify": {"channels": []}})
        self.assertEqual(load_session_dirs(), [])

    def test_reads_and_resolves_absolute(self) -> None:
        d = self.tmp_path / "proj"
        d.mkdir()
        self._write_global({"session_dirs": [str(d)]})
        self.assertEqual(load_session_dirs(), [str(d.resolve())])

    def test_expanduser(self) -> None:
        self._write_global({"session_dirs": ["~/somewhere"]})
        from pathlib import Path
        self.assertEqual(load_session_dirs(), [str(Path("~/somewhere").expanduser().resolve())])

    def test_skips_non_string_and_dedups(self) -> None:
        d = self.tmp_path / "proj"
        d.mkdir()
        self._write_global({"session_dirs": [str(d), 42, None, str(d)]})
        self.assertEqual(load_session_dirs(), [str(d.resolve())])

    def test_skips_non_absolute_entries(self) -> None:
        # "" / relative paths would resolve against clu's cwd — must be rejected.
        d = self.tmp_path / "proj"
        d.mkdir()
        self._write_global({"session_dirs": ["", "relative/path", ".", str(d)]})
        self.assertEqual(load_session_dirs(), [str(d.resolve())])

    def test_malformed_file_is_empty(self) -> None:
        path = global_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertEqual(load_session_dirs(), [])

    def test_non_list_value_is_empty(self) -> None:
        self._write_global({"session_dirs": "/a/single/string"})
        self.assertEqual(load_session_dirs(), [])


if __name__ == "__main__":
    unittest.main()
