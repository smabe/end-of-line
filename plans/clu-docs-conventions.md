# clu-docs-conventions — write docs/conventions.md

You are running as phase 5 of the `clu-docs` plan. The conventions
doc is what an AI agent or contributor reads to understand the
project-specific rules that aren't visible from any single file.

## Read first

- `docs/_outline.md` — boundary contract
- `CLAUDE.md` — the canonical source of the conventions, especially
  the "Conventions (mandatory)" block and "What NOT to do" block.
  Your job is to expand each rule into a paragraph with rationale +
  an example, not just paraphrase.
- `tests/test_*.py` — to ground the TDD examples
- `end_of_line/cli.py` — `ExitCode`, `_die`, `@_translate_claim_mismatch`
- `end_of_line/state.py` — `validate_slug`, `EVENT_*` constants,
  `assert_claim_match`, `release_claim`
- `tests/__init__.py` — `isolate_registry` helper
- Recent commit messages (`git log --oneline -10`) — for the
  structured commit format example

## Produce

`docs/conventions.md` — a per-rule explanation, in roughly this
order:

1. **TDD discipline.** Write failing tests first. AAA. Factory
   helpers (point at `tests/test_worker_callbacks.py` for the setUp
   template). After multi-file changes, full suite must pass before
   commit.

2. **`/simplify` after non-trivial work.** What it does, when to run
   it, why (Day-1 simplify pass cut test runtime in half + collapsed
   9 error sites into `_die()` — pays its own rent).

3. **Structured commit format.** Show the template (Title / Why /
   What's new / Under the hood / Tests / Co-Authored-By). Reference
   one good example commit by SHA.

4. **`ExitCode` enum, never bare ints.** Example: `return
   _die(ExitCode.CLAIM_MISMATCH, str(exc))`.

5. **Worker callback contract.** Every worker-side CLI command
   requires `--token`. Use `@_translate_claim_mismatch` so
   `ClaimMismatch` propagates as `ExitCode.CLAIM_MISMATCH`. Why this
   is load-bearing.

6. **Slug validation.** `validate_slug` regex, what counts as a
   slug, mandatory check before any filesystem path. Don't bypass.

7. **Event type constants.** Never write raw event-type strings.
   `EVENT_*` constants in `state.py`. A typo silently breaks
   projections like `completed_phase_ids`.

8. **Test isolation helper.** `tests.isolate_registry(self, tmp)` is
   mandatory for any test that touches the host registry. Why
   (avoids polluting `~/.config/clu/registry.json`).

9. **Atomic state mutations.** Always through `with st.mutate(path)
   as data:`. Don't `st.load` + `st.save_atomic` separately when you
   need both read and write — the lock window is the point.

10. **"What NOT to do" rules.** From CLAUDE.md:
    - No SwiftUI / iOS code — pure Python
    - No `git add -A` — stage explicit paths
    - No third-party deps without a real justification — stdlib has
      everything we need
    - Don't break "one tick = one action" in `supervisor.tick`

11. **AI-agent specific notes.** This whole doc is for AI agents in
    practice. End with a short paragraph: when running as a worker
    via `/clu-phase`, follow these conventions. When iterating with
    an operator, follow these conventions. The conventions don't
    change based on who's at the keyboard.

## Constraints

- Each rule gets a paragraph (~3-6 sentences) + example. Not a
  one-liner. The point is rationale.
- No code-style rules (line length, import order). Those are obvious
  from the codebase. The conventions doc is for non-obvious project
  policies.
- Don't duplicate `contract.md` (state schema) or `reference.md`
  (per-module API). Reference them by link when a convention is
  about a specific module.

## Done

```
python3 -m unittest discover -s tests
```

Then commit + complete.
