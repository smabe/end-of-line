"""Tests for `clu ship` — single-action post-worker integration.

Phase 3 of clu-ship.md: `clu ship --plan X --direct` validates,
previews, requires --yes, merges to main (FF-first, merge-commit
fallback), pushes origin main + the branch, triggers an immediate
tick so auto_archive_rule fires without waiting for cron.

Later phases extend with --all-done (phase 4), --as-pr (phase 5-6),
and ship_mode config default (phase 7).
"""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from end_of_line import state as st
from end_of_line.cli import ExitCode, main
from tests import isolate_registry


PLAN_BODY = """\
# T

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| A | `{slug}-a.md` | thing | 1h |
"""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check,
    )


class ShipBase(unittest.TestCase):
    """Real git project + origin remote + a DONE plan on a worker branch."""

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
        self.origin = self.parent / "origin.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", str(self.origin)],
            check=True, capture_output=True,
        )
        _git(self.project, "remote", "add", "origin", str(self.origin))
        _git(self.project, "push", "-u", "origin", "main")
        _git(self.project, "remote", "set-head", "origin", "main")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state_path(self, slug: str) -> Path:
        return self.project / "plans" / ".orchestrator" / f"{slug}.state.json"

    def _init_plan(self, slug: str = "alpha") -> Path:
        plan_md = self.project / "plans" / f"{slug}.md"
        plan_md.write_text(PLAN_BODY.format(slug=slug))
        _git(self.project, "add", f"plans/{slug}.md")
        _git(self.project, "commit", "-m", f"add {slug} plan")
        _git(self.project, "push", "origin", "main")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main([
                "init", "--project", str(self.project),
                "--plan", slug, "--worktree",
            ])
        return self._state_path(slug)

    def _set_done(self, slug: str) -> None:
        with st.mutate(self._state_path(slug)) as data:
            data["status"] = st.STATUS_DONE

    def _add_worker_commit(self, slug: str, msg: str = "worker work") -> str:
        data = st.load(self._state_path(slug))
        wt = st.get_worktree(data)
        assert wt is not None
        wt_path = Path(wt["path"])
        # Make a real change so the worker branch diverges from main.
        (wt_path / f"{slug}-work.txt").write_text("worker output\n")
        _git(wt_path, "add", f"{slug}-work.txt")
        _git(wt_path, "commit", "-m", msg)
        return _git(wt_path, "rev-parse", "HEAD").stdout.strip()

    def _branch(self, slug: str) -> str:
        return st.get_worktree(st.load(self._state_path(slug)))["branch"]

    def _ship(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["ship", "--project", str(self.project), *args])
        return rc, out.getvalue(), err.getvalue()


