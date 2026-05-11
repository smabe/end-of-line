"""Outbound iMessage adapter (Day-2 Cliff 2)."""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import notify, state as st
from end_of_line.cli import main
from end_of_line.config import DispatchSpec, NotifySpec, ProjectConfig
from end_of_line.supervisor import tick
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `test-plan-a.md` | thing | 1h |
"""


class QuietHoursTestCase(unittest.TestCase):
    def _at(self, hour: int, minute: int = 0) -> _dt.datetime:
        return _dt.datetime(2026, 5, 11, hour, minute)

    def test_overnight_window_quiet_at_2am(self) -> None:
        self.assertTrue(notify.is_quiet_hours(
            self._at(2), _dt.time(22, 0), _dt.time(8, 0),
        ))

    def test_overnight_window_loud_at_noon(self) -> None:
        self.assertFalse(notify.is_quiet_hours(
            self._at(12), _dt.time(22, 0), _dt.time(8, 0),
        ))

    def test_overnight_boundary_start_is_quiet(self) -> None:
        # 22:00 == start → inside the window.
        self.assertTrue(notify.is_quiet_hours(
            self._at(22), _dt.time(22, 0), _dt.time(8, 0),
        ))

    def test_overnight_boundary_end_is_loud(self) -> None:
        # 08:00 == end → outside (half-open interval).
        self.assertFalse(notify.is_quiet_hours(
            self._at(8), _dt.time(22, 0), _dt.time(8, 0),
        ))

    def test_same_day_window(self) -> None:
        # Daytime focus window 13:00–17:00.
        self.assertTrue(notify.is_quiet_hours(
            self._at(15), _dt.time(13, 0), _dt.time(17, 0),
        ))
        self.assertFalse(notify.is_quiet_hours(
            self._at(18), _dt.time(13, 0), _dt.time(17, 0),
        ))

    def test_zero_width_window_never_quiet(self) -> None:
        self.assertFalse(notify.is_quiet_hours(
            self._at(3), _dt.time(0, 0), _dt.time(0, 0),
        ))


class NotifyDispatchTestCase(unittest.TestCase):
    """notify() — quiet-hour gating, missing config, sender injection."""

    def setUp(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def _sender(self, to: str, body: str) -> None:
        self.sent.append((to, body))

    def _spec(self, **overrides) -> NotifySpec:
        base = {
            "imessage_to": "+15551234567",
            "quiet_hours": ("22:00", "08:00"),
        }
        base.update(overrides)
        return NotifySpec(**base)

    def test_sends_during_loud_hours(self) -> None:
        ok = notify.notify(
            self._spec(), notify.KIND_BLOCKER, "hello",
            now=_dt.datetime(2026, 5, 11, 12, 0),
            sender=self._sender,
        )
        self.assertTrue(ok)
        self.assertEqual(self.sent, [("+15551234567", "hello")])

    def test_suppresses_during_quiet_hours(self) -> None:
        ok = notify.notify(
            self._spec(), notify.KIND_BLOCKER, "hello",
            now=_dt.datetime(2026, 5, 11, 2, 0),
            sender=self._sender,
        )
        self.assertFalse(ok)
        self.assertEqual(self.sent, [])

    def test_skips_when_no_handle_configured(self) -> None:
        ok = notify.notify(
            self._spec(imessage_to=None), notify.KIND_BLOCKER, "hello",
            now=_dt.datetime(2026, 5, 11, 12, 0),
            sender=self._sender,
        )
        self.assertFalse(ok)
        self.assertEqual(self.sent, [])

    def test_no_quiet_window_means_always_loud(self) -> None:
        spec = NotifySpec(imessage_to="+15550000000", quiet_hours=None)
        ok = notify.notify(
            spec, notify.KIND_BLOCKER, "hello",
            now=_dt.datetime(2026, 5, 11, 3, 0),
            sender=self._sender,
        )
        self.assertTrue(ok)

    def test_malformed_quiet_hours_treated_as_loud(self) -> None:
        ok = notify.notify(
            self._spec(quiet_hours=("banana", "moose")),
            notify.KIND_BLOCKER, "hello",
            now=_dt.datetime(2026, 5, 11, 2, 0),
            sender=self._sender,
        )
        # Bad config falls open (deliver) rather than silently swallowing.
        self.assertTrue(ok)

    def test_sender_failure_is_swallowed(self) -> None:
        def angry(to: str, body: str) -> None:
            raise subprocess.SubprocessError("Messages.app is sulking")

        ok = notify.notify(
            self._spec(), notify.KIND_BLOCKER, "hello",
            now=_dt.datetime(2026, 5, 11, 12, 0),
            sender=angry,
        )
        self.assertFalse(ok)

    def test_osascript_send_uses_argv_form(self) -> None:
        """The `--` separator + argv is what keeps Messages.app safe against
        user-controlled text in the body. Lock the invocation shape."""
        with mock.patch("end_of_line.notify.subprocess.Popen") as popen:
            notify._osascript_send("+15551234567", "hi from clu")
        args = popen.call_args.args[0]
        self.assertEqual(args[0], "osascript")
        self.assertEqual(args[1], "-e")
        # Script must come BEFORE the -- separator; args after `--` are argv.
        sep_idx = args.index("--")
        self.assertEqual(args[sep_idx + 1], "+15551234567")
        self.assertEqual(args[sep_idx + 2], "hi from clu")


class NotifyIntegrationTestCase(unittest.TestCase):
    """CLI wiring — cmd_block and cmd_tick should call notify with the right body."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        (self.project / ".orchestrator.json").write_text(json.dumps({
            "plan_dir": "plans",
            "dispatch": {"kind": "shell", "command": "echo {phase_id}"},
            "notify": {
                "imessage": {"to": "+15550000000"},
                # No quiet hours → deterministic in tests.
            },
        }))
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        main(["init", "--project", str(self.project), "--plan", "test-plan"])

        self.sent: list[tuple[str, str]] = []
        # mock.patch auto-restores on tearDown even if a test raises mid-way,
        # which a direct rebind of notify._osascript_send wouldn't.
        patcher = mock.patch.object(
            notify, "_osascript_send",
            side_effect=lambda to, body: self.sent.append((to, body)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cmd_block_sends_blocker_notification(self) -> None:
        with st.mutate(self.state_path) as data:
            token = st.claim_phase(data, "a", lease_minutes=30)
        rc = main([
            "block", "--project", str(self.project), "--plan", "test-plan",
            "--phase", "a", "--token", token,
            "--question", "Pick framework", "--option", "FastAPI", "--option", "Flask",
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        to, body = self.sent[0]
        self.assertEqual(to, "+15550000000")
        self.assertIn("Pick framework", body)
        self.assertIn("[0] FastAPI", body)
        self.assertIn("[1] Flask", body)

    def test_cmd_tick_sends_plan_completed(self) -> None:
        with st.mutate(self.state_path) as data:
            st.append_event(
                data, st.EVENT_PHASE_COMPLETED, phase="a", commits=["abc1234"],
            )
        rc = main([
            "tick", "--project", str(self.project), "--plan", "test-plan",
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        _, body = self.sent[0]
        self.assertIn("test-plan", body)
        self.assertIn("1 commit", body)


class StalledNotifyTestCase(unittest.TestCase):
    """Supervisor populates notify_kind/body for stalled detections."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        self.cfg = ProjectConfig(
            project_root=self.project, plan_dir="plans",
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

    def test_stalled_tick_carries_notify_payload(self) -> None:
        tick(self.state_path, self.cfg)  # claim phase a
        with st.mutate(self.state_path) as data:
            data["current_claim"]["last_heartbeat_at"] = "2020-01-01T00:00:00Z"
        result = tick(self.state_path, self.cfg)
        self.assertEqual(result.action, "stalled")
        # Slug-prefixed so multi-plan recipients can disambiguate.
        self.assertIn("test-plan/a stalled", result.notify_body)


if __name__ == "__main__":
    unittest.main()
