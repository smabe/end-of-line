"""Outbound iMessage adapter (Day-2 Cliff 2)."""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import notify, notify_imessage
from end_of_line import state as st
from end_of_line.cli import main
from end_of_line.config import DispatchSpec, NotifySpec, ProjectConfig
from end_of_line.supervisor import tick
from tests import CluTestCase, isolate_registry

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
        self.assertTrue(
            notify.is_quiet_hours(
                self._at(2),
                _dt.time(22, 0),
                _dt.time(8, 0),
            )
        )

    def test_overnight_window_loud_at_noon(self) -> None:
        self.assertFalse(
            notify.is_quiet_hours(
                self._at(12),
                _dt.time(22, 0),
                _dt.time(8, 0),
            )
        )

    def test_overnight_boundary_start_is_quiet(self) -> None:
        # 22:00 == start → inside the window.
        self.assertTrue(
            notify.is_quiet_hours(
                self._at(22),
                _dt.time(22, 0),
                _dt.time(8, 0),
            )
        )

    def test_overnight_boundary_end_is_loud(self) -> None:
        # 08:00 == end → outside (half-open interval).
        self.assertFalse(
            notify.is_quiet_hours(
                self._at(8),
                _dt.time(22, 0),
                _dt.time(8, 0),
            )
        )

    def test_same_day_window(self) -> None:
        # Daytime focus window 13:00–17:00.
        self.assertTrue(
            notify.is_quiet_hours(
                self._at(15),
                _dt.time(13, 0),
                _dt.time(17, 0),
            )
        )
        self.assertFalse(
            notify.is_quiet_hours(
                self._at(18),
                _dt.time(13, 0),
                _dt.time(17, 0),
            )
        )

    def test_zero_width_window_never_quiet(self) -> None:
        self.assertFalse(
            notify.is_quiet_hours(
                self._at(3),
                _dt.time(0, 0),
                _dt.time(0, 0),
            )
        )


