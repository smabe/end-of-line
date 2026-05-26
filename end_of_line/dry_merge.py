"""Dry-merge engine for multi-plan parallel batches.

See plans/dry-merge-gate.md and docs/architecture.md.
Pure function: takes project_root + base_ref + list of branches
+ optional test_command, returns a MergeResult. No state I/O.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_OUTCOME_CLEAN = "clean"
_OUTCOME_TEXTUAL_CONFLICT = "textual_conflict"
_OUTCOME_SUITE_FAILED = "suite_failed"

_STDERR_TAIL_CHARS = 2000


@dataclass
class MergeResult:
    outcome: str
    conflict_files: list[str] = field(default_factory=list)
    test_exit_code: int | None = None
    stderr_tail: str = ""
    merged_branches: list[str] = field(default_factory=list)
    base_sha: str = ""


def attempt_merge(
    project_root: Path,
    base_ref: str,
    branches: list[str],
    test_command: str | None = None,
    *,
    timeout: int = 300,
) -> MergeResult:
    """Dry-merge `branches` off `base_ref` in a scratch worktree.

    Returns a MergeResult with outcome 'clean', 'textual_conflict', or
    'suite_failed'.  The scratch worktree is always removed in a
    try/finally — leak prevention is load-bearing.
    """
    base_sha = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", base_ref],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    tmpdir = tempfile.mkdtemp(prefix="clu-dry-merge-")
    try:
        subprocess.run(
            ["git", "-C", str(project_root), "worktree", "add", "--detach", tmpdir, base_sha],
            capture_output=True,
            text=True,
            check=True,
        )

        for branch in branches:
            merge = subprocess.run(
                ["git", "-C", tmpdir, "merge", "--no-ff", "--no-edit", branch],
                capture_output=True,
                text=True,
            )
            if merge.returncode != 0:
                conflicts = (
                    subprocess.run(
                        ["git", "-C", tmpdir, "diff", "--name-only", "--diff-filter=U"],
                        capture_output=True,
                        text=True,
                    )
                    .stdout.strip()
                    .splitlines()
                )
                return MergeResult(
                    outcome=_OUTCOME_TEXTUAL_CONFLICT,
                    conflict_files=conflicts,
                    merged_branches=branches,
                    base_sha=base_sha,
                )

        if test_command is None:
            return MergeResult(
                outcome=_OUTCOME_CLEAN,
                merged_branches=branches,
                base_sha=base_sha,
            )

        try:
            proc = subprocess.run(
                test_command,
                shell=True,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return MergeResult(
                outcome=_OUTCOME_SUITE_FAILED,
                test_exit_code=-1,
                stderr_tail=f"<timeout after {timeout}s>",
                merged_branches=branches,
                base_sha=base_sha,
            )

        if proc.returncode == 0:
            return MergeResult(
                outcome=_OUTCOME_CLEAN,
                merged_branches=branches,
                base_sha=base_sha,
            )

        raw = proc.stderr or proc.stdout
        tail = raw[-_STDERR_TAIL_CHARS:]
        return MergeResult(
            outcome=_OUTCOME_SUITE_FAILED,
            test_exit_code=proc.returncode,
            stderr_tail=tail,
            merged_branches=branches,
            base_sha=base_sha,
        )

    finally:
        try:
            r = subprocess.run(
                ["git", "-C", str(project_root), "worktree", "remove", "--force", tmpdir],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                print(f"dry_merge: teardown error: {r.stderr.strip()}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"dry_merge: teardown error: {exc}", file=sys.stderr)
