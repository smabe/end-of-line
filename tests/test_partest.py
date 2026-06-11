"""scripts/partest.py — module-sharded parallel unittest runner.

The script lives outside the package (dev tooling beside canary.sh), so
tests load it via importlib from its file path. Pure functions only here;
the subprocess fan-out is exercised by using the tool itself (and the
count-parity guard makes every real run self-checking).
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
import unittest
from pathlib import Path

from tests import CluTestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "partest.py"
if not _SCRIPT.exists():
    # Packaging contexts may ship tests/ without scripts/ — skip the module
    # rather than turning every gate's collection phase into an error.
    raise unittest.SkipTest("scripts/partest.py not present in this tree")
_spec = importlib.util.spec_from_file_location("partest", _SCRIPT)
assert _spec is not None and _spec.loader is not None
partest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(partest)


class ParseRanTest(CluTestCase):
    def test_plural(self):
        self.assertEqual(partest.parse_ran("Ran 12 tests in 0.01s\n\nOK\n"), 12)

    def test_singular(self):
        self.assertEqual(partest.parse_ran("Ran 1 test in 0.00s\n\nOK\n"), 1)

    def test_failure_output_still_parses(self):
        out = "FAIL: test_x\n----\nRan 9 tests in 1.2s\n\nFAILED (failures=1)\n"
        self.assertEqual(partest.parse_ran(out), 9)

    def test_last_match_wins_over_leaked_nested_output(self):
        out = "Ran 3 tests in 0.1s\n(nested runner echo)\nRan 41 tests in 2.0s\n\nOK\n"
        self.assertEqual(partest.parse_ran(out), 41)

    def test_no_summary_returns_none(self):
        self.assertIsNone(partest.parse_ran("Traceback (most recent call last):\n"))


class DiscoverModulesTest(CluTestCase):
    def test_lists_test_modules_sorted_with_package_prefix(self):
        d = self.tmp_path / "tests"
        d.mkdir()
        for name in ("test_b.py", "test_a.py", "helper.py", "conftest.py"):
            (d / name).write_text("")
        self.assertEqual(
            partest.discover_modules(d),
            ["tests.test_a", "tests.test_b"],
        )

    def test_empty_dir(self):
        d = self.tmp_path / "empty"
        d.mkdir()
        self.assertEqual(partest.discover_modules(d), [])


class ExpectedCountTest(CluTestCase):
    """Hermetic fixture packages — never the real suite: counting the real
    tests/ from inside a test import-executes every sibling module outside
    its own isolation (and the tool's runtime parity guard already pins the
    real number on every partest run)."""

    def _fixture(self, pkg: str, body: str) -> Path:
        root = self.tmp_path / pkg
        root.mkdir()
        (root / "__init__.py").write_text("")
        (root / "test_fixture.py").write_text(textwrap.dedent(body))
        # discover imports as "<pkg>.test_fixture"; keep sys.modules clean
        # for sibling tests in this process.
        self.addCleanup(
            lambda: [sys.modules.pop(k) for k in list(sys.modules) if k.startswith(pkg)]
        )
        return root

    def test_counts_cases(self):
        d = self._fixture(
            "partest_fix_ok",
            """
            import unittest
            class T(unittest.TestCase):
                def test_a(self): pass
                def test_b(self): pass
            """,
        )
        self.assertEqual(partest.expected_count(d), 2)

    def test_loader_error_is_fatal(self):
        d = self._fixture("partest_fix_broken", "import nonexistent_module_xyz\n")
        with self.assertRaises(partest.LoaderError):
            partest.expected_count(d)

    def test_sys_path_restored(self):
        d = self._fixture(
            "partest_fix_path",
            """
            import unittest
            class T(unittest.TestCase):
                def test_a(self): pass
            """,
        )
        before = list(sys.path)
        partest.expected_count(d)
        self.assertEqual(sys.path, before)