class NotifyDispatchTestCase(CluTestCase):
    """notify() — quiet-hour gating, missing config, backend injection."""

    def _spec(self, *, to: str = "+15551234567", quiet_hours=("22:00", "08:00")) -> NotifySpec:
        return NotifySpec.imessage_only(to, quiet_hours=quiet_hours)

    def test_sends_during_loud_hours(self) -> None:
        with mock.patch("end_of_line.notify_imessage._osascript_send") as m:
            ok = notify.notify(
                self._spec(),
                notify.KIND_BLOCKER,
                "hello",
                now=_dt.datetime(2026, 5, 11, 12, 0),
            )
        self.assertTrue(ok)
        m.assert_called_once_with("+15551234567", "hello")

    def test_suppresses_during_quiet_hours(self) -> None:
        with mock.patch("end_of_line.notify_imessage._osascript_send") as m:
            ok = notify.notify(
                self._spec(),
                notify.KIND_BLOCKER,
                "hello",
                now=_dt.datetime(2026, 5, 11, 2, 0),
            )
        self.assertFalse(ok)
        m.assert_not_called()

    def test_skips_when_no_handle_configured(self) -> None:
        spec = NotifySpec()  # no channels
        with mock.patch("end_of_line.notify_imessage._osascript_send") as m:
            ok = notify.notify(
                spec,
                notify.KIND_BLOCKER,
                "hello",
                now=_dt.datetime(2026, 5, 11, 12, 0),
            )
        self.assertFalse(ok)
        m.assert_not_called()

    def test_no_quiet_window_means_always_loud(self) -> None:
        with mock.patch("end_of_line.notify_imessage._osascript_send") as m:
            ok = notify.notify(
                self._spec(to="+15550000000", quiet_hours=None),
                notify.KIND_BLOCKER,
                "hello",
                now=_dt.datetime(2026, 5, 11, 3, 0),
            )
        self.assertTrue(ok)
        m.assert_called_once()

    def test_send_writes_outbound_pending_mark(self) -> None:
        # After osascript fires, the notifier records a {chat_id, sent_at}
        # mark so the inbound poller can resolve the outbound-floor on the
        # next poll tick (otherwise clu's own row would loop back as input).
        from end_of_line.notify_imessage import IMessageNotifier
        from end_of_line.notify_imessage_inbound import outbound_pending_path

        with mock.patch("end_of_line.notify_imessage._osascript_send"):
            IMessageNotifier(to="+15551234567").send(
                notify.KIND_BLOCKER,
                "body",
                plan_slug="p",
            )
        data = json.loads(outbound_pending_path().read_text())
        self.assertEqual(len(data["marks"]), 1)
        self.assertEqual(data["marks"][0]["chat_id"], "+15551234567")

    def test_malformed_quiet_hours_treated_as_loud(self) -> None:
        with mock.patch("end_of_line.notify_imessage._osascript_send") as m:
            ok = notify.notify(
                self._spec(quiet_hours=("banana", "moose")),
                notify.KIND_BLOCKER,
                "hello",
                now=_dt.datetime(2026, 5, 11, 2, 0),
            )
        # Bad config falls open (deliver) rather than silently swallowing.
        self.assertTrue(ok)
        m.assert_called_once()

    def test_sender_failure_is_swallowed(self) -> None:
        with mock.patch.object(
            notify_imessage.IMessageNotifier,
            "send",
            side_effect=subprocess.SubprocessError("Messages.app is sulking"),
        ):
            ok = notify.notify(
                self._spec(),
                notify.KIND_BLOCKER,
                "hello",
                now=_dt.datetime(2026, 5, 11, 12, 0),
            )
        self.assertFalse(ok)

    def test_halted_bypasses_quiet_hours(self) -> None:
        with mock.patch("end_of_line.notify_imessage._osascript_send") as m:
            ok = notify.notify(
                self._spec(),
                notify.KIND_HALTED,
                "boom",
                now=_dt.datetime(2026, 5, 11, 3, 0),
            )
        self.assertTrue(ok)
        m.assert_called_once_with("+15551234567", "boom")

    def test_blocker_still_gated_during_quiet_hours(self) -> None:
        with mock.patch("end_of_line.notify_imessage._osascript_send") as m:
            ok = notify.notify(
                self._spec(),
                notify.KIND_BLOCKER,
                "boom",
                now=_dt.datetime(2026, 5, 11, 3, 0),
            )
        self.assertFalse(ok)
        m.assert_not_called()

    def test_osascript_send_uses_argv_form(self) -> None:
        """The `--` separator + argv is what keeps Messages.app safe against
        user-controlled text in the body. Lock the invocation shape."""
        with mock.patch("end_of_line.notify_imessage.subprocess.Popen") as popen:
            notify_imessage._osascript_send("+15551234567", "hi from clu")
        args = popen.call_args.args[0]
        self.assertEqual(args[0], "osascript")
        self.assertEqual(args[1], "-e")
        # Script must come BEFORE the -- separator; args after `--` are argv.
        sep_idx = args.index("--")
        self.assertEqual(args[sep_idx + 1], "+15551234567")
        self.assertEqual(args[sep_idx + 2], "hi from clu")

    def test_writes_inbox_event_when_plan_slug_and_project_root_provided(self) -> None:
        writes: list[dict] = []
        with mock.patch("end_of_line.notify_imessage._osascript_send"):
            notify.notify(
                self._spec(),
                notify.KIND_BLOCKER,
                "Pick framework?\n[0] FastAPI",
                now=_dt.datetime(2026, 5, 11, 12, 0),
                plan_slug="test-plan",
                project_root="/some/proj",
                inbox_writer=lambda **kw: writes.append(kw),
            )
        self.assertEqual(len(writes), 1)
        entry = writes[0]
        self.assertEqual(entry["type"], notify.KIND_BLOCKER)
        self.assertEqual(entry["plan_slug"], "test-plan")
        self.assertEqual(entry["project_root"], "/some/proj")
        self.assertIn("Pick framework?", entry["summary"])

    def test_writes_inbox_event_even_during_quiet_hours(self) -> None:
        writes: list[dict] = []
        with mock.patch("end_of_line.notify_imessage._osascript_send") as m:
            ok = notify.notify(
                self._spec(),
                notify.KIND_BLOCKER,
                "Pick framework?",
                now=_dt.datetime(2026, 5, 11, 3, 0),  # quiet
                plan_slug="test-plan",
                project_root="/x",
                inbox_writer=lambda **kw: writes.append(kw),
            )
        # iMessage suppressed during quiet hours.
        self.assertFalse(ok)
        m.assert_not_called()
        # But inbox event still recorded — Claude needs the signal next turn.
        self.assertEqual(len(writes), 1)

    def test_skips_inbox_write_when_plan_slug_missing(self) -> None:
        writes: list[dict] = []
        with mock.patch("end_of_line.notify_imessage._osascript_send"):
            notify.notify(
                self._spec(),
                notify.KIND_BLOCKER,
                "hello",
                now=_dt.datetime(2026, 5, 11, 12, 0),
                inbox_writer=lambda **kw: writes.append(kw),
            )
        self.assertEqual(writes, [])


