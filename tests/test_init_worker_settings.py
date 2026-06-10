"""Tests for cmd_init emitting `~/.config/clu/worker-settings.json` (#90).

When the file is absent, init materializes it from the bundled
`worker-settings.template.json` and prints the path plus a hardened-
command hint. An existing file is operator intent and is NEVER
overwritten — same contract as `_ensure_quality_stub`.

XDG isolation: `isolate_registry` points `XDG_CONFIG_HOME` at the test
tmp dir, so `clu_config_dir()` resolves inside it and these tests never
touch the real `~/.config/clu/`.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line._xdg_guard import clu_config_dir
from end_of_line.cli import main
from tests import isolate_registry

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| phase-a | `test-plan-phase-a.md` | thing | 1h |
"""


class InitWorkerSettingsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init(self, plan: str = "test-plan") -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = main(["init", "--project", str(self.project), "--plan", plan])
        return rc, out.getvalue()

    def _settings_path(self) -> Path:
        # Resolve AFTER isolate_registry has patched XDG_CONFIG_HOME.
        return clu_config_dir() / "worker-settings.json"

    def test_init_creates_worker_settings_from_template(self) -> None:
        target = self._settings_path()
        self.assertFalse(target.exists())
        rc, _ = self._init()
        self.assertEqual(rc, 0)
        data = json.loads(target.read_text())
        self.assertIs(data["sandbox"]["enabled"], True)
        self.assertIs(data["sandbox"]["failIfUnavailable"], True)
        self.assertIs(data["sandbox"]["allowUnsandboxedCommands"], False)
        self.assertIn("clu *", data["sandbox"]["excludedCommands"])

    def test_init_prints_emission_path_and_hint(self) -> None:
        rc, stdout = self._init()
        self.assertEqual(rc, 0)
        self.assertIn(str(self._settings_path()), stdout)
        self.assertIn("Hardened worker dispatch", stdout)

    def test_init_never_overwrites_existing_settings(self) -> None:
        target = self._settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"custom": true}\n')
        rc, _ = self._init()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(target.read_text()), {"custom": True})

    def test_second_init_is_quiet_about_settings(self) -> None:
        # File created by the first init; the second init must not re-print
        # the emission line (existing file == operator intent, leave alone).
        self._init()
        (self.project / "plans" / "test-plan-2.md").write_text(PLAN_BODY)
        rc, stdout = self._init(plan="test-plan-2")
        self.assertEqual(rc, 0)
        self.assertNotIn(str(self._settings_path()), stdout)


if __name__ == "__main__":
    unittest.main()
