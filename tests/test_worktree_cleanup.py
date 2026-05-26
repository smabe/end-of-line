"""Worktree cleanup on plan end (#34).

Three callers share the upstream-reachability gate
`_is_branch_reachable_from_origin`:

- `cmd_complete` opportunistic cleanup when its completion finishes the
  last pending phase
- `cmd_archive` explicit plan-level cleanup
- `cmd_worktree_gc` retrofit — retain-and-warn when branch is ahead

Fixture wires up a bare origin remote so the reachable / ahead branches
can both be exercised against real git state. The no-origin case is
already covered by `tests/test_worktree_gc.py` (which doesn't configure
a remote and expects pre-#34 behavior to be preserved)."""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import isolate_registry


# Plan-file basenames must be `<slug>-<phase>.md` so plan_parser extracts
# clean phase ids ("a", "b") instead of treating the whole basename as the id.
PLAN_BODY_TWO_PHASES_TMPL = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `{slug}-a.md` | thing | 1h |
| B | `{slug}-b.md` | thing | 1h |
"""

PLAN_BODY_ONE_PHASE_TMPL = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `{slug}-a.md` | thing | 1h |
"""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


class WorktreeCleanupBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self._tmp.name)
        self.project = self.parent / "myrepo"
        self.project.mkdir()
        isolate_registry(self, self.parent)
        (self.project / "plans").mkdir()
        _git(self.project, "init", "-q")
        _git(self.project, "config", "user.email", "t@t")
        _git(self.project, "config", "user.name", "t")
        _git(self.project, "commit", "--allow-empty", "-m", "init")
        _git(self.project, "branch", "-M", "main")
        # Bare origin remote so origin/<default> is a real ref.
        self.origin = self.parent / "origin.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", str(self.origin)],
            check=True,
            capture_output=True,
        )
        _git(self.project, "remote", "add", "origin", str(self.origin))
        _git(self.project, "push", "-u", "origin", "main")
        _git(self.project, "remote", "set-head", "origin", "main")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state_path(self, slug: str) -> Path:
        return self.project / "plans" / ".orchestrator" / f"{slug}.state.json"

    def _init_plan(
        self,
        slug: str,
        body_tmpl: str = PLAN_BODY_TWO_PHASES_TMPL,
        *,
        worktree: bool = True,
    ) -> Path:
        plan_md = self.project / "plans" / f"{slug}.md"
        plan_md.write_text(body_tmpl.format(slug=slug))
        _git(self.project, "add", f"plans/{slug}.md")
        _git(self.project, "commit", "-m", f"add {slug} plan")
        _git(self.project, "push", "origin", "main")
        init_args = ["init", "--project", str(self.project), "--plan", slug]
        if worktree:
            init_args.append("--worktree")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(init_args)
        return self._state_path(slug)

    def _archive(self, slug: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["archive", "--project", str(self.project), "--plan", slug])
        return rc, out.getvalue(), err.getvalue()

    def _set_status(self, slug: str, status: str) -> None:
        with st.mutate(self._state_path(slug)) as data:
            data["status"] = status

    def _claim_phase(self, slug: str, phase: str) -> str:
        state_path = self._state_path(slug)
        with st.mutate(state_path) as data:
            token = st.claim_phase(data, phase, lease_minutes=10)
        return token

    def _complete(self, slug: str, phase: str, token: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "complete",
                    "--project",
                    str(self.project),
                    "--plan",
                    slug,
                    "--phase",
                    phase,
                    "--token",
                    token,
                    "--skip-verify",
                    "--skip-simplify",
                ]
            )
        return rc, err.getvalue()

    def _add_ahead_commit(self, slug: str) -> None:
        """Add a commit on the worktree's branch so it's ahead of origin."""
        data = st.load(self._state_path(slug))
        wt = st.get_worktree(data)
        assert wt is not None
        _git(Path(wt["path"]), "commit", "--allow-empty", "-m", "ahead")

    def _wt_record(self, slug: str) -> dict | None:
        return st.get_worktree(st.load(self._state_path(slug)))


