"""Tests for DEFAULT_LEASE_TTL_MIN=60 bump and regression guard.

Covers the default-bump phase of lease-reliability (#58 part 3/3).
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line.cli import main
from end_of_line.state import DEFAULT_LEASE_TTL_MIN, load
from tests import isolate_registry

_PLAN = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `t-a.md` | thing | 1h |
"""


class DefaultLeaseTTLConstantTestCase(unittest.TestCase):

    def test_default_lease_ttl_is_60(self) -> None:
        self.assertEqual(DEFAULT_LEASE_TTL_MIN, 60)


class DefaultLeaseTTLInitTestCase(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "t.md").write_text(_PLAN)
        (self.project / ".orchestrator.json").write_text(
            json.dumps({"dispatch": {"kind": "shell", "command": "echo"}})
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init(self) -> int:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            return main(["init", "--project", str(self.project), "--plan", "t"])

    def test_init_writes_60_when_no_override(self) -> None:
        rc = self._init()
        self.assertEqual(rc, 0)
        state_path = self.project / "plans" / ".orchestrator" / "t.state.json"
        data = load(state_path)
        self.assertEqual(data["config"]["lease_ttl_minutes"], 60)


if __name__ == "__main__":
    unittest.main()
