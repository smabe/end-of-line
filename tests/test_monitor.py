"""Tests for the monitor-hook predicate and its install metadata.

Two separate things live in `end_of_line/monitor.py` and this file keeps
them apart on purpose:

**The predicate** — `hook_state(surface, settings_path)` — is DERIVED from
`~/.claude/settings.json`, because that file is what Claude Code reads and
therefore what decides whether a hook fires. It answers per surface
(SessionStart dashboard vs the opt-in UserPromptSubmit inbox) and in three
states, so "cannot tell" never reads as "not installed".

**The marker** — the rows in the `monitor` table of the host database at
`$XDG_CONFIG_HOME/clu/clu.db` — is install METADATA: when the install
happened and which settings file it wrote into. It decides nothing. Its
tolerance contract is unchanged: no rows, no database, and a database a
newer clu wrote all surface as `None`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import db, monitor
from tests import isolate_monitor_marker, must

SESSION_START = monitor.Surface.SESSION_START
INBOX = monitor.Surface.INBOX


def _settings(tmp: Path, hooks: dict | None = None, *, raw: str | None = None) -> Path:
    """Write a settings.json fixture and return its path.

    Every test injects this path explicitly. `_hook_settings_path` resolves
    through `Path.home()` rather than the XDG dir, so a predicate test that
    let the path default would read the developer's real
    `~/.claude/settings.json`.
    """
    path = tmp / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps({"hooks": hooks or {}}))
    return path


def _nested(command: str) -> dict:
    return {"hooks": [{"type": "command", "command": command, "timeout": 5}]}


def _flat(command: str) -> dict:
    return {"type": "command", "command": command}


class MarkerStorePathTests(unittest.TestCase):
    def test_store_path_respects_xdg_config_home(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(db.host_db_path(), Path("/tmp/xdg") / "clu" / "clu.db")

    def test_store_path_defaults_to_home_dotconfig(self) -> None:
        env = dict(os.environ)
        env.pop("XDG_CONFIG_HOME", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                db.host_db_path(),
                Path.home() / ".config" / "clu" / "clu.db",
            )


class HookStateTests(unittest.TestCase):
    """The derived predicate. Nothing here touches the marker."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_present_when_the_entry_is_in_settings_json(self) -> None:
        path = _settings(self.tmp, {"SessionStart": [_nested("py /a/clu_session_start.py")]})
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.PRESENT)

    def test_present_for_a_flat_shape_entry_too(self) -> None:
        # Both settings.json array styles are valid and the operator's
        # machine may carry either.
        path = _settings(self.tmp, {"SessionStart": [_flat("py /a/clu_session_start.py")]})
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.PRESENT)

    def test_present_when_the_hook_lives_at_a_different_absolute_path(self) -> None:
        # THE reason matching is by basename: a clu reinstalled into a new
        # venv still has a working hook, and reporting it missing would send
        # the operator to fix something that is not broken.
        path = _settings(
            self.tmp,
            {"SessionStart": [_nested("/old/venv/bin/python -u /old/co/clu_session_start.py")]},
        )
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.PRESENT)

    def test_absent_when_the_hooks_block_has_no_entry_for_the_surface(self) -> None:
        path = _settings(self.tmp, {"PreToolUse": [_flat("echo pre")]})
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.ABSENT)

    def test_absent_when_the_surface_list_holds_only_other_peoples_hooks(self) -> None:
        path = _settings(self.tmp, {"SessionStart": [_nested("echo theirs")]})
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.ABSENT)

    def test_absent_when_settings_json_does_not_exist(self) -> None:
        # SAFE DIRECTION: a file that is not there is a fact we CAN read —
        # no settings.json means Claude Code loads no user hooks, so the
        # dashboard genuinely is not installed and the tip should fire.
        # This is the one missing-input case that is ABSENT rather than
        # UNREADABLE.
        self.assertIs(
            monitor.hook_state(SESSION_START, self.tmp / "nope" / "settings.json"),
            monitor.HookState.ABSENT,
        )

    def test_unreadable_when_settings_json_is_malformed(self) -> None:
        # SAFE DIRECTION: "cannot tell" must never read as "not installed".
        # An operator mid-edit would otherwise be told their working hook is
        # missing — the same conflation this phase removes from the marker.
        path = _settings(self.tmp, raw="not json {{{")
        state = monitor.hook_state(SESSION_START, path)
        self.assertIs(state, monitor.HookState.UNREADABLE)
        self.assertIsNot(state, monitor.HookState.ABSENT)

    def test_unreadable_when_settings_json_cannot_be_opened(self) -> None:
        # A locked / unreadable file, not a missing one. Same direction as
        # malformed: unknown, never "not installed".
        path = self.tmp / ".claude" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()  # a directory where a file is expected — read raises OSError
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.UNREADABLE)

    def test_unreadable_when_the_document_is_not_an_object(self) -> None:
        path = _settings(self.tmp, raw=json.dumps([1, 2, 3]))
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.UNREADABLE)

    def test_unreadable_when_the_hooks_block_is_the_wrong_shape(self) -> None:
        path = _settings(self.tmp, raw=json.dumps({"hooks": "all of them"}))
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.UNREADABLE)

    def test_unreadable_when_the_surface_entry_is_not_a_list(self) -> None:
        path = _settings(self.tmp, raw=json.dumps({"hooks": {"SessionStart": {"a": 1}}}))
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.UNREADABLE)

    def test_absent_when_there_is_no_hooks_block_at_all(self) -> None:
        # A well-formed settings.json with no hooks is readable and says
        # nothing is installed. Readable ≠ unreadable.
        path = _settings(self.tmp, raw=json.dumps({"model": "opus"}))
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.ABSENT)

    def test_a_garbage_entry_in_the_list_does_not_crash_the_read(self) -> None:
        path = _settings(
            self.tmp,
            raw=json.dumps(
                {"hooks": {"SessionStart": ["a string", None, _nested("py clu_session_start.py")]}}
            ),
        )
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.PRESENT)


