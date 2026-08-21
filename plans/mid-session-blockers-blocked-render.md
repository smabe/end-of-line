# mid-session-blockers-blocked-render — put the choices and the command in the live line

You are phase `blocked-render` of the `mid-session-blockers` plan. A blocker
raised while a session runs currently reaches it as one truncated line with no
options and no way to reply. You are extracting the shared option renderer and
giving watch's blocked line the question, the numbered options, and the exact
answer command. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/mid-session-blockers.md`. Summary:

- Watch's FIRST line keeps its existing `slug/phase: BLOCKED q-1 — question`
  grammar. The new content is appended below it.
- Do NOT call `state_blocker.render_blocker` from watch. Its answer command is
  hardcoded without `--project`/`--blocker`, and its header is alien to the
  stream.
- Extract the option-block formatting + per-option truncation into a shared
  helper both callers use. `render_blocker`'s rendered output must stay
  BYTE-IDENTICAL — it feeds Discord.
- Options are read from plan state ONLY when a blocked event is seen, via
  `plan_store.snapshot(*keys[path])`. Never per tick.
- Multi-line yes; indentation no — leading whitespace is stripped in transit.
- The answer command is reserved against truncation; the question shrinks
  first. NO option cap — every option renders (measured: real blockers carry
  2-4, and a single-digit reply grammar bounds it at ten anyway).

## Read first

- `plans/mid-session-blockers.md` `## Findings log` — phase `msg-framing` may
  have left something here.
- `end_of_line/watch.py:113-119` — `_fmt_blocked`; `_trunc` is called at `:115`.
- `end_of_line/watch.py:122-127` — `_FORMATTERS`; the blocked entry registered at
  `:127` serves default, `--verbose` and `--operator` alike
  (`EVENT_PHASE_BLOCKED` is in `_DEFAULT_VISIBLE` at `:25` and
  `_OPERATOR_VISIBLE` at `:88`, absent from `_VERBOSE_ONLY`).
- `end_of_line/watch.py:715-733` — `project_event`, which today receives only
  the event. ONE production caller: `watch.py:692`.
- `end_of_line/watch.py:608-619` — the pre-loop snapshot. `plan_store.snapshot`
  at `:613`; `baseline` at `:619`, consumed at `:636-637` and never read again;
  `keys[path]` stashed at `:614` is what the loop retains.
- `end_of_line/watch.py:655-703` — the poll loop. Note `:659-660`: the idle poll
  is "one PRAGMA and no query at all" — your new read must not break that for
  the idle case.
- `end_of_line/state_blocker.py:105-134` — `render_blocker`. The option block is
  built at `:114-118`; the hardcoded answer command is `:113`; the header you
  must NOT adopt is `:127`.
- `end_of_line/hooks/clu_session_start.py:67` — `_BLOCKER_FOOTER_RESERVE = 180`,
  consumed at `:244`. This is the reserve-the-action shape to mirror.
- `end_of_line/hooks/clu_session_start.py:83` — the exact command form to emit.
- `end_of_line/cli.py:4932` — `--blocker` requires `--project`; the command you
  render must satisfy that or it will refuse.
- `end_of_line/state.py:1108-1115` — `open_blockers`, for the row lookup.
- `tests/test_watch_project_event.py`, `tests/test_watch_operator_filter.py` —
  harnesses; both already cover `EVENT_PHASE_BLOCKED`.
- `tests/test_demo_script.py:44-52` — whole-list golden comparison; `:54-68` —
  the `startswith(f"{slug}/{phase}: BLOCKED ")` assertion.

## Produce

