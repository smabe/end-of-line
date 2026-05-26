"""Tests for the CluTestCase base class."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests import CluTestCase


class TestCluTestCaseProvidesTmpPath(CluTestCase):
    def test_tmp_path_is_a_directory(self) -> None:
        self.assertIsInstance(self.tmp_path, Path)
        self.assertTrue(self.tmp_path.is_dir())

    def test_clu_test_mode_is_set(self) -> None:
        self.assertEqual(os.environ.get("CLU_TEST_MODE"), "1")


class TestCluTestModeClearedAfterTeardown(unittest.TestCase):
    """CLU_TEST_MODE must not bleed into non-CluTestCase tests."""

    def test_clu_test_mode_absent(self) -> None:
        self.assertNotIn("CLU_TEST_MODE", os.environ)
