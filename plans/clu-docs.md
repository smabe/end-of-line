# clu-docs — review clu, write a docs library, slim CLAUDE.md

clu has shipped through Day 2.9 (security, UX, notifications, halt) and
Day 3.0-3.3 (real worker dispatch end-to-end). The brainstorm docs that
informed Day 1 are stale as required reading. CLAUDE.md still references
them as "Read these before changing anything," which is wrong now.

This plan does three things in sequence:

1. Audit the codebase and decide a docs/ outline
2. Write the docs (architecture, reference, operations, conventions)
3. Rewrite CLAUDE.md as a concise pointer file and archive `brainstorm/`

Each phase is a narrow scope so a worker can ship it cleanly. Phase 1
produces an outline file (`docs/_outline.md`) that phases 2-5 read as
their structural contract.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| Audit + outline | `clu-docs-audit.md` | Walk the package, propose docs/ structure | 30m |
| Architecture | `clu-docs-architecture.md` | 1-page system shape doc | 30m |
| Reference | `clu-docs-reference.md` | Per-module reference per the outline | 1h |
| Operations | `clu-docs-operations.md` | macOS setup, LaunchAgents, troubleshooting | 30m |
| Conventions | `clu-docs-conventions.md` | TDD, /simplify, commits, slugs, tokens, events | 30m |
| CLAUDE.md + archive | `clu-docs-claude-md.md` | Rewrite CLAUDE.md + move brainstorm → docs/history | 45m |
