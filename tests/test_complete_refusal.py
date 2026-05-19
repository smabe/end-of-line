"""Tests for `cmd_complete` quality gates: verify + simplify attestation refusal.

The gate refuses with STATUS_TRANSITION when:
- verify attestation is missing or stale (always checked)
- simplify attestation is missing or stale AND diff exceeds threshold

Operator can bypass each gate independently with --skip-verify / --skip-simplify.
Each bypass emits an audit event.
"""
from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from end_of_line.config import CONFIG_FILENAME
from tests import isolate_registry


PLAN_BODY = """\
# Test plan

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| phase-a | `test-plan-phase-a.md` | thing | 1h |
"""


def _write_config(project: Path, *, quality: dict | None = None) -> None:
    cfg: dict = {"dispatch": {"command": "echo hi"}}
    if quality:
        cfg["quality"] = quality
    (project / CONFIG_FILENAME).write_text(json.dumps(cfg))


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


class CompleteRefusalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        (self.project / "plans").mkdir()
        (self.project / "plans" / "test-plan.md").write_text(PLAN_BODY)
        _write_config(self.project)
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        _git("config", "user.email", "t@t", cwd=self.project)
        _git("config", "user.name", "t", cwd=self.project)
        _git("commit", "--allow-empty", "-m", "base", cwd=self.project)
        self.base_sha = _git("rev-parse", "HEAD", cwd=self.project)
        self.state_path = (
            self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        )
        self.assertEqual(
            main(["init", "--project", str(self.project), "--plan", "test-plan"]), 0,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- helpers ---------------------------------------------------------------

    def _claim(self, phase: str = "phase-a") -> str:
        with st.mutate(self.state_path) as data:
            token = st.claim_phase(data, phase, lease_minutes=30)
            data["current_claim"]["head_sha_at_claim"] = self.base_sha
            return token

    def _head(self) -> str:
        return _git("rev-parse", "HEAD", cwd=self.project)

    def _make_commit(self, filename: str = "x.txt", lines: int = 5) -> str:
        """Write a file with `lines` lines, stage it, commit, return new HEAD SHA."""
        path = self.project / filename
        path.write_text("\n".join(f"line {i}" for i in range(lines)))
        _git("add", filename, cwd=self.project)
        _git("commit", "-m", f"add {filename}", cwd=self.project)
        return self._head()

    def _stamp_verify(self, sha: str | None = None) -> None:
        sha = sha or self._head()
        with st.mutate(self.state_path) as data:
            st.stamp_attestation(data, st.ATTESTATION_VERIFY, sha)

    def _stamp_simplify(self, sha: str | None = None) -> None:
        sha = sha or self._head()
        with st.mutate(self.state_path) as data:
            st.stamp_attestation(data, st.ATTESTATION_SIMPLIFY, sha)

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())

    def _complete(self, token: str, *extra: str) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main([
                "complete",
                "--project", str(self.project),
                "--plan", "test-plan",
                "--phase", "phase-a",
                "--token", token,
                *extra,
            ])
        return rc, buf.getvalue()

    def _claim_is_live(self) -> bool:
        return self._read().get("current_claim") is not None

    def _events_of_type(self, event_type: str) -> list[dict]:
        return [e for e in self._read()["events"] if e.get("type") == event_type]

    # ---- verify gate -----------------------------------------------------------

    def test_complete_refused_when_no_verify_attestation(self) -> None:
        token = self._claim()
        rc, err = self._complete(token)
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("verify", err.lower())
        self.assertTrue(self._claim_is_live(), "claim must stay live on refusal")

    def test_complete_refused_when_verify_stale(self) -> None:
        token = self._claim()
        old_sha = self._head()
        self._stamp_verify(old_sha)
        new_sha = self._make_commit("stale.txt")
        rc, err = self._complete(token)
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn(old_sha[:7], err)
        self.assertIn(new_sha[:7], err)
        self.assertTrue(self._claim_is_live())

    def test_complete_accepts_fresh_verify_stamp(self) -> None:
        token = self._claim()
        self._stamp_verify()
        rc, _ = self._complete(token)
        self.assertEqual(rc, ExitCode.OK)

    def test_complete_with_skip_verify_bypasses_gate(self) -> None:
        token = self._claim()
        rc, _ = self._complete(token, "--skip-verify")
        self.assertEqual(rc, ExitCode.OK)
        events = self._events_of_type(st.EVENT_OPERATOR_SKIP_VERIFY)
        self.assertEqual(len(events), 1)

    # ---- simplify gate ---------------------------------------------------------

    def test_complete_no_simplify_required_when_diff_below_threshold(self) -> None:
        # Default threshold: {files: 1, lines: 30}.
        # 1 file × 10 lines: files_changed=1, 1 > 1 is False → no simplify needed.
        token = self._claim()
        self._make_commit("small.txt", lines=10)
        self._stamp_verify()
        rc, _ = self._complete(token)
        self.assertEqual(rc, ExitCode.OK)

    def test_complete_simplify_required_when_files_exceed(self) -> None:
        # 2 files → files_changed=2 > t_files=1 → gate fires.
        token = self._claim()
        self._make_commit("a.txt", lines=5)
        self._make_commit("b.txt", lines=5)
        self._stamp_verify()
        rc, err = self._complete(token)
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("simplify", err.lower())
        self.assertTrue(self._claim_is_live())

    def test_complete_simplify_required_when_lines_exceed(self) -> None:
        # 1 file × 50 lines → lines_changed=50 > t_lines=30 → gate fires.
        token = self._claim()
        self._make_commit("big.txt", lines=50)
        self._stamp_verify()
        rc, err = self._complete(token)
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("simplify", err.lower())
        self.assertTrue(self._claim_is_live())

    def test_complete_simplify_stale_refused(self) -> None:
        # Simplify stamp then another commit → stamp is stale → gate refuses.
        token = self._claim()
        self._make_commit("big.txt", lines=50)
        self._stamp_verify()
        old_sha = self._head()
        self._stamp_simplify(old_sha)
        new_sha = self._make_commit("extra.txt", lines=5)
        self._stamp_verify()
        rc, err = self._complete(token)
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn(old_sha[:7], err)
        self.assertIn(new_sha[:7], err)
        self.assertTrue(self._claim_is_live())

    def test_complete_with_skip_simplify_bypasses_gate(self) -> None:
        token = self._claim()
        self._make_commit("big.txt", lines=50)
        self._stamp_verify()
        rc, _ = self._complete(token, "--skip-simplify")
        self.assertEqual(rc, ExitCode.OK)
        events = self._events_of_type(st.EVENT_OPERATOR_SKIP_SIMPLIFY)
        self.assertEqual(len(events), 1)

    # ---- threshold override ----------------------------------------------------

    def test_complete_honors_simplify_threshold_override(self) -> None:
        # Custom threshold {files: 5, lines: 100}: 3 files × 30 lines = 90 total.
        # Above default (1 file / 30 lines), below override (5 files / 100 lines) → pass.
        _write_config(self.project, quality={"simplify_threshold": {"files": 5, "lines": 100}})
        token = self._claim()
        for i in range(3):
            self._make_commit(f"f{i}.txt", lines=30)
        self._stamp_verify()
        rc, _ = self._complete(token)
        self.assertEqual(rc, ExitCode.OK)

    def test_complete_gate_everything_threshold(self) -> None:
        # {files: 0, lines: 0}: any diff triggers simplify gate.
        _write_config(self.project, quality={"simplify_threshold": {"files": 0, "lines": 0}})
        token = self._claim()
        self._make_commit("tiny.txt", lines=1)
        self._stamp_verify()
        rc, err = self._complete(token)
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("simplify", err.lower())

    # ---- combined / edge cases -------------------------------------------------

    def test_complete_both_skip_flags_independent(self) -> None:
        token = self._claim()
        self._make_commit("big.txt", lines=50)
        rc, _ = self._complete(token, "--skip-verify", "--skip-simplify")
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(len(self._events_of_type(st.EVENT_OPERATOR_SKIP_VERIFY)), 1)
        self.assertEqual(len(self._events_of_type(st.EVENT_OPERATOR_SKIP_SIMPLIFY)), 1)

    def test_complete_no_commits_phase_still_requires_verify(self) -> None:
        # 0 commits → diff is empty (below threshold) but verify still required.
        token = self._claim()
        rc, err = self._complete(token)
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("verify", err.lower())
        # Release and re-claim so we can try --skip-verify.
        with st.mutate(self.state_path) as data:
            st.release_claim(data)
        token2 = self._claim()
        rc2, _ = self._complete(token2, "--skip-verify")
        self.assertEqual(rc2, ExitCode.OK)


if __name__ == "__main__":
    unittest.main()
