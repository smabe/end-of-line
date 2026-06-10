"""Tests for `clu install-hook` / `clu uninstall-hook` — register the
UserPromptSubmit hook script in `~/.claude/settings.json`.

Settings.json format detection: the operator's machine may already have
hook entries in either nested-array `{matcher?, hooks: [{type, command,
timeout?}]}` shape or flat-array `{type, command}` shape. Install must
detect and preserve whichever style is already present.

Idempotency contract: install detects an existing entry by absolute
hook_path match (the path baked into the entry's `command`), not by
fuzzy name match. Re-running install is a no-op.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import monitor
from end_of_line.cli import ExitCode, main
from tests import must


class InstallHookTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        # Redirect HOME so settings.json + marker land in tmp.
        self.patcher_home = mock.patch.dict(
            os.environ,
            {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")},
        )
        self.patcher_home.start()
        self.addCleanup(self.patcher_home.stop)
        self.settings = self.home / ".claude" / "settings.json"

    def _run_install(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-hook"])
        return rc, out.getvalue(), err.getvalue()

    def _run_uninstall(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["uninstall-hook"])
        return rc, out.getvalue(), err.getvalue()

    def _ups_entries(self) -> list:
        data = json.loads(self.settings.read_text())
        return data.get("hooks", {}).get("UserPromptSubmit", [])


class FreshInstallTests(InstallHookTestBase):
    def test_install_creates_settings_json_when_absent(self) -> None:
        self.assertFalse(self.settings.exists())
        rc, out, err = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertTrue(self.settings.exists())
        entries = self._ups_entries()
        self.assertEqual(len(entries), 1)

    def test_install_writes_marker_v2(self) -> None:
        rc, _, _ = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK))
        m = must(monitor.load_marker())
        self.assertEqual(m["schema_version"], 2)
        self.assertIn("hook_path", m)
        self.assertIn("settings_json_path", m)
        self.assertTrue(m["hook_installed_at"].endswith("Z"))

    def test_install_idempotent_by_path_match(self) -> None:
        rc1, _, _ = self._run_install()
        rc2, _, _ = self._run_install()
        self.assertEqual(rc1, int(ExitCode.OK))
        self.assertEqual(rc2, int(ExitCode.OK))
        entries = self._ups_entries()
        self.assertEqual(len(entries), 1)

    def test_install_proceeds_in_non_tty(self) -> None:
        # Regression for #21: the previous TTY gate blocked the legitimate
        # /clu-monitor → Bash → clu install-hook path, since Claude Code's
        # Bash tool runs subprocesses without a TTY. /clu-monitor is the
        # only caller of install-hook in practice, so the safety was
        # speculative. This asserts the install proceeds when stdout
        # isatty() is False (the default with redirect_stdout(StringIO)).
        rc, _, err = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertTrue(self.settings.exists())


class FormatPreservationTests(InstallHookTestBase):
    def _seed(self, hooks: dict) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps({"hooks": hooks}, indent=2))

    def test_preserves_nested_array_format(self) -> None:
        # Operator's real-machine style: SessionStart with nested-array.
        self._seed(
            {
                "SessionStart": [
                    {
                        "matcher": "startup",
                        "hooks": [
                            {"type": "command", "command": "echo hi", "timeout": 5},
                        ],
                    },
                ],
            }
        )
        rc, _, err = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        entries = self._ups_entries()
        self.assertEqual(len(entries), 1)
        # Nested-array: the entry has a `hooks` list.
        self.assertIn("hooks", entries[0])
        self.assertEqual(entries[0]["hooks"][0]["type"], "command")

    def test_preserves_flat_array_format(self) -> None:
        # Flat: hook event maps to a list of {type, command} dicts directly.
        self._seed(
            {
                "PreToolUse": [{"type": "command", "command": "echo pre"}],
            }
        )
        rc, _, err = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        entries = self._ups_entries()
        self.assertEqual(len(entries), 1)
        # Flat-array: the entry has command at the top level.
        self.assertEqual(entries[0]["type"], "command")
        self.assertNotIn("hooks", entries[0])

    def test_does_not_clobber_other_user_hooks(self) -> None:
        self._seed(
            {
                "SessionStart": [
                    {
                        "hooks": [{"type": "command", "command": "echo ss", "timeout": 5}],
                    }
                ],
                "PreToolUse": [
                    {
                        "hooks": [{"type": "command", "command": "echo pre", "timeout": 5}],
                    }
                ],
            }
        )
        rc, _, _ = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK))
        data = json.loads(self.settings.read_text())
        # Existing hooks untouched.
        self.assertEqual(
            data["hooks"]["SessionStart"][0]["hooks"][0]["command"],
            "echo ss",
        )
        self.assertEqual(
            data["hooks"]["PreToolUse"][0]["hooks"][0]["command"],
            "echo pre",
        )
        # New one added.
        self.assertEqual(len(data["hooks"]["UserPromptSubmit"]), 1)

    def test_refuses_on_malformed_settings_json(self) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text("not json {{{")
        rc, _, err = self._run_install()
        self.assertNotEqual(rc, int(ExitCode.OK))
        self.assertIn("malformed", err.lower())
        # File NOT overwritten.
        self.assertEqual(self.settings.read_text(), "not json {{{")


class UninstallTests(InstallHookTestBase):
    def test_uninstall_removes_only_our_entry(self) -> None:
        # Seed with a user's own UserPromptSubmit hook first.
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {"type": "command", "command": "echo theirs", "timeout": 5}
                                ]
                            },
                        ],
                    },
                }
            )
        )
        # Install ours.
        rc, _, _ = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertEqual(len(self._ups_entries()), 2)
        # Uninstall removes only our entry.
        rc, _, _ = self._run_uninstall()
        self.assertEqual(rc, int(ExitCode.OK))
        remaining = self._ups_entries()
        self.assertEqual(len(remaining), 1)
        # Their hook intact.
        their_cmd = remaining[0]["hooks"][0]["command"]
        self.assertEqual(their_cmd, "echo theirs")

    def test_uninstall_clears_marker(self) -> None:
        rc, _, _ = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertTrue(monitor.is_scheduled())
        rc, _, _ = self._run_uninstall()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertFalse(monitor.is_scheduled())

    def test_uninstall_idempotent_when_absent(self) -> None:
        # No settings.json, no marker.
        self.assertFalse(self.settings.exists())
        rc, _, _ = self._run_uninstall()
        self.assertEqual(rc, int(ExitCode.OK))

    def test_uninstall_when_nothing_installed(self) -> None:
        # settings.json exists but lacks our entry.
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [{"type": "command", "command": "echo pre"}],
                    },
                }
            )
        )
        rc, out, _ = self._run_uninstall()
        self.assertEqual(rc, int(ExitCode.OK))
        # Other hooks untouched.
        data = json.loads(self.settings.read_text())
        self.assertEqual(
            data["hooks"]["PreToolUse"][0]["command"],
            "echo pre",
        )


if __name__ == "__main__":
    unittest.main()
