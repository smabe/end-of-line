"""Drift detection between clu's bundled skills and the installed copies.

`scan()` is the whole surface: it reads `~/.claude/skills/<name>/SKILL.md` for
each bundled skill and returns one `SkillStatus` per skill. It prints nothing,
writes nothing, and decides no policy — three callers need the same facts and
three different behaviours (doctor reports; `clu init` and `clu queue add`
repair), so detection, policy and formatting are separate layers.

Two properties are separate on purpose:

* **placement** is what is actually on disk. It is decided with `is_symlink()`
  BEFORE `exists()`, because `exists()` follows the link and reports a dangling
  symlink as "nothing installed". A read that raises is `unreadable`, never
  silently treated as in sync.
* **writable** is whether clu may safely write that path — a filesystem
  property, not a name. A target is unsafe when the leaf OR any parent
  component is a symlink, because a write then resolves into a directory clu
  does not own. Note the parent case: for `~/.claude/skills/plan/SKILL.md` the
  symlink is usually the *directory*, so a leaf-only check calls it writable
  when it is not. The walk goes to the filesystem root — a symlinked `~` or
  `~/.claude` is the same hazard one level up.

One `SKILL.md` per skill is the packaged unit (`pyproject.toml` ships
`skills/*/SKILL.md` and nothing else), so this compares that one file rather
than walking the directory. `tests/test_skill_sync.py` asserts the invariant.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

# What is on disk at the target path.
#   file       — a regular file, no symlink anywhere on its path to it
#   link       — the SKILL.md itself is a symlink that resolves
#   absent     — nothing there
#   broken     — a symlink whose destination does not exist
#   unreadable — something is there, but its bytes could not be read
Placement = Literal["file", "link", "absent", "broken", "unreadable"]

# How the installed bytes compare to the bundled copy. `unknown` means there
# were no bytes to compare (absent / broken / unreadable), never "same".
Content = Literal["in_sync", "differs", "unknown"]

SKILL_FILENAME = "SKILL.md"


@dataclass(frozen=True)
class SkillStatus:
    """One skill's installed state. Facts only — no policy, no formatting."""

    name: str
    target: Path
    placement: Placement
    content: Content
    writable: bool


def installed_path(name: str) -> Path:
    """Where clu installs `name`: `~/.claude/skills/<name>/SKILL.md`."""
    return Path.home() / ".claude" / "skills" / name / SKILL_FILENAME


def bundled_bytes(name: str) -> bytes:
    """The bundled `SKILL.md` for `name`, as shipped in the package."""
    return files("end_of_line").joinpath(f"skills/{name}/{SKILL_FILENAME}").read_bytes()


def is_writable_target(target: Path) -> bool:
    """False when the leaf or ANY parent component is a symlink.

    `Path.resolve()` is deliberately not used: it collapses the very
    distinction being tested. Each component is checked with `is_symlink()`,
    which is false for a component that does not exist yet — an install into a
    path clu will create is safe.
    """
    if target.is_symlink():
        return False
    return not any(parent.is_symlink() for parent in target.parents)


def _classify(name: str, target: Path) -> tuple[Placement, Content]:
    """Placement + content for one target. `is_symlink()` before `exists()`."""
    link = target.is_symlink()
    if link and not target.exists():
        return "broken", "unknown"
    if not link and not target.exists():
        return "absent", "unknown"
    try:
        installed = target.read_bytes()
    except OSError:
        # Permissions, a directory where a file belongs, an I/O error. The
        # previous check swallowed this and reported the skill as in sync.
        return "unreadable", "unknown"
    same = installed == bundled_bytes(name)
    return ("link" if link else "file"), ("in_sync" if same else "differs")


def scan(names: Iterable[str] | None = None) -> list[SkillStatus]:
    """Classify each named skill's installed copy. Defaults to every bundled
    skill, in `BUNDLED_SKILLS` order. Read-only: creates no directories."""
    # Imported here rather than at module scope: cli imports this module.
    from .cli import BUNDLED_SKILLS

    if names is None:
        names = BUNDLED_SKILLS
    else:
        # Validate up front so an unbundled name fails the SAME way whether or
        # not something happens to be installed at its path. Without this the
        # bundled read raises only when the installed read succeeded first,
        # which makes a caller's typo look like a filesystem problem.
        names = list(names)
        unknown = [n for n in names if n not in BUNDLED_SKILLS]
        if unknown:
            raise ValueError(f"not bundled clu skills: {', '.join(sorted(unknown))}")
    out: list[SkillStatus] = []
    for name in names:
        target = installed_path(name)
        placement, content = _classify(name, target)
        out.append(
            SkillStatus(
                name=name,
                target=target,
                placement=placement,
                content=content,
                writable=is_writable_target(target),
            )
        )
    return out
