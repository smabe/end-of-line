# bundle-operator-subcommands — ship #1, #5, #6 in one plan

Three small additive `clu` subcommands in `end_of_line/cli.py` that
share a review context (argparse wiring, `tests.isolate_registry`,
one row each in `docs/reference.md`). Triage synthesis flagged them
as the strongest bundle candidate; this plan ships all three
sequentially as a multi-phase code-producing test of the dispatch
pipeline.

Order is simplest-first so failure isolation is cleanest if a phase
blocks:

1. **#6 — `clu prior-blocker --phase X`** — read-only state inspector
   for the worker skill. Smallest blast radius, no operator-facing
   semantics. Phase scope also includes updating
   `examples/clu-phase-skill.md` and `examples/fake-worker.sh` to use
   the helper (rule-of-three extraction — see project memory).
2. **#5 — `clu tick-all`** — registry iteration + per-plan error
   isolation. Also deletes `examples/clu-tick-all.sh` and rewires
   `examples/clu.tick.plist`. Slightly more surface than #6 (touches
   examples/ and the install path), but no design ambiguity.
3. **#1 — `clu logs <plan>`** — log file tailer. Trickiest of the
   three (state-aware default vs. directory fallback, `--follow`
   streaming). Punt `--follow` rotation semantics — ship the happy
   path; rotation is a follow-up if anyone complains.

## Per-phase done checklist (applies to all three)

- Failing test first (TDD per CLAUDE.md), then minimal implementation.
- `/simplify` after if the diff crosses 1 file or ~30 lines.
- Full test suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths only (no `git add -A`).
- Close the GitHub issue from the worker after the commit:
  ```bash
  gh issue close <N> --repo smabe/end-of-line --reason completed \
      --comment "Shipped in <short-sha>."
  ```
- `clu complete --commit <sha>` with the actual SHA.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| prior-blocker | `bundle-operator-subcommands-prior-blocker.md` | `clu prior-blocker` + update examples to use it (closes #6) | 30m |
| tick-all | `bundle-operator-subcommands-tick-all.md` | `clu tick-all` + retire `clu-tick-all.sh` (closes #5) | 30m |
| logs | `bundle-operator-subcommands-logs.md` | `clu logs <plan>` + `--follow` happy path (closes #1) | 45m |
