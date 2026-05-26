"""Tests for the INSTRUCTION constant in clu_inbox_surface hook.

Verifies the literal contains a syntactically correct `clu answer` invocation.
The actual CLI signature (cmd_answer argparse) has no `blocker_id` positional —
only `--plan SLUG` and `<answer>`.
"""

from __future__ import annotations

import unittest

from end_of_line.hooks.clu_inbox_surface import INSTRUCTION


class InstructionSyntaxTests(unittest.TestCase):
    def test_instruction_uses_correct_clu_answer_syntax(self) -> None:
        self.assertIn("clu answer --plan <slug> <answer>", INSTRUCTION)
        self.assertNotIn("<blocker_id>", INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