class CmdCompleteCleanupTests(WorktreeCleanupBase):
    def test_completes_last_phase_cleans_up_when_reachable(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL)
        token = self._claim_phase("alpha", "a")
        rc, _ = self._complete("alpha", "a", token)
        self.assertEqual(rc, 0)
        record = self._wt_record("alpha")
        self.assertIsNone(record)
        events = st.load(self._state_path("alpha"))["events"]
        kinds = [e.get("type") for e in events]
        self.assertIn(st.EVENT_WORKTREE_CLEANED, kinds)

    def test_completes_interim_phase_leaves_worktree_in_place(self) -> None:
        # Two-phase plan: completing A is NOT plan end → no cleanup.
        self._init_plan("alpha", PLAN_BODY_TWO_PHASES_TMPL)
        before = self._wt_record("alpha")
        self.assertIsNotNone(before)
        token = self._claim_phase("alpha", "a")
        rc, _ = self._complete("alpha", "a", token)
        self.assertEqual(rc, 0)
        # Worktree record still present.
        self.assertIsNotNone(self._wt_record("alpha"))
        # No CLEANED event yet.
        events = st.load(self._state_path("alpha"))["events"]
        kinds = [e.get("type") for e in events]
        self.assertNotIn(st.EVENT_WORKTREE_CLEANED, kinds)

    def test_completes_last_phase_retains_when_ahead(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL)
        self._add_ahead_commit("alpha")  # worktree branch now ahead of origin/main
        token = self._claim_phase("alpha", "a")
        rc, _ = self._complete("alpha", "a", token)
        self.assertEqual(rc, 0)
        # Worktree record retained.
        self.assertIsNotNone(self._wt_record("alpha"))
        events = st.load(self._state_path("alpha"))["events"]
        retain_evts = [e for e in events if e.get("type") == st.EVENT_WORKTREE_RETAINED_AHEAD]
        self.assertEqual(len(retain_evts), 1)
        evt = retain_evts[0]
        self.assertEqual(evt["trigger"], "complete")
        self.assertTrue(evt["ahead_commits"], "expected at least one ahead SHA")

    def test_complete_without_worktree_is_noop(self) -> None:
        # Init without --worktree → no record → cleanup helper short-circuits.
        (self.project / "plans" / "alpha.md").write_text(
            PLAN_BODY_ONE_PHASE_TMPL.format(slug="alpha"),
        )
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["init", "--project", str(self.project), "--plan", "alpha"])
        token = self._claim_phase("alpha", "a")
        rc, _ = self._complete("alpha", "a", token)
        self.assertEqual(rc, 0)
        events = st.load(self._state_path("alpha"))["events"]
        kinds = [e.get("type") for e in events]
        self.assertNotIn(st.EVENT_WORKTREE_CLEANED, kinds)
        self.assertNotIn(st.EVENT_WORKTREE_RETAINED_AHEAD, kinds)