class PerSurfaceTests(unittest.TestCase):
    """Two hooks with independent lifecycles must have two answers."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_an_inbox_only_install_does_not_report_the_dashboard_installed(self) -> None:
        path = _settings(self.tmp, {"UserPromptSubmit": [_nested("py /a/clu_inbox_surface.py")]})
        self.assertIs(monitor.hook_state(INBOX, path), monitor.HookState.PRESENT)
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.ABSENT)

    def test_a_dashboard_only_install_does_not_report_the_inbox_installed(self) -> None:
        path = _settings(self.tmp, {"SessionStart": [_nested("py /a/clu_session_start.py")]})
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.PRESENT)
        self.assertIs(monitor.hook_state(INBOX, path), monitor.HookState.ABSENT)

    def test_the_two_surfaces_own_disjoint_marker_keys(self) -> None:
        self.assertEqual(
            set(SESSION_START.marker_keys) & set(INBOX.marker_keys),
            set(),
        )


class DivergenceTests(unittest.TestCase):
    """The two directions the marker used to get wrong, now unrepresentable."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)

    def test_an_installed_hook_reads_installed_with_an_empty_marker_table(self) -> None:
        # The live state of the operator's machine when this was written:
        # the entry is in settings.json and the monitor table has no rows.
        path = _settings(self.tmp, {"SessionStart": [_nested("py /a/clu_session_start.py")]})
        self.assertIsNone(monitor.load_marker())
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.PRESENT)

    def test_a_marker_row_does_not_make_an_absent_hook_read_installed(self) -> None:
        # The worse direction, and the one the marker silently trusted: clu
        # stays quiet about a hook that is not firing.
        monitor.record_session_start_installed("/a/clu_session_start.py", "/a/settings.json")
        path = _settings(self.tmp, {})
        self.assertIsNotNone(monitor.load_marker())
        self.assertIs(monitor.hook_state(SESSION_START, path), monitor.HookState.ABSENT)


