# clu-docs-audit — walk the package and propose a docs/ outline

You are running as the first phase of the `clu-docs` plan. The
subsequent phases (architecture, reference, operations, conventions,
claude-md) will use the outline you produce as their structural
contract. Get this right; downstream phases inherit your decisions.

## Read first

- `CLAUDE.md` — current project conventions and status block
- `README.md` — public-facing description of clu, post-rewrite
- `docs/contract.md` — existing state schema + worker callback doc
- Every `end_of_line/*.py` — survey the public surface of each module
- `tests/` — get a sense of what tests cover (informs reference doc structure)

Don't read `brainstorm/` for this — those are stale design docs that
phase 6 will archive.

## Produce

A single file at `docs/_outline.md` describing the docs library
structure the next phases will fill in. It must answer:

1. **Reference layout.** Single `docs/reference.md` with one H2 per
   module, or a `docs/reference/` directory with one file per module?
   Decide based on what you actually see in the codebase. Defaults
   matter — if modules are roughly similar in size and concept (~250
   lines each, all stateless utility plus one orchestrator entry), a
   single file with module sections is easier to grep and updates as
   one diff. Pick a directory only if you see a real reason — e.g. one
   module is much larger and would dominate, or modules have wildly
   different audiences. Document the reason in the outline.

2. **Module list.** Enumerate every `end_of_line/*.py` module that
   should get reference coverage, with a one-sentence summary of its
   responsibility. This list is what phase 3 will turn into the
   reference doc(s).

3. **Cross-document boundaries.** For each topic below, state which
   doc owns it (so phase 2-5 workers don't duplicate or fight):
   - State schema → `contract.md` (already exists, keep)
   - Worker callback contract → `contract.md`
   - System diagram + dataflow → `architecture.md`
   - Per-module API and invariants → `reference.md`
   - macOS install / LaunchAgent / FDA / troubleshooting → `operations.md`
   - TDD, /simplify, commit format, slug rules, event constants, token discipline → `conventions.md`

4. **Anything missing?** If you discover a topic the proposed
   five-file layout doesn't fit (e.g. a security model that deserves
   its own doc), flag it in a "Proposed additions" section. Subsequent
   phases will read the outline; mentioning it here is enough — don't
   create new sub-plan files.

## Constraints

- Do NOT write `architecture.md`, `reference.md`, `operations.md`, or
  `conventions.md` — those are later phases. The outline is the only
  artifact you produce.
- Do NOT modify code, `CLAUDE.md`, `README.md`, or any existing docs.
- The outline file is short — probably 50-100 lines, no more. It's a
  blueprint, not a draft.

## Done

```
clu complete --project <project> --plan clu-docs \
    --phase audit --token <token> --commit <sha>
```

Commit message follows the project's structured commit format from
CLAUDE.md.

## Escape hatch

If during the audit you discover something genuinely surprising about
the codebase — a topic that doesn't fit the planned five-file layout,
a structural concern that makes the whole docs plan wrong, etc. — use
`clu block` with a focused question so the operator can decide before
the rest of the plan barrels forward with a bad outline.