class CmdArchiveTests(WorktreeCleanupBase):
    def test_happy_path_cleans_when_reachable(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL)
        self._set_status("alpha", st.STATUS_DONE)
        rc, stdout, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        self.assertIsNone(self._wt_record("alpha"))
        self.assertIn("removed", stdout)

    def test_retains_when_ahead(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL)
        self._add_ahead_commit("alpha")
        self._set_status("alpha", st.STATUS_HALTED)
        rc, stdout, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        self.assertIsNotNone(self._wt_record("alpha"))
        self.assertIn("retained", stdout)
        events = st.load(self._state_path("alpha"))["events"]
        kinds = [e.get("type") for e in events]
        self.assertIn(st.EVENT_WORKTREE_RETAINED_AHEAD, kinds)

    def test_refuses_running_status(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL)
        # Default after init is STATUS_RUNNING.
        rc, _stdout, stderr = self._archive("alpha")
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("running", stderr.lower())

    def test_idempotent_when_no_worktree(self) -> None:
        # Init without --worktree → state has no worktree record → archive
        # should be a clean no-op success.
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        self._set_status("alpha", st.STATUS_DONE)
        rc, stdout, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        self.assertIn("no worktree", stdout)

    def test_archive_then_archive_is_idempotent(self) -> None:
        # First archive removes; second archive sees None → "no worktree".
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL)
        self._set_status("alpha", st.STATUS_DONE)
        rc1, _stdout1, _ = self._archive("alpha")
        self.assertEqual(rc1, 0)
        rc2, stdout2, _ = self._archive("alpha")
        self.assertEqual(rc2, 0)
        self.assertIn("no worktree", stdout2)

    def test_refuses_unknown_plan(self) -> None:
        rc, _stdout, _stderr = self._archive("nonexistent")
        self.assertEqual(rc, ExitCode.UNKNOWN_TASK)


class CmdWorktreeGcUpstreamTests(WorktreeCleanupBase):
    def _gc(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "worktree",
                    "gc",
                    "--project",
                    str(self.project),
                    *extra,
                ]
            )
        return rc, out.getvalue(), err.getvalue()

    def test_gc_retains_branch_ahead_of_origin(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL)
        self._add_ahead_commit("alpha")
        self._set_status("alpha", st.STATUS_DONE)
        rc, _stdout, stderr = self._gc("--confirm")
        self.assertEqual(rc, 0)
        # Worktree is preserved on disk.
        self.assertIsNotNone(self._wt_record("alpha"))
        wt_path = Path(self._wt_record("alpha")["path"])
        self.assertTrue(wt_path.exists())
        self.assertIn("retained", stderr.lower())
        self.assertIn("ahead", stderr.lower())

    def test_gc_removes_when_reachable(self) -> None:
        # Plan with origin configured but branch == origin/main → reachable.
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL)
        self._set_status("alpha", st.STATUS_DONE)
        rc, stdout, _ = self._gc("--confirm")
        self.assertEqual(rc, 0)
        self.assertIn("removed", stdout)


