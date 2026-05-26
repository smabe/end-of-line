"""Worktree conflict warning: init stderr hint + tick-time pair emission.

The conflict shape is "two ACTIVE plans in the same project, BOTH
without a worktree" — concurrent ticks would clobber each other's
working tree. Suppression rides on each plan's `in_conflict_with` list;
the canonical-pair rule (`slug_a < slug_b` emits) keeps it to one
event + one iMessage per (project, pair) onset.
"""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import cross_plan_rules, notify, state as st
from end_of_line.cli import main
from end_of_line.config import load_project_config
from end_of_line.cross_plan_rules import worktree_conflict_rule
from tests import isolate_registry


PLAN_BODY = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `t-a.md` | thing | 1h |
"""


class ConflictWarningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "myrepo"
        self.project.mkdir()
        isolate_registry(self, Path(self._tmp.name))
        (self.project / "plans").mkdir()
        self._rules_snapshot = list(cross_plan_rules._RULES)
        cross_plan_rules._RULES.clear()
        cross_plan_rules._RULES.append(worktree_conflict_rule)

    def tearDown(self) -> None:
        cross_plan_rules._RULES[:] = self._rules_snapshot
        self._tmp.cleanup()

    def _init_plan(self, slug: str, *, capture: bool = False) -> str:
        (self.project / "plans" / f"{slug}.md").write_text(PLAN_BODY)
        if capture:
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                main(["init", "--project", str(self.project), "--plan", slug])
            return err.getvalue()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["init", "--project", str(self.project), "--plan", slug])
        return ""

    def _state_path(self, slug: str) -> Path:
        return self.project / "plans" / ".orchestrator" / f"{slug}.state.json"

    def _run_conflict_rule(self) -> None:
        """Run the worktree_conflict_rule via run_rules and fire any notifies."""
        project = self.project.resolve()
        cfg = load_project_config(project)
        plans = cross_plan_rules.load_plans_for_project(project, cfg)
        result = cross_plan_rules.run_rules(project, plans)
        if result:
            for kind, body in result.notifies:
                notify.notify(cfg.notify, kind, body)

    # --- init-time hint -----------------------------------------------

    def test_init_no_hint_when_first_plan(self) -> None:
        stderr = self._init_plan("alpha", capture=True)
        self.assertNotIn("hint:", stderr)

    def test_init_hint_when_active_sibling_lacks_worktree(self) -> None:
        # First plan: silent.
        self._init_plan("alpha")
        # Both default to status=RUNNING with no worktree → conflict.
        stderr = self._init_plan("beta", capture=True)
        self.assertIn("hint:", stderr)
        self.assertIn("alpha", stderr)
        self.assertIn("--worktree", stderr)

    def test_init_no_hint_when_sibling_paused(self) -> None:
        self._init_plan("alpha")
        with st.mutate(self._state_path("alpha")) as data:
            data["status"] = st.STATUS_PAUSED
        stderr = self._init_plan("beta", capture=True)
        self.assertNotIn("hint:", stderr)

    def test_init_no_hint_when_self_has_worktree(self) -> None:
        # `--worktree` opts the operator out of the warning regardless
        # of sibling state — they're already isolated.
        subprocess.run(["git", "-C", str(self.project), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.email", "t@t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.name", "t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "--allow-empty", "-m", "i"],
            check=True,
            capture_output=True,
        )
        self._init_plan("alpha")
        (self.project / "plans" / "beta.md").write_text(PLAN_BODY)
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            main(
                [
                    "init",
                    "--project",
                    str(self.project),
                    "--plan",
                    "beta",
                    "--worktree",
                ]
            )
        self.assertNotIn("hint:", err.getvalue())

    # --- tick-time detection ------------------------------------------

    def test_tick_emits_one_event_per_pair(self) -> None:
        self._init_plan("alpha")
        self._init_plan("beta")
        with mock.patch.object(notify, "notify") as notify_mock:
            self._run_conflict_rule()

        # One notification, one canonical-pair event.
        self.assertEqual(notify_mock.call_count, 1)
        args, kwargs = notify_mock.call_args
        kind = args[1] if len(args) >= 2 else kwargs.get("kind")
        self.assertEqual(kind, notify.KIND_HALTED)

        data_a = st.load(self._state_path("alpha"))
        data_b = st.load(self._state_path("beta"))
        evts_a = [e for e in data_a["events"] if e["type"] == "worktree_conflict_warning"]
        evts_b = [e for e in data_b["events"] if e["type"] == "worktree_conflict_warning"]
        # alpha < beta lexicographically → alpha emits, beta only updates flag.
        self.assertEqual(len(evts_a), 1)
        self.assertEqual(evts_a[0]["other_slug"], "beta")
        self.assertEqual(evts_b, [])
        # Both plans persist the conflict flag.
        self.assertEqual(data_a["in_conflict_with"], ["beta"])
        self.assertEqual(data_b["in_conflict_with"], ["alpha"])

    def test_tick_suppresses_second_emission(self) -> None:
        self._init_plan("alpha")
        self._init_plan("beta")
        # First pass emits.
        with mock.patch.object(notify, "notify"):
            self._run_conflict_rule()
        # Second pass with no state change must NOT re-emit.
        with mock.patch.object(notify, "notify") as second_call:
            self._run_conflict_rule()
        self.assertEqual(second_call.call_count, 0)

    def test_tick_clears_flag_when_sibling_pauses(self) -> None:
        self._init_plan("alpha")
        self._init_plan("beta")
        with mock.patch.object(notify, "notify"):
            self._run_conflict_rule()
        # Pause beta → alpha's conflict resolves.
        with st.mutate(self._state_path("beta")) as data:
            data["status"] = st.STATUS_PAUSED
        with mock.patch.object(notify, "notify"):
            self._run_conflict_rule()
        data_a = st.load(self._state_path("alpha"))
        data_b = st.load(self._state_path("beta"))
        self.assertEqual(data_a["in_conflict_with"], [])
        self.assertEqual(data_b["in_conflict_with"], [])

    def test_tick_no_conflict_when_one_has_worktree(self) -> None:
        self._init_plan("alpha")
        self._init_plan("beta")
        # Give alpha a worktree record by hand-editing state.
        with st.mutate(self._state_path("alpha")) as data:
            data["worktree"] = {
                "path": "/tmp/fake-wt",
                "branch": "clu/alpha",
                "base_ref": "0" * 40,
            }
        with mock.patch.object(notify, "notify") as notify_mock:
            self._run_conflict_rule()
        self.assertEqual(notify_mock.call_count, 0)

    def test_tick_three_plans_emits_each_unique_pair(self) -> None:
        self._init_plan("alpha")
        self._init_plan("beta")
        self._init_plan("gamma")
        with mock.patch.object(notify, "notify") as notify_mock:
            self._run_conflict_rule()
        # 3 plans → 3 unique pairs: (alpha,beta), (alpha,gamma), (beta,gamma).
        self.assertEqual(notify_mock.call_count, 3)
        data_a = st.load(self._state_path("alpha"))
        data_b = st.load(self._state_path("beta"))
        data_g = st.load(self._state_path("gamma"))
        self.assertEqual(data_a["in_conflict_with"], ["beta", "gamma"])
        self.assertEqual(data_b["in_conflict_with"], ["alpha", "gamma"])
        self.assertEqual(data_g["in_conflict_with"], ["alpha", "beta"])
        # alpha emits 2 events (vs beta, vs gamma); beta emits 1 (vs gamma);
        # gamma emits 0 (lexicographically last).
        emits_a = [e for e in data_a["events"] if e["type"] == "worktree_conflict_warning"]
        emits_b = [e for e in data_b["events"] if e["type"] == "worktree_conflict_warning"]
        emits_g = [e for e in data_g["events"] if e["type"] == "worktree_conflict_warning"]
        self.assertEqual(len(emits_a), 2)
        self.assertEqual(len(emits_b), 1)
        self.assertEqual(emits_g, [])

    def test_tick_skips_pair_when_pair_already_known(self) -> None:
        self._init_plan("alpha")
        self._init_plan("beta")
        # Pre-seed both as already-warned about each other.
        with st.mutate(self._state_path("alpha")) as data:
            data["in_conflict_with"] = ["beta"]
        with st.mutate(self._state_path("beta")) as data:
            data["in_conflict_with"] = ["alpha"]
        with mock.patch.object(notify, "notify") as notify_mock:
            self._run_conflict_rule()
        self.assertEqual(notify_mock.call_count, 0)


if __name__ == "__main__":
    unittest.main()
