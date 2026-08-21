"""`clu answer` scoping + explicit blocker addressing (answer-scope phase).

Covers the three answer-path defects the plan reproduces by probe:
  - a reply resolves against every host plan, so `--project A` can answer B;
  - two projects may share a slug, so `--plan` alone cannot disambiguate;
  - two open siblings on one plan share the plan's most-recent blocked ts, so
    a bare digit ties forever and never resolves.
Plus the new `--blocker <q-N>` explicit-addressing path (free text; refusal).
"""

from __future__ import annotations

import contextlib
import io

from end_of_line import db, registry
from end_of_line import state as st
from end_of_line.cli import ExitCode
from end_of_line.cli import main as cli_main
from tests import CluTestCase, write_state


def _blocker(bid: str, options: list[str], *, phase: str = "p1") -> dict:
    return {
        "id": bid,
        "phase_id": phase,
        "type": "blocked_input",
        "question": f"Question {bid}?",
        "options": options,
        "context": "",
        "asked_at": "2026-01-01T00:00:00Z",
        "answer": None,
        "answered_at": None,
    }


def _blocked_event(bid: str, ts: str, *, phase: str = "p1") -> dict:
    return {"ts": ts, "type": st.EVENT_PHASE_BLOCKED, "phase": phase, "blocker_id": bid}


