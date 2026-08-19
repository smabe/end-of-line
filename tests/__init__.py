"""Shared test helpers."""

from __future__ import annotations

import datetime as _dt
import json
import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import TypeVar
from unittest import mock

from end_of_line import state as st
from end_of_line.cli import main as cli_main
from end_of_line.config import CONFIG_FILENAME

_T = TypeVar("_T")

# The developer's actual home, read from the password database rather than
# `$HOME` so it stays correct no matter when this module is imported and no
# matter what the harness has already patched. Used only to prove a test never
# wrote there.
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()

# Where `real_home_canary` writes: the exact shape clu installs a skill into,
# so the guard exercises the path later phases actually use.
CANARY_RELPATH = Path(".claude") / "skills" / "clu-home-canary" / "SKILL.md"


def real_home_canary(testcase: unittest.TestCase) -> Path:
    """Write a canary skill under `Path.home()` and prove it missed the real home.

    The failure this guards is silent: a test that writes to the developer's
    real `~/.claude/skills/` leaves a green run behind. Fails `testcase` if
    `Path.home()` still points at `REAL_HOME`, or if the twin path under the
    real home exists after the write. Returns the path actually written.
    """
    home = Path.home().resolve()
    if home == REAL_HOME:
        testcase.fail(
            f"Path.home() is the developer's real home ({home}) inside a test — "
            f"HOME is not patched (see CluTestCase.setUp in tests/__init__.py)"
        )
    target = home / CANARY_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# clu test-harness canary — must never reach the real home\n")
    testcase.assertFalse(
        (REAL_HOME / CANARY_RELPATH).exists(),
        f"a test wrote into the developer's real home: {REAL_HOME / CANARY_RELPATH}",
    )
    return target


def must(x: _T | None) -> _T:
    """Narrow Optional in test assertions; AssertionError here means the
    fixture/regex produced nothing."""
    assert x is not None
    return x


def capture_inbox_writer(writes: list[dict]):
    """inbox_writer stub honoring the declared `(...) -> str` protocol
    (the real writer returns the event id); appends each call's kwargs."""

    def _writer(**kw) -> str:
        writes.append(kw)
        return "evt-test"

    return _writer


