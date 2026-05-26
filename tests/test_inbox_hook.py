"""Tests for the `clu_inbox_surface` UserPromptSubmit hook script.

The hook script is invoked by Claude Code at the start of every user
prompt. It reads `~/.config/clu/inbox/`, filters events to the current
project_root, emits `hookSpecificOutput` JSON on stdout, and marks each
surfaced event processed.

Tests invoke the script as a subprocess to exercise the full headless
boundary — stdin payload + stdout JSON shape + filesystem side effects.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from end_of_line import inbox, registry
from end_of_line import state as st
from end_of_line.notify_base import BlockerDetail, open_blockers_with_details
from tests import isolate_monitor_marker


def _run_hook(
    *,
    cwd: Path,
    xdg: Path,
    stdin_payload: str = "{}",
    timeout: float = 5.0,
) -> tuple[int, str, str, float]:
    """Run the hook as a subprocess from `cwd` with `XDG_CONFIG_HOME=xdg`."""
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(xdg)
    env["PYTHONPATH"] = (
        str(
            Path(__file__).resolve().parent.parent,
        )
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    start = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "end_of_line.hooks.clu_inbox_surface"],
        cwd=str(cwd),
        env=env,
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.time() - start
    return proc.returncode, proc.stdout, proc.stderr, elapsed


class HookTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        isolate_monitor_marker(self, self.tmp)
        self.xdg = self.tmp
        self.proj = self.tmp / "proj-a"
        self.proj.mkdir()


class HookSurfacingTests(HookTestBase):
    def test_hook_empty_inbox_exits_zero_no_stdout(self) -> None:
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_hook_surfaces_events_for_current_project(self) -> None:
        inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root=str(self.proj),
            summary="proj-a event 1",
        )
        inbox.write_event(
            type="blocked",
            plan_slug="foo",
            project_root=str(self.proj),
            summary="proj-a event 2",
        )
        other = self.tmp / "proj-b"
        other.mkdir()
        inbox.write_event(
            type="halted",
            plan_slug="bar",
            project_root=str(other),
            summary="proj-b event",
        )

        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"],
            "UserPromptSubmit",
        )
        self.assertIn("proj-a event 1", ctx)
        self.assertIn("proj-a event 2", ctx)
        self.assertNotIn("proj-b event", ctx)

    def test_hook_marks_surfaced_events_processed(self) -> None:
        inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root=str(self.proj),
            summary="event 1",
        )
        inbox.write_event(
            type="blocked",
            plan_slug="foo",
            project_root=str(self.proj),
            summary="event 2",
        )
        rc, _, _, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0)
        # Subsequent run sees an empty unprocessed inbox.
        rc2, out2, _, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc2, 0)
        self.assertEqual(out2, "")

    def test_hook_caps_at_20_events_with_footer(self) -> None:
        for i in range(25):
            inbox.write_event(
                type="halted",
                plan_slug="foo",
                project_root=str(self.proj),
                summary=f"event {i:02d}",
            )
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        # The 5 oldest must be summarized as a footer, not listed inline.
        self.assertNotIn("event 00", ctx)
        self.assertNotIn("event 04", ctx)
        self.assertIn("event 24", ctx)
        self.assertIn("5 older events", ctx)

    def test_hook_truncates_additional_context_at_10k_chars(self) -> None:
        huge = "X" * 2000
        for i in range(20):
            inbox.write_event(
                type="halted",
                plan_slug="foo",
                project_root=str(self.proj),
                summary=f"{i:02d} {huge}",
            )
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), 10_000)
        self.assertIn("truncated", ctx.lower())

    def test_hook_falls_back_to_cwd_when_no_git(self) -> None:
        non_repo = self.tmp / "no-repo"
        non_repo.mkdir()
        inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root=str(non_repo),
            summary="cwd fallback",
        )
        rc, out, err, _ = _run_hook(cwd=non_repo, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("cwd fallback", ctx)

    def test_hook_exits_zero_on_corrupt_event_file(self) -> None:
        inbox.write_event(
            type="halted",
            plan_slug="foo",
            project_root=str(self.proj),
            summary="ok",
        )
        # Corrupt a sibling file.
        root = inbox.inbox_root()
        (root / "bogus.json").write_text("{{{ not valid")
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        # Valid event still surfaces.
        payload = json.loads(out)
        self.assertIn("ok", payload["hookSpecificOutput"]["additionalContext"])

    def test_hook_under_500ms_with_50_events(self) -> None:
        for i in range(50):
            inbox.write_event(
                type="halted",
                plan_slug="foo",
                project_root=str(self.proj),
                summary=f"event {i}",
            )
        # CI buffer: 2s. Local typical: <500ms. The mandate is "comfortably
        # under the latency budget"; if this trips on slow CI, raise the
        # ceiling, don't delete the test.
        _, _, _, elapsed = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertLess(elapsed, 2.0)


class BlockerSurfacingTests(HookTestBase):
    """Tests for the active-blocker section emitted by the hook."""

    def _seed_plan(
        self,
        project: Path,
        slug: str,
        blockers: list[dict],
    ) -> None:
        """Write state file with given blockers and register the plan."""
        sp = project / "plans" / ".orchestrator" / f"{slug}.state.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        with st.locked(sp):
            data = st.empty_state(slug, "plans")
            data["blockers"] = blockers
            st.save_atomic(sp, data)
        registry.register(project, slug)

    def _open_blocker(
        self,
        bid: str,
        phase: str = "p1",
        question: str = "Which approach?",
        options: list[str] | None = None,
    ) -> dict:
        return {
            "id": bid,
            "phase_id": phase,
            "type": st.BLOCKER_INPUT,
            "question": question,
            "options": options or ["Option A", "Option B"],
            "context": "",
            "asked_at": st.utcnow(),
            "answer": None,
            "answered_at": None,
        }

    def test_hook_surfaces_active_blocker(self) -> None:
        self._seed_plan(
            self.proj,
            "my-plan",
            [
                self._open_blocker(
                    "q-1",
                    phase="impl",
                    question="Which database?",
                    options=["SQLite", "PostgreSQL"],
                )
            ],
        )
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("## Active blockers", ctx)
        self.assertIn("Plan `my-plan`", ctx)
        self.assertIn("phase `impl`", ctx)
        self.assertIn("blocker `q-1`", ctx)
        self.assertIn("Which database?", ctx)
        self.assertIn("[0] SQLite", ctx)
        self.assertIn("[1] PostgreSQL", ctx)
        self.assertIn("clu answer", ctx)

    def test_hook_omits_blockers_section_when_none_open(self) -> None:
        # Write an inbox event so the hook produces output we can inspect.
        inbox.write_event(
            type="halted",
            plan_slug="my-plan",
            project_root=str(self.proj),
            summary="plan halted",
        )
        # No BLOCKED plans registered — section must be absent.
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("## Active blockers", ctx)

    def test_hook_surfaces_multiple_blockers_across_plans(self) -> None:
        self._seed_plan(
            self.proj,
            "plan-a",
            [self._open_blocker("q-1", question="Question A?", options=["Yes", "No"])],
        )
        self._seed_plan(
            self.proj,
            "plan-b",
            [self._open_blocker("q-2", question="Question B?", options=["Fast", "Slow"])],
        )
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("plan-a", ctx)
        self.assertIn("plan-b", ctx)
        self.assertIn("Question A?", ctx)
        self.assertIn("Question B?", ctx)

    def test_hook_scopes_blockers_to_current_project(self) -> None:
        proj_b = self.tmp / "proj-b"
        proj_b.mkdir()
        self._seed_plan(
            self.proj, "plan-a", [self._open_blocker("q-1", question="Proj A question?")]
        )
        self._seed_plan(proj_b, "plan-b", [self._open_blocker("q-2", question="Proj B question?")])
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Proj A question?", ctx)
        self.assertNotIn("Proj B question?", ctx)

    def test_hook_caps_blockers_at_10(self) -> None:
        for i in range(12):
            slug = f"plan-{i:02d}"
            self._seed_plan(
                self.proj, slug, [self._open_blocker(f"q-{i}", question=f"Question {i}?")]
            )
        rc, out, err, _ = _run_hook(cwd=self.proj, xdg=self.xdg)
        self.assertEqual(rc, 0, msg=err)
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("## Active blockers", ctx)
        self.assertIn("+2 more", ctx)
        self.assertEqual(ctx.count("Question:"), 10)

    def test_open_blockers_with_details_includes_question(self) -> None:
        proj = self.tmp / "proj-unit"
        proj.mkdir()
        self._seed_plan(
            proj,
            "test-plan",
            [
                self._open_blocker(
                    "q-1",
                    phase="build",
                    question="Pick an option",
                    options=["Alpha", "Beta", "Gamma"],
                )
            ],
        )
        entries = registry.entries()
        result = open_blockers_with_details(entries, proj)
        self.assertEqual(len(result), 1)
        d = result[0]
        self.assertEqual(d.plan_slug, "test-plan")
        self.assertEqual(d.phase_id, "build")
        self.assertEqual(d.blocker_id, "q-1")
        self.assertEqual(d.question, "Pick an option")
        self.assertEqual(d.options, ("Alpha", "Beta", "Gamma"))


if __name__ == "__main__":
    unittest.main()
