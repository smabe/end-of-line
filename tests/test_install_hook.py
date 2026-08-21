"""Tests for `clu install-hook` / `clu uninstall-hook` — register clu's
hook scripts in `~/.claude/settings.json`.

Settings.json format detection: the operator's machine may already have
hook entries in either nested-array `{matcher?, hooks: [{type, command,
timeout?}]}` shape or flat-array `{type, command}` shape. Install must
detect and preserve whichever style is already present.

Idempotency contract: install detects an existing entry by the hook
script's BASENAME, not its absolute path. Absolute-path matching is why a
clu reinstalled into a new venv appended a duplicate entry beside the one
already working, and why uninstall then orphaned the old one. Re-running
install is a no-op; re-running it from a NEW path is also a no-op.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import db, monitor
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
        # These cases exercise UserPromptSubmit install MECHANICS
        # (idempotence, array style, not clobbering), which now live
        # behind `--inbox` since the inbox surface was retired.
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-hook", "--inbox"])
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

    def test_the_answer_crosses_a_process_boundary(self) -> None:
        # The predicate's whole job is to be read by a LATER invocation — the
        # CLI hint, `/clu-monitor`'s short-circuit. In-process assertions
        # would pass against a value cached in this interpreter, so this one
        # asks a fresh python, exactly the way the next `clu` command will.
        # It reads settings.json now rather than the marker, so removing the
        # ENTRY is what has to flip the answer.
        rc, _, err = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertEqual(self._hook_state_in_a_fresh_process(), "PRESENT")

        self.settings.write_text(json.dumps({"hooks": {}}))
        self.assertEqual(self._hook_state_in_a_fresh_process(), "ABSENT")

    def _hook_state_in_a_fresh_process(self) -> str:
        env = dict(os.environ)
        env["PYTHONPATH"] = (
            str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from end_of_line import monitor; "
                "print(monitor.hook_state(monitor.Surface.INBOX).name)",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        return proc.stdout.strip()

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

    def test_uninstall_clears_the_install_record(self) -> None:
        rc, _, _ = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertIsNotNone(monitor.load_marker())
        rc, _, _ = self._run_uninstall()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertIsNone(monitor.load_marker())

    def test_uninstall_survives_a_record_it_cannot_clear(self) -> None:
        # Clearing used to be an unlink that could not realistically fail. The
        # record now lives in the host database, and the hook entry is already
        # gone from settings.json by the time it is cleared — so a raise here
        # would leave a half-finished uninstall reported as a traceback.
        rc, _, _ = self._run_install()
        self.assertEqual(rc, int(ExitCode.OK))
        with mock.patch.object(
            monitor, "clear_surface_marker", side_effect=db.DbBusy("host db busy")
        ):
            rc, _, err = self._run_uninstall()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertIn("install record could not be cleared", err)
        # The hook entry itself is gone — the half that matters succeeded.
        self.assertNotIn("clu_inbox_surface", self.settings.read_text())

    def test_uninstall_removes_an_entry_left_by_a_clu_that_has_since_moved(self) -> None:
        # Absolute-path matching orphaned exactly this entry: install from a
        # new venv appended a second one, and uninstall then removed only the
        # new one, leaving a dead hook firing on every prompt.
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {"type": "command", "command": "/old/py -u /old/clu_inbox_surface.py"}
                        ],
                        "SessionStart": [
                            {"type": "command", "command": "/old/py -u /old/clu_session_start.py"}
                        ],
                    }
                }
            )
        )
        rc, out, _ = self._run_uninstall()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertNotIn("clu_inbox_surface", self.settings.read_text())
        self.assertNotIn("clu_session_start", self.settings.read_text())
        self.assertNotIn("No clu hooks present", out)

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

class BasenameRecognitionTests(InstallHookTestBase):
    """A clu that moved must recognise its own hook, not duplicate it.

    Absolute-path matching is why a reinstall into a new venv appended a
    second entry beside a working one; both then fired on every event, and
    uninstall (also path-matched) orphaned the older one.
    """

    def _seed(self, hooks: dict) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps({"hooks": hooks}, indent=2))

    def _ss_entries(self) -> list:
        data = json.loads(self.settings.read_text())
        return data.get("hooks", {}).get("SessionStart", [])

    def _install(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-hook", *args])
        return rc, out.getvalue(), err.getvalue()

    def test_install_recognises_the_same_hook_at_a_different_path(self) -> None:
        self._seed(
            {
                "SessionStart": [
                    {"type": "command", "command": "/old/venv/py -u /old/co/clu_session_start.py"}
                ]
            }
        )
        rc, out, err = self._install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertEqual(len(self._ss_entries()), 1)
        self.assertIn("already installed", out)

    def test_the_already_installed_message_names_the_path_actually_installed(self) -> None:
        # Printing clu's own resolved path would assert something false —
        # the entry that exists points somewhere else.
        self._seed(
            {
                "SessionStart": [
                    {"type": "command", "command": "/old/venv/py -u /old/co/clu_session_start.py"}
                ]
            }
        )
        _, out, _ = self._install()
        self.assertIn("/old/co/clu_session_start.py", out)

    def test_install_does_not_rewrite_the_entry_it_recognises(self) -> None:
        # Recognise, do not repoint: settings.json is the operator's file and
        # the path in it still works.
        self._seed(
            {
                "SessionStart": [
                    {"type": "command", "command": "/old/venv/py -u /old/co/clu_session_start.py"}
                ]
            }
        )
        before = self.settings.read_text()
        self._install()
        self.assertEqual(self.settings.read_text(), before)

    def test_install_dedupes_entries_a_previous_install_left(self) -> None:
        self._seed(
            {
                "SessionStart": [
                    {"type": "command", "command": "/a/py -u /a/clu_session_start.py"},
                    {"type": "command", "command": "/b/py -u /b/clu_session_start.py"},
                    {"type": "command", "command": "/c/py -u /c/clu_session_start.py"},
                ]
            }
        )
        rc, out, err = self._install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        entries = self._ss_entries()
        self.assertEqual(len(entries), 1)
        # The FIRST one survives — the oldest install, the one whose date the
        # marker records.
        self.assertIn("/a/clu_session_start.py", entries[0]["command"])
        self.assertIn("removed 2 duplicate entries", out)

    def test_deduping_leaves_the_operators_own_entries_alone(self) -> None:
        # settings.json is theirs. Pruning clu's leftovers is defensible;
        # touching anything else is not.
        self._seed(
            {
                "SessionStart": [
                    {"type": "command", "command": "echo theirs"},
                    {"type": "command", "command": "/a/py -u /a/clu_session_start.py"},
                    {"type": "command", "command": "/b/py -u /b/clu_session_start.py"},
                    {"type": "command", "command": "echo also theirs"},
                ]
            }
        )
        self._install()
        commands = [e["command"] for e in self._ss_entries()]
        self.assertEqual(
            commands,
            ["echo theirs", "/a/py -u /a/clu_session_start.py", "echo also theirs"],
        )

    def test_installing_twice_from_two_paths_leaves_one_entry(self) -> None:
        # The end-to-end shape of the bug: install, move clu, install again.
        self._install()
        entry = self._ss_entries()[0]
        command = entry.get("command") or entry["hooks"][0]["command"]
        moved = command.replace("clu_session_start.py", "elsewhere/clu_session_start.py")
        self._seed({"SessionStart": [{"type": "command", "command": moved}]})
        self._install()
        self.assertEqual(len(self._ss_entries()), 1)


class CheckOnlyTests(InstallHookTestBase):
    """`clu install-hook --check` — the supported read-only answer."""

    def _check(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-hook", "--check"])
        return rc, out.getvalue(), err.getvalue()

    def _install(self, *args: str) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(["install-hook", *args])

    def test_check_reports_not_installed_on_a_clean_machine(self) -> None:
        rc, out, err = self._check()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertIn("SessionStart: not installed", out)
        self.assertIn("UserPromptSubmit: not installed", out)

    def test_check_writes_nothing(self) -> None:
        self.assertFalse(self.settings.exists())
        self._check()
        self.assertFalse(self.settings.exists())
        self.assertIsNone(monitor.load_marker())

    def test_check_reports_installed_after_an_install(self) -> None:
        self.assertEqual(self._install(), int(ExitCode.OK))
        _, out, _ = self._check()
        self.assertIn("SessionStart: installed", out)
        self.assertIn("UserPromptSubmit: not installed", out)
        self.assertIn("recorded", out)

    def test_check_reports_installed_with_no_install_record_at_all(self) -> None:
        # THE live divergence: the entry is in settings.json and the monitor
        # table is empty. The answer comes from the file.
        self.assertEqual(self._install(), int(ExitCode.OK))
        monitor.clear_marker()
        _, out, _ = self._check()
        self.assertIn("SessionStart: installed", out)
        self.assertNotIn("recorded", out)

    def test_check_reports_not_installed_when_only_a_record_survives(self) -> None:
        # The reverse divergence: the operator hand-removed the entry.
        self.assertEqual(self._install(), int(ExitCode.OK))
        self.settings.write_text(json.dumps({"hooks": {}}))
        _, out, _ = self._check()
        self.assertIn("SessionStart: not installed", out)

    def test_check_says_unknown_rather_than_not_installed_on_a_bad_file(self) -> None:
        # SAFE DIRECTION: "cannot tell" must never be reported as "not
        # installed" — that is the answer that sends `/clu-monitor` off to
        # install a hook that is already there.
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text("not json {{{")
        rc, out, err = self._check()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertIn("SessionStart: unknown", out)
        self.assertNotIn("not installed", out)

    def test_check_does_not_refuse_on_a_malformed_file(self) -> None:
        # The INSTALL path refuses (it would have to guess how to repair);
        # a read-only report has nothing to repair and must stay usable.
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text("not json {{{")
        rc, _, _ = self._check()
        self.assertEqual(rc, int(ExitCode.OK))
        self.assertEqual(self.settings.read_text(), "not json {{{")


class DefaultSurfaceTests(InstallHookTestBase):
    """The inbox surface is retired: `install-hook` arms the operator
    dashboard (SessionStart) and leaves the inbox hook unwired unless
    `--inbox` asks for it explicitly. The inbox code stays on disk and
    importable — only the thing that STARTS it is gone.
    """

    def _ss_entries(self) -> list:
        data = json.loads(self.settings.read_text())
        return data.get("hooks", {}).get("SessionStart", [])

    def _install(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["install-hook", *args])
        return rc, out.getvalue(), err.getvalue()

    def test_default_install_arms_session_start(self) -> None:
        rc, _, err = self._install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertEqual(len(self._ss_entries()), 1)

    def test_default_install_does_not_wire_the_inbox_hook(self) -> None:
        rc, _, err = self._install()
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertEqual(self._ups_entries(), [])

    def test_default_install_still_writes_a_marker(self) -> None:
        # `/clu-monitor` short-circuits on marker presence; the retired
        # inbox surface must not take that idempotence guard with it.
        rc, _, _ = self._install()
        self.assertEqual(rc, int(ExitCode.OK))
        m = must(monitor.load_marker())
        self.assertIn("session_start_hook_path", m)
        self.assertIn("settings_json_path", m)

    def test_inbox_flag_wires_the_dormant_surface_back_up(self) -> None:
        rc, _, err = self._install("--inbox")
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertEqual(len(self._ups_entries()), 1)
        self.assertEqual(len(self._ss_entries()), 1)

    def test_reinstall_does_not_bump_the_install_timestamp(self) -> None:
        # `/clu-monitor` prints this field back as "installed <when>". A no-op
        # re-run writes no settings, so it must not rewrite the date either.
        self._install()
        first = must(monitor.load_marker())["session_start_installed_at"]
        self._install()
        self.assertEqual(must(monitor.load_marker())["session_start_installed_at"], first)

    def test_session_start_flag_says_it_no_longer_adds_the_inbox(self) -> None:
        rc, out, err = self._install("--session-start")
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertIn("--inbox", out)
        self.assertEqual(self._ups_entries(), [])

    def test_session_start_flag_is_accepted_and_redundant(self) -> None:
        # Kept so existing muscle memory and scripts don't break.
        rc, _, err = self._install("--session-start")
        self.assertEqual(rc, int(ExitCode.OK), msg=err)
        self.assertEqual(len(self._ss_entries()), 1)
        self.assertEqual(self._ups_entries(), [])


if __name__ == "__main__":
    unittest.main()
