"""CLI tip emission + CLAUDE.md injection prompt (phase `cli-hints`).

Covers the four-layer discoverability hardening from #19 phase 2:
  - `/clu-monitor` tip after `clu init` / `clu queue add` (TTY + marker-absent gate)
  - Interactive CLAUDE.md `## clu` injection on first init
  - `--inject-claude-md` / `--no-claude-md` flag overrides
  - Decline marker semantics (per-project, opt-out persistence)

All tests run in an isolated XDG dir so the real `~/.config/clu/monitor.json`
is never touched.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import monitor, registry
from end_of_line.cli import ExitCode, main
from tests import isolate_monitor_marker

_PLAN_BODY = "# placeholder plan\n"


def _make_project(tmp: Path) -> Path:
    project = tmp
    project.mkdir(parents=True, exist_ok=True)
    project = project.resolve()
    (project / "plans").mkdir(exist_ok=True)
    return project


def _write_plan(project: Path, slug: str) -> None:
    (project / "plans" / f"{slug}.md").write_text(_PLAN_BODY)


class _BaseHintsCase(unittest.TestCase):
    """Shared scaffolding: isolated XDG dir, fresh project root, TTY default ON.

    Tests opt out of TTY by stacking another patch over `_stdout_isatty` /
    `_stdin_isatty`. Default-on matches the operator's daily-driver shell
    where both streams are TTYs — the suppression cases are the deviations.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp_path)
        self.project = _make_project(self.tmp_path / "proj")

        # Default to TTY for stdin; tests override individually.
        self._stdin_tty = mock.patch("sys.stdin.isatty", return_value=True)
        self._stdin_tty.start()
        self.addCleanup(self._stdin_tty.stop)
        # stdout TTY tracked as a flag — applied inside `_run` after
        # `redirect_stdout` swaps sys.stdout to the capture buffer,
        # otherwise the patch lands on the wrong stream.
        self._stdout_tty_flag = True

    def _run(self, *argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            with mock.patch.object(
                sys.stdout,
                "isatty",
                return_value=self._stdout_tty_flag,
            ):
                rc = main(list(argv))
        return rc, buf.getvalue()

    def _init(self, *extra: str) -> tuple[int, str]:
        return self._run(
            "init",
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            "--no-notify-prompt",  # tests focus on CLAUDE.md / monitor tips, not notify
            *extra,
        )

    def _queue_add(self, *slugs: str) -> tuple[int, str]:
        return self._run(
            "queue",
            "add",
            *slugs,
            "--project",
            str(self.project),
        )


# ---------------------------------------------------------------------------
# cmd_init tip
# ---------------------------------------------------------------------------


class InitTipTestCase(_BaseHintsCase):
    def test_init_prints_monitor_tip_when_marker_absent_and_tty(self) -> None:
        rc, out = self._init()
        self.assertEqual(rc, 0)
        self.assertIn("/clu-monitor", out)
        self.assertIn("background notifications", out)

    def test_init_suppresses_tip_when_marker_present(self) -> None:
        monitor.record_hook_installed(
            "/abs/hook.py",
            "/home/x/.claude/settings.json",
        )
        rc, out = self._init()
        self.assertEqual(rc, 0)
        self.assertNotIn("/clu-monitor", out)

    def test_init_suppresses_tip_when_stdout_not_tty(self) -> None:
        self._stdout_tty_flag = False
        rc, out = self._init()
        self.assertEqual(rc, 0)
        self.assertNotIn("/clu-monitor", out)


# ---------------------------------------------------------------------------
# cmd_queue_add tip
# ---------------------------------------------------------------------------


class QueueAddTipTestCase(_BaseHintsCase):
    def setUp(self) -> None:
        super().setUp()
        # `clu queue add` requires the project to be in the host registry.
        registry.register(self.project, "seed")
        _write_plan(self.project, "seed")
        _write_plan(self.project, "a")
        _write_plan(self.project, "b")
        _write_plan(self.project, "c")

    def test_queue_add_prints_monitor_tip_when_marker_absent_and_tty(self) -> None:
        rc, out = self._queue_add("a")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("/clu-monitor", out)

    def test_queue_add_suppresses_tip_when_marker_present(self) -> None:
        monitor.record_hook_installed(
            "/abs/hook.py",
            "/home/x/.claude/settings.json",
        )
        rc, out = self._queue_add("a")
        self.assertEqual(rc, ExitCode.OK)
        self.assertNotIn("/clu-monitor", out)

    def test_queue_add_suppresses_tip_when_stdout_not_tty(self) -> None:
        self._stdout_tty_flag = False
        rc, out = self._queue_add("a")
        self.assertEqual(rc, ExitCode.OK)
        self.assertNotIn("/clu-monitor", out)

    def test_queue_add_multi_arg_prints_tip_once(self) -> None:
        rc, out = self._queue_add("a", "b", "c")
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(out.count("/clu-monitor"), 1)


# ---------------------------------------------------------------------------
# CLAUDE.md injection
# ---------------------------------------------------------------------------


CANONICAL_SECTION = """

## clu

This project uses clu for autonomous plan execution.

- `clu queue add <slug>` to enqueue a plan; cron dispatches on each tick.
- `clu queue list` for pending; `clu list` for fleet status.
- Run `/clu-monitor` once per machine for background notifications on
  halts and blockers (status: `~/.config/clu/monitor.json`).
- The `/plan`, `/clu-plan`, and `/brainstorm` skills (bundled via
  `clu install-skill`) are the canonical authoring + pre-planning entry
  points. `/plan` is project-agnostic; `/clu-plan` produces the master +
  sub-plan files clu's supervisor expects for queue dispatch.
"""


def _decline_marker(project: Path) -> Path:
    return project / "plans" / ".orchestrator" / ".no-claude-md"


class ClaudeMdInjectionTestCase(_BaseHintsCase):
    def _write_claude_md(self, body: str = "# Existing project doc\n") -> Path:
        p = self.project / "CLAUDE.md"
        p.write_text(body)
        return p

    def test_init_prompts_for_claude_md_inject_when_file_exists_no_section_no_marker(
        self,
    ) -> None:
        claude_md = self._write_claude_md()
        original = claude_md.read_text()
        with mock.patch("builtins.input", return_value="y"):
            rc, out = self._init()
        self.assertEqual(rc, 0)
        text = claude_md.read_text()
        self.assertTrue(text.startswith(original))
        self.assertIn("## clu", text)
        self.assertIn("Added clu section", out)

    def test_init_skips_prompt_when_no_claude_md_file(self) -> None:
        # No CLAUDE.md created. input() must NOT be called.
        with mock.patch(
            "builtins.input",
            side_effect=AssertionError("input called"),
        ):
            rc, _ = self._init()
        self.assertEqual(rc, 0)
        self.assertFalse(_decline_marker(self.project).exists())

    def test_init_skips_prompt_when_section_already_present(self) -> None:
        claude_md = self._write_claude_md("# Doc\n\n## clu\n\nalready here\n")
        before = claude_md.read_text()
        with mock.patch(
            "builtins.input",
            side_effect=AssertionError("input called"),
        ):
            rc, _ = self._init()
        self.assertEqual(rc, 0)
        self.assertEqual(claude_md.read_text(), before)

    def test_init_skips_prompt_when_decline_marker_present(self) -> None:
        claude_md = self._write_claude_md()
        # Pre-create the orchestrator dir so we can drop the marker
        # before main(["init", ...]) runs.
        marker = _decline_marker(self.project)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        before = claude_md.read_text()
        with mock.patch(
            "builtins.input",
            side_effect=AssertionError("input called"),
        ):
            rc, _ = self._init()
        self.assertEqual(rc, 0)
        self.assertEqual(claude_md.read_text(), before)

    def test_init_decline_writes_marker(self) -> None:
        claude_md = self._write_claude_md()
        before = claude_md.read_text()
        with mock.patch("builtins.input", return_value="n"):
            rc, _ = self._init()
        self.assertEqual(rc, 0)
        self.assertEqual(claude_md.read_text(), before)
        self.assertTrue(_decline_marker(self.project).exists())

    def test_init_decline_via_empty_input_writes_marker(self) -> None:
        claude_md = self._write_claude_md()
        before = claude_md.read_text()
        with mock.patch("builtins.input", return_value=""):
            rc, _ = self._init()
        self.assertEqual(rc, 0)
        self.assertEqual(claude_md.read_text(), before)
        self.assertTrue(_decline_marker(self.project).exists())

    def test_init_inject_flag_forces_inject_without_prompt(self) -> None:
        claude_md = self._write_claude_md()
        original = claude_md.read_text()
        with mock.patch(
            "builtins.input",
            side_effect=AssertionError("input called"),
        ):
            rc, _ = self._init("--inject-claude-md")
        self.assertEqual(rc, 0)
        text = claude_md.read_text()
        self.assertTrue(text.startswith(original))
        self.assertIn("## clu", text)
        self.assertFalse(_decline_marker(self.project).exists())

    def test_init_no_claude_md_flag_writes_decline_marker_without_prompt(self) -> None:
        claude_md = self._write_claude_md()
        before = claude_md.read_text()
        with mock.patch(
            "builtins.input",
            side_effect=AssertionError("input called"),
        ):
            rc, _ = self._init("--no-claude-md")
        self.assertEqual(rc, 0)
        self.assertEqual(claude_md.read_text(), before)
        self.assertTrue(_decline_marker(self.project).exists())

    def test_init_no_claude_md_flag_idempotent_with_existing_marker(self) -> None:
        self._write_claude_md()
        marker = _decline_marker(self.project)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        mtime_before = marker.stat().st_mtime
        with mock.patch(
            "builtins.input",
            side_effect=AssertionError("input called"),
        ):
            rc, _ = self._init("--no-claude-md")
        self.assertEqual(rc, 0)
        self.assertTrue(marker.exists())
        # touch(exist_ok=True) without modifying — mtime should be unchanged
        # in practice, but we don't strictly require equality. The key check:
        # the operation didn't error.
        self.assertGreaterEqual(marker.stat().st_mtime, mtime_before - 1)

    def test_init_inject_flag_idempotent_with_existing_section(self) -> None:
        claude_md = self._write_claude_md("# Doc\n\n## clu\n\nalready here\n")
        before = claude_md.read_text()
        with mock.patch(
            "builtins.input",
            side_effect=AssertionError("input called"),
        ):
            rc, _ = self._init("--inject-claude-md")
        self.assertEqual(rc, 0)
        # No double-append; bytes unchanged.
        self.assertEqual(claude_md.read_text(), before)

    def test_init_skips_prompt_when_stdin_not_tty(self) -> None:
        claude_md = self._write_claude_md()
        before = claude_md.read_text()
        with mock.patch("sys.stdin.isatty", return_value=False):
            with mock.patch(
                "builtins.input",
                side_effect=AssertionError("input called"),
            ):
                rc, _ = self._init()
        self.assertEqual(rc, 0)
        self.assertEqual(claude_md.read_text(), before)
        # Operator didn't decline — no marker.
        self.assertFalse(_decline_marker(self.project).exists())

    def test_init_appends_canonical_section_verbatim(self) -> None:
        claude_md = self._write_claude_md("# Doc\n")
        original = claude_md.read_text()
        with mock.patch("builtins.input", return_value="y"):
            rc, _ = self._init()
        self.assertEqual(rc, 0)
        text = claude_md.read_text()
        self.assertEqual(text, original + CANONICAL_SECTION)


class WorkerModelTipTestCase(_BaseHintsCase):
    """`clu init` / operator `clu queue add` surface the worker model.

    Default (no `.orchestrator.json` → empty `dispatch.command`) prints
    the "resolves via settings" line. A config with `--model X` in
    dispatch.command prints the pinned name. Worker callback path
    (`--token`) skips both — covered by routing, not asserted here.
    """

    def _write_config(self, command: str) -> None:
        (self.project / ".orchestrator.json").write_text(
            json.dumps(
                {
                    "plan_dir": "plans",
                    "dispatch": {"kind": "shell", "command": command},
                }
            )
        )

    def test_init_unpinned_prints_settings_line(self) -> None:
        rc, out = self._init()
        self.assertEqual(rc, 0)
        self.assertIn("worker model: resolves via Claude Code settings", out)
        self.assertIn("no --model in dispatch.command", out)

    def test_init_pinned_prints_model_name(self) -> None:
        self._write_config("claude --print --model claude-opus-4-7 '/clu-phase {plan_slug}'")
        rc, out = self._init()
        self.assertEqual(rc, 0)
        self.assertIn(
            "worker model: claude-opus-4-7 (pinned via --model in dispatch.command)",
            out,
        )

    def test_queue_add_pinned_prints_model_name(self) -> None:
        self._write_config("claude --print --model claude-sonnet-4-6 '/clu-phase'")
        rc, _ = self._init()
        self.assertEqual(rc, 0)
        _write_plan(self.project, "next-plan")
        rc, out = self._queue_add("next-plan")
        self.assertEqual(rc, 0)
        self.assertIn(
            "worker model: claude-sonnet-4-6 (pinned via --model in dispatch.command)",
            out,
        )


if __name__ == "__main__":
    unittest.main()
