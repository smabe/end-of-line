"""Tests for `clu notify-worker-dead` — the heartbeat daemon's death report.

Covers both halves of the worker-death-visibility plan:

death-report (surfacing):
  - a wrong token → ExitCode.CLAIM_MISMATCH, no event written, nothing released
  - first call → EVENT_PHASE_WORKER_DEAD_REPORTED carrying phase, pid, and the
    ATTEMPT log path (claim["log_path"], NOT the daemon's .hb.log sidecar);
    exactly one inbox event; notify called once
  - a claim already marked reported short-circuits BEFORE the release
  - the notify kind is NOT in QUIET_HOURS_BYPASS_KINDS

death-recovery (this phase, #104):
  - the reporter releases the claim it just reported dead, so the phase is
    redispatchable without waiting for a supervisor tick
  - quota classification runs BEFORE the release (release clears the claim that
    carries log_path); a quota match records the pause + EVENT_QUOTA_DEATH and
    swaps the generic death ping for the actionable quota-pause notification
  - coolant stop fires for the released claim when coolant is enabled
"""

from __future__ import annotations

import json
from unittest import mock

from end_of_line import config, inbox, notify, quota
from end_of_line import state as st
from end_of_line.cli import main
from tests import CluTestCase, plan_body

PLAN_BODY = plan_body("a")

