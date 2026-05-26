"""Unit tests for end_of_line.coolant — script-path resolver + fire-and-forget shell-out.

Coverage:
  - resolve_script_dir() picks up CLU_COOLANT_SCRIPT_DIR override,
    falls through to the marketplace cache glob, returns None if neither.
  - emit_start / emit_stop short-circuit on empty session_id or agent_id.
  - emit_start / emit_stop no-op when no script dir resolves.
  - subprocess.run is called with stdout=DEVNULL, stderr=DEVNULL,
    timeout=2, check=False, and the stdin JSON has the expected shape.
  - TimeoutExpired and FileNotFoundError are swallowed (fire-and-forget).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

from end_of_line import coolant
from tests import CluTestCase


def _write_fake_scripts(scripts_dir: Path) -> None:
    """Place no-op `agent-start.sh` / `agent-stop.sh` in a tmp dir."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in ("agent-start.sh", "agent-stop.sh"):
        (scripts_dir / name).write_text("#!/bin/bash\nexit 0\n")
        (scripts_dir / name).chmod(0o755)


class TestResolveScriptDir(CluTestCase):
    def test_env_var_override_wins(self):
        scripts = self.tmp_path / "scripts"
        _write_fake_scripts(scripts)
        with mock.patch.dict(os.environ, {"CLU_COOLANT_SCRIPT_DIR": str(scripts)}):
            self.assertEqual(coolant.resolve_script_dir(), scripts)

    def test_env_var_pointing_at_missing_dir_returns_none(self):
        with mock.patch.dict(os.environ, {"CLU_COOLANT_SCRIPT_DIR": str(self.tmp_path / "nope")}):
            with mock.patch.object(coolant, "_marketplace_glob", return_value=None):
                self.assertIsNone(coolant.resolve_script_dir())

    def test_empty_env_var_treated_as_unset(self):
        scripts = self.tmp_path / "scripts"
        _write_fake_scripts(scripts)
        with mock.patch.dict(os.environ, {"CLU_COOLANT_SCRIPT_DIR": ""}):
            with mock.patch.object(coolant, "_marketplace_glob", return_value=scripts):
                self.assertEqual(coolant.resolve_script_dir(), scripts)

    def test_marketplace_glob_fallback(self):
        scripts = self.tmp_path / "mp-scripts"
        _write_fake_scripts(scripts)
        with mock.patch.dict(os.environ, {"CLU_COOLANT_SCRIPT_DIR": ""}):
            with mock.patch.object(coolant, "_marketplace_glob", return_value=scripts):
                self.assertEqual(coolant.resolve_script_dir(), scripts)

    def test_no_resolution_returns_none(self):
        with mock.patch.dict(os.environ, {"CLU_COOLANT_SCRIPT_DIR": ""}):
            with mock.patch.object(coolant, "_marketplace_glob", return_value=None):
                self.assertIsNone(coolant.resolve_script_dir())

    def test_override_arg_takes_precedence_over_env(self):
        env_scripts = self.tmp_path / "env-scripts"
        override_scripts = self.tmp_path / "override-scripts"
        _write_fake_scripts(env_scripts)
        _write_fake_scripts(override_scripts)
        with mock.patch.dict(os.environ, {"CLU_COOLANT_SCRIPT_DIR": str(env_scripts)}):
            self.assertEqual(
                coolant.resolve_script_dir(override=str(override_scripts)),
                override_scripts,
            )


class TestMarketplaceGlob(CluTestCase):
    """Verify the version-sorted glob behavior in isolation."""

    def test_picks_highest_version_when_multiple(self):
        cache_root = self.tmp_path / "cache" / "todd-w-shaffer" / "coolant"
        for v in ("0.1.0", "0.2.0", "0.1.5"):
            (cache_root / v / "scripts").mkdir(parents=True)
            _write_fake_scripts(cache_root / v / "scripts")
        with mock.patch.object(coolant, "_plugin_cache_root", return_value=self.tmp_path / "cache"):
            result = coolant._marketplace_glob()
            self.assertIsNotNone(result)
            self.assertEqual(result.parent.name, "0.2.0")

    def test_returns_none_when_cache_root_missing(self):
        with mock.patch.object(coolant, "_plugin_cache_root", return_value=self.tmp_path / "nope"):
            self.assertIsNone(coolant._marketplace_glob())


class TestEmitStartShortCircuit(CluTestCase):
    """Validation guards on emit_start — must no-op on empty inputs."""

    def test_empty_session_id_short_circuits(self):
        with mock.patch.object(coolant, "resolve_script_dir") as resolver:
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_start(
                    session_id="",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )
                resolver.assert_not_called()
                run.assert_not_called()

    def test_empty_agent_id_short_circuits(self):
        with mock.patch.object(coolant, "resolve_script_dir") as resolver:
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_start(
                    session_id="tok",
                    agent_id="",
                    agent_type="clu-worker",
                )
                resolver.assert_not_called()
                run.assert_not_called()

    def test_no_script_dir_no_ops(self):
        with mock.patch.object(coolant, "resolve_script_dir", return_value=None):
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_start(
                    session_id="tok",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )
                run.assert_not_called()