class MarkerLifecycleTests(unittest.TestCase):
    """Install metadata: written on install, read back by `/clu-monitor`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)

    def _record(self) -> None:
        monitor.record_hook_installed(
            "/abs/hook.py",
            "/home/x/.claude/settings.json",
        )

    def test_load_marker_returns_none_when_absent(self) -> None:
        self.assertFalse(db.host_db_path().exists())
        self.assertIsNone(monitor.load_marker())

    def test_load_marker_returns_none_when_the_store_is_unreadable(self) -> None:
        path = db.host_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not a database")
        self.assertIsNone(monitor.load_marker())

    def test_load_marker_returns_none_when_the_schema_is_newer(self) -> None:
        self._record()
        conn = sqlite3.connect(str(db.host_db_path()))
        conn.execute(f"PRAGMA user_version = {db.HOST_SCHEMA_VERSION + 1}")
        conn.close()
        self.assertIsNone(monitor.load_marker())

    def test_record_hook_installed_writes_marker(self) -> None:
        monitor.record_hook_installed("/abs/hook.py", "/home/x/settings.json")
        data = must(monitor.load_marker())
        self.assertEqual(data["schema_version"], monitor.SCHEMA_VERSION)
        self.assertEqual(data["hook_path"], "/abs/hook.py")
        self.assertEqual(data["settings_json_path"], "/home/x/settings.json")
        self.assertTrue(data["hook_installed_at"].endswith("Z"))

    def test_record_hook_installed_overwrites_existing(self) -> None:
        monitor.record_hook_installed("/old/hook.py", "/old/settings.json")
        monitor.record_hook_installed("/new/hook.py", "/new/settings.json")
        data = must(monitor.load_marker())
        self.assertEqual(data["hook_path"], "/new/hook.py")
        self.assertEqual(data["settings_json_path"], "/new/settings.json")

    def test_session_start_stamp_survives_a_reinstall(self) -> None:
        # #70: the two install paths are separate commands and neither may
        # erase the other's stamp.
        self._record()
        monitor.record_session_start_installed("/abs/session_start.py")
        monitor.record_hook_installed("/new/hook.py", "/new/settings.json")
        data = must(monitor.load_marker())
        self.assertEqual(data["session_start_hook_path"], "/abs/session_start.py")
        self.assertEqual(data["hook_path"], "/new/hook.py")
        self.assertTrue(data["session_start_installed_at"].endswith("Z"))

    def test_clear_marker_empties_the_table(self) -> None:
        self._record()
        self.assertIsNotNone(monitor.load_marker())
        monitor.clear_marker()
        self.assertIsNone(monitor.load_marker())

    def test_clear_marker_idempotent_when_absent(self) -> None:
        self.assertIsNone(monitor.load_marker())
        monitor.clear_marker()  # must not raise
        self.assertIsNone(monitor.load_marker())

    def test_load_marker_returns_dict_when_present(self) -> None:
        monitor.record_hook_installed("/abs/hook.py", "/home/x/settings.json")
        loaded = must(monitor.load_marker())
        self.assertEqual(loaded["hook_path"], "/abs/hook.py")


class ClearSurfaceMarkerTests(unittest.TestCase):
    """Uninstalling one surface must not wipe the other's metadata.

    The old `clear_marker` deleted every row, so removing the inbox hook
    also erased what clu knew about the dashboard install.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        monitor.record_session_start_installed("/a/clu_session_start.py", "/a/settings.json")
        monitor.record_hook_installed("/a/clu_inbox_surface.py", "/a/settings.json")

    def test_clearing_the_inbox_leaves_the_dashboard_metadata_intact(self) -> None:
        monitor.clear_surface_marker(INBOX)
        data = must(monitor.load_marker())
        self.assertEqual(data["session_start_hook_path"], "/a/clu_session_start.py")
        self.assertIn("session_start_installed_at", data)
        self.assertNotIn("hook_path", data)
        self.assertNotIn("hook_installed_at", data)

    def test_the_shared_settings_path_survives_while_a_surface_remains(self) -> None:
        monitor.clear_surface_marker(INBOX)
        self.assertEqual(must(monitor.load_marker())["settings_json_path"], "/a/settings.json")

    def test_clearing_the_last_surface_empties_the_table(self) -> None:
        # `settings_json_path` belongs to no single surface, so it goes with
        # the last one — otherwise a full uninstall leaves a marker behind
        # that describes nothing.
        monitor.clear_surface_marker(INBOX)
        monitor.clear_surface_marker(SESSION_START)
        self.assertIsNone(monitor.load_marker())

    def test_clearing_a_surface_that_was_never_recorded_is_a_no_op(self) -> None:
        monitor.clear_surface_marker(INBOX)
        monitor.clear_surface_marker(INBOX)  # must not raise
        self.assertIsNotNone(monitor.load_marker())


if __name__ == "__main__":
    unittest.main()
