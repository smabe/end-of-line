"""Tests for `clu tick` CLI wiring — default is now dispatch.

Pre-flip, `clu tick` defaulted to no-dispatch; an opt-in `--dispatch`
flag spawned the worker. That produced a phantom-claim footgun for
manual ticks (a 30-min lease block on cron). The flip inverts the
default: `clu tick` dispatches; `--dry-tick` is the explicit opt-out.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from end_of_line.cli import ExitCode, main
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
"""


class TickDefaultDispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        (self.project / ".orchestrator.json").write_text(json.dumps({
            "plan_dir": "plans",
            "dispatch": {"kind": "shell", "command": "true"},
        }))
        rc = main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _argv(self, *extra: str) -> list[str]:
        return ["tick", "--project", str(self.project), "--plan", "test-plan", *extra]

    def test_tick_default_dispatches(self) -> None:
        with mock.patch("end_of_line.dispatch.dispatch_for_tick") as mocked, \
                redirect_stdout(StringIO()):
            rc = main(self._argv())
        self.assertTrue(mocked.called)
        self.assertEqual(rc, ExitCode.OK)

    def test_tick_dry_tick_skips_dispatch(self) -> None:
        with mock.patch("end_of_line.dispatch.dispatch_for_tick") as mocked, \
                redirect_stdout(StringIO()):
            rc = main(self._argv("--dry-tick"))
        self.assertFalse(mocked.called)
        self.assertEqual(rc, ExitCode.OK)

    def test_tick_old_dispatch_flag_rejected(self) -> None:
        # --dispatch is gone — argparse rejects with SystemExit(2).
        with self.assertRaises(SystemExit):
            main(self._argv("--dispatch"))


if __name__ == "__main__":
    unittest.main()
