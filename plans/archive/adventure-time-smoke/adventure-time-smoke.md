# adventure-time-smoke — throwaway clu watch --task-list smoke

Two-phase touch plan to exercise the just-shipped `clu watch --task-list`
auto-arm end-to-end. Worker googles Adventure Time facts in two passes
(characters, then lore) and writes them to `adventure-time-facts.md`.
The real deliverable is the watch protocol: TASK_CREATE for parent +
2 children at startup, TASK_UPDATE per phase transition through Monitor.

Validates that:
1. `/clu-plan` auto-arms with `--task-list` (just shipped in clu-watch-task-list)
2. Bootstrap emits 3 TASK_CREATE lines before any phase fires
3. Each phase transition produces a parseable TASK_UPDATE
4. Claude's TaskCreate UI mirrors execution

Discard after the smoke: `/post-ship` lite (rm facts file, archive
plans, `clu archive` worktree).

## Per-phase done checklist

- TDD: skip (no logic, just markdown content).
- /simplify: skip (single-file content commit).
- Full suite green still required (regression guard).
- Stage explicit path (`adventure-time-facts.md`).
- Structured commit format.
- Call `clu complete --plan adventure-time-smoke --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| characters | `adventure-time-smoke-characters.md` | Web-search character facts → ## Characters section | 15m |
| lore | `adventure-time-smoke-lore.md` | Web-search world/production facts → ## World & Production section | 15m |
