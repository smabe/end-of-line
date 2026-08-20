"""Shared test helpers."""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import pwd
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar
from unittest import mock

from end_of_line import db, plan_store
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
    """Isolate registry + queue paths for a queue test.

    The queue lives in each project's `.orchestrator/clu.db` — so per-test
    isolation falls out naturally as long as the project root is itself
    tmp-scoped. The only shared sink that needs patching is the host
    registry, since `clu queue add`'s bootstrap check reads it.
    """
    isolate_registry(testcase, tmp_path)


QUOTA_PAUSE_DEFAULTS: dict = {
    "paused_until": "2026-06-12T05:52:00Z",
    "signature": "session_limit",
    "line": "You've hit your session limit · resets 1:50am (America/New_York)",
    "canary_plan": None,
    "canary_deadline": None,
    "created_at": "2026-06-12T03:00:00Z",
}


def seed_quota_pause(orch_dir: Path, **over) -> None:
    """Write the project's quota pause row — the fixture that wrote a file.

    Goes through the store's own column tuple so a schema change breaks the
    seeder loudly rather than seeding a row the gate cannot read.
    """
    from end_of_line import db as _db
    from end_of_line import quota as _quota

    row = {**QUOTA_PAUSE_DEFAULTS, **over}
    with _db.project_conn(orch_dir) as conn, _db.write_txn(conn) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO quota (id, paused_until, signature, line, "
            "canary_plan, canary_deadline, created_at) VALUES (1, ?, ?, ?, ?, ?, ?)",
            tuple(row[c] for c in _quota._QUOTA_COLUMNS),
        )


def stamp_future_schema(db_path: Path) -> None:
    """Make a clu database read as written by a NEWER clu than this one.

    The replacement for tests that wrote garbage into a JSON store to prove
    the tolerant-read path. A transaction cannot leave a half-written
    database, so the reachable "cannot read this store" case is a schema
    from the future, which upstream decision #6 says to skip rather than
    interpret. Creates the file if it is not there yet — an empty database
    at a future `user_version` refuses exactly the same way.
    """
    import sqlite3

    from end_of_line import db as _db

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        conn.execute(f"PRAGMA user_version = {_db.PROJECT_SCHEMA_VERSION + 1}")
    finally:
        conn.close()


@contextlib.contextmanager
def mutate_state(state_path: Path, *, timeout_s: float | None = None) -> Iterator[dict]:
    """Load a plan, yield the whole dict for mutation, write it all back.

    THE TEST-SEEDING SEAM, and deliberately test-only. Production stopped
    writing plan state this way when the file primitives went: every writer in
    the package now names the rows it changes (`plan_store.op_*`) or declares
    the preconditions its decision rested on (`apply_tick_delta`). Neither
    shape can express "put this plan into an arbitrary state", which is what a
    fixture needs and only a fixture needs — so the whole-document
    read-modify-write lives here, where no shipped code path can reach it.

    Same transaction discipline as the ops: one `BEGIN IMMEDIATE`, the version
    bumped so a poller notices, and the event log treated as append-only (only
    entries past the loaded length are inserted).
    """
    orch_dir, slug = plan_store.key_for_state_path(state_path)
    with (
        plan_store._write_conn(orch_dir, timeout_s=timeout_s) as conn,
        db.write_txn(conn, timeout_s=timeout_s) as cur,
    ):
        cur.execute("UPDATE plans SET version = version + 1 WHERE slug = ?", (slug,))
        if cur.rowcount == 0:
            raise FileNotFoundError(str(state_path))
        data = plan_store._snapshot_in_txn(cur, orch_dir, slug)
        recorded = len(data["events"])
        yield data
        plan_store._update_head(cur, slug, data)
        plan_store._write_claim(cur, slug, data.get("current_claim"))
        cur.execute("DELETE FROM blockers WHERE plan_slug = ?", (slug,))
        plan_store._write_blockers(cur, slug, data.get("blockers") or [])
        cur.execute("DELETE FROM spawned_tasks WHERE plan_slug = ?", (slug,))
        plan_store._write_tasks(cur, slug, data.get("spawned_tasks") or [])
        plan_store._insert_events(cur, slug, data["events"][recorded:])


def write_state(state_path: Path, data: dict) -> None:
    """Replace a plan's rows with `data`, creating the plan if it is absent.

    The fixture spelling of "this plan starts out looking exactly like this".
    Test-only for the same reason `mutate_state` is; events are re-inserted
    rather than diffed, so their ids are minted fresh — which is what
    overwriting a whole document always meant.
    """
    orch_dir, slug = plan_store.key_for_state_path(state_path)
    with plan_store._write_conn(orch_dir) as conn, db.write_txn(conn) as cur:
        if cur.execute("SELECT 1 FROM plans WHERE slug = ?", (slug,)).fetchone() is None:
            plan_store._insert_plan(cur, slug, data)
            return
        for table in ("claims", "blockers", "spawned_tasks", "events"):
            cur.execute(f"DELETE FROM {table} WHERE plan_slug = ?", (slug,))
        plan_store._update_head(cur, slug, data)
        plan_store._write_claim(cur, slug, data.get("current_claim"))
        plan_store._write_blockers(cur, slug, data.get("blockers") or [])
        plan_store._write_tasks(cur, slug, data.get("spawned_tasks") or [])
        plan_store._insert_events(cur, slug, data.get("events") or [])


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
        with mutate_state(self.state_path) as data:
            return st.claim_phase(data, phase, lease_minutes=30)

    def _read(self) -> dict:
        # Through the store, not the filesystem: `state_path` is the KEY to a
        # row in the project database now, and this one seam carries every
        # `_read` call site in the suite across the engine swap.
        return st.load(self.state_path)


def isolate_monitor_marker(testcase: unittest.TestCase, tmp_path: Path) -> None:
    """Point clu's monitor marker file at a per-test XDG dir.

    `monitor.marker_path()` resolves through `XDG_CONFIG_HOME`, so this
    is the same monkeypatch as `isolate_registry`. Named separately so a
    monitor test that doesn't touch registry doesn't read as registry-coupled.
    """
    isolate_registry(testcase, tmp_path)