class ChannelDispatchTestCase(unittest.TestCase):
    """Channel-routing tests: kinds filter, enabled gate, unregistered kind."""

    def _imessage_spec(self, *, kinds=None, enabled=True) -> NotifySpec:
        from end_of_line.config import ChannelSpec, NotifySpec

        ch = ChannelSpec(kind="imessage", kinds=kinds, enabled=enabled, params={"to": "+1"})
        return NotifySpec(channels=(ch,))

    def test_dispatcher_fires_only_matching_channels(self) -> None:
        from end_of_line.config import ChannelSpec, NotifySpec

        sends: list[str] = []
        ch_halted = ChannelSpec(kind="imessage", kinds=frozenset({"halted"}), params={"to": "+1"})
        ch_all = ChannelSpec(kind="imessage", kinds=None, params={"to": "+2"})
        spec = NotifySpec(channels=(ch_halted, ch_all))
        with mock.patch(
            "end_of_line.notify_imessage.IMessageNotifier.send",
            side_effect=lambda *a, **kw: sends.append(kw.get("plan_slug", "") or a[0]),
        ):
            notify.notify(spec, notify.KIND_BLOCKER, "body", now=_dt.datetime(2026, 5, 11, 12, 0))
        # only the unfiltered channel fires for KIND_BLOCKER
        self.assertEqual(len(sends), 1)

    def test_dispatcher_fires_all_when_kinds_none(self) -> None:
        sends: list = []
        spec = self._imessage_spec(kinds=None)
        with mock.patch(
            "end_of_line.notify_imessage.IMessageNotifier.send",
            side_effect=lambda *a, **kw: sends.append(True),
        ):
            notify.notify(spec, notify.KIND_HALTED, "body", now=_dt.datetime(2026, 5, 11, 12, 0))
        self.assertEqual(len(sends), 1)

    def test_dispatcher_skips_disabled_channel(self) -> None:
        sends: list = []
        spec = self._imessage_spec(enabled=False)
        with mock.patch(
            "end_of_line.notify_imessage.IMessageNotifier.send",
            side_effect=lambda *a, **kw: sends.append(True),
        ):
            result = notify.notify(
                spec, notify.KIND_BLOCKER, "body", now=_dt.datetime(2026, 5, 11, 12, 0)
            )
        self.assertFalse(result)
        self.assertEqual(sends, [])

    def test_dispatcher_skips_unregistered_kind_with_warning(self) -> None:
        from end_of_line.config import ChannelSpec, NotifySpec

        spec = NotifySpec(channels=(ChannelSpec(kind="slack", params={}),))
        import io

        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            result = notify.notify(
                spec, notify.KIND_BLOCKER, "body", now=_dt.datetime(2026, 5, 11, 12, 0)
            )
        self.assertFalse(result)
        self.assertIn("slack", buf.getvalue())

    def test_quiet_hours_gate_applied_before_channel_loop(self) -> None:
        from end_of_line.config import NotifySpec

        sends: list = []
        spec = NotifySpec.imessage_only("+1", quiet_hours=("22:00", "08:00"))
        with mock.patch(
            "end_of_line.notify_imessage.IMessageNotifier.send",
            side_effect=lambda *a, **kw: sends.append(True),
        ):
            result = notify.notify(
                spec, notify.KIND_BLOCKER, "body", now=_dt.datetime(2026, 5, 11, 3, 0)
            )
        self.assertFalse(result)
        self.assertEqual(sends, [])

    def test_halt_bypass_works_across_channels(self) -> None:
        from end_of_line.config import NotifySpec

        sends: list = []
        spec = NotifySpec.imessage_only("+1", quiet_hours=("22:00", "08:00"))
        with mock.patch(
            "end_of_line.notify_imessage.IMessageNotifier.send",
            side_effect=lambda *a, **kw: sends.append(True),
        ):
            result = notify.notify(
                spec, notify.KIND_HALTED, "body", now=_dt.datetime(2026, 5, 11, 3, 0)
            )
        self.assertTrue(result)
        self.assertEqual(len(sends), 1)


