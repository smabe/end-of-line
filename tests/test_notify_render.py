"""Tests for notify.render_blocker — covers the existing gap and the new
enrichment from issue #33 (terminal command + soft-limit fallback).

Sibling render_* helpers in notify.py have grown around render_blocker
without dedicated unit tests; the only coverage so far is the
render_stuck_blocker happy path in test_stuck_blocker.py. This module
locks down the public surface of render_blocker so future edits don't
silently break the inbound iMessage reply grammar."""
from __future__ import annotations

import unittest

from end_of_line import notify


PLAN = "workout-rearchitect-hk"
BID = "q-1"
PHASE = "t5-hkworkoutsession"
QUESTION = "Should the watch app push HKWorkoutSession or pull?"
OPTIONS = ["Push (live)", "Pull (poll)", "Skip — defer to phase 2"]


class RenderBlockerBasicTests(unittest.TestCase):
    def test_includes_plan_blocker_phase_header(self) -> None:
        body = notify.render_blocker(PLAN, BID, PHASE, QUESTION, OPTIONS)
        self.assertIn(f"{PLAN}/{BID}", body)
        self.assertIn(f"[{PHASE}]", body)

    def test_includes_question_text(self) -> None:
        body = notify.render_blocker(PLAN, BID, PHASE, QUESTION, OPTIONS)
        self.assertIn(QUESTION, body)

    def test_includes_numbered_options(self) -> None:
        body = notify.render_blocker(PLAN, BID, PHASE, QUESTION, OPTIONS)
        for i, opt in enumerate(OPTIONS):
            self.assertIn(f"[{i}]", body)
            self.assertIn(opt, body)

    def test_preserves_imessage_reply_grammar_hint(self) -> None:
        # notify_inbound.py REPLY_RE parses "<slug> <digit>". The hint
        # line in render_blocker is the user-facing prompt for that
        # grammar — must stay or the inbound parser is orphaned.
        body = notify.render_blocker(PLAN, BID, PHASE, QUESTION, OPTIONS)
        self.assertIn(f"`{PLAN} <number>`", body)

    def test_includes_copy_pastable_clu_answer_command(self) -> None:
        body = notify.render_blocker(PLAN, BID, PHASE, QUESTION, OPTIONS)
        self.assertIn(f"clu answer --plan {PLAN} <choice>", body)
        self.assertNotIn(BID, body.split("Terminal:")[-1])

    def test_no_options_renders_free_text_blocker(self) -> None:
        body = notify.render_blocker(PLAN, BID, PHASE, QUESTION, [])
        self.assertIn(QUESTION, body)
        self.assertIn(f"clu answer --plan {PLAN} <choice>", body)


class RenderBlockerTruncationTests(unittest.TestCase):
    def test_long_question_truncated_at_word_boundary(self) -> None:
        long_q = (
            "This is a very long question that goes on and on and on and "
            "on and on and on and on and on and on and on and on and on "
            "and on and on and on and on and on and on and on and on so "
            "that it exceeds the per-line cap and forces truncation"
        )
        body = notify.render_blocker(PLAN, BID, PHASE, long_q, OPTIONS)
        # Truncation marker present and no mid-word break.
        self.assertIn("…", body)
        # The full untruncated question is NOT in the body.
        self.assertNotIn(long_q, body)
        # The truncated prefix is a clean word-ending (no half-words).
        for line in body.splitlines():
            if line.endswith("…"):
                before = line[:-1].rstrip()
                # The character before the ellipsis is end-of-word, not mid-word.
                self.assertTrue(
                    before == "" or before[-1].isalnum() or before[-1] in ".,;:!?",
                    f"truncation broke mid-word: {line!r}",
                )

    def test_long_option_truncated_per_line(self) -> None:
        long_opts = [
            "Short option",
            "A genuinely very long option label that should be cut down to a "
            "manageable length so it does not blow out the iMessage body",
            "Another short",
        ]
        body = notify.render_blocker(PLAN, BID, PHASE, QUESTION, long_opts)
        self.assertIn("Short option", body)
        self.assertIn("Another short", body)
        # The long one is truncated.
        self.assertNotIn(long_opts[1], body)
        self.assertIn("…", body)


class RenderBlockerFallbackTests(unittest.TestCase):
    def _huge(self) -> tuple[str, list[str]]:
        # Build inputs guaranteed to blow past the soft limit even after
        # per-field truncation. No internal spaces in options so
        # word-boundary truncation lands at the per-option cap, not at
        # the first space — that's what overflows the body.
        q = "Question " + "x" * 4000
        opts = [f"option_{i}_" + "y" * 4000 for i in range(40)]
        return q, opts

    def test_oversize_body_uses_short_fallback(self) -> None:
        q, opts = self._huge()
        body = notify.render_blocker(PLAN, BID, PHASE, q, opts)
        # Soft limit is the contract — fallback body must respect it.
        self.assertLessEqual(len(body), notify.BLOCKER_BODY_SOFT_LIMIT)

    def test_fallback_includes_clu_answer_command(self) -> None:
        q, opts = self._huge()
        body = notify.render_blocker(PLAN, BID, PHASE, q, opts)
        self.assertIn(f"clu answer --plan {PLAN} <choice>", body)

    def test_fallback_keeps_plan_and_blocker_header(self) -> None:
        q, opts = self._huge()
        body = notify.render_blocker(PLAN, BID, PHASE, q, opts)
        self.assertIn(f"{PLAN}/{BID}", body)
        self.assertIn(f"[{PHASE}]", body)

    def test_fallback_indicates_option_count(self) -> None:
        q, opts = self._huge()
        body = notify.render_blocker(PLAN, BID, PHASE, q, opts)
        # Operator needs to know there ARE options even if we can't fit them.
        self.assertIn(str(len(opts)), body)


if __name__ == "__main__":
    unittest.main()
