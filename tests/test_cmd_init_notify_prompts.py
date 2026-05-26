"""`clu init` notify channel setup prompts.

Tests for --no-notify-prompt flag and interactive iMessage/Discord prompts
added in the notify-multi-channel docs phase.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line.cli import ExitCode, main
from end_of_line.config import CONFIG_FILENAME, load_project_config
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `foo-a.md` | thing | 1h |
"""


class InitNotifyPromptsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        isolate_registry(self, Path(self._tmp.name))
        (self.project / "plans").mkdir()
        (self.project / "plans" / "foo.md").write_text(PLAN_BODY)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "init",
                    "--project",
                    str(self.project),
                    "--plan",
                    "foo",
                    "--no-claude-md",  # avoid CLAUDE.md prompt interference
                    *extra,
                ]
            )
        return rc, out.getvalue(), err.getvalue()

    # --- flag recognition --------------------------------------------------

    def test_init_no_notify_prompt_flag_recognized(self) -> None:
        rc, _out, err = self._init("--no-notify-prompt")
        self.assertEqual(rc, 0, err)

    # --- --no-notify-prompt skips all prompts ------------------------------

    def test_init_no_notify_prompt_skips_prompts(self) -> None:
        rc, _out, err = self._init("--no-notify-prompt")
        self.assertEqual(rc, 0, err)
        cfg = load_project_config(self.project)
        self.assertEqual(cfg.notify.channels, ())

    # --- interactive prompt: iMessage default Y on macOS -------------------

    def test_init_imessage_prompt_default_yes_on_macos(self) -> None:
        # Enter (accept Y default) for iMessage, then provide handle.
        # Enter (accept N default) for Discord.
        inputs = iter(["", "+15550001234", ""])
        with (
            mock.patch("platform.system", return_value="Darwin"),
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)),
        ):
            rc, _out, err = self._init()
        self.assertEqual(rc, 0, err)
        cfg = load_project_config(self.project)
        self.assertEqual(len(cfg.notify.channels), 1)
        ch = cfg.notify.channels[0]
        self.assertEqual(ch.kind, "imessage")
        self.assertEqual(ch.params["to"], "+15550001234")

    # --- interactive prompt: iMessage default N off macOS ------------------

    def test_init_imessage_prompt_default_no_off_mac(self) -> None:
        # Enter (accept N default) for iMessage + Enter (accept N) for Discord.
        inputs = iter(["", ""])
        with (
            mock.patch("platform.system", return_value="Linux"),
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)),
        ):
            rc, _out, err = self._init()
        self.assertEqual(rc, 0, err)
        cfg = load_project_config(self.project)
        self.assertEqual(cfg.notify.channels, ())

    # --- interactive prompt: TTY guard skips prompts in non-TTY context ---

    def test_init_non_tty_skips_notify_prompts(self) -> None:
        # sys.stdin.isatty() returns False → no prompts, no config written.
        with mock.patch("sys.stdin.isatty", return_value=False):
            rc, _out, err = self._init()
        self.assertEqual(rc, 0, err)
        cfg = load_project_config(self.project)
        self.assertEqual(cfg.notify.channels, ())

    # --- interactive prompt: Discord "yes" path ----------------------------

    def test_init_discord_prompt_writes_channel_when_yes(self) -> None:
        # "n" → skip iMessage, "y" → Discord, then token + user_id.
        inputs = iter(["n", "y", "Bot.Token.Here", "987654321"])
        with (
            mock.patch("platform.system", return_value="Darwin"),
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)),
        ):
            rc, _out, err = self._init()
        self.assertEqual(rc, 0, err)
        cfg = load_project_config(self.project)
        self.assertEqual(len(cfg.notify.channels), 1)
        ch = cfg.notify.channels[0]
        self.assertEqual(ch.kind, "discord")
        self.assertEqual(ch.params["bot_token"], "Bot.Token.Here")
        self.assertEqual(ch.params["user_id"], "987654321")
