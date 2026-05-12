# bundle-operator-subcommands-prior-blocker — ship `clu prior-blocker`

You are phase `prior-blocker` of the `bundle-operator-subcommands`
plan. Implement GH issue #6: `clu prior-blocker --project P --plan S
--phase X` that exits 0 + prints the answer when an answered blocker
exists for the phase, non-zero otherwise. Then update the two example
worker scripts to call it instead of inline Python.

## Read first

- GH issue #6 for the full acceptance criteria:
  ```
  gh issue view 6 --repo smabe/end-of-line
  ```
- The triage comment on #6 (already posted; may have refined guidance):
  ```
  gh issue view 6 --repo smabe/end-of-line --comments \
      --jq '.comments[].body' --json comments
  ```
- `end_of_line/cli.py` — find an existing read-only subcommand
  (`status` at cli.py:105, `list` at cli.py:103) to mirror the
  argparse wiring style.
- `end_of_line/state.py` — `blockers` schema (the `answer` field, the
  `phase_id` field, the `id` field).
- `examples/clu-phase-skill.md` and `examples/fake-worker.sh` — the
  inline-Python prior-blocker detection that this command replaces.
- `tests/__init__.py` — `isolate_registry` is required in `setUp` for
  any test that runs `main([...])` (project convention; see CLAUDE.md).
- `tests/test_lifecycle.py` — a clean reference for the test-a-CLI-
  subcommand pattern (`setUp` → init → call `main([...])` → assert).

## Produce

1. **Failing test first.** New file `tests/test_prior_blocker.py` (or
   extend `test_worker_callbacks.py` if you think the locality is
   better there). Cover three branches:
   - Answered blocker exists for the phase → exit 0 + answer on stdout.
   - Unanswered blocker exists for the phase → non-zero + clear stderr.
   - No blocker at all for the phase → non-zero + clear stderr.
   Use `isolate_registry(self, tmp_path)` in `setUp`.
2. **Minimal implementation.** Add a `prior-blocker` subparser to
   `end_of_line/cli.py` and a handler that loads state, scans
   `blockers`, and prints/exits per the test spec. Choose an
   `ExitCode` — prefer reusing `ExitCode.UNKNOWN_TASK` rather than
   inventing a new constant unless it's a poor semantic fit; if you
   add a new one, justify it in the commit message.
3. **Wire the examples to use the helper.** Update
   `examples/clu-phase-skill.md` and `examples/fake-worker.sh` to
   replace the inline-Python detection with the new CLI invocation.
   Keep them functionally identical from the worker's POV.
4. **Run `/simplify`** if the diff spans >1 module or >30 lines (it
   probably does — cli.py + tests + 2 examples).
5. **Run the full suite.** `python3 -m unittest discover -s tests`
   must be 151+ tests, all green.
6. **Commit** (structured format from CLAUDE.md). Stage explicit paths:
   `git add end_of_line/cli.py tests/test_prior_blocker.py examples/clu-phase-skill.md examples/fake-worker.sh`.
7. **Close GH #6:**
   ```bash
   gh issue close 6 --repo smabe/end-of-line --reason completed \
       --comment "Shipped in $(git rev-parse --short HEAD)."
   ```

## Constraints

- Don't touch `~/.claude/skills/clu-phase/SKILL.md` — that's the
  symlinked user-level skill, governed by a separate repo. Only the
  in-repo `examples/clu-phase-skill.md` is in scope.
- Don't add a new test framework, pytest fixtures, or any third-party
  dep. `unittest` + `isolate_registry` per project convention.
- Don't add `--phase X` semantics beyond what the test specifies.
  Punt the answered-vs-unanswered nuance into the stderr message; the
  exit code is a simple binary.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan bundle-operator-subcommands --phase prior-blocker \
    --token <token> --commit <sha>
```

## Escape hatch

`clu block` if:
- The test suite fails on `main` before you've touched anything (env
  bug, not yours to fix). Capture the failure list and block with
  options "fix here", "skip phase", "halt plan".
- You discover an existing `prior-blocker` or near-identical helper
  somewhere — semantic conflict needs operator call.
- The example-update step would require touching the symlinked
  user-level skill to be coherent (it shouldn't — verify first).
