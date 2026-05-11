# clu-docs-reference — write the per-module reference

You are running as phase 3 of the `clu-docs` plan. Phase 1 produced
`docs/_outline.md` which dictates the reference layout — either a
single `docs/reference.md` with H2 per module, or a `docs/reference/`
directory with one file per module. Honor the outline's decision.

## Read first

- `docs/_outline.md` — your structural contract (especially the
  "Reference layout" and "Module list" sections)
- `docs/architecture.md` — for tone + the boundary that says "API
  details live here, not there"
- For each module in the outline's list: the `end_of_line/<module>.py`
  file itself, its docstring, public functions/classes, and any
  obviously load-bearing constants

You can grep `tests/test_<module>.py` if you need to understand a
function's contract from the test side.

## Produce

Whichever shape the outline picked. Per module, one section
containing:

1. **What it owns.** One sentence on the responsibility — the
   "elevator pitch" that distinguishes this module from its
   neighbors.

2. **Key types and functions.** A short list (~5-15 items) of the
   names a contributor or AI agent would call into. For each: a
   one-line summary. No full signatures unless the name doesn't
   make the call shape obvious.

3. **Invariants and gotchas.** The load-bearing rules that aren't
   obvious from the code: e.g. "every event-type write must go
   through `EVENT_*` constants" for `state.py`, or "lease releases
   must happen inside `st.mutate()` so the lock is held."

4. **See also.** Cross-links to other modules or docs when relevant.

## Modules to cover

Phase 1's outline is authoritative. As a sanity check, the package
contains at least: `cli`, `supervisor`, `state`, `dispatch`,
`notify`, `notify_inbound`, `fleet`, `registry`, `config`,
`plan_parser`. If the outline names something else, follow the
outline.

## Constraints

- Don't duplicate the state schema or callback table — those live in
  `contract.md`. Reference them by link.
- Don't duplicate flow descriptions from `architecture.md`. The
  reference is for "what is this thing called and what does it do,"
  not "how do the things work together."
- Each module section: 2-4 paragraphs + the bullet lists. The whole
  reference doc(s) should be navigable — if you're approaching 1000
  lines, you're padding.

## Done

Tests must pass before the commit:

```
python3 -m unittest discover -s tests
```

Then commit per project format and call:

```
clu complete --project <project> --plan clu-docs \
    --phase reference --token <token> --commit <sha>
```

If the outline chose a directory and you commit multiple new files in
one go, that's fine — one commit for the whole reference doc(s).
