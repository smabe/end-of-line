"""Tests for `clu heartbeat-daemon` — the daemonized heartbeat loop (#90).

Covers the sub-plan's contract:
  - tick_once: worker-dead → exit action; stale-token/claim-gone → exit
    action (NOT a strike); transient error → strike; success → ok.
  - run_loop: strike accumulation, 3rd consecutive strike fires the
    notify path exactly once, successful ping resets the counter,
    exit actions stop the loop.
  - CLI surface: bad phase slug rejected, missing token rejected, bad
    token rejected BEFORE daemonizing, good args dispatch to run().

The double-fork itself is deliberately not unit-tested (detach=False is
the test seam); phase migrate-dogfood's live smoke covers real forking.
"""

from __future__ import annotations

import unittest
from unittest import mock

from end_of_line import heartbeat_daemon as hbd
from end_of_line import state as st
from end_of_line.cli import main
from tests import CluTestCase, plan_body

PLAN_BODY = plan_body("a")


class TickOnceTestCase(CluTestCase):
    """Per-tick decision against a real state file."""

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

    def test_worker_dead_returns_exit_action(self) -> None:
        action = hbd.tick_once(
            self.state_path, "a", self.token, 12345,
            pid_alive=lambda pid: False,
        )
        self.assertEqual(action, hbd.ACTION_EXIT_WORKER_DEAD)

    def test_dead_worker_does_not_ping(self) -> None:
        before = st.load(self.state_path)["current_claim"]["last_heartbeat_at"]
        hbd.tick_once(
            self.state_path, "a", self.token, 12345,
            pid_alive=lambda pid: False,
        )
        after = st.load(self.state_path)["current_claim"]["last_heartbeat_at"]
        self.assertEqual(before, after)

    def test_live_worker_pings_heartbeat(self) -> None:
        with st.mutate(self.state_path) as data:
            data["current_claim"]["last_heartbeat_at"] = "2020-01-01T00:00:00Z"
        action = hbd.tick_once(
            self.state_path, "a", self.token, 12345,
            pid_alive=lambda pid: True,
        )
        self.assertEqual(action, hbd.ACTION_OK)
        stamp = st.load(self.state_path)["current_claim"]["last_heartbeat_at"]
        self.assertNotEqual(stamp, "2020-01-01T00:00:00Z")

    def test_stale_token_returns_claim_gone(self) -> None:
        action = hbd.tick_once(
            self.state_path, "a", "session-imposter00000000", 12345,
            pid_alive=lambda pid: True,
        )
        self.assertEqual(action, hbd.ACTION_EXIT_CLAIM_GONE)

    def test_released_claim_returns_claim_gone(self) -> None:
        """Post-`clu complete` shutdown: claim released → clean exit, not a strike."""
        with st.mutate(self.state_path) as data:
            st.release_claim(data)
        action = hbd.tick_once(
            self.state_path, "a", self.token, 12345,
            pid_alive=lambda pid: True,
        )
        self.assertEqual(action, hbd.ACTION_EXIT_CLAIM_GONE)

    def test_transient_error_returns_strike(self) -> None:
        def boom(state_path, token, phase):
            raise OSError("lock contention")

        action = hbd.tick_once(
            self.state_path, "a", self.token, 12345,
            pid_alive=lambda pid: True,
            ping=boom,
        )
        self.assertEqual(action, hbd.ACTION_STRIKE)

    def test_default_pid_alive_sees_own_process(self) -> None:
        import os

        action = hbd.tick_once(self.state_path, "a", self.token, os.getpid())
        self.assertEqual(action, hbd.ACTION_OK)


