#!/usr/bin/env python3
"""Regenerate `end_of_line/skills_manifest.json` from git history.

The manifest answers one question at scan time: *did clu ever ship these
bytes?* An installed SKILL.md whose hash appears here was written by some
version of clu, however old — so a repair may overwrite it. One whose hash
appears nowhere was edited by a person, and clu leaves it alone. A single
"current" hash could only say "differs", which cannot tell those two apart.

Run this BY HAND whenever a bundled SKILL.md changes, and commit the result
alongside the skill change — `tests/test_skill_sync.py` fails when a bundled
skill's current hash is missing from the manifest, so a forgotten run is
caught at the gate rather than in the field. Nothing runs it at install time:
an installed clu has no git checkout to read.

The working-tree copy is included alongside the committed history. Without it,
regenerating BEFORE committing a skill change could never produce a manifest
containing that change, and the currency guard could only be satisfied by a
second commit — leaving one commit red in between.

Hashes are SHA-256 of the FILE CONTENT. Not `git hash-object`: a blob id is
taken over a NUL-terminated `blob <len>` header plus the content, so it never
equals a hash of the same bytes sitting on disk. Getting that wrong yields a
well-formed manifest in which nothing ever matches — every installed copy
reads as foreign, silently, forever.

Usage: python3 scripts/gen_skill_manifest.py [--check]
       --check exits non-zero if the file on disk is not what this would write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "end_of_line" / "skills"
MANIFEST = REPO_ROOT / "end_of_line" / "skills_manifest.json"
SKILL_FILENAME = "SKILL.md"


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        check=True,
    ).stdout


def revisions(relpath: str) -> list[tuple[str, str]]:
    """(rev, path-at-that-rev) for every commit touching `relpath`, newest first.

    `--follow` is what reaches versions committed before a rename — `clu-phase`
    lived at `examples/clu-phase-skill.md` and then `end_of_line/skill/SKILL.md`
    before its current path, and those really are versions clu shipped. Each
    commit's path is read back from `--name-only` rather than assumed, because
    `git show <rev>:<current-path>` fails for any commit predating the rename.
    """
    out = _git(
        "log",
        "--follow",
        "--format=%x00%H",
        "--name-only",
        "--",
        relpath,
    ).decode()
    pairs: list[tuple[str, str]] = []
    rev = ""
    for line in out.splitlines():
        if line.startswith("\0"):
            rev = line[1:].strip()
        elif line.strip() and rev:
            pairs.append((rev, line.strip()))
            rev = ""  # one path per commit for a single-file log
    return pairs


def fingerprints(name: str) -> list[str]:
    """Every content hash this skill has ever had, newest first, deduped."""
    relpath = f"end_of_line/skills/{name}/{SKILL_FILENAME}"
    seen: list[str] = []
    working = (SKILLS_DIR / name / SKILL_FILENAME).read_bytes()
    for blob in [working] + [_git("show", f"{rev}:{path}") for rev, path in revisions(relpath)]:
        h = hashlib.sha256(blob).hexdigest()
        if h not in seen:
            seen.append(h)
    return seen


def build() -> dict[str, list[str]]:
    names = sorted(p.name for p in SKILLS_DIR.iterdir() if (p / SKILL_FILENAME).is_file())
    manifest = {name: fingerprints(name) for name in names}
    empty = [name for name, hashes in manifest.items() if not hashes]
    if empty:
        # Every bundled skill was committed at least once, so an empty list is
        # a generation bug (a bad path, a shallow clone), never a real history.
        raise SystemExit(f"no fingerprints found for: {', '.join(empty)}")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed manifest is stale; write nothing",
    )
    args = ap.parse_args()

    manifest = build()
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    if args.check:
        current = MANIFEST.read_text() if MANIFEST.exists() else ""
        if current != text:
            print(f"{MANIFEST} is stale — re-run {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"{MANIFEST} is current")
        return 0

    MANIFEST.write_text(text)
    for name, hashes in manifest.items():
        print(f"{name}: {len(hashes)} fingerprints")
    print(f"wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
