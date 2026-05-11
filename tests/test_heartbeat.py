"""Worker heartbeat → stalled status (Day 2, Cliff 1)."""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import main
from end_of_line.config import DispatchSpec, ProjectConfig
from end_of_line.supervisor import tick


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
| B | `test-plan-b.md` | thing | 1h |
"""


def _backdate_claim(state_path: Path, *, minutes: int) -> None:
    """Pretend the worker last heartbeat-ed `minutes` ago."""
    past = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=minutes))
    stamp = past.strftime("%Y-%m-%dT%H:%M:%SZ")
    with st.mutate(state_path) as data:
        data["current_claim"]["last_heartbeat_at"] = stamp


class HeartbeatStateTestCase(unittest.TestCase):
    """Pure state helpers — no CLI plumbing."""

    def _claim(self, **overrides) -> dict:
        base = {
            "phase_id": "a",
            "claimed_by": "session-aaaa1111bbbb2222",
            "lease_expires": "2099-01-01T00:00:00Z",
            "started_at": "2026-05-11T00:00:00Z",
            "last_heartbeat_at": "2026-05-11T00:00:00Z",
            "attempts": 1,
        }
        base.update(overrides)
        return base

    def test_claim_phase_seeds_heartbeat(self) -> None:
        data = st.empty_state("p", "plans")
        st.claim_phase(data, "a", lease_minutes=30)
        claim = data["current_claim"]
        self.assertIn("last_heartbeat_at", claim)
        # Seeded equal to started_at — the worker has 0s of "alive" credit at start.
        self.assertEqual(claim["last_heartbeat_at"], claim["started_at"])

    def test_record_heartbeat_updates_timestamp(self) -> None:
        data = st.empty_state("p", "plans")
        token = st.claim_phase(data, "a", lease_minutes=30)
        events_before = len(data["events"])
        data["current_claim"]["last_heartbeat_at"] = "2020-01-01T00:00:00Z"
        new_ts = st.record_heartbeat(data, token, "a")
        self.assertNotEqual(new_ts, "2020-01-01T00:00:00Z")
        self.assertEqual(data["current_claim"]["last_heartbeat_at"], new_ts)
        # Heartbeats fire every ~2 min; appending an event each time would flood.
        self.assertEqual(len(data["events"]), events_before)

    def test_record_heartbeat_rejects_wrong_token(self) -> None:
        data = st.empty_state("p", "plans")
        st.claim_phase(data, "a", lease_minutes=30)
        with self.assertRaises(st.ClaimMismatch):
            st.record_heartbeat(data, "session-wrong000000000000", "a")

    def test_record_heartbeat_rejects_wrong_phase(self) -> None:
        data = st.empty_state("p", "plans")
        token = st.claim_phase(data, "a", lease_minutes=30)
        with self.assertRaises(st.ClaimMismatch):
            st.record_heartbeat(data, token, "b")

    def test_is_claim_stalled_below_threshold(self) -> None:
        now = _dt.datetime(2026, 5, 11, 12, 0, 0, tzinfo=_dt.timezone.utc)
        claim = self._claim(last_heartbeat_at="2026-05-11T11:55:00Z")  # 5 min ago
        self.assertFalse(st.is_claim_stalled(claim, threshold_minutes=10, now=now))

    def test_is_claim_stalled_above_threshold(self) -> None:
        now = _dt.datetime(2026, 5, 11, 12, 0, 0, tzinfo=_dt.timezone.utc)
        claim = self._claim(last_heartbeat_at="2026-05-11T11:45:00Z")  # 15 min ago
        self.assertTrue(st.is_claim_stalled(claim, threshold_minutes=10, now=now))


class HeartbeatCliTestCase(unittest.TestCase):
    """End-to-end CLI invocation."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        main(["init", "--project", str(self.project), "--plan", "test-plan"])
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_heartbeat_cli_succeeds_with_matching_token(self) -> None:
        rc = main([
            "heartbeat", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "a", "--token", self.token,
        ])
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertEqual(
            data["current_claim"]["last_heartbeat_at"][:4], "2026",
        )

    def test_heartbeat_cli_rejects_bad_token(self) -> None:
        rc = main([
            "heartbeat", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "a", "--token", "session-imposter00000000",
        ])
        self.assertEqual(rc, 4)


class StalledSupervisorTestCase(unittest.TestCase):
    """Supervisor surfaces stalled claims as a first-class action."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.cfg = ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command="echo {phase_id}"),
        )
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        self.state_path.parent.mkdir(parents=True)
        with st.locked(self.state_path):
            st.save_atomic(self.state_path, st.empty_state("test-plan", "plans"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def test_stalled_emitted_once_per_claim(self) -> None:
        tick(self.state_path, self.cfg)  # claims a
        _backdate_claim(self.state_path, minutes=15)

        first = tick(self.state_path, self.cfg)
        self.assertEqual(first.action, "stalled")
        events = [
            evt for evt in self._read()["events"]
            if evt["type"] == st.EVENT_PHASE_STALLED
        ]
        self.assertEqual(len(events), 1)

        # Second tick on the same stalled claim should NOT re-emit.
        second = tick(self.state_path, self.cfg)
        self.assertEqual(second.action, "idle")
        events = [
            evt for evt in self._read()["events"]
            if evt["type"] == st.EVENT_PHASE_STALLED
        ]
        self.assertEqual(len(events), 1)

    def test_fresh_heartbeat_clears_stalled_path(self) -> None:
        tick(self.state_path, self.cfg)  # claims a, fresh heartbeat
        result = tick(self.state_path, self.cfg)
        # No backdate → not stalled → falls through to "active claim → idle".
        self.assertEqual(result.action, "idle")
        events = [
            evt for evt in self._read()["events"]
            if evt["type"] == st.EVENT_PHASE_STALLED
        ]
        self.assertEqual(events, [])

    def test_stalled_does_not_release_claim(self) -> None:
        """The lease expiry is the source of truth for retry; stalled is a signal."""
        tick(self.state_path, self.cfg)
        _backdate_claim(self.state_path, minutes=15)
        tick(self.state_path, self.cfg)
        self.assertIsNotNone(self._read()["current_claim"])

    def test_lease_expiry_takes_priority_over_stalled(self) -> None:
        """If the lease is past, the lease-expiry path fires — not stalled."""
        tick(self.state_path, self.cfg)
        with st.mutate(self.state_path) as data:
            data["current_claim"]["lease_expires"] = "2020-01-01T00:00:00Z"
        _backdate_claim(self.state_path, minutes=99)
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "lease_expired")


if __name__ == "__main__":
    unittest.main()
