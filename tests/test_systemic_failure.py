"""Systemic-failure detection on worker fast-fail (closes #7).

When the post-spawn fast-fail catches a worker exit, the dispatcher
inspects the per-token log for known systemic-failure signatures
(missing binary, rate limit, auth). On match, the plan pauses with
a distinct event + a halt-bypass iMessage, and the attempt is not
counted against the phase — the phase isn't at fault.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import notify_imessage
from end_of_line import state as st
from end_of_line.cli import main
from end_of_line.config import DispatchSpec, NotifySpec, ProjectConfig
from end_of_line.dispatch import (
    _match_systemic_signature,
    dispatch_for_tick,
)
from end_of_line.supervisor import TickResult
from tests import CluTestCase, isolate_registry

PLAN = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `t-a.md` | thing | 1h |
"""


def _systemic_event(data: dict) -> dict | None:
    for evt in data["events"]:
        if evt["type"] == st.EVENT_SYSTEMIC_FAILURE:
            return evt
    return None


class MatchSignatureTestCase(unittest.TestCase):
    """The helper is the single source of truth for what counts as systemic.

    Keep the regex set hard-coded; new signatures land via PR with a test.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self._tmp.name) / "worker.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, body: str) -> Path:
        self.log_path.write_text(body)
        return self.log_path

    def test_missing_binary_match(self) -> None:
        path = self._write("bash: claude: command not found\n")
        self.assertEqual(_match_systemic_signature(path, rc=127), "missing_binary")

    def test_missing_binary_requires_rc127(self) -> None:
        # `command not found` text alone, with a different rc, isn't enough.
        path = self._write("the phrase command not found appeared somewhere\n")
        self.assertIsNone(_match_systemic_signature(path, rc=1))

    def test_rate_limit_keyword(self) -> None:
        path = self._write("anthropic.RateLimitError: too many requests\n")
        self.assertEqual(_match_systemic_signature(path, rc=1), "rate_limit")

    def test_rate_limit_lowercase_phrase(self) -> None:
        path = self._write("Hit rate limit; backing off.\n")
        self.assertEqual(_match_systemic_signature(path, rc=1), "rate_limit")

    def test_429_alone_is_not_enough(self) -> None:
        # Conservative: 429 alone risks false positives (HTTP samples in code,
        # comments). Require it to co-occur with a rate-limit keyword.
        path = self._write("http status 429 returned\n")
        self.assertIsNone(_match_systemic_signature(path, rc=1))

    def test_auth_failure_unauthorized(self) -> None:
        path = self._write("HTTP/1.1 401 Unauthorized\n")
        self.assertEqual(_match_systemic_signature(path, rc=1), "auth_failure")

    def test_auth_failure_invalid_api_key(self) -> None:
        path = self._write("Error: Invalid API key.\n")
        self.assertEqual(_match_systemic_signature(path, rc=1), "auth_failure")

    def test_no_match_for_generic_traceback(self) -> None:
        path = self._write("Traceback ...\nValueError: bad\n")
        self.assertIsNone(_match_systemic_signature(path, rc=1))

    def test_long_log_truncated_to_last_50(self) -> None:
        # Signature sits in line 4990 of 5000. Helper must still find it
        # because only the tail is inspected, but the head must NOT be the
        # cause of a false miss — write the signature ONLY in the tail.
        lines = (
            ["benign log line\n"] * 4990
            + [
                "anthropic.RateLimitError: throttled\n",
            ]
            + ["more benign\n"] * 9
        )
        path = self._write("".join(lines))
        self.assertEqual(_match_systemic_signature(path, rc=1), "rate_limit")

    def test_signature_in_head_only_is_ignored(self) -> None:
        # If a phrase only appears in the first 5000 lines and the tail is
        # benign, it must not match — the helper reads only the tail.
        lines = ["bash: claude: command not found\n"] + [
            "benign\n",
        ] * 5000
        path = self._write("".join(lines))
        self.assertIsNone(_match_systemic_signature(path, rc=127))

    def test_no_file(self) -> None:
        missing = Path(self._tmp.name) / "does-not-exist.log"
        self.assertIsNone(_match_systemic_signature(missing, rc=127))

    def test_first_match_wins(self) -> None:
        # All three patterns present — order is missing_binary, rate_limit,
        # auth_failure. With rc=127, missing_binary wins.
        path = self._write("bash: claude: command not found\nrate limit hit\n401 Unauthorized\n")
        self.assertEqual(_match_systemic_signature(path, rc=127), "missing_binary")


class _SystemicFixture(CluTestCase):
    """Spin up a project + plan + claim + dispatch wiring for end-to-end tests."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "t.md").write_text(PLAN)
        main(["init", "--project", str(self.project), "--plan", "t"])
        self.state_path = self.project / "plans" / ".orchestrator" / "t.state.json"
        with st.mutate(self.state_path) as data:
            self.token = st.claim_phase(data, "a", lease_minutes=30)
        self.log_dir = self.state_path.parent / "logs"
        self.log_dir.mkdir(exist_ok=True)
        self.log_path = self.log_dir / f"a.{self.token}.log"
        self.sent: list[tuple[str, str]] = []
        patcher = mock.patch.object(
            notify_imessage,
            "_osascript_send",
            side_effect=lambda to, body: self.sent.append((to, body)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cfg(self, cmd: str) -> ProjectConfig:
        return ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command=cmd),
            notify=NotifySpec.imessage_only("+15550000000"),
        )

    def _result(self) -> TickResult:
        return TickResult(
            action="dispatch",
            detail="",
            phase_id="a",
            token=self.token,
        )

    def _seed_log(self, body: str) -> None:
        self.log_path.write_text(body)

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())