class NotifyIntegrationTestCase(CluTestCase):
    """CLI wiring — cmd_block and cmd_tick should call notify with the right body."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        (self.project / ".orchestrator.json").write_text(
            json.dumps(
                {
                    "plan_dir": "plans",
                    "dispatch": {"kind": "shell", "command": "echo {phase_id}"},
                    "notify": {
                        "imessage": {"to": "+15550000000"},
                        # No quiet hours → deterministic in tests.
                    },
                }
            )
        )
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        main(["init", "--project", str(self.project), "--plan", "test-plan"])

        self.sent: list[tuple[str, str]] = []
        # mock.patch auto-restores on tearDown even if a test raises mid-way,
        # which a direct rebind of notify._osascript_send wouldn't.
        patcher = mock.patch.object(
            notify_imessage,
            "_osascript_send",
            side_effect=lambda to, body: self.sent.append((to, body)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cmd_block_sends_blocker_notification(self) -> None:
        with st.mutate(self.state_path) as data:
            token = st.claim_phase(data, "a", lease_minutes=30)
        rc = main(
            [
                "block",
                "--project",
                str(self.project),
                "--plan",
                "test-plan",
                "--phase",
                "a",
                "--token",
                token,
                "--question",
                "Pick framework",
                "--option",
                "FastAPI",
                "--option",
                "Flask",
            ]
        )
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
                data,
                st.EVENT_PHASE_COMPLETED,
                phase="a",
                commits=["abc1234"],
            )
        rc = main(
            [
                "tick",
                "--project",
                str(self.project),
                "--plan",
                "test-plan",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        _, body = self.sent[0]
        self.assertIn("test-plan", body)
        self.assertIn("1 commit", body)

    def test_cmd_tick_sends_halt_notification(self) -> None:
        with st.mutate(self.state_path) as data:
            data["config"]["max_attempts_per_phase"] = 2
            st.append_event(data, "phase_started", phase="a", claimed_by="x")
            st.append_event(data, "lease_expired", phase="a")
            st.append_event(data, "phase_started", phase="a", claimed_by="y")
            st.append_event(data, "lease_expired", phase="a")
        rc = main(
            [
                "tick",
                "--project",
                str(self.project),
                "--plan",
                "test-plan",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        _, body = self.sent[0]
        self.assertIn("test-plan", body)
        self.assertIn("a", body)
        self.assertIn("halted", body.lower())


class StalledNotifyTestCase(unittest.TestCase):
    """Supervisor populates notify_kind/body for stalled detections."""

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
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
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