class TestEmitStartSubprocess(CluTestCase):
    """Behavior of the actual subprocess call — payload + kwargs."""

    def _resolved(self):
        scripts = self.tmp_path / "scripts"
        _write_fake_scripts(scripts)
        return scripts

    def test_calls_agent_start_with_devnull_redirects(self):
        scripts = self._resolved()
        with mock.patch.object(coolant, "resolve_script_dir", return_value=scripts):
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_start(
                    session_id="tok",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )
                run.assert_called_once()
                call = run.call_args
                self.assertEqual(call.args[0], [str(scripts / "agent-start.sh")])
                self.assertEqual(call.kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)
                self.assertEqual(call.kwargs["timeout"], 2)
                self.assertEqual(call.kwargs["check"], False)

    def test_stdin_payload_shape(self):
        scripts = self._resolved()
        with mock.patch.object(coolant, "resolve_script_dir", return_value=scripts):
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_start(
                    session_id="tok-abc",
                    agent_id="clu-foo-bar",
                    agent_type="clu-worker",
                )
                payload = json.loads(run.call_args.kwargs["input"])
                self.assertEqual(
                    payload,
                    {
                        "session_id": "tok-abc",
                        "agent_id": "clu-foo-bar",
                        "agent_type": "clu-worker",
                    },
                )

    def test_swallows_timeout(self):
        scripts = self._resolved()
        with mock.patch.object(coolant, "resolve_script_dir", return_value=scripts):
            with mock.patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="x", timeout=2),
            ):
                # Must not raise.
                coolant.emit_start(
                    session_id="tok",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )

    def test_swallows_file_not_found(self):
        scripts = self._resolved()
        with mock.patch.object(coolant, "resolve_script_dir", return_value=scripts):
            with mock.patch.object(
                subprocess,
                "run",
                side_effect=FileNotFoundError(2, "no such file"),
            ):
                coolant.emit_start(
                    session_id="tok",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )

    def test_swallows_generic_oserror(self):
        scripts = self._resolved()
        with mock.patch.object(coolant, "resolve_script_dir", return_value=scripts):
            with mock.patch.object(
                subprocess,
                "run",
                side_effect=OSError("boom"),
            ):
                coolant.emit_start(
                    session_id="tok",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )


class TestEmitStop(CluTestCase):
    """Stop has the same shape as start — verify the contract holds."""

    def _resolved(self):
        scripts = self.tmp_path / "scripts"
        _write_fake_scripts(scripts)
        return scripts

    def test_empty_session_short_circuits(self):
        with mock.patch.object(coolant, "resolve_script_dir") as resolver:
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_stop(
                    session_id="",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )
                resolver.assert_not_called()
                run.assert_not_called()

    def test_empty_agent_id_short_circuits(self):
        with mock.patch.object(coolant, "resolve_script_dir") as resolver:
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_stop(
                    session_id="tok",
                    agent_id="",
                    agent_type="clu-worker",
                )
                resolver.assert_not_called()
                run.assert_not_called()

    def test_calls_agent_stop_with_devnull_redirects(self):
        scripts = self._resolved()
        with mock.patch.object(coolant, "resolve_script_dir", return_value=scripts):
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_stop(
                    session_id="tok",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )
                run.assert_called_once()
                call = run.call_args
                self.assertEqual(call.args[0], [str(scripts / "agent-stop.sh")])
                self.assertEqual(call.kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)
                self.assertEqual(call.kwargs["timeout"], 2)
                self.assertEqual(call.kwargs["check"], False)

    def test_stdin_payload_shape(self):
        scripts = self._resolved()
        with mock.patch.object(coolant, "resolve_script_dir", return_value=scripts):
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_stop(
                    session_id="tok-xyz",
                    agent_id="clu-zap-2",
                    agent_type="clu-worker",
                )
                payload = json.loads(run.call_args.kwargs["input"])
                self.assertEqual(
                    payload,
                    {
                        "session_id": "tok-xyz",
                        "agent_id": "clu-zap-2",
                        "agent_type": "clu-worker",
                    },
                )

    def test_swallows_timeout(self):
        scripts = self._resolved()
        with mock.patch.object(coolant, "resolve_script_dir", return_value=scripts):
            with mock.patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd="x", timeout=2),
            ):
                coolant.emit_stop(
                    session_id="tok",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )

    def test_no_script_dir_no_ops(self):
        with mock.patch.object(coolant, "resolve_script_dir", return_value=None):
            with mock.patch.object(subprocess, "run") as run:
                coolant.emit_stop(
                    session_id="tok",
                    agent_id="clu-p-1",
                    agent_type="clu-worker",
                )
                run.assert_not_called()