class SystemicDispatchTestCase(_SystemicFixture):
    def test_missing_binary_pauses_without_attempt_increment(self) -> None:
        # The shell stub exits 127 AND prints `command not found` — same as
        # the real PATH bug that motivated this feature.
        cfg = self._cfg(
            f"sh -c 'echo \"bash: claude: command not found\" >> {self.log_path}; exit 127'",
        )
        ok = dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        self.assertFalse(ok)
        data = self._read()
        self.assertEqual(data["status"], st.STATUS_PAUSED)
        self.assertIsNone(data["current_claim"])
        evt = _systemic_event(data)
        self.assertIsNotNone(evt)
        self.assertEqual(evt["signature"], "missing_binary")
        self.assertEqual(evt["phase"], "a")
        self.assertEqual(evt["token"], self.token)
        self.assertEqual(evt["log_path"], str(self.log_path))
        # The attempt that hit systemic failure must NOT count — otherwise
        # transient PATH bugs would burn the phase's 3-attempt budget.
        self.assertEqual(st.attempts_for_phase(data, "a"), 0)
        # Halt-bypass iMessage fired exactly once.
        self.assertEqual(len(self.sent), 1)
        self.assertIn("missing_binary", self.sent[0][1])
        self.assertIn("t/a", self.sent[0][1])

    def test_rate_limit_pauses(self) -> None:
        cfg = self._cfg(
            f"sh -c 'echo \"anthropic.RateLimitError: throttled\" >> {self.log_path}; exit 1'",
        )
        dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        data = self._read()
        self.assertEqual(data["status"], st.STATUS_PAUSED)
        evt = _systemic_event(data)
        self.assertIsNotNone(evt)
        self.assertEqual(evt["signature"], "rate_limit")
        self.assertEqual(st.attempts_for_phase(data, "a"), 0)

    def test_auth_failure_pauses(self) -> None:
        cfg = self._cfg(
            f"sh -c 'echo \"HTTP/1.1 401 Unauthorized\" >> {self.log_path}; exit 1'",
        )
        dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        data = self._read()
        self.assertEqual(data["status"], st.STATUS_PAUSED)
        evt = _systemic_event(data)
        self.assertIsNotNone(evt)
        self.assertEqual(evt["signature"], "auth_failure")
        self.assertEqual(st.attempts_for_phase(data, "a"), 0)

    def test_generic_failure_keeps_running_and_increments_attempts(self) -> None:
        cfg = self._cfg(
            f"sh -c 'echo \"Traceback: ValueError\" >> {self.log_path}; exit 1'",
        )
        dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        data = self._read()
        # Existing dispatch_failed path: NOT paused, attempts counted.
        self.assertEqual(data["status"], st.STATUS_RUNNING)
        self.assertIsNone(_systemic_event(data))
        self.assertEqual(st.attempts_for_phase(data, "a"), 1)
        types = [e["type"] for e in data["events"]]
        self.assertIn(st.EVENT_DISPATCH_FAILED, types)
        # No iMessage on generic failure — that's not user-actionable.
        self.assertEqual(self.sent, [])

    def test_long_log_still_matches(self) -> None:
        # Pre-seed 5000 noise lines, then have the worker append the
        # signature in the tail. Helper reads only the last 50 lines but
        # still flips status to paused.
        self.log_path.write_text("benign\n" * 5000)
        cfg = self._cfg(
            f"sh -c 'echo \"bash: claude: command not found\" >> {self.log_path}; exit 127'",
        )
        dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        data = self._read()
        self.assertEqual(data["status"], st.STATUS_PAUSED)
        evt = _systemic_event(data)
        self.assertEqual(evt["signature"], "missing_binary")

    def test_no_log_file_falls_through_to_generic(self) -> None:
        # `true` is exit 0; force a fast-fail with `false` so the existing
        # path runs without ever creating log content beyond the
        # popen-allocated file (which may not exist yet on some systems).
        # Even with an empty/missing log, the helper returns None and we
        # take the dispatch_failed path.
        if self.log_path.exists():
            self.log_path.unlink()
        cfg = self._cfg("false")
        dispatch_for_tick(self._result(), cfg, "t", self.state_path)
        data = self._read()
        self.assertIsNone(_systemic_event(data))
        self.assertEqual(data["status"], st.STATUS_RUNNING)


