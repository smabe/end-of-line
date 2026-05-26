"""Tests for `clu doctor` + the extracted `dispatch.build_worker_env` helper.

Closes #14.  Mirrors test_dispatch.py's setUp/isolate_registry pattern; the
doctor command is read-only so most cases construct a `ProjectConfig`
directly via `_write_cfg` rather than the heavier `clu init` path.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import dispatch
from end_of_line import state as st
from end_of_line.cli import main
from end_of_line.config import DispatchSpec, ProjectConfig
from tests import isolate_registry

PLAN = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `t-a.md` | thing | 1h |
"""


class BuildWorkerEnvTestCase(unittest.TestCase):
    """Helper-level tests — pure function, no filesystem."""

    def _cfg(self, path: str) -> ProjectConfig:
        return ProjectConfig(
            project_root=Path("/tmp/x"),
            dispatch=DispatchSpec(kind="shell", command="ignored", path=path),
        )

    def test_build_worker_env_with_path_override(self) -> None:
        env = dispatch.build_worker_env(self._cfg("/foo:/bar"))
        self.assertIsNotNone(env)
        assert env is not None  # narrow for type-checkers / future readers
        self.assertEqual(env["PATH"], "/foo:/bar")
        # Merge semantic: HOME survives the override (the bug from #9).
        if "HOME" in os.environ:
            self.assertEqual(env["HOME"], os.environ["HOME"])

    def test_build_worker_env_without_path_override(self) -> None:
        # Empty string is the missing-field default; helper must treat
        # it as "no override" and return None so the caller leaves env unset.
        self.assertIsNone(dispatch.build_worker_env(self._cfg("")))


class DoctorCommandTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_cfg(self, extra: dict | None = None, **dispatch_fields) -> None:
        payload: dict = {
            "dispatch": {"kind": "shell", "command": "echo", **dispatch_fields},
        }
        if extra:
            payload.update(extra)
        (self.project / ".orchestrator.json").write_text(json.dumps(payload))

    def _run_doctor(self, project: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        target = project if project is not None else self.project
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["doctor", "--project", str(target)])
        return rc, out.getvalue(), err.getvalue()

    def test_doctor_prints_path_and_resolved_binaries(self) -> None:
        # Use the same PATH the test process has — that way at least one
        # of gh/pipx/clu is likely resolved on the operator's machine,
        # but the assertions don't require any specific binary to exist.
        path = os.environ["PATH"]
        self._write_cfg(path=path)
        rc, stdout, _ = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertIn(f"PATH={path}", stdout)
        # Each probed binary either resolves or is reported NOT FOUND;
        # both branches mention the binary name on the line.
        for name in ("gh", "pipx", "clu"):
            self.assertIn(name, stdout)
        self.assertIn("(source: dispatch.path)", stdout)

    def test_doctor_prints_inherited_when_no_override(self) -> None:
        # No `path` field => DispatchSpec.path == "" => no override.
        self._write_cfg()
        rc, stdout, _ = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertIn("(source: inherited)", stdout)

    def test_doctor_handles_missing_binary(self) -> None:
        # An empty-ish PATH guarantees `command -v` fails for every probe,
        # which is the failure mode operators hit when LaunchAgent sparse
        # PATH masks gh/pipx/clu. The shell guard must convert each miss
        # into a `NOT FOUND: <name>` line.
        self._write_cfg(path="/nonexistent-dir-xyzzy")
        rc, stdout, _ = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertIn("NOT FOUND: gh", stdout)
        self.assertIn("NOT FOUND: pipx", stdout)
        self.assertIn("NOT FOUND: clu", stdout)

    def test_doctor_missing_orchestrator_json(self) -> None:
        # A fresh dir with no .orchestrator.json. cmd_doctor refuses
        # (without an override there's nothing useful to report; the
        # operator is asking about a project that isn't initialized).
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        rc, _, stderr = self._run_doctor(project=empty)
        self.assertNotEqual(rc, 0)
        self.assertIn(".orchestrator.json", stderr)

    def test_doctor_worktree_flag_reports_alive_and_missing(self) -> None:
        """`--worktree` walks plans, reports liveness + missing rows."""
        # Need a real git repo for `clu init --worktree` to succeed.
        import subprocess

        subprocess.run(["git", "-C", str(self.project), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.email", "t@t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.name", "t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "--allow-empty", "-m", "i"],
            check=True,
            capture_output=True,
        )
        # Plan alpha: with worktree (alive). Plan beta: with worktree
        # that we delete to simulate operator removal. Plan gamma:
        # plain init (no worktree → "(none)").
        for slug in ("alpha", "beta", "gamma"):
            (self.project / "plans" / f"{slug}.md").write_text(PLAN)
        self._write_cfg(path=os.environ["PATH"])
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["init", "--project", str(self.project), "--plan", "alpha", "--worktree"])
            main(["init", "--project", str(self.project), "--plan", "beta", "--worktree"])
            main(["init", "--project", str(self.project), "--plan", "gamma"])
        # Wipe beta's worktree dir to trigger MISSING.
        beta_wt = self.project.resolve().parent / f"{self.project.name}-beta"
        import shutil

        shutil.rmtree(beta_wt)

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["doctor", "--project", str(self.project), "--worktree"])
        stdout = out.getvalue()
        self.assertEqual(rc, 0)
        # Each plan should appear with its expected status.
        self.assertRegex(stdout, r"alpha\s+ok\s+")
        self.assertRegex(stdout, r"beta\s+MISSING\s+")
        self.assertRegex(stdout, r"gamma\s+-\s+\(none\)")

    def test_doctor_without_worktree_flag_skips_section(self) -> None:
        self._write_cfg(path=os.environ["PATH"])
        rc, stdout, _ = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertNotIn("Worktrees:", stdout)

    def test_doctor_does_not_touch_state(self) -> None:
        # Initialize a plan, snapshot state.json mtime, run doctor, confirm
        # mtime unchanged.  Guarantees the command is pure read.
        (self.project / "plans" / "t.md").write_text(PLAN)
        self._write_cfg(path=os.environ["PATH"])
        main(["init", "--project", str(self.project), "--plan", "t"])
        state_path = self.project / "plans" / ".orchestrator" / "t.state.json"
        before = state_path.stat().st_mtime_ns
        self._run_doctor()
        after = state_path.stat().st_mtime_ns
        self.assertEqual(before, after)

    # ---- coolant section ------------------------------------------------------

    def test_doctor_reports_coolant_disabled(self) -> None:
        self._write_cfg(extra={"coolant": {"enabled": False}})
        rc, stdout, _ = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertIn("Coolant:", stdout)
        self.assertIn("disabled", stdout)

    def test_doctor_reports_coolant_script_dir_override(self) -> None:
        scripts = self.project / "fake-coolant-scripts"
        scripts.mkdir()
        self._write_cfg(extra={"coolant": {"script_dir": str(scripts)}})
        rc, stdout, _ = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertIn(str(scripts), stdout)
        self.assertIn("override", stdout)

    def test_doctor_reports_coolant_override_missing(self) -> None:
        bogus = self.project / "no-such-dir"
        self._write_cfg(extra={"coolant": {"script_dir": str(bogus)}})
        # `_marketplace_glob` may resolve to a real install on the dev
        # machine; mock it so the override-misses branch is exercised
        # cleanly.
        from unittest import mock

        with mock.patch("end_of_line.coolant._marketplace_glob", return_value=None):
            rc, stdout, _ = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertIn("does not resolve", stdout)

    def test_doctor_reports_coolant_not_installed(self) -> None:
        self._write_cfg(extra={"coolant": {}})
        from unittest import mock

        with mock.patch("end_of_line.coolant._marketplace_glob", return_value=None):
            rc, stdout, _ = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertIn("coolant scripts not found", stdout)

    def _write_cfg_with_notify(self, channels: list[dict]) -> None:
        payload = {
            "dispatch": {"kind": "shell", "command": "echo"},
            "notify": {"channels": channels},
        }
        (self.project / ".orchestrator.json").write_text(json.dumps(payload))

    def test_doctor_notify_section_absent_without_imessage(self) -> None:
        self._write_cfg(path=os.environ["PATH"])
        _, stdout, _ = self._run_doctor()
        self.assertNotIn("Notify channels:", stdout)

    def test_doctor_reports_override_without_chatdb(self) -> None:
        # An explicit self_chat_id short-circuits the chat.db lookup, so
        # doctor reports cleanly even in test environments without chat.db.
        self._write_cfg_with_notify(
            [
                {"kind": "imessage", "to": "+15551234567", "self_chat_id": "+15551234567"},
            ]
        )
        _, stdout, _ = self._run_doctor()
        self.assertIn("Notify channels:", stdout)
        self.assertIn("self_chat=+15551234567", stdout)
        self.assertIn("override", stdout)

    def test_doctor_reports_resolver_error_for_unmatched_handle(self) -> None:
        # No override + a synthetic handle that won't match the operator's
        # real chat.db → resolver surfaces SelfChatLookupError, doctor prints
        # the hint pointing at the override knob.
        self._write_cfg_with_notify(
            [
                {"kind": "imessage", "to": "+15550000000"},
            ]
        )
        _, stdout, _ = self._run_doctor()
        self.assertIn("Notify channels:", stdout)
        self.assertIn("self_chat_id", stdout)
        self.assertIn("+15550000000", stdout)


class DoctorEffortWarningTestCase(unittest.TestCase):
    """Tests for the Effort-cell health scan added in lease-reliability/#58."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / ".orchestrator.json").write_text(
            json.dumps({"dispatch": {"kind": "shell", "command": "echo"}})
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init_plan(self, slug: str, effort: str) -> None:
        plan_md = (
            f"# {slug}\n\n## Sessions index\n\n"
            "| Session | Plan file | Scope | Effort |\n"
            "|---|---|---|---|\n"
            f"| A | `{slug}-a.md` | thing | {effort} |\n"
        )
        (self.project / "plans" / f"{slug}.md").write_text(plan_md)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            main(["init", "--project", str(self.project), "--plan", slug])

    def _run_doctor(self) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["doctor", "--project", str(self.project)])
        return rc, out.getvalue()

    def test_doctor_warns_on_malformed_effort(self) -> None:
        self._init_plan("myplan", "abc")
        rc, stdout = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertIn("[warn]", stdout)
        self.assertIn("myplan:a", stdout)
        self.assertIn("Effort=abc", stdout)

    def test_doctor_silent_when_effort_clean(self) -> None:
        self._init_plan("myplan", "1h")
        _, stdout = self._run_doctor()
        self.assertNotIn("[warn]", stdout)

    def test_doctor_silent_when_effort_empty(self) -> None:
        self._init_plan("myplan", "")
        _, stdout = self._run_doctor()
        self.assertNotIn("[warn]", stdout)


if __name__ == "__main__":
    unittest.main()
