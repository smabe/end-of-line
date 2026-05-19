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


class InitPerPhaseLeaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        isolate_registry(self, Path(self._tmp.name))
        (self.project / "plans").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init_plan(
        self, slug: str, plan_body: str, *, extra: tuple[str, ...] = ()
    ) -> tuple[int, dict]:
        (self.project / "plans" / f"{slug}.md").write_text(plan_body)
        state_path = (
            self.project / "plans" / ".orchestrator" / f"{slug}.state.json"
        )
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                ["init", "--project", str(self.project), "--plan", slug, *extra]
            )
        data = st.load(state_path) if rc == 0 else {}
        return rc, data

    def _phase(self, data: dict, phase_id: str) -> dict:
        for p in data.get("phases", []):
            if p.get("id") == phase_id:
                return p
        return {}

    def _plan_body(self, slug: str, effort: str) -> str:
        return (
            f"# {slug} plan\n\n## Sessions index\n\n"
            "| Session | Plan file | Scope | Effort |\n"
            "|---|---|---|---|\n"
            f"| phase-a | `{slug}-phase-a.md` | scope | {effort} |\n"
        )

    # Effort `4h` → 240min × 0.5 = 120 = max(30, 120)
    def test_init_writes_per_phase_ttl_from_effort(self) -> None:
        rc, data = self._init_plan("effort-p1", self._plan_body("effort-p1", "4h"))
        self.assertEqual(rc, 0)
        phase = self._phase(data, "phase-a")
        self.assertIn("phase-a", [p["id"] for p in data["phases"]])
        self.assertEqual(phase.get("lease_ttl_minutes"), 120)

    # Empty Effort cell → no per-phase TTL stored
    def test_init_omits_per_phase_ttl_when_effort_missing(self) -> None:
        rc, data = self._init_plan("effort-p2", self._plan_body("effort-p2", ""))
        self.assertEqual(rc, 0)
        phase = self._phase(data, "phase-a")
        self.assertNotIn("lease_ttl_minutes", phase)

    # Malformed Effort → no per-phase TTL, init succeeds
    def test_init_omits_per_phase_ttl_when_effort_malformed(self) -> None:
        rc, data = self._init_plan("effort-p3", self._plan_body("effort-p3", "abc"))
        self.assertEqual(rc, 0)
        phase = self._phase(data, "phase-a")
        self.assertNotIn("lease_ttl_minutes", phase)

    # lease_ttl_scale=1.0 in config, Effort `2h` → 120min × 1.0 = 120
    def test_init_respects_lease_ttl_scale_override(self) -> None:
        import json as _json
        (self.project / ".orchestrator.json").write_text(
            _json.dumps({"lease_ttl_scale": 1.0})
        )
        rc, data = self._init_plan("effort-p4", self._plan_body("effort-p4", "2h"))
        self.assertEqual(rc, 0)
        phase = self._phase(data, "phase-a")
        self.assertEqual(phase.get("lease_ttl_minutes"), 120)

    # Effort `0.5h` → 30min × 0.5 = 15 → max(global=30, 15) = 30
    def test_init_per_phase_floor_at_global_default(self) -> None:
        rc, data = self._init_plan("effort-p5", self._plan_body("effort-p5", "0.5h"))
        self.assertEqual(rc, 0)
        phase = self._phase(data, "phase-a")
        self.assertEqual(phase.get("lease_ttl_minutes"), st.DEFAULT_LEASE_TTL_MIN)


if __name__ == "__main__":
    unittest.main()