# A parseable quota signature: `resets 3am` yields a non-null paused_until.
QUOTA_LINE = "You've hit your session limit — resets 3am"


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
        self.attempt_log.parent.mkdir(parents=True, exist_ok=True)
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

    def _pause_row(self) -> dict | None:
        return quota.read_pause(self.state_path.parent)

    # --- death-report contract -------------------------------------------

    def test_wrong_token_rejected(self) -> None:
        rc = self._call(token="session-imposter00000000")
        self.assertEqual(rc, 4)  # ExitCode.CLAIM_MISMATCH
        data = st.load(self.state_path)
        types = [e["type"] for e in data["events"]]
        self.assertNotIn(st.EVENT_PHASE_WORKER_DEAD_REPORTED, types)
        # A rejected token must not release the claim it doesn't own.
        self.assertIsNotNone(data["current_claim"])

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
        # The ATTEMPT log path, not the .hb.log sidecar — recovery needs this
        # field for quota classification.
        self.assertEqual(dead[0]["log_path"], str(self.attempt_log))
        box = [
            e
            for e in inbox.read_unprocessed()
            if e["type"] == "phase_worker_dead_reported"
        ]
        self.assertEqual(len(box), 1)
        self.assertEqual(box[0]["details"]["log_path"], str(self.attempt_log))
        self.assertEqual(m_notify.call_count, 1)

    def test_already_reported_marker_skips_release(self) -> None:
        # The dedup guard must sit BEFORE the release: a claim already marked
        # reported (e.g. by a prior daemon fire) short-circuits without
        # releasing, or a second invocation would release a newly dispatched
        # worker's claim.
        with st.mutate(self.state_path) as data:
            st.mark_worker_death_reported(data["current_claim"], st._now_utc())
        with mock.patch.object(notify, "notify") as m_notify:
            rc = self._call()
        self.assertEqual(rc, 0)
        data = st.load(self.state_path)
        self.assertIsNotNone(data["current_claim"])  # NOT released
        types = [e["type"] for e in data["events"]]
        self.assertNotIn(st.EVENT_PHASE_WORKER_DEAD_REPORTED, types)
        self.assertEqual(m_notify.call_count, 0)

    def test_kind_not_in_quiet_hours_bypass(self) -> None:
        self.assertNotIn(
            notify.KIND_WORKER_DEAD_REPORTED, notify.QUIET_HOURS_BYPASS_KINDS
        )

    # --- death-recovery contract -----------------------------------------

    def test_claim_released(self) -> None:
        # The reporter releases the claim it reported dead — the phase is now
        # redispatchable without a supervisor tick.
        self._call()
        data = st.load(self.state_path)
        self.assertIsNone(data["current_claim"])

    def test_second_call_after_release_is_noop(self) -> None:
        with mock.patch.object(notify, "notify") as m_notify:
            self._call()
            # The claim is gone after the first call — the second finds nothing
            # to report or release.
            rc = self._call()
        self.assertEqual(rc, 4)  # ExitCode.CLAIM_MISMATCH — no active claim
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

    def test_no_quota_sends_ordinary_death_notification(self) -> None:
        with mock.patch.object(notify, "notify") as m_notify:
            self._call()
        self.assertEqual(m_notify.call_count, 1)
        self.assertEqual(
            m_notify.call_args.args[1], notify.KIND_WORKER_DEAD_REPORTED
        )
        data = st.load(self.state_path)
        types = [e["type"] for e in data["events"]]
        self.assertNotIn(st.EVENT_QUOTA_DEATH, types)

    def test_quota_death_recorded_before_release(self) -> None:
        self.attempt_log.write_text(QUOTA_LINE + "\n")
        with mock.patch.object(notify, "notify") as m_notify:
            self._call()
        data = st.load(self.state_path)
        types = [e["type"] for e in data["events"]]
        # EVENT_QUOTA_DEATH can only exist if classification ran while the claim
        # (and its log_path) was still live — i.e. BEFORE the release. If a
        # future edit reorders release ahead of classify, log_path is gone,
        # classify returns None, and these two events vanish.
        self.assertIn(st.EVENT_QUOTA_DEATH, types)
        self.assertIn(st.EVENT_QUOTA_PAUSED, types)
        death = next(e for e in data["events"] if e["type"] == st.EVENT_QUOTA_DEATH)
        self.assertEqual(death["phase"], "a")
        self.assertEqual(death["token"], self.token)
        # The pause row is written with the parsed reset (non-null paused_until).
        pause = self._pause_row()
        assert pause is not None
        self.assertEqual(pause["signature"], "session_limit")
        self.assertIsNotNone(pause["paused_until"])
        # Claim released despite the quota branch.
        self.assertIsNone(data["current_claim"])
        # The operator gets the actionable quota-pause ping, not a generic death.
        self.assertEqual(m_notify.call_count, 1)
        self.assertEqual(m_notify.call_args.args[1], notify.KIND_QUOTA_PAUSED)

    def test_coolant_stop_fires_when_enabled(self) -> None:
        # init writes coolant enabled by default.
        with mock.patch("end_of_line.state.coolant.emit_stop") as emit:
            self._call()
        self.assertEqual(emit.call_count, 1)
        self.assertEqual(emit.call_args.kwargs["session_id"], self.token)

    def test_reaps_worker_pgroup_after_release(self) -> None:
        # The supervisor's reap needs the claim this callback releases, so the
        # daemon must reap the worker's orphaned pgroup itself. pgid falls back
        # to pid when the claim carries no explicit pgid.
        with mock.patch("end_of_line.state.reap_orphan_pgroup") as reap:
            self._call()
        reap.assert_called_once()
        self.assertEqual(reap.call_args.args[0], 4242)
        self.assertEqual(reap.call_args.kwargs["cmdline_match"], "test-plan")

    def test_reap_prefers_explicit_pgid(self) -> None:
        with st.mutate(self.state_path) as data:
            data["current_claim"]["pgid"] = 7777
        with mock.patch("end_of_line.state.reap_orphan_pgroup") as reap:
            self._call()
        self.assertEqual(reap.call_args.args[0], 7777)

    def test_reap_failure_does_not_break_report(self) -> None:
        # Reap is best-effort — a killpg/ps failure must not fail the callback
        # (the durable state transition already happened).
        with mock.patch(
            "end_of_line.state.reap_orphan_pgroup", side_effect=OSError("boom")
        ):
            rc = self._call()
        self.assertEqual(rc, 0)
        self.assertIsNone(st.load(self.state_path)["current_claim"])

    def test_coolant_stop_skipped_when_disabled(self) -> None:
        (self.project / config.CONFIG_FILENAME).write_text(
            json.dumps({"coolant": {"enabled": False}})
        )
        with mock.patch("end_of_line.state.coolant.emit_stop") as emit:
            self._call()
        self.assertEqual(emit.call_count, 0)
        # Release still happens even with coolant off.
        self.assertIsNone(st.load(self.state_path)["current_claim"])