class AnswerScopingTestCase(CluTestCase):
    def _make_project(
        self,
        dirname: str,
        slug: str,
        blockers: list[dict],
        events: list[dict] | None = None,
    ):
        project = self.tmp_path / dirname
        project.mkdir()
        state_dir = project / "plans" / ".orchestrator"
        state_dir.mkdir(parents=True)
        state_path = state_dir / f"{slug}.state.json"
        data = st.empty_state(slug, "plans")
        data["blockers"] = blockers
        data["events"] = events or []
        write_state(state_path, data)
        registry.register(project, slug)
        return project

    def _answer_of(self, blocker_id: str, project, slug: str) -> str | None:
        state_path = project / "plans" / ".orchestrator" / f"{slug}.state.json"
        data = st.load(state_path)
        for b in data["blockers"]:
            if b["id"] == blocker_id:
                return b["answer"]
        raise AssertionError(f"no blocker {blocker_id} in {slug}")

    # ------------------------------------------------------------------ #
    # --project scopes resolution                                          #
    # ------------------------------------------------------------------ #

    def test_project_scoped_answer_ignores_other_projects(self) -> None:
        # The cross-repo misroute: the OTHER project was pinged more recently,
        # so a host-wide bare digit lands there. --project must pin it to A.
        a = self._make_project(
            "dir-a", "plan-a", [_blocker("q-1", ["x", "y", "z"])],
            [_blocked_event("q-1", "2026-01-01T00:00:00Z")],
        )
        b = self._make_project(
            "dir-b", "plan-b", [_blocker("q-1", ["x", "y", "z"])],
            [_blocked_event("q-1", "2026-02-01T00:00:00Z")],
        )
        rc = cli_main(["answer", "--project", str(a), "2"])
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._answer_of("q-1", a, "plan-a"), "z")
        self.assertIsNone(self._answer_of("q-1", b, "plan-b"))

    def test_answer_without_project_stays_host_wide(self) -> None:
        # No --project → the terminal / Discord-poller path: host-wide, one
        # open blocker resolves without any scoping flag.
        a = self._make_project(
            "dir-a", "plan-a", [_blocker("q-1", ["x", "y", "z"])],
            [_blocked_event("q-1", "2026-01-01T00:00:00Z")],
        )
        rc = cli_main(["answer", "2"])
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._answer_of("q-1", a, "plan-a"), "z")

    def test_same_slug_in_two_projects_resolves_by_project(self) -> None:
        # Identical slug under two roots. Register the WRONG one first so a
        # slug-only match would pick it; --project must override the order.
        b = self._make_project(
            "dir-b", "shared", [_blocker("q-1", ["x", "y"])],
            [_blocked_event("q-1", "2026-02-01T00:00:00Z")],
        )
        a = self._make_project(
            "dir-a", "shared", [_blocker("q-1", ["x", "y"])],
            [_blocked_event("q-1", "2026-01-01T00:00:00Z")],
        )
        rc = cli_main(["answer", "--project", str(a), "--plan", "shared", "1"])
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._answer_of("q-1", a, "shared"), "y")
        self.assertIsNone(self._answer_of("q-1", b, "shared"))

    # ------------------------------------------------------------------ #
    # --blocker addresses exactly one blocker                              #
    # ------------------------------------------------------------------ #

    def test_blocker_flag_addresses_one_blocker(self) -> None:
        p = self._make_project(
            "dir-a", "plan-a",
            [_blocker("q-1", ["a", "b"]), _blocker("q-2", ["c", "d"])],
        )
        rc = cli_main(
            ["answer", "--project", str(p), "--plan", "plan-a", "--blocker", "q-2", "1"]
        )
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._answer_of("q-2", p, "plan-a"), "d")
        self.assertIsNone(self._answer_of("q-1", p, "plan-a"))

    def test_blocker_flag_accepts_free_text(self) -> None:
        p = self._make_project("dir-a", "plan-a", [_blocker("q-1", ["a", "b"])])
        rc = cli_main(
            ["answer", "--project", str(p), "--plan", "plan-a", "--blocker", "q-1",
             "use argon2"]
        )
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._answer_of("q-1", p, "plan-a"), "use argon2")

    def test_blocker_flag_joins_multiword_free_text(self) -> None:
        # Unquoted multi-word free text arrives as separate argv tokens (the
        # help promises "free text"); nargs="+" + join must reassemble it.
        p = self._make_project("dir-a", "plan-a", [_blocker("q-1", ["a", "b"])])
        rc = cli_main(
            ["answer", "--project", str(p), "--plan", "plan-a", "--blocker", "q-1",
             "go", "with", "argon2"]
        )
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._answer_of("q-1", p, "plan-a"), "go with argon2")

    def test_blocker_on_missing_store_creates_no_stray_db(self) -> None:
        # A typo'd plan/project must refuse WITHOUT materializing an empty
        # .orchestrator/clu.db — the direct path opens a write connection, which
        # would otherwise mkdir + create the store as a side effect.
        project = self.tmp_path / "empty-proj"
        project.mkdir()
        orch_dir = project / "plans" / ".orchestrator"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rc = cli_main(
                ["answer", "--project", str(project), "--plan", "ghost",
                 "--blocker", "q-1", "x"]
            )
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)
        self.assertFalse(db.project_db_path(orch_dir).exists())
        self.assertFalse(orch_dir.exists())

    def test_unknown_blocker_id_is_refused(self) -> None:
        # A bad --blocker must not fall through to fuzzy routing; it refuses,
        # naming the ids that ARE open, and touches nothing.
        p = self._make_project("dir-a", "plan-a", [_blocker("q-1", ["a", "b"])])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rc = cli_main(
                ["answer", "--project", str(p), "--plan", "plan-a", "--blocker",
                 "q-99", "1"]
            )
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)
        self.assertIsNone(self._answer_of("q-1", p, "plan-a"))
        self.assertIn("q-1", stderr.getvalue())

    # ------------------------------------------------------------------ #
    # per-blocker timestamps break the sibling deadlock                    #
    # ------------------------------------------------------------------ #

    def test_sibling_blockers_do_not_deadlock_a_bare_digit(self) -> None:
        # Two open siblings, each with its OWN phase_blocked ts. Stamping both
        # with the plan's most-recent ts (the bug) ties them forever; per-blocker
        # stamps let the more-recently-pinged one win.
        p = self._make_project(
            "dir-a", "plan-a",
            [_blocker("q-1", ["a", "b"]), _blocker("q-2", ["c", "d"])],
            [
                _blocked_event("q-1", "2026-01-01T00:00:00Z"),
                _blocked_event("q-2", "2026-02-01T00:00:00Z"),
            ],
        )
        rc = cli_main(["answer", "--project", str(p), "1"])
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(self._answer_of("q-2", p, "plan-a"), "d")
        self.assertIsNone(self._answer_of("q-1", p, "plan-a"))
