"""`clu init` config knobs — lease_ttl_minutes, stalled_heartbeat_minutes,
max_attempts_per_phase flags and their validation.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `foo-a.md` | thing | 1h |
"""


class InitConfigKnobsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        isolate_registry(self, Path(self._tmp.name))
        (self.project / "plans").mkdir()
        (self.project / "plans" / "foo.md").write_text(PLAN_BODY)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "foo.state.json"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([
                "init", "--project", str(self.project), "--plan", "foo",
                *extra,
            ])
        return rc, out.getvalue(), err.getvalue()

    # --- happy paths -------------------------------------------------------

    def test_init_lease_ttl_flag_writes_override(self) -> None:
        rc, _out, _err = self._init("--lease-ttl-minutes", "720")
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertEqual(data["config"]["lease_ttl_minutes"], 720)

    def test_init_stalled_heartbeat_flag_writes_override(self) -> None:
        rc, _out, _err = self._init("--stalled-heartbeat-minutes", "60")
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertEqual(data["config"]["stalled_heartbeat_minutes"], 60)

    def test_init_max_attempts_flag_writes_override(self) -> None:
        rc, _out, _err = self._init("--max-attempts-per-phase", "5")
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertEqual(data["config"]["max_attempts_per_phase"], 5)

    def test_init_default_lease_ttl_when_flag_omitted(self) -> None:
        rc, _out, _err = self._init()
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertEqual(data["config"]["lease_ttl_minutes"], st.DEFAULT_LEASE_TTL_MIN)

    # --- refusal paths -----------------------------------------------------

    def test_init_lease_ttl_rejects_zero(self) -> None:
        rc, _out, _err = self._init("--lease-ttl-minutes", "0")
        self.assertEqual(rc, ExitCode.INVALID_VALUE)

    def test_init_lease_ttl_rejects_negative(self) -> None:
        rc, _out, _err = self._init("--lease-ttl-minutes", "-30")
        self.assertEqual(rc, ExitCode.INVALID_VALUE)


if __name__ == "__main__":
    unittest.main()
