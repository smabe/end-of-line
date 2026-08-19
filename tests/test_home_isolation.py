"""Guard: the test harness redirects `HOME` away from the developer's machine.

Every later phase of `skill-drift-trigger` writes under `~/.claude/skills/`.
Without a redirected `HOME` those writes land in the operator's real home and
the suite still reports green — the failure this file exists to make loud.

Four tests cover the four ways the redirect can be wrong: `Path.home()` not
following it, a write escaping to the real home, the redirect colliding with
`XDG_CONFIG_HOME` (which trips `assert_xdg_safe` on every clu config write
under `CLU_TEST_MODE=1`), and the patch being dropped from
`CluTestCase.setUp` altogether. A fifth keeps the harness-coverage audit from
rotting as new test files arrive.
"""

from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path

from end_of_line._xdg_guard import assert_xdg_safe, clu_config_dir
from tests import CANARY_RELPATH, REAL_HOME, CluTestCase, real_home_canary


class HomeIsolationTest(CluTestCase):
    def test_path_home_resolves_inside_the_per_test_temp_dir(self):
        home = Path.home().resolve()

        self.assertNotEqual(home, REAL_HOME)
        self.assertTrue(
            home.is_relative_to(self.tmp_path.resolve()),
            f"Path.home() -> {home} is not under {self.tmp_path.resolve()}",
        )

    def test_synthetic_skill_write_lands_in_the_temp_home(self):
        written = real_home_canary(self)

        self.assertTrue(written.is_file())
        self.assertTrue(written.resolve().is_relative_to(self.tmp_path.resolve()))
        self.assertFalse((REAL_HOME / CANARY_RELPATH).exists())

    def test_assert_xdg_safe_accepts_the_clu_config_dir(self):
        # Regression guard for the sibling-path decision: HOME == the XDG dir
        # would make clu_config_dir() a child of Path.home(), and this raises.
        assert_xdg_safe(clu_config_dir() / "registry.json")

        self.assertFalse(clu_config_dir().resolve().is_relative_to(Path.home().resolve()))

    def test_harness_patches_home_to_a_sibling_of_the_xdg_dir(self):
        # Fails loudly if "HOME" is ever dropped from CluTestCase's patcher.
        self.assertEqual(os.environ.get("HOME"), str(self.tmp_path / "home"))
        self.assertEqual(Path(os.environ["HOME"]).parent, Path(os.environ["XDG_CONFIG_HOME"]))
        self.assertTrue((self.tmp_path / "home").is_dir())


# --- standing audit -------------------------------------------------------
# The one-time audit that shipped with this phase found 43 test classes across
# 34 files driving a clu command that reads or writes under `~/.claude/` while
# only the XDG registry was isolated. Encoding it as a test is what stops the
# next such class from being added silently.

_HARNESS_BASES = {"CluTestCase", "GitProjectTestCase"}
_ISOLATORS = {"isolate_registry", "isolate_queue", "isolate_monitor_marker"}
# clu subcommands that reach `Path.home()`. `doctor` reads
# ~/.claude/skills/<name>/SKILL.md for the drift check today; `init` and
# `queue add` gain the skill-repair WRITE in phase p4, which is why they are
# listed before that write exists.
_HOME_TOUCHING = {"init", "queue", "install-skill", "doctor"}


def _called_names(node: ast.AST):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            fn = n.func
            yield (fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")), n


def _patches_home(cls: ast.ClassDef) -> bool:
    """True when the class actually passes "HOME" as a key to patch.dict.

    A substring search for the text `"HOME"` reads any class that merely
    MENTIONS the variable as isolated — `assertNotIn("HOME", env)` is the
    shape that fooled the first version of this check.
    """
    for fn, call in _called_names(cls):
        if fn != "dict":  # mock.patch.dict / patch.dict
            continue
        for arg in call.args:
            if isinstance(arg, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == "HOME" for k in arg.keys
            ):
                return True
    return False


def _invoked_command(call: ast.Call) -> str | None:
    """The clu subcommand a `main(...)` call drives, for the audit's purposes.

    Returns the subcommand when it is statically decidable and home-touching,
    `None` when it is statically decidable and harmless, and the sentinel
    `"<dynamic>"` when the argv cannot be read at all. **Unreadable must not
    read as safe** — `main(argv)` with a built list is common in this suite
    (tests/test_queue_add.py, tests/test_fleet.py), and the first version of
    this audit skipped every one of them silently.
    """
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, (ast.List, ast.Tuple)):
        if first.elts and isinstance(first.elts[0], ast.Constant):
            value = first.elts[0].value
            return value if value in _HOME_TOUCHING else None
        return "<dynamic>"  # literal list, non-constant head
    return "<dynamic>"  # a name, call, comprehension: not statically readable


def _base_names(cls: ast.ClassDef) -> list[str]:
    return [b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in cls.bases]


def _unisolated_home_touching_classes(path: Path) -> list[str]:
    """Classes in `path` that drive a home-touching clu command uncovered.

    Covered means: derives from `CluTestCase`, or the class (or one of its
    in-file bases) calls an `isolate_*` helper or patches `HOME` itself.
    """
    source = path.read_text()
    tree = ast.parse(source)
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}

    def isolated(name: str) -> bool:
        stack, seen = [name], set()
        while stack:
            current = stack.pop()
            if current in _HARNESS_BASES:
                return True
            if current in seen or current not in classes:
                continue
            seen.add(current)
            cls = classes[current]
            if any(fn in _ISOLATORS for fn, _ in _called_names(cls)):
                return True
            if _patches_home(cls):
                return True
            stack.extend(_base_names(cls))
        return False

    bad = []
    for name, cls in classes.items():
        cmds = {
            cmd
            for fn, call in _called_names(cls)
            if fn in ("main", "cli_main")
            for cmd in (_invoked_command(call),)
            if cmd is not None
        }
        if cmds and not isolated(name):
            bad.append(f"{path.name}::{name} (runs clu {'/'.join(sorted(cmds))})")
    return bad


class HarnessCoverageAuditTest(unittest.TestCase):
    def test_every_home_touching_test_class_is_isolated(self):
        offenders = [
            row
            for path in sorted(Path(__file__).parent.glob("test_*.py"))
            for row in _unisolated_home_touching_classes(path)
        ]

        self.assertEqual(
            offenders,
            [],
            "these test classes drive a clu command that reads or writes under "
            "~/.claude/ without isolating HOME — subclass CluTestCase, or call "
            "isolate_registry(self, tmp_path) in setUp:\n  " + "\n  ".join(offenders),
        )