class MultiPlanIndependenceTestCase(CluTestCase):
    """Each plan observes systemic failure independently.

    No cross-plan preemption: if plan A flags a rate-limit, plan B's next
    dispatch still runs and discovers the same failure on its own. v1
    accepts N iMessages for one underlying problem.
    """

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "t1.md").write_text(PLAN)
        (self.project / "plans" / "t2.md").write_text(PLAN)
        main(["init", "--project", str(self.project), "--plan", "t1"])
        main(["init", "--project", str(self.project), "--plan", "t2"])
        self.sent: list[tuple[str, str]] = []
        patcher = mock.patch.object(
            notify_imessage,
            "_osascript_send",
            side_effect=lambda to, body: self.sent.append((to, body)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cfg(self, cmd: str) -> ProjectConfig:
        return ProjectConfig(
            project_root=self.project,
            plan_dir="plans",
            dispatch=DispatchSpec(kind="shell", command=cmd),
            notify=NotifySpec.imessage_only("+15550000000"),
        )

    def test_two_plans_independently_pause(self) -> None:
        for slug in ("t1", "t2"):
            sp = self.project / "plans" / ".orchestrator" / f"{slug}.state.json"
            with st.mutate(sp) as data:
                token = st.claim_phase(data, "a", lease_minutes=30)
            log = sp.parent / "logs" / f"a.{token}.log"
            log.parent.mkdir(exist_ok=True)
            cfg = self._cfg(
                f"sh -c 'echo \"rate limit hit\" >> {log}; exit 1'",
            )
            result = TickResult(
                action="dispatch",
                detail="",
                phase_id="a",
                token=token,
            )
            dispatch_for_tick(result, cfg, slug, sp)

        for slug in ("t1", "t2"):
            sp = self.project / "plans" / ".orchestrator" / f"{slug}.state.json"
            data = json.loads(sp.read_text())
            self.assertEqual(
                data["status"],
                st.STATUS_PAUSED,
                f"{slug} should be paused",
            )
            evt = _systemic_event(data)
            self.assertIsNotNone(evt, f"{slug} should have a systemic event")
            self.assertEqual(evt["signature"], "rate_limit")
        # Both plans fired their own iMessage — no cross-plan deduping.
        self.assertEqual(len(self.sent), 2)


if __name__ == "__main__":
    unittest.main()