class CmdArchivePlanMoveTests(WorktreeCleanupBase):
    """Plan-file git-mv step added to cmd_archive in #31."""

    def test_moves_plan_file(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        self._set_status("alpha", st.STATUS_DONE)
        rc, _, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        self.assertFalse((self.project / "plans" / "alpha.md").exists())
        self.assertTrue((self.project / "plans" / "archive" / "alpha" / "alpha.md").exists())

    def test_creates_archive_dir_if_missing(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        self._set_status("alpha", st.STATUS_DONE)
        self.assertFalse((self.project / "plans" / "archive" / "alpha").exists())
        rc, _, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        self.assertTrue((self.project / "plans" / "archive" / "alpha").is_dir())

    def test_idempotent_on_missing_plan_file(self) -> None:
        # File already moved/deleted from plans/ → skip silently, exit 0.
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        self._set_status("alpha", st.STATUS_DONE)
        (self.project / "plans" / "alpha.md").unlink()
        rc, _, _ = self._archive("alpha")
        self.assertEqual(rc, 0)

    def test_git_mv_failure_surfaces(self) -> None:
        # Plan file exists on disk but is NOT tracked by git → git mv fails.
        plan_md = self.project / "plans" / "alpha.md"
        plan_md.write_text(PLAN_BODY_ONE_PHASE_TMPL.format(slug="alpha"))
        # Intentionally NOT git-adding the file.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["init", "--project", str(self.project), "--plan", "alpha"])
        self._set_status("alpha", st.STATUS_DONE)
        rc, _, stderr = self._archive("alpha")
        self.assertNotEqual(rc, 0)
        self.assertTrue(
            "git" in stderr.lower() or "alpha.md" in stderr,
            f"expected git/filename mention in stderr; got: {stderr!r}",
        )

    def test_status_print_mentions_plan_move(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        self._set_status("alpha", st.STATUS_DONE)
        rc, stdout, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        self.assertIn("archive", stdout.lower())


class CmdArchiveAtomicCommitTests(WorktreeCleanupBase):
    """clu archive commits the plan-file move atomically — no
    leftover staged-uncommitted state for the operator to chase."""

    def _staged_or_modified(self) -> str:
        # Filter out untracked (??) lines — .orchestrator/ state files
        # are intentionally untracked and orthogonal to archive's
        # staged-rename footgun.
        lines = _git(self.project, "status", "--porcelain").stdout.splitlines()
        return "\n".join(line for line in lines if not line.startswith("??"))

    def _last_commit_subject(self) -> str:
        return _git(self.project, "log", "-1", "--format=%s").stdout.strip()

    def _commit_count(self) -> int:
        return int(_git(self.project, "rev-list", "--count", "HEAD").stdout.strip())

    def test_archive_commits_the_move(self) -> None:
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        self._set_status("alpha", st.STATUS_DONE)
        before = self._commit_count()
        rc, _, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        self.assertEqual(
            self._staged_or_modified(),
            "",
            "expected no staged/modified files after archive; got footgun state",
        )
        self.assertEqual(
            self._commit_count(),
            before + 1,
            "expected exactly one new commit for the archive move",
        )

    def test_archive_commit_message_is_operator_form(self) -> None:
        # Operator-driven archive says "chore: archive <slug>", NOT
        # "chore: auto-archive <slug>" (which is reserved for the
        # supervisor's auto_archive_rule path).
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        self._set_status("alpha", st.STATUS_DONE)
        rc, _, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        subject = self._last_commit_subject()
        self.assertEqual(subject, "chore: archive alpha")
        self.assertNotIn("auto-archive", subject)

    def test_archive_skips_commit_when_nothing_moved(self) -> None:
        # Plan file already gone from plans/ → no move → no extra commit.
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        self._set_status("alpha", st.STATUS_DONE)
        # Drop the plan file as if a prior archive partially ran.
        plan_md = self.project / "plans" / "alpha.md"
        _git(self.project, "rm", str(plan_md.relative_to(self.project)))
        _git(self.project, "commit", "-m", "drop alpha plan")
        before = self._commit_count()
        rc, _, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        self.assertEqual(
            self._commit_count(),
            before,
            "expected no extra commit when nothing moved",
        )

    def test_archive_then_archive_creates_only_one_commit(self) -> None:
        # Second archive is idempotent; should not stack a second move-commit.
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        self._set_status("alpha", st.STATUS_DONE)
        rc1, _, _ = self._archive("alpha")
        self.assertEqual(rc1, 0)
        after_first = self._commit_count()
        rc2, _, _ = self._archive("alpha")
        self.assertEqual(rc2, 0)
        self.assertEqual(self._commit_count(), after_first)

    def test_archive_clears_ship_pending_marker(self) -> None:
        # Operator-facing archive clears stale clu-ship markers so they
        # don't haunt the orphaned state file post-archive (clu-ship.md
        # phase 7 requirement).
        self._init_plan("alpha", PLAN_BODY_ONE_PHASE_TMPL, worktree=False)
        with st.mutate(self._state_path("alpha")) as data:
            data["status"] = st.STATUS_DONE
            data["ship_pending"] = {
                "mode": "as_pr",
                "pr_url": "https://github.com/example/repo/pull/1",
                "ts": "2026-05-23T12:00:00Z",
            }
            data["ready_to_ship_announced"] = {"branch_sha": "abc123"}
        rc, _, _ = self._archive("alpha")
        self.assertEqual(rc, 0)
        data = st.load(self._state_path("alpha"))
        self.assertNotIn("ship_pending", data)
        self.assertNotIn("ready_to_ship_announced", data)


if __name__ == "__main__":
    unittest.main()
