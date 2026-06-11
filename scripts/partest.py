#!/usr/bin/env python3
"""Module-sharded parallel unittest runner — the ITERATION runner.

Stdlib `unittest discover` is strictly serial (~2min on this suite);
sharding one subprocess per test module across N workers runs the same
tests in ~20-30s (measured 2026-06-10: 110s serial-equivalent → 20s at 8
jobs, zero failures — `CluTestCase`'s per-test XDG/registry/tmp isolation
is what makes the modules embarrassingly parallel).

Scope: local dev loops and worker red/green ITERATION only. The
pre-commit green and the AUTHORITATIVE gates stay on canonical serial
`python3 -m unittest discover -s tests`: `clu verify`
(quality.verify_command, which also runs basedpyright — this script
sees no type errors) and scripts/canary.sh. Keep it that way — the gate
should be boring.

Self-checking: after the shards finish, the summed per-module "Ran N"
must equal `unittest discover`'s case count, and any discover load error
is fatal (a broken-import module counts as ONE case, which would
silently shrink the parity target). A green partest run therefore cannot
cover less than discover would.

Usage: python3 scripts/partest.py [-j N]   (cwd-independent)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

# Anchor everything to the repo this script lives in — shards and the
# parity counter must share one import root regardless of caller cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _REPO_ROOT / "tests"

# canary.sh extracts the same unittest summary line (scripts/canary.sh,
# TESTS_RUN grep) — keep the two patterns in sync if this ever changes.
_RAN_RE = re.compile(r"^Ran (\d+) tests?\b", re.MULTILINE)
# Tail of a failing module's output to show; full logs would bury the signal.
_FAIL_TAIL_CHARS = 4000
# A shard that exceeds this is wedged, not slow — the whole suite runs in
# ~2min serially, so no single module legitimately approaches 10 minutes.
_SHARD_TIMEOUT_SEC = 600


class LoaderError(RuntimeError):
    """discover() swallowed a module import error; parity target is invalid."""


def discover_modules(tests_dir: Path) -> list[str]:
    """Importable module names for every test_*.py directly in tests_dir."""
    return sorted(f"{tests_dir.name}.{p.stem}" for p in tests_dir.glob("test_*.py"))


def parse_ran(output: str) -> int | None:
    """Test count from a unittest runner's 'Ran N tests' summary.

    LAST match wins: a test that leaks a nested runner's output prints its
    spurious 'Ran N tests' BEFORE the shard's real trailing summary.
    """
    matches = _RAN_RE.findall(output)
    return int(matches[-1]) if matches else None


def expected_count(tests_dir: Path) -> int:
    """What canonical discover would run — the parity target for the shards.

    Imports package-correct (`tests.test_x`, top_level_dir = repo root),
    exactly how the shards run them. Raises LoaderError if any module
    fails to import: discover counts a broken module as ONE _FailedTest,
    which would silently shrink the target. sys.path is restored — this
    is also called from inside the test suite, where a lasting mutation
    would leak across tests.
    """
    # Resolve BOTH paths: a symlinked tests_dir (macOS /var -> /private/var
    # tmp dirs) otherwise lands outside the resolved top_level_dir and
    # discover asserts "Path must be within the project".
    tests_dir = tests_dir.resolve()
    root = str(tests_dir.parent)
    inserted = root not in sys.path
    if inserted:
        sys.path.insert(0, root)
    try:
        loader = unittest.TestLoader()
        suite = loader.discover(str(tests_dir), top_level_dir=root)
        if loader.errors:
            raise LoaderError(loader.errors[0][:600])
        return suite.countTestCases()
    finally:
        if inserted:
            sys.path.remove(root)


def run_module(module: str) -> tuple[str, int | None, bool, str, float]:
    """(module, ran, ok, output, seconds) for one shard subprocess."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", module, "-q"],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            timeout=_SHARD_TIMEOUT_SEC,
        )
        # Real summary is on stderr; only fall back to stdout if absent.
        ran = parse_ran(proc.stderr) or parse_ran(proc.stdout)
        ok = proc.returncode == 0
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        ran, ok = None, False
        output = (
            f"SHARD TIMEOUT after {_SHARD_TIMEOUT_SEC}s (wedged test?)\n"
            f"partial output:\n{(exc.stdout or '')[-1000:]}{(exc.stderr or '')[-1000:]}"
        )
    return module, ran, ok, output, time.monotonic() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Module-sharded parallel unittest runner (iteration use; "
        "the canonical gate stays `unittest discover`)."
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=min(8, os.cpu_count() or 2),
        help="parallel shard subprocesses (default: min(8, cpus))",
    )
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")

    modules = discover_modules(_TESTS_DIR)
    if not modules:
        print(f"no test_*.py modules under {_TESTS_DIR}/", file=sys.stderr)
        return 2

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs + 1) as pool:
        # Parity target imports every module in-process — seconds of work
        # that overlaps the shard fan-out instead of preceding it.
        expected_future = pool.submit(expected_count, _TESTS_DIR)
        results = list(pool.map(run_module, modules))
    wall = time.monotonic() - started
    try:
        expected = expected_future.result()
    except LoaderError as exc:
        print(f"discover load error — parity target invalid:\n{exc}", file=sys.stderr)
        return 2

    ran = sum(n or 0 for _, n, _, _, _ in results)
    failures = [(mod, out) for mod, _, ok, out, _ in results if not ok]
    for mod, out in failures:
        print(f"\n=== FAILED shard: {mod} ===")
        print(out[-_FAIL_TAIL_CHARS:])

    slowest = sorted(results, key=lambda r: -r[4])[:3]
    print(f"Ran {ran} tests across {len(modules)} modules in {wall:.1f}s "
          f"({args.jobs} jobs; canonical discover expects {expected})")
    print("slowest: " + ", ".join(f"{m.split('.')[-1]} {d:.1f}s" for m, _, _, _, d in slowest))
    if failures:
        print(f"FAILED (modules={len(failures)})")
        return 1
    if ran != expected:
        print(f"COUNT MISMATCH: shards ran {ran}, discover expects {expected} — "
              f"a module or loader path is being missed; do NOT trust this run")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
