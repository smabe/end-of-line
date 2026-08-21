# Resume — worker-idle-115 (briefed 2026-08-21, after blocker-surfacing)

```
Blocker surfacing shipped — `415200b` (inbox retired, quiet hours off, docs
drift closed), `ab2a9aa`/`00f1178` (session-start-blockers), `742e014`/`f134003`
(mid-session-blockers). Main is at `56ee32e`, 2381/2381 green, basedpyright clean.

Cleanup pass done: plans auto-archived by `clu ship` to
plans/archive/session-start-blockers/ and plans/archive/mid-session-blockers/,
memory rewritten as a shipped record, #97/#111/#114 closed, follow-ups filed as
#115–#119.

Next steps available (pick one or propose your own):
- #115: worker_idle's Anthropic-socket suppression can never fire — three
  verified defects: `lsof -p PID -i` is an OR (returns every socket on the
  machine), claim.pid is the PTY shim which holds no sockets by design, and the
  string "anthropic" never appears because those IPs have no matching PTR record.
- #116: the monitor marker and settings.json are two sources of truth for one
  fact, currently diverged — clu nags to install a hook that IS installed, on
  every TTY command.
- #117: three blocker renderers disagree on truncation and on which `clu answer`
  command they print — the Discord one still names the unscoped form.
- #118: watch._DEFAULT_VISIBLE is dead — never read, but its comments read as a
  decision record and will mislead the next person adding an event type.
- #119: the task-list `msg=` cap is documented and implemented at 100, asserted
  at 120, pinned by nothing.
- #109: clu watch reports every attempt as "attempt 1" (see
  plans/RESUME-watch-attempt-count.md for the fuller briefing).

Recommended next pickup: #115, optionally batched with #116 — they are the same
failure in two places. Both make clu assert something false about its own state,
repeatedly, which is how an operator learns to filter out its warnings. #115 is
the more urgent: every phase runs /code-review, code-review works in subagents,
subagents cannot stamp `active_tool_started_at` (documented at
skills/clu-phase/SKILL.md:350) — so the idle watchdog false-fires on essentially
every phase of every plan. It already did once during this session's own run.
They touch disjoint files (supervisor.py vs monitor.py + cli.py), so a two-phase
plan works cleanly.

Read first if continuing from this work:
- ~/.claude/projects/-Users-smabe-projects-end-of-line/memory/project_blocker_surfacing.md
- gh issue view 115  (and 116 if batching)
- end_of_line/supervisor.py:638-731 — `_emit_worker_idle`, including the socket
  suppression at :715-731 and the tree_pids set at :677 that it fails to reuse

Open questions or blockers: #115's fix mechanism is deliberately unsettled — the
issue lists three candidates (repair the lsof probe, stamp activity around
SubagentStart/SubagentStop, or use cumulative CPU delta) without picking one.
The operator said they want to decide that while working it, so do NOT scope a
plan that assumes the socket approach.
```