def utcnow_minus(seconds: int) -> str:
    """ISO8601 stamp N seconds before `datetime.now(UTC)`.

    Used to seed `current_claim.active_tool_started_at` and other
    "N seconds ago" timestamps in tests without freezing the clock.
    """
    return (_dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def plan_body(*sessions: str) -> str:
    """Build a PLAN_BODY markdown string with the given session ids."""
    rows = "\n".join(f"| {s} | `test-plan-{s.lower()}.md` | thing | 1h |" for s in sessions)
    return (
        "# Test plan\n\n"
        "## Sessions index\n\n"
        "| Session | Plan file | Scope | Effort |\n"
        "|---|---|---|---|\n"
        f"{rows}\n"
    )


DEFAULT_PLAN_BODY = plan_body("a", "b")


def write_config(
    project: Path,
    *,
    test_command: str | None = None,
    quality: dict | None = None,
) -> None:
    """Write `.orchestrator.json` with `dispatch.command='echo hi'` and optional
    `test_command` / `quality` blocks. Used by tests that exercise cli paths
    needing a parsed config."""
    cfg: dict = {"dispatch": {"command": "echo hi"}}
    if test_command is not None:
        cfg["test_command"] = test_command
    if quality:
        cfg["quality"] = quality
    (project / CONFIG_FILENAME).write_text(json.dumps(cfg))


class CluTestCase(unittest.TestCase):
    """unittest base that isolates XDG paths and sets CLU_TEST_MODE=1.

    Subclasses MUST call `super().setUp()` BEFORE any registry/inbox-
    touching code. Pairs with the phase-2 XDG guard, which refuses
    writes to real ~/.config/clu/ under CLU_TEST_MODE=1.
    """

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = Path(tmp.name)
        (self.tmp_path / "home").mkdir(exist_ok=True)
        isolate_registry(self, self.tmp_path, home=self.tmp_path / "home")
        patcher = mock.patch.dict(
            os.environ,
            {
                "CLU_TEST_MODE": "1",
                # A SIBLING of XDG_CONFIG_HOME (which isolate_registry points at
                # tmp_path itself), never tmp_path — HOME == the XDG dir makes
                # clu_config_dir() a child of Path.home(), and assert_xdg_safe
                # then raises on every registry / inbox / monitor / notify /
                # worker-settings write under CLU_TEST_MODE=1.
                "HOME": str(self.tmp_path / "home"),
                # Empty CLU_COOLANT_SCRIPT_DIR + redirected COOLANT_* keep tests
                # off any real coolant install on the dev machine. Tests that
                # exercise coolant resolution override these explicitly.
                "CLU_COOLANT_SCRIPT_DIR": "",
                "COOLANT_COUNTER": str(self.tmp_path / "coolant.count"),
                "COOLANT_EVENTS": str(self.tmp_path / "coolant.events.jsonl"),
                "COOLANT_LOG": str(self.tmp_path / "coolant.log"),
                "COOLANT_LOCKFILE": str(self.tmp_path / "coolant.lock"),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)


def isolate_registry(
    testcase: unittest.TestCase, tmp_path: Path, home: Path | None = None
) -> None:
    """Point clu's XDG-based registry AND `HOME` at per-test temp dirs.

    Without the XDG patch, `cmd_init` writes to the user's real
    `~/.config/clu/registry.json` during tests. `HOME` is patched for the
    same reason one level up: clu also reads and writes
    `~/.claude/skills/<name>/SKILL.md`, and the ~30 test classes that call
    this helper instead of subclassing `CluTestCase` would otherwise reach
    the developer's real home. Call from setUp after creating tmp_path;
    both patches auto-restore via addCleanup.

    `HOME` defaults to its OWN temp dir rather than a subdirectory of `tmp_path`,
    because callers routinely pass a git project root here (e.g.
    tests/test_verify_opt_out.py, tests/test_worktree_cleanup.py) and a
    home directory inside a worktree would show up in the `git status`
    that clu's own quality gates read. `CluTestCase` can use
    `tmp_path / "home"` safely — its project lives in a sibling subdir.
    """
    if home is None:
        # No home supplied: allocate one. Callers that already have a home to
        # use pass it, so `CluTestCase` does not pay for a directory it is
        # about to override one line later.
        holder = tempfile.TemporaryDirectory()
        testcase.addCleanup(holder.cleanup)
        home = Path(holder.name)
    patcher = mock.patch.dict(
        os.environ,
        {"XDG_CONFIG_HOME": str(tmp_path), "HOME": str(home)},
    )
    patcher.start()
    testcase.addCleanup(patcher.stop)


def isolate_queue(testcase: unittest.TestCase, tmp_path: Path) -> None:
    """Isolate registry + queue file paths for a queue test.

    queue.json lives under each project's `.orchestrator/` — so per-test
    isolation falls out naturally as long as the project root is itself
    tmp-scoped. The only shared sink that needs patching is the host
    registry, since `clu queue add`'s bootstrap check reads it.
    """
    isolate_registry(testcase, tmp_path)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command inside `repo`. Use in tests that need real git repos."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def make_git_project(base: Path, *, subdir: str = "myrepo") -> Path:
    """Create a minimal git project with a `plans/` dir and one initial commit.

    Returns the project root. The caller's temp dir owns cleanup.
    """
    project = base / subdir
    project.mkdir()
    (project / "plans").mkdir()
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.email", "t@test.invalid")
    git(project, "config", "user.name", "Test User")
    (project / "README").write_text("init\n")
    git(project, "add", "README")
    git(project, "commit", "-m", "init")
    return project


def make_worktree(
    project: Path,
    *,
    branch: str = "clu/p",
) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
    """Create a linked git worktree with one empty commit on a new branch.

    Returns (wt_tmp, wt_path, wt_sha). Caller must call wt_tmp.cleanup().
    """
    wt_tmp = tempfile.TemporaryDirectory()
    wt_path = Path(wt_tmp.name) / "wt"
    git(project, "worktree", "add", "-b", branch, str(wt_path))
    git(wt_path, "commit", "--allow-empty", "-m", "W")
    wt_sha = git(wt_path, "rev-parse", "HEAD").stdout.strip()
    return wt_tmp, wt_path, wt_sha


class GitProjectTestCase(CluTestCase):
    """Per-test temp project + initialized clu plan, ready to claim.

    Set `NEEDS_GIT = False` to skip the `make_git_project` shell-out for
    tests that inject state directly. Override `PLAN_BODY` at the class
    level for a non-default session id.

    After setUp: `self.project`, `self.sha` (empty if no git),
    `self.state_path`, plus `_argv`, `_claim`, `_read` helpers.
    """

    PLAN_BODY: str = DEFAULT_PLAN_BODY
    NEEDS_GIT: bool = True

    def setUp(self) -> None:
        super().setUp()
        if self.NEEDS_GIT:
            self.project = make_git_project(self.tmp_path)
            self.sha = git(self.project, "rev-parse", "HEAD").stdout.strip()
        else:
            self.project = self.tmp_path / "myrepo"
            self.project.mkdir()
            (self.project / "plans").mkdir()
            self.sha = ""
        (self.project / "plans" / "test-plan.md").write_text(self.PLAN_BODY)
        self.state_path = self.project / "plans" / ".orchestrator" / "test-plan.state.json"
        rc = cli_main(["init", "--project", str(self.project), "--plan", "test-plan"])
        self.assertEqual(rc, 0)

    def _argv(self, cmd: str, *extra: str) -> list[str]:
        return [
            cmd,
            "--project",
            str(self.project),
            "--plan",
            "test-plan",
            *extra,
        ]

    def _claim(self, phase: str = "a") -> str:
        with st.mutate(self.state_path) as data:
            return st.claim_phase(data, phase, lease_minutes=30)

    def _read(self) -> dict:
        return json.loads(self.state_path.read_text())


def isolate_monitor_marker(testcase: unittest.TestCase, tmp_path: Path) -> None:
    """Point clu's monitor marker file at a per-test XDG dir.

    `monitor.marker_path()` resolves through `XDG_CONFIG_HOME`, so this
    is the same monkeypatch as `isolate_registry`. Named separately so a
    monitor test that doesn't touch registry doesn't read as registry-coupled.
    """
    isolate_registry(testcase, tmp_path)
