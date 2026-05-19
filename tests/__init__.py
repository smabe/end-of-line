"""Shared test helpers."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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
        isolate_registry(self, self.tmp_path)
        patcher = mock.patch.dict(os.environ, {
            "CLU_TEST_MODE": "1",
            # Empty CLU_COOLANT_SCRIPT_DIR + redirected COOLANT_* keep tests
            # off any real coolant install on the dev machine. Tests that
            # exercise coolant resolution override these explicitly.
            "CLU_COOLANT_SCRIPT_DIR": "",
            "COOLANT_COUNTER": str(self.tmp_path / "coolant.count"),
            "COOLANT_EVENTS": str(self.tmp_path / "coolant.events.jsonl"),
            "COOLANT_LOG": str(self.tmp_path / "coolant.log"),
            "COOLANT_LOCKFILE": str(self.tmp_path / "coolant.lock"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)


def isolate_registry(testcase: unittest.TestCase, tmp_path: Path) -> None:
    """Point clu's XDG-based registry at a per-test temp dir.

    Without this, `cmd_init` writes to the user's real
    `~/.config/clu/registry.json` during tests. Call from setUp after
    creating tmp_path; the patch auto-restores via addCleanup.
    """
    patcher = mock.patch.dict(
        os.environ, {"XDG_CONFIG_HOME": str(tmp_path)},
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
        capture_output=True, text=True, check=check,
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
    project: Path, *, branch: str = "clu/p",
) -> tuple["tempfile.TemporaryDirectory[str]", Path, str]:
    """Create a linked git worktree with one empty commit on a new branch.

    Returns (wt_tmp, wt_path, wt_sha). Caller must call wt_tmp.cleanup().
    """
    wt_tmp = tempfile.TemporaryDirectory()
    wt_path = Path(wt_tmp.name) / "wt"
    git(project, "worktree", "add", "-b", branch, str(wt_path))
    git(wt_path, "commit", "--allow-empty", "-m", "W")
    wt_sha = git(wt_path, "rev-parse", "HEAD").stdout.strip()
    return wt_tmp, wt_path, wt_sha


def isolate_monitor_marker(testcase: unittest.TestCase, tmp_path: Path) -> None:
    """Point clu's monitor marker file at a per-test XDG dir.

    `monitor.marker_path()` resolves through `XDG_CONFIG_HOME`, so this
    is the same monkeypatch as `isolate_registry`. Named separately so a
    monitor test that doesn't touch registry doesn't read as registry-coupled.
    """
    isolate_registry(testcase, tmp_path)
