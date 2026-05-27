"""Tests for `clu notify-heartbeat-failure` — worker-side heartbeat-failure escalation.

Tests the 4 assertions from the sub-plan:
  - test_emits_event_and_inbox_on_first_call
  - test_idempotent_on_second_call
  - test_token_mismatch_rejects
  - test_log_path_passed_through_to_inbox
"""

from __future__ import annotations

from end_of_line import inbox
from end_of_line import state as st
from end_of_line.cli import main
from tests import CluTestCase

PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| a | `test-plan-a.md` | thing | 1h |
"""


class NotifyHeartbeatFailureTestCase(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        main(["init", "--project", str(self.project), "--plan", "test-plan"])
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)
        logs_dir = self.project / "plans" / ".orchestrator" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = logs_dir / "heartbeat-errors.test-plan.a.log"
        self.log_path.write_text("clu heartbeat: lock timeout\n")

    def _call(self, *, token: str | None = None, log_path=None) -> int:
        return main(
            [
                "notify-heartbeat-failure",
                "--project",
                str(self.project),
                "--plan",
                "test-plan",
                "--phase",
                "a",
                "--token",
                token or self.token,
                "--log",
                str(log_path or self.log_path),
            ]
        )

    def test_emits_event_and_inbox_on_first_call(self) -> None:
        rc = self._call()
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        types = [e["type"] for e in data["events"]]
        self.assertIn(st.EVENT_HEARTBEAT_LOOP_FAILING, types)
        self.assertTrue(data["current_claim"]["heartbeat_loop_failing_notified"])
        events = inbox.read_unprocessed()
        hbf = [e for e in events if e["type"] == "heartbeat_loop_failing"]
        self.assertEqual(len(hbf), 1)

    def test_idempotent_on_second_call(self) -> None:
        self._call()
        self._call()
        data = st.load(self.state_path)
        hbf_events = [
            e for e in data["events"] if e["type"] == st.EVENT_HEARTBEAT_LOOP_FAILING
        ]
        self.assertEqual(len(hbf_events), 1)
        events = inbox.read_unprocessed()
        hbf = [e for e in events if e["type"] == "heartbeat_loop_failing"]
        self.assertEqual(len(hbf), 1)

    def test_token_mismatch_rejects(self) -> None:
        rc = self._call(token="session-imposter00000000")
        self.assertEqual(rc, 4)  # ExitCode.CLAIM_MISMATCH
        data = st.load(self.state_path)
        types = [e["type"] for e in data["events"]]
        self.assertNotIn(st.EVENT_HEARTBEAT_LOOP_FAILING, types)

    def test_log_path_passed_through_to_inbox(self) -> None:
        rc = self._call()
        self.assertEqual(rc, 0)
        events = inbox.read_unprocessed()
        hbf = [e for e in events if e["type"] == "heartbeat_loop_failing"]
        self.assertEqual(len(hbf), 1)
        self.assertEqual(hbf[0]["details"]["log_path"], str(self.log_path))
