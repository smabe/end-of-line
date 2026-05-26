"""CLI input-validation tests (path-traversal guards)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from end_of_line.cli import main
from tests import isolate_registry


class TestSlugValidation(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rejects_dotdot_slug(self) -> None:
        rc = main(
            [
                "init",
                "--project",
                str(self.project),
                "--plan",
                "../../../tmp/pwn",
            ]
        )
        self.assertEqual(rc, 2)

    def test_rejects_absolute_slug(self) -> None:
        rc = main(
            [
                "init",
                "--project",
                str(self.project),
                "--plan",
                "/etc/passwd",
            ]
        )
        self.assertEqual(rc, 2)

    def test_rejects_uppercase(self) -> None:
        rc = main(
            [
                "init",
                "--project",
                str(self.project),
                "--plan",
                "BadSlug",
            ]
        )
        self.assertEqual(rc, 2)

    def test_rejects_shell_metachar(self) -> None:
        rc = main(
            [
                "init",
                "--project",
                str(self.project),
                "--plan",
                "good;rm-rf",
            ]
        )
        self.assertEqual(rc, 2)

    def test_accepts_valid_slug(self) -> None:
        rc = main(
            [
                "init",
                "--project",
                str(self.project),
                "--plan",
                "watch-start-workout",
            ]
        )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
