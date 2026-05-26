"""Tests for `clu integrate` — the deprecation alias for `clu validate`.

The verb `integrate` was misleading (it never updated main; it was a
dry-merge validator). clu-ship.md retired it in favor of `clu
validate`. This file used to carry the full integrate coverage; that
coverage now lives in `tests/test_cli_validate.py`. Only the
deprecation-alias contract stays here.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from end_of_line.cli import ExitCode, main
from end_of_line.dry_merge import MergeResult
from tests import CluTestCase


_CLEAN = MergeResult(outcome="clean", merged_branches=["a", "b"], base_sha="abc123")


class IntegrateDeprecationAliasTests(CluTestCase):
    """`clu integrate` is now a stderr-warning alias for `clu validate`."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp_path / "proj"
        self.project.mkdir()
        (self.project / "plans" / ".orchestrator").mkdir(parents=True)

    def _argv(self, *extra: str) -> list[str]:
        return ["integrate", "--project", str(self.project), *extra]

    def test_prints_deprecation_warning_to_stderr(self) -> None:
        err = io.StringIO()
        with mock.patch("end_of_line.dry_merge.attempt_merge", return_value=_CLEAN):
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = main(self._argv("--branches", "a,b"))
        self.assertEqual(rc, ExitCode.OK)
        msg = err.getvalue().lower()
        self.assertIn("deprecat", msg)
        self.assertIn("validate", msg)

    def test_delegates_to_validate_behavior(self) -> None:
        # Verify the validate logic still runs — branches are forwarded
        # to dry_merge.attempt_merge unchanged.
        with mock.patch("end_of_line.dry_merge.attempt_merge") as m:
            m.return_value = _CLEAN
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                rc = main(self._argv("--branches", "clu/plan-a,clu/plan-b"))
        self.assertEqual(rc, ExitCode.OK)
        self.assertEqual(m.call_args[0][2], ["clu/plan-a", "clu/plan-b"])


if __name__ == "__main__":
    unittest.main()
