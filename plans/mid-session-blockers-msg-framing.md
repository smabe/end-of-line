# mid-session-blockers-msg-framing — keep one event on one line

You are phase `msg-framing` of the `mid-session-blockers` plan. A worker that
calls `clu block` with a newline anywhere in its question breaks the task-list
protocol today: the `msg="…"` quote never closes and the consumer drops the tail
as a non-`TASK_*` line. You are fixing that and pinning the invariant so no
future edit can reintroduce it. One commit. This phase does not touch the
blocker render — that is phase `blocked-render`.

## Locked decisions (do NOT re-litigate)

See `plans/mid-session-blockers.md`. Summary:

- Escape `\n` and `\r` to their two-character literal forms. ESCAPE, don't strip
  — the operator's wording stays recoverable by a reader.
- Assert the invariant across every `_TASK_STATUS_MAP` key, not just the blocked
  branch.
- Independent of phase 2; this is a live defect on `main`.

## Read first

- `plans/mid-session-blockers.md` `## Findings log` — empty if you are first.
- `end_of_line/watch.py:317-318` — `_escape_msg`, which escapes `\` and `"` only.
- `end_of_line/watch.py:321-331` — `_task_line`; `msg_field` at `:330`
  interpolates `msg` raw into `f' msg="{msg}"'`.
- `end_of_line/watch.py:279-289` — `_TASK_STATUS_MAP`, plus the three
  conditional keys added at `:290-295`. That is the full key set your invariant
  test must cover. **`:297-300` is a DIFFERENT map** (`_TASK_VERBOSE_STATUS_MAP`)
  — do not conflate them.
- `end_of_line/watch.py:334-359` — `_task_msg_for`; the blocked branch is at
  `340-343` and calls `_trunc`.
- `end_of_line/watch.py:100-103` — `_trunc`. Note it does NOT sanitize newlines:
  truncation has never been the thing keeping events on one line.
- `tests/test_watch_task_protocol.py` — the harness to extend. `:192,205` are
  the existing quote and backslash cases; `_escape_msg` itself has zero test
  references. `:219-229` is `test_long_question_truncated_to_100_chars`, which
  despite its name asserts `assertLessEqual(..., 120)` — the 100 is documented
  (`docs/reference.md:1193`) and implemented, but nothing pins it.

## Produce

1. **Failing tests first.** In `tests/test_watch_task_protocol.py`:
   - `test_a_newline_in_a_question_stays_on_one_line` — a `phase_blocked` event
     whose `question` contains `\n`; assert the emitted line contains no raw
     newline and that `msg="` is closed by a matching unescaped `"`.
   - `test_every_task_status_map_key_emits_exactly_one_line` — table-driven over
     every key in `_TASK_STATUS_MAP`, each with a newline-bearing fixture;
     assert `"\n" not in project_event_task(...)`. This is the invariant; write
     it so adding a key without a fixture is a visible failure, not a silent gap.
   - `test_a_carriage_return_is_escaped_too` — `\r` alone, same assertion.
   - `test_escaping_survives_a_quote_and_a_newline_together` — the combination,
     since `_escape_msg` runs replacements in sequence and order matters.
   - `test_existing_msg_cap_is_unchanged` — the truncation behavior asserted at
     `:219-229` still holds. This phase must not move that number and must not
     tighten the loose assertion either; a cap change deserves its own diff.

2. **Implementation.**
   - `end_of_line/watch.py:317-318`: extend `_escape_msg` to also replace
     `\n` → `\\n` and `\r` → `\\r`. Keep the backslash replacement FIRST so the
     escapes it introduces are not double-escaped by the later passes.
   - Nothing else. Do not change `_trunc`, the cap, or any formatter — `_trunc`
     is shared with `_fmt_blocked` (`watch.py:114`) and phase 2 depends on its
     current behavior.

3. **Acceptance.**
   - All 5 new tests green.
   - `python3 -m unittest discover -s tests` fully green; report the count.
   - `basedpyright` clean.
   - Smoke: `python3 -c "from end_of_line.watch import _escape_msg;
     print(repr(_escape_msg('a\"b\\nc')))"` shows both escapes applied and no raw
     newline.

4. **Commit + attest + complete.**
   - Record cross-phase findings in the master's `## Findings log` if any.
   - Commit: `mid-session-blockers: phase msg-framing — escape newlines in the task-list msg field`.
   - Stage explicit paths.
   - After the commit: `clu verify` then `clu attest --simplify`, both with
     `--plan mid-session-blockers --phase msg-framing --token <T>`.
   - `clu complete --plan mid-session-blockers --phase msg-framing --token <T>`.

## Failure modes to watch

- **Replacement order.** `_escape_msg` chains `str.replace`. If you add the
  newline replacement before the backslash one, the `\` you emit gets escaped by
  the later pass and the output is wrong. Backslash first, always.
- **Don't "fix" it by stripping.** Dropping the newline loses the operator's
  wording silently, which is the same class of failure as today's bug.
- **`_trunc` is not the sanitizer and must not become one.** It is shared with
  `_fmt_blocked` (called at `watch.py:115`), which phase 2 depends on. Escaping
  belongs in `_escape_msg`.
- **The golden is watching.** `tests/goldens/watch-demo.txt` is compared as a
  whole list by `tests/test_demo_script.py:44-52`. This phase should not change
  it — if it does, you changed more than the escaping.