class ShipRefusalTests(ShipBase):
    """`clu ship --plan X --direct` refuses with a clear message when
    the project state isn't ready for a ship."""

    def test_refuses_not_done(self) -> None:
        # Init leaves status RUNNING; ship requires DONE.
        self._init_plan("alpha")
        rc, _, err = self._ship("--plan", "alpha", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("done", err.lower())

    def test_refuses_no_worktree(self) -> None:
        plan_md = self.project / "plans" / "alpha.md"
        plan_md.write_text(PLAN_BODY.format(slug="alpha"))
        _git(self.project, "add", "plans/alpha.md")
        _git(self.project, "commit", "-m", "add alpha plan")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["init", "--project", str(self.project), "--plan", "alpha"])
        self._set_done("alpha")
        rc, _, err = self._ship("--plan", "alpha", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("worktree", err.lower())

    def test_refuses_already_merged(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        # Pre-merge into main + push so the branch is already integrated.
        branch = self._branch("alpha")
        _git(self.project, "merge", "--no-ff", "--no-edit", branch)
        _git(self.project, "push", "origin", "main")
        self._set_done("alpha")
        rc, _, err = self._ship("--plan", "alpha", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("already merged", err.lower())

    def test_refuses_canonical_dirty(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        # Leave a staged change on canonical to simulate dirty operator work.
        (self.project / "extra.txt").write_text("oops\n")
        _git(self.project, "add", "extra.txt")
        rc, _, err = self._ship("--plan", "alpha", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("uncommitted", err.lower())

    def test_refuses_validate_fail(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        # Inject a conflicting change on main so dry-merge produces
        # textual_conflict.
        (self.project / "alpha-work.txt").write_text("conflicting main change\n")
        _git(self.project, "add", "alpha-work.txt")
        _git(self.project, "commit", "-m", "main writes the same file")
        _git(self.project, "push", "origin", "main")
        rc, out, _ = self._ship("--plan", "alpha", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("conflict", out.lower())


class ShipPreviewAndCheckTests(ShipBase):
    """--check validates only; default (no --yes) shows a preview."""

    def test_check_validates_and_exits_ok(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        rc, out, _ = self._ship("--plan", "alpha", "--direct", "--check")
        self.assertEqual(rc, ExitCode.OK)
        # No merge happened — main HEAD didn't move.
        main_head = _git(self.project, "rev-parse", "main").stdout.strip()
        origin_head = _git(self.project, "rev-parse", "origin/main").stdout.strip()
        self.assertEqual(main_head, origin_head)
        self.assertIn("ready", out.lower())

    def test_preview_without_yes_does_not_merge(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        rc, out, _ = self._ship("--plan", "alpha", "--direct")
        self.assertEqual(rc, ExitCode.OK)
        main_head = _git(self.project, "rev-parse", "main").stdout.strip()
        origin_head = _git(self.project, "rev-parse", "origin/main").stdout.strip()
        self.assertEqual(main_head, origin_head)
        self.assertIn("--yes", out)


class ShipHappyPathTests(ShipBase):
    """`--plan X --direct --yes` actually lands code on main."""

    def test_happy_path_ff_merge(self) -> None:
        self._init_plan("alpha")
        worker_sha = self._add_worker_commit("alpha")
        self._set_done("alpha")
        with mock.patch("end_of_line.cli._spawn_post_action_tick") as spawn:
            rc, _, _ = self._ship("--plan", "alpha", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        # main advanced to the worker SHA via FF (no merge commit).
        main_head = _git(self.project, "rev-parse", "main").stdout.strip()
        self.assertEqual(main_head, worker_sha)
        # Origin advanced too.
        origin_head = _git(self.project, "rev-parse", "origin/main").stdout.strip()
        self.assertEqual(origin_head, worker_sha)
        spawn.assert_called_once()

    def test_happy_path_merge_commit_fallback(self) -> None:
        self._init_plan("alpha")
        worker_sha = self._add_worker_commit("alpha")
        self._set_done("alpha")
        # Diverge main so FF isn't possible — add a commit to main that
        # ISN'T a conflict (different file).
        (self.project / "main-side.txt").write_text("main diverges\n")
        _git(self.project, "add", "main-side.txt")
        _git(self.project, "commit", "-m", "main diverges")
        _git(self.project, "push", "origin", "main")
        with mock.patch("end_of_line.cli._spawn_post_action_tick"):
            rc, _, _ = self._ship("--plan", "alpha", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        # main is now a merge commit — not equal to worker SHA but
        # reachable from it.
        main_head = _git(self.project, "rev-parse", "main").stdout.strip()
        self.assertNotEqual(main_head, worker_sha)
        # The merge-base of main and worker SHA is worker SHA itself
        # (worker is an ancestor of main).
        r = _git(
            self.project, "merge-base", "--is-ancestor", worker_sha, main_head,
            check=False,
        )
        self.assertEqual(r.returncode, 0)

    def test_happy_path_triggers_post_action_tick(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        with mock.patch("end_of_line.cli._spawn_post_action_tick") as spawn:
            rc, _, _ = self._ship("--plan", "alpha", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        spawn.assert_called_once()

    def test_branch_push_failure_is_warning_not_fatal(self) -> None:
        # If origin/<branch> push fails (third-party deletes the branch,
        # etc.), the ship still succeeds — main has the work.
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        branch = self._branch("alpha")
        real_run = subprocess.run

        def fake_run(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args", [])
            # Symlink-resilient match: cli resolves the project path via
            # .resolve() so str(self.project) might not equal what argv
            # carries on macOS (/var → /private/var). Match on the verb
            # + branch instead.
            if (
                isinstance(argv, list)
                and "push" in argv and "origin" in argv and branch in argv
            ):
                return subprocess.CompletedProcess(
                    argv, 1, stdout="", stderr="error: branch deleted upstream\n",
                )
            return real_run(*args, **kwargs)

        with mock.patch("end_of_line.cli.subprocess.run", side_effect=fake_run):
            with mock.patch("end_of_line.cli._spawn_post_action_tick"):
                rc, _, err = self._ship("--plan", "alpha", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("warning", err.lower())


class ShipAsPrPlanTests(ShipBase):
    """`clu ship --as-pr --plan X` opens a GitHub PR instead of
    merging directly. The supervisor's auto_archive_rule picks up
    the cleanup once GitHub merges the PR and the operator (or a
    fetch) bumps local origin/main."""

    def _gh_fake(
        self, *,
        version_ok: bool = True,
        auth_ok: bool = True,
        pr_create_succeeds: bool = True,
        pr_create_already_open: bool = False,
        existing_pr_url: str = "https://github.com/example/repo/pull/42",
    ):
        """Build a subprocess.run fake that intercepts gh calls and
        passes git calls through. Returns the fake fn."""
        real_run = subprocess.run

        def fake(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args", [])
            if not isinstance(argv, list):
                return real_run(*args, **kwargs)
            if argv[:2] == ["gh", "--version"]:
                if version_ok:
                    return subprocess.CompletedProcess(argv, 0, "gh 2.0.0\n", "")
                raise FileNotFoundError("gh")
            if argv[:3] == ["gh", "auth", "status"]:
                return subprocess.CompletedProcess(
                    argv,
                    0 if auth_ok else 1,
                    "Logged in\n" if auth_ok else "",
                    "" if auth_ok else "not authenticated\n",
                )
            if argv[:3] == ["gh", "pr", "create"]:
                if pr_create_already_open:
                    return subprocess.CompletedProcess(
                        argv, 1, "",
                        "a pull request for branch already exists\n",
                    )
                if pr_create_succeeds:
                    return subprocess.CompletedProcess(
                        argv, 0,
                        f"{existing_pr_url}\n", "",
                    )
                return subprocess.CompletedProcess(
                    argv, 1, "", "gh: unexpected error\n",
                )
            if argv[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(
                    argv, 0, f'{{"url":"{existing_pr_url}"}}\n', "",
                )
            return real_run(*args, **kwargs)

        return fake

    def test_refuses_not_done(self) -> None:
        self._init_plan("alpha")
        rc, _, err = self._ship("--plan", "alpha", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.STATUS_TRANSITION)
        self.assertIn("done", err.lower())

    def test_refuses_already_merged(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        branch = self._branch("alpha")
        _git(self.project, "merge", "--no-ff", "--no-edit", branch)
        _git(self.project, "push", "origin", "main")
        self._set_done("alpha")
        rc, _, err = self._ship("--plan", "alpha", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("already merged", err.lower())

    def test_check_validates_no_pr_opened(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        with mock.patch(
            "end_of_line.cli.subprocess.run",
            side_effect=self._gh_fake(),
        ):
            rc, out, _ = self._ship("--plan", "alpha", "--as-pr", "--check")
        self.assertEqual(rc, ExitCode.OK)
        # No PR was created — gh pr create wasn't called.
        self.assertNotIn("pull/", out)

    def test_preview_without_yes_does_not_open_pr(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        rc, out, _ = self._ship("--plan", "alpha", "--as-pr")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("--yes", out)
        self.assertIn("pr", out.lower())
        # State has no ship_pending stamp.
        data = st.load(self._state_path("alpha"))
        self.assertNotIn("ship_pending", data)

    def test_happy_path_creates_pr_and_stamps_state(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        with mock.patch(
            "end_of_line.cli.subprocess.run",
            side_effect=self._gh_fake(),
        ):
            rc, out, _ = self._ship("--plan", "alpha", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("pull/42", out)
        # ship_pending stamped with mode=as_pr + URL + timestamp.
        data = st.load(self._state_path("alpha"))
        pending = data.get("ship_pending")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["mode"], "as_pr")
        self.assertIn("pull/42", pending["pr_url"])
        self.assertIn("ts", pending)

    def test_gh_not_installed(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        with mock.patch(
            "end_of_line.cli.subprocess.run",
            side_effect=self._gh_fake(version_ok=False),
        ):
            rc, _, err = self._ship("--plan", "alpha", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("gh", err.lower())

    def test_gh_not_authenticated(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        with mock.patch(
            "end_of_line.cli.subprocess.run",
            side_effect=self._gh_fake(auth_ok=False),
        ):
            rc, _, err = self._ship("--plan", "alpha", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("auth", err.lower())

    def test_pr_already_open_is_idempotent(self) -> None:
        # gh pr create fails with "already exists" → we look up the
        # existing URL via gh pr view and return success.
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        with mock.patch(
            "end_of_line.cli.subprocess.run",
            side_effect=self._gh_fake(pr_create_already_open=True),
        ):
            rc, out, _ = self._ship("--plan", "alpha", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("pull/42", out)
        # Still stamps the state with the existing URL.
        data = st.load(self._state_path("alpha"))
        self.assertEqual(data["ship_pending"]["mode"], "as_pr")


class ShipModeResolutionTests(ShipBase):
    """`cmd_ship` resolves mode from .orchestrator.json
    `dispatch.ship_mode` when no --direct/--as-pr flag is passed."""

    def _write_config(self, mode: str) -> None:
        (self.project / ".orchestrator.json").write_text(
            f'{{"dispatch":{{"ship_mode":"{mode}"}}}}'
        )

    def test_no_flag_no_config_defaults_to_direct(self) -> None:
        # Config absent → DispatchSpec default "direct" → direct
        # path runs validate/preview without opening any PR.
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        rc, out, _ = self._ship("--plan", "alpha", "--check")
        self.assertEqual(rc, ExitCode.OK)
        # Direct's --check body says "ready to ship", PR mode says
        # "ready to open PR".
        self.assertIn("ready to ship", out.lower())
        self.assertNotIn("open pr", out.lower())

    def test_config_as_pr_routes_to_pr_mode(self) -> None:
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        self._write_config("as_pr")
        rc, out, _ = self._ship("--plan", "alpha", "--check")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("open pr", out.lower())

    def test_explicit_flag_overrides_config(self) -> None:
        # Config says as_pr; --direct flag overrides.
        self._init_plan("alpha")
        self._add_worker_commit("alpha")
        self._set_done("alpha")
        self._write_config("as_pr")
        rc, out, _ = self._ship("--plan", "alpha", "--direct", "--check")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("ready to ship", out.lower())
        self.assertNotIn("open pr", out.lower())


class ShipAsPrAllDoneTests(ShipBase):
    """`clu ship --as-pr --all-done` opens PRs for every DONE plan
    with an unmerged worktree branch, behind one --yes."""

    def _setup_done_plan(self, slug: str) -> None:
        self._init_plan(slug)
        self._add_worker_commit(slug)
        self._set_done(slug)

    def _gh_fake_batch(
        self, *,
        version_ok: bool = True,
        auth_ok: bool = True,
        fail_for: set[str] | None = None,
    ):
        """gh fake that returns a unique URL per branch and can
        selectively fail PR-create for specific branch names."""
        real_run = subprocess.run
        fail_for = fail_for or set()

        def fake(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args", [])
            if not isinstance(argv, list):
                return real_run(*args, **kwargs)
            if argv[:2] == ["gh", "--version"]:
                if version_ok:
                    return subprocess.CompletedProcess(argv, 0, "gh 2.0.0\n", "")
                raise FileNotFoundError("gh")
            if argv[:3] == ["gh", "auth", "status"]:
                return subprocess.CompletedProcess(
                    argv,
                    0 if auth_ok else 1,
                    "ok" if auth_ok else "",
                    "" if auth_ok else "not authenticated\n",
                )
            if argv[:3] == ["gh", "pr", "create"]:
                # Extract --head value for per-branch URL/failure.
                head = ""
                for i, a in enumerate(argv):
                    if a == "--head" and i + 1 < len(argv):
                        head = argv[i + 1]
                        break
                if head in fail_for:
                    return subprocess.CompletedProcess(
                        argv, 1, "", "gh: simulated failure\n",
                    )
                slug = head.rsplit("/", 1)[-1] if "/" in head else head
                return subprocess.CompletedProcess(
                    argv, 0,
                    f"https://github.com/example/repo/pull/{slug}\n", "",
                )
            return real_run(*args, **kwargs)

        return fake

    def test_no_eligible_plans_returns_ok(self) -> None:
        rc, out, _ = self._ship("--all-done", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("nothing to ship", out.lower())

    def test_skips_already_merged_plans(self) -> None:
        self._setup_done_plan("alpha")
        branch = self._branch("alpha")
        _git(self.project, "merge", "--no-ff", "--no-edit", branch)
        _git(self.project, "push", "origin", "main")
        rc, out, _ = self._ship("--all-done", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("nothing to ship", out.lower())

    def test_preview_lists_eligible_plans(self) -> None:
        self._setup_done_plan("alpha")
        self._setup_done_plan("beta")
        rc, out, _ = self._ship("--all-done", "--as-pr")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        self.assertIn("--yes", out)
        # No PR stamps.
        for slug in ("alpha", "beta"):
            data = st.load(self._state_path(slug))
            self.assertNotIn("ship_pending", data)

    def test_check_validates_each_eligible_plan(self) -> None:
        self._setup_done_plan("alpha")
        self._setup_done_plan("beta")
        with mock.patch(
            "end_of_line.cli.subprocess.run",
            side_effect=self._gh_fake_batch(),
        ):
            rc, out, _ = self._ship("--all-done", "--as-pr", "--check")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        # No PR stamps under --check.
        for slug in ("alpha", "beta"):
            data = st.load(self._state_path(slug))
            self.assertNotIn("ship_pending", data)

    def test_ships_multiple_plans_opens_a_pr_each(self) -> None:
        self._setup_done_plan("alpha")
        self._setup_done_plan("beta")
        with mock.patch(
            "end_of_line.cli.subprocess.run",
            side_effect=self._gh_fake_batch(),
        ):
            rc, out, _ = self._ship("--all-done", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        for slug in ("alpha", "beta"):
            data = st.load(self._state_path(slug))
            pending = data.get("ship_pending")
            self.assertIsNotNone(pending, f"{slug} should be stamped")
            self.assertEqual(pending["mode"], "as_pr")
            self.assertIn(slug, pending["pr_url"])

    def test_continues_past_per_plan_failure(self) -> None:
        self._setup_done_plan("alpha")
        self._setup_done_plan("beta")
        alpha_branch = self._branch("alpha")
        with mock.patch(
            "end_of_line.cli.subprocess.run",
            side_effect=self._gh_fake_batch(fail_for={alpha_branch}),
        ):
            rc, _, err = self._ship("--all-done", "--as-pr", "--yes")
        # Overall rc reflects failure; beta still got its PR.
        self.assertNotEqual(rc, ExitCode.OK)
        self.assertIn("alpha", err.lower())
        beta_data = st.load(self._state_path("beta"))
        self.assertEqual(beta_data["ship_pending"]["mode"], "as_pr")
        alpha_data = st.load(self._state_path("alpha"))
        self.assertNotIn("ship_pending", alpha_data)

    def test_gh_preflight_failure_halts_before_apply(self) -> None:
        self._setup_done_plan("alpha")
        self._setup_done_plan("beta")
        with mock.patch(
            "end_of_line.cli.subprocess.run",
            side_effect=self._gh_fake_batch(version_ok=False),
        ):
            rc, _, err = self._ship("--all-done", "--as-pr", "--yes")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("gh", err.lower())
        # No PR was opened — both plans untouched.
        for slug in ("alpha", "beta"):
            data = st.load(self._state_path(slug))
            self.assertNotIn("ship_pending", data)


class ShipAllDoneTests(ShipBase):
    """`clu ship --all-done --direct` ships every DONE plan with an
    unmerged worktree branch, behind one --yes."""

    def _setup_done_plan(self, slug: str) -> None:
        self._init_plan(slug)
        self._add_worker_commit(slug)
        self._set_done(slug)

    def test_no_eligible_plans_returns_ok(self) -> None:
        # No DONE plans at all.
        rc, out, _ = self._ship("--all-done", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("nothing to ship", out.lower())

    def test_skips_already_merged_plans(self) -> None:
        # Plan that's DONE but already merged into origin/main should
        # NOT be in the eligible set (auto_archive_rule owns it).
        self._setup_done_plan("alpha")
        branch = self._branch("alpha")
        _git(self.project, "merge", "--no-ff", "--no-edit", branch)
        _git(self.project, "push", "origin", "main")
        rc, out, _ = self._ship("--all-done", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("nothing to ship", out.lower())

    def test_preview_lists_eligible_plans(self) -> None:
        self._setup_done_plan("alpha")
        self._setup_done_plan("beta")
        rc, out, _ = self._ship("--all-done", "--direct")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        self.assertIn("--yes", out)
        # No merge happened — main HEAD unchanged.
        main_head = _git(self.project, "rev-parse", "main").stdout.strip()
        origin_head = _git(self.project, "rev-parse", "origin/main").stdout.strip()
        self.assertEqual(main_head, origin_head)

    def test_ships_multiple_plans(self) -> None:
        self._setup_done_plan("alpha")
        self._setup_done_plan("beta")
        alpha_branch = self._branch("alpha")
        beta_branch = self._branch("beta")
        with mock.patch("end_of_line.cli._spawn_post_action_tick") as spawn:
            rc, _, _ = self._ship("--all-done", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.OK)
        # Both branches are ancestors of main.
        for b in (alpha_branch, beta_branch):
            r = _git(
                self.project, "merge-base", "--is-ancestor", b, "main",
                check=False,
            )
            self.assertEqual(r.returncode, 0, f"branch {b} not in main")
        # Tick triggered exactly once (post-batch), not per-plan.
        spawn.assert_called_once()

    def test_check_validates_each_eligible_plan(self) -> None:
        self._setup_done_plan("alpha")
        self._setup_done_plan("beta")
        rc, out, _ = self._ship("--all-done", "--direct", "--check")
        self.assertEqual(rc, ExitCode.OK)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        # No merge happened.
        main_head = _git(self.project, "rev-parse", "main").stdout.strip()
        origin_head = _git(self.project, "rev-parse", "origin/main").stdout.strip()
        self.assertEqual(main_head, origin_head)

    def test_continues_past_per_plan_failure(self) -> None:
        # alpha will fail (conflicting change on main); beta should
        # still ship.
        self._setup_done_plan("alpha")
        self._setup_done_plan("beta")
        # Inject conflict for alpha by writing alpha-work.txt on main.
        (self.project / "alpha-work.txt").write_text("conflict\n")
        _git(self.project, "add", "alpha-work.txt")
        _git(self.project, "commit", "-m", "main writes alpha file")
        _git(self.project, "push", "origin", "main")
        with mock.patch("end_of_line.cli._spawn_post_action_tick"):
            rc, out, err = self._ship("--all-done", "--direct", "--yes")
        # rc reflects overall failure (alpha failed) but beta still
        # shipped.
        self.assertNotEqual(rc, ExitCode.OK)
        beta_branch = self._branch("beta")
        r = _git(
            self.project, "merge-base", "--is-ancestor", beta_branch, "main",
            check=False,
        )
        self.assertEqual(r.returncode, 0, "beta should have shipped despite alpha failure")
        combined = (out + err).lower()
        self.assertIn("alpha", combined)
        self.assertIn("beta", combined)

    def test_canonical_dirty_refuses_before_starting(self) -> None:
        self._setup_done_plan("alpha")
        (self.project / "extra.txt").write_text("oops\n")
        _git(self.project, "add", "extra.txt")
        rc, _, err = self._ship("--all-done", "--direct", "--yes")
        self.assertEqual(rc, ExitCode.GENERIC)
        self.assertIn("uncommitted", err.lower())
        # Nothing shipped.
        alpha_branch = self._branch("alpha")
        r = _git(
            self.project, "merge-base", "--is-ancestor", alpha_branch, "main",
            check=False,
        )
        self.assertNotEqual(r.returncode, 0)


class ShipDefaultsProjectToCwdTests(ShipBase):
    """clu ship --plan X accepts being invoked without --project from the project CWD."""

    def test_ship_defaults_project_to_cwd(self) -> None:
        old_cwd = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, str(old_cwd))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["ship", "--plan", "alpha", "--direct"])
        combined_err = err.getvalue()
        self.assertNotIn("required", combined_err.lower())
        self.assertNotIn("AttributeError", combined_err)


if __name__ == "__main__":
    unittest.main()
