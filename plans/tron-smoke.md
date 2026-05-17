# tron-smoke — throwaway clu watch smoke test

One-phase touch plan to exercise `clu watch` end-to-end. The phase
googles facts about the 1982 film *Tron* and commits them. Whole
point is to watch state-event transitions stream through the Monitor
tool — the actual deliverable is the watch experience, not the file.

Discard after the smoke is done (`/post-ship` or `rm -rf plans/tron-smoke* tron-facts.md`).

## Per-phase done checklist

- TDD: skip (no logic, just a file write).
- Full suite green: still required (regression guard) —
  `python3 -m unittest discover -s tests`.
- Stage explicit path (`tron-facts.md`).
- Structured commit format.
- Call `clu complete --plan tron-smoke --phase trivia --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| trivia | `tron-smoke-trivia.md` | Web-search Tron facts → tron-facts.md → commit | 20m |