class RunLoopTestCase(unittest.TestCase):
    """Strike/notify semantics with a scripted tick sequence — no I/O."""

    def _run(self, actions: list[str], *, max_ticks: int | None = None):
        notify_calls: list[tuple] = []
        script = iter(actions)
        rc = hbd.run_loop(
            project_root="/proj",
            plan="test-plan",
            phase="a",
            token="session-aaaa1111bbbb2222",
            worker_pid=12345,
            state_path="/proj/plans/.orchestrator/test-plan.state.json",
            log_path="/proj/plans/.orchestrator/logs/a.tok.hb.log",
            sleep=lambda s: None,
            tick=lambda *args: next(script),
            notify_failure=lambda *args: notify_calls.append(args),
            max_ticks=max_ticks if max_ticks is not None else len(actions),
        )
        return rc, notify_calls

    def test_third_consecutive_strike_notifies_exactly_once(self) -> None:
        rc, calls = self._run([hbd.ACTION_STRIKE] * 5)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)

    def test_two_strikes_do_not_notify(self) -> None:
        _, calls = self._run([hbd.ACTION_STRIKE, hbd.ACTION_STRIKE, hbd.ACTION_OK])
        self.assertEqual(calls, [])

    def test_successful_ping_resets_strikes(self) -> None:
        seq = [
            hbd.ACTION_STRIKE,
            hbd.ACTION_STRIKE,
            hbd.ACTION_OK,
            hbd.ACTION_STRIKE,
            hbd.ACTION_STRIKE,
        ]
        _, calls = self._run(seq)
        self.assertEqual(calls, [])

    def test_exit_worker_dead_stops_loop(self) -> None:
        consumed = []

        def tick(*args):
            consumed.append(1)
            return hbd.ACTION_EXIT_WORKER_DEAD

        rc = hbd.run_loop(
            project_root="/proj",
            plan="test-plan",
            phase="a",
            token="t",
            worker_pid=1,
            state_path="s",
            log_path="l",
            sleep=lambda s: None,
            tick=tick,
            notify_failure=lambda *args: self.fail("must not notify"),
            max_ticks=10,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(consumed), 1)

    def test_exit_claim_gone_stops_loop_without_strike(self) -> None:
        rc, calls = self._run(
            [hbd.ACTION_STRIKE, hbd.ACTION_STRIKE, hbd.ACTION_EXIT_CLAIM_GONE],
            max_ticks=10,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_notify_failure_exception_does_not_kill_loop(self) -> None:
        def explode(*args):
            raise RuntimeError("notify transport down")

        script = iter([hbd.ACTION_STRIKE] * 4)
        rc = hbd.run_loop(
            project_root="/proj",
            plan="test-plan",
            phase="a",
            token="t",
            worker_pid=1,
            state_path="s",
            log_path="l",
            sleep=lambda s: None,
            tick=lambda *args: next(script),
            notify_failure=explode,
            max_ticks=4,
        )
        self.assertEqual(rc, 0)


class HeartbeatDaemonCliTestCase(CluTestCase):
    """CLI surface — validation happens before any fork."""

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

    def _argv(self, *, phase: str = "a", token: str | None = None,
              worker_pid: str | None = "12345") -> list[str]:
        argv = [
            "heartbeat-daemon",
            "--project", str(self.project),
            "--plan", "test-plan",
            "--phase", phase,
            "--token", token or self.token,
        ]
        if worker_pid is not None:
            argv += ["--worker-pid", worker_pid]
        return argv

    def test_bad_phase_slug_rejected(self) -> None:
        with mock.patch.object(hbd, "run") as run:
            rc = main(self._argv(phase="NOT A SLUG"))
        self.assertEqual(rc, 2)  # ExitCode.INVALID_SLUG
        run.assert_not_called()

    def test_missing_token_rejected(self) -> None:
        argv = [a for a in self._argv() if a != "--token" and a != self.token]
        with self.assertRaises(SystemExit):
            main(argv)

    def test_bad_token_rejected_before_daemonizing(self) -> None:
        with mock.patch.object(hbd, "run") as run:
            rc = main(self._argv(token="session-imposter00000000"))
        self.assertEqual(rc, 4)  # ExitCode.CLAIM_MISMATCH
        run.assert_not_called()

    def test_nonpositive_worker_pid_rejected(self) -> None:
        with mock.patch.object(hbd, "run") as run:
            rc = main(self._argv(worker_pid="0"))
        self.assertEqual(rc, 8)  # ExitCode.INVALID_VALUE
        run.assert_not_called()

    def test_valid_args_stamp_first_heartbeat_and_dispatch_to_run(self) -> None:
        with st.mutate(self.state_path) as data:
            data["current_claim"]["last_heartbeat_at"] = "2020-01-01T00:00:00Z"
        with mock.patch.object(hbd, "run", return_value=0) as run:
            rc = main(self._argv())
        self.assertEqual(rc, 0)
        run.assert_called_once()
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["plan"], "test-plan")
        self.assertEqual(kwargs["phase"], "a")
        self.assertEqual(kwargs["token"], self.token)
        self.assertEqual(kwargs["worker_pid"], 12345)
        # Arm-time validation doubles as the first heartbeat.
        stamp = st.load(self.state_path)["current_claim"]["last_heartbeat_at"]
        self.assertNotEqual(stamp, "2020-01-01T00:00:00Z")

    def test_omitted_worker_pid_defaults_to_claim_pid(self) -> None:
        # The dispatcher stamps the worker PID into the claim; under
        # scoped-permission dispatch `$PPID` doesn't survive the
        # permission matcher, so the flag must be optional (#90 smoke).
        with st.mutate(self.state_path) as data:
            data["current_claim"]["pid"] = 54321
        with mock.patch.object(hbd, "run", return_value=0) as run:
            rc = main(self._argv(worker_pid=None))
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_args.kwargs["worker_pid"], 54321)

    def test_omitted_worker_pid_without_claim_pid_rejected(self) -> None:
        with st.mutate(self.state_path) as data:
            data["current_claim"]["pid"] = None
        with mock.patch.object(hbd, "run") as run:
            rc = main(self._argv(worker_pid=None))
        self.assertEqual(rc, 8)  # ExitCode.INVALID_VALUE
        run.assert_not_called()

    def test_omitted_worker_pid_with_nonpositive_claim_pid_rejected(self) -> None:
        # A non-positive claim pid must not reach the daemon: os.kill(-1, 0)
        # signals every process the user owns, so a corrupt stamp would make
        # the liveness probe always succeed.
        with st.mutate(self.state_path) as data:
            data["current_claim"]["pid"] = -7
        with mock.patch.object(hbd, "run") as run:
            rc = main(self._argv(worker_pid=None))
        self.assertEqual(rc, 8)  # ExitCode.INVALID_VALUE
        run.assert_not_called()

    def test_sidecar_log_path_uses_phase_and_token(self) -> None:
        with mock.patch.object(hbd, "run", return_value=0) as run:
            main(self._argv())
        log_path = run.call_args.kwargs["log_path"]
        self.assertEqual(log_path.name, f"a.{self.token}.hb.log")
        # cfg.project_root is symlink-resolved (/var → /private/var on macOS).
        self.assertEqual(
            log_path.parent, (self.state_path.parent / "logs").resolve()
        )


if __name__ == "__main__":
    unittest.main()