1. **Failing tests first.**
   - `test_blocked_render_keeps_the_stream_line_grammar` — the FIRST rendered
     line still starts `f"{slug}/{phase}: BLOCKED "`.
   - `test_blocked_render_includes_numbered_options` — each option appears with
     its index.
   - `test_blocked_render_includes_the_scoped_answer_command` — the text
     contains `clu answer` with `--project`, `--plan` and `--blocker <q-N>`.
   - `test_blocked_render_uses_no_leading_indentation` — no rendered line begins
     with a space or tab. This pins a probe finding; without it a reviewer will
     "tidy" the output into an indented block that arrives flattened.
   - `test_a_long_question_shrinks_before_the_answer_command_is_lost` — an
     oversized question; the answer command still appears in full.
   - `test_every_option_renders_no_cap` — a blocker with eight options; all
     eight appear. Pins the decision that there is no cap to regress into.
   - `test_render_blocker_output_is_unchanged_by_the_extraction` — a
     characterization test pinning `render_blocker`'s exact string BEFORE you
     refactor, so the Discord path is provably untouched. Write this one FIRST.
   - `test_task_list_mode_blocked_line_is_unchanged` — the protocol path still
     emits its single line, byte-identical.
   - `test_blocker_with_no_options_still_renders_question_and_command` — a
     free-text blocker; no empty options block, command still present.
   - `test_idle_poll_does_not_read_plan_state` — no blocked event in the batch
     means no `plan_store.snapshot` call. Assert with a spy/patch; this is what
     keeps `:659-660`'s idle guarantee honest.

2. **Implementation.**
   - `end_of_line/state_blocker.py`: extract the option-block build at
     `:114-118` into a module-level helper taking `(options, per_option_limit)`
     and returning the formatted block. `render_blocker` calls it and its output
     does not change.
   - `end_of_line/watch.py`: give `project_event` an optional blocker-record
     parameter, defaulted so the two test modules keep working. In the poll
     loop, when `evt["type"] == st.EVENT_PHASE_BLOCKED`, call
     `plan_store.snapshot(*keys[path])` and locate the row by `blocker_id` via
     `state.open_blockers`; pass it in. `_fmt_blocked` renders its existing first
     line, then the shared option block, then the reserved answer command built
     in the `clu_session_start.py:83` form.
   - Regenerate `tests/goldens/watch-demo.txt` — expect the tail to shift, not
     one line to change.
   - Update `docs/operations.md:1683-1733` where it documents watch output.

3. **Acceptance.**
   - All 10 new tests green.
   - `python3 -m unittest discover -s tests` fully green; report the count.
   - `basedpyright` clean.
   - Smoke: the demo run's blocked entry carries the options and the command,
     and `tests/test_demo_script.py` passes against the regenerated golden.
   - `grep -n "render_blocker" end_of_line/watch.py` returns nothing — watch must
     not call it.
   - `docs/operations.md` describes the new multi-line blocked output.

4. **Commit + attest + complete.**
   - Record cross-phase findings in the master's `## Findings log` if any.
   - Commit: `mid-session-blockers: phase blocked-render — carry options and the answer command in the live line`.
   - Stage explicit paths.
   - After the commit: `clu verify` then `clu attest --simplify`, both with
     `--plan mid-session-blockers --phase blocked-render --token <T>`.
   - `clu complete --plan mid-session-blockers --phase blocked-render --token <T>`.

## Failure modes to watch

- **`render_blocker` feeds Discord** (`cli.py:6798-6810`). The extraction is
  behavior-preserving refactoring; write the characterization test first so a
  byte of drift fails loudly.
- **The golden shifts, it does not edit.** `tests/test_demo_script.py:44-52`
  compares the whole list. Regenerate it; do not hand-patch line 5. And do not
  "fix" a failure by reverting the render.
- **The idle poll is a performance contract.** `watch.py:659-660` documents the
  idle case as one PRAGMA and no query. A snapshot read placed outside the
  blocked-event branch silently makes every tick of every plan pay for it.
- **Don't touch `_trunc`.** Shared with the task-list path via `_task_msg_for`;
  budget inside the blocked renderer instead.
- **Keep the block short.** More lines per event brings the `--all --operator`
  dashboard closer to the Monitor's auto-stop, and an auto-stopped stream looks
  exactly like a quiet plan. The threshold is undocumented — stay tight rather
  than tuning to a number.
