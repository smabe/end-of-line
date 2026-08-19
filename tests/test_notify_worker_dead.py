"""Tests for `clu notify-worker-dead` — the heartbeat daemon's death report.

Covers the death-report sub-plan contract:
  - a wrong token → ExitCode.CLAIM_MISMATCH, no event written
  - first call → EVENT_PHASE_WORKER_DEAD_REPORTED carrying phase, pid, and the
    ATTEMPT log path (claim["log_path"], NOT the daemon's .hb.log sidecar);
    exactly one inbox event; notify called once
  - second call with the same claim → no-op ExitCode.OK — no second event, no
    second inbox file, no second notify
  - the claim is NOT released (that is phase death-recovery)
  - the notify kind is NOT in QUIET_HOURS_BYPASS_KINDS
"""

from __future__ import annotations

from unittest import mock

from end_of_line import inbox
from end_of_line import notify
from end_of_line import state as st
from end_of_line.cli import main
from tests import CluTestCase, plan_body

PLAN_BODY = plan_body("a")


class NotifyWorkerDeadTestCase(CluTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        main(["init", "--project", str(self.project), "--plan", "test-plan"])
        # The attempt log the dispatcher stamps onto the claim — the post-mortem
        # target, distinct from the daemon's own .hb.log sidecar.
        self.attempt_log = (
            self.project / "plans" / ".orchestrator" / "logs" / "a.session-x.log"
        )
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)
            data["current_claim"]["pid"] = 4242
            data["current_claim"]["log_path"] = str(self.attempt_log)

    def _call(self, *, token: str | None = None) -> int:
        return main(
            [
                "notify-worker-dead",
                "--project",
                str(self.project),
                "--plan",
                "test-plan",
                "--phase",
                "a",
                "--token",
                token or self.token,
            ]
        )

    def test_wrong_token_rejected(self) -> None:
        rc = self._call(token="session-imposter00000000")
        self.assertEqual(rc, 4)  # ExitCode.CLAIM_MISMATCH
        data = st.load(self.state_path)
        types = [e["type"] for e in data["events"]]
        self.assertNotIn(st.EVENT_PHASE_WORKER_DEAD_REPORTED, types)

    def test_first_call_emits_event_inbox_notify(self) -> None:
        with mock.patch.object(notify, "notify") as m_notify:
            rc = self._call()
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        dead = [
            e for e in data["events"] if e["type"] == st.EVENT_PHASE_WORKER_DEAD_REPORTED
        ]
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0]["phase"], "a")
        self.assertEqual(dead[0]["pid"], 4242)
        # The ATTEMPT log path, not the .hb.log sidecar — death-recovery needs
        # this field for quota classification.
        self.assertEqual(dead[0]["log_path"], str(self.attempt_log))
        self.assertTrue(st.worker_death_already_reported(data["current_claim"]))
        box = [
            e
            for e in inbox.read_unprocessed()
            if e["type"] == "phase_worker_dead_reported"
        ]
        self.assertEqual(len(box), 1)
        self.assertEqual(box[0]["details"]["log_path"], str(self.attempt_log))
        self.assertEqual(m_notify.call_count, 1)

    def test_claim_not_released(self) -> None:
        # death-report surfaces; death-recovery releases. The claim stays live.
        self._call()
        data = st.load(self.state_path)
        self.assertIsNotNone(data["current_claim"])

    def test_second_call_is_noop(self) -> None:
        with mock.patch.object(notify, "notify") as m_notify:
            self._call()
            self._call()
        data = st.load(self.state_path)
        dead = [
            e for e in data["events"] if e["type"] == st.EVENT_PHASE_WORKER_DEAD_REPORTED
        ]
        self.assertEqual(len(dead), 1)
        box = [
            e
            for e in inbox.read_unprocessed()
            if e["type"] == "phase_worker_dead_reported"
        ]
        self.assertEqual(len(box), 1)
        self.assertEqual(m_notify.call_count, 1)

    def test_kind_not_in_quiet_hours_bypass(self) -> None:
        self.assertNotIn(
            notify.KIND_WORKER_DEAD_REPORTED, notify.QUIET_HOURS_BYPASS_KINDS
        )
