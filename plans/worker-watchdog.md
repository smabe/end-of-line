# worker-watchdog — supervisor stuck-tool detection + cross-plan event delivery (closes #67 #68)

Workers wedge on tool calls (e.g. xcodebuild hanging on simulator HK auth) and
the operator has no signal until manual `ps`-tree archaeology. This plan adds
supervisor-side detection that emits `TOOL_STUCK` events plus a delivery path
so the operator's primary Claude session learns about them without the operator
having to ask "huh, is it stuck?"

Receipts: 2026-05-21 HealthDash debugging session (the worker on
`simplify-refactor-batch-1/ai-tools` sat in `xcodebuild test` for 9+ min with
zero token output; diagnosis required five surfaces — state.json, `ps` worker,
`pgrep -P` children, `/private/tmp/claude-501/.../tasks/*.output`, `cat`).

## Locked design decisions

### Detection (#67)

- **Mechanism:** supervisor-observed, not worker-emitted. Walk worker pid's
  process tree via `ps` + `pgrep -P` on each tick. No dependency on the
  undocumented `/private/tmp/claude-<uid>/.../tasks/*.output` path (verified
  via `claude-code-guide` — implementation internal, not a stability contract).
- **Trigger:** any descendant subprocess of the worker pid whose `etime` exceeds
  `stuck_tool_threshold_seconds` (default 300) AND whose CPU time accumulation
  is below `stuck_tool_cpu_threshold_seconds` (default 5) — i.e. has been
  alive a long time but hasn't done much work. CPU-time threshold replaces the
  output-mtime heuristic we'd planned earlier.
- **Filter:** skip descendants we expect to be long-lived and quiet
  (`github-mcp-server`, `xcodebuildmcp`, polling shells like
  `while kill -0 ... sleep`). Maintain an explicit allowlist in
  `supervisor.STUCK_TOOL_IGNORE_PREFIXES`.
- **Event constant:** `EVENT_TOOL_STUCK = "tool_stuck"` in `state.py` next to
  existing `EVENT_*` block. Fields: `plan`, `phase`, `worker_pid`,
  `descendant_pid`, `command` (first 200 chars), `elapsed_seconds`,
  `cpu_seconds`.
- **Deduplication:** record `current_claim.stuck_tool_emitted_at` after the
  first emit per descendant_pid so we don't re-emit on every tick. Clear when
  the descendant exits or claim is released.
- **No auto-kill.** Detection only. Operator owns intervention.

### Delivery (#68)

- **Two surfaces, both ship:**
  - **Inbox-hook (primary, known-good):** session-start hook surfaces unread
    `TOOL_STUCK` events at the next session start with the
    investigate-then-recommend instruction block.
  - **Long-running Monitor (additive, contingent on P6 experiment):** new
    `clu watch --operator` filter mode + session-start hook arms a Monitor
    on it. If P6 confirms Monitor survives the relevant lifecycle events,
    ships in P7. If not, P7 files a Claude Code feedback item and stays
    with inbox-hook as sole delivery.
- **`clu watch --operator` filter:** emits cross-plan events worth interrupting
  the operator for: `TOOL_STUCK`, `BLOCKER_NEW`, `ATTESTATION_REFUSED`,
  `STALL_GUARD_TRIPPED`. Suppresses per-plan TaskCreate/Update noise.
- **SKILL contract for the primary session on `TOOL_STUCK`:**
  - Investigate autonomously: `ps` worker tree, identify wedged subprocess.
  - Synthesize a recommendation with a kill plan.
  - Surface proactively (proactive text / SendUserFile).
  - **Never auto-intervene.** Kill / release-claim / force-complete require
    explicit operator approval per the operator-approval checkpoint.

## Non-goals

- **No worker-emitted PreToolUse/PostToolUse callbacks.** Bigger contract
  change, deferred. Supervisor-observed covers the case.
- **No multi-project union Monitor.** Per-project Monitor only. Cross-project
  view is a separate feature.
- **No quiet-hours deferral for TOOL_STUCK.** Wedges should surface immediately;
  the operator can mute if it becomes noisy.
- **No retry policy change.** A stuck tool still counts against `max_attempts`
  via existing lease-expiry semantics.
- **No Windows support.** macOS + Linux, like `reap_orphan_pid`.

## Files touched

- `end_of_line/state.py` — P1 NEW (`EVENT_TOOL_STUCK` constant,
  `current_claim.stuck_tool_emitted_at` slot). **API hotspot.**
- `end_of_line/supervisor.py` — P2 NEW (`walk_worker_tree` helper,
  `STUCK_TOOL_IGNORE_PREFIXES`), P3 modified (`detect_stuck_tools` + tick
  wiring). **API hotspot.**
- `end_of_line/config.py` — P3 modified — `stuck_tool_threshold_seconds: int = 300`,
  `stuck_tool_cpu_threshold_seconds: int = 5` fields on `ProjectConfig`.
- `end_of_line/cli.py` — P4 modified (`clu doctor` surfacing).
- `end_of_line/watch.py` — P4 modified (TOOL_STUCK event line), P7 modified
  (`--operator` filter mode if P6 green).
- `end_of_line/inbox_hook.py` — P5 modified — surface unread TOOL_STUCK events
  with investigate-then-recommend block. **API hotspot.**
- `tests/test_supervisor_stuck_tool.py` — P2-P3 NEW.
- `tests/test_watch_operator_filter.py` — P7 NEW (if Monitor path ships).
- `tests/test_inbox_hook_tool_stuck.py` — P5 NEW.
- `docs/operations.md` — P3 modified (config knobs), P6 modified (Monitor
  empirical findings), P5 modified (inbox-hook surface).
- `docs/reference.md` — P4 modified.
- `.claude/skills/clu-monitor/SKILL.md` — P5/P7 modified — SKILL contract
  for the primary session's TOOL_STUCK handling.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).

## Phase sequence

1. **P1 — event + state slot.** `EVENT_TOOL_STUCK` constant +
   `current_claim.stuck_tool_emitted_at` field + tests for state-roundtrip.
2. **P2 — process-tree walker.** Pure `walk_worker_tree(pid) -> list[Descendant]`
   helper using `subprocess.run(["ps", ...])`. Mocked `subprocess` tests for
   tree parsing, ignore-prefix filtering, missing-pid handling.
3. **P3 — supervisor wiring.** `detect_stuck_tools(state, cfg)` called late in
   the tick chain (after liveness, before dispatch). Threshold config, emit
   event with deduplication, integration test asserting tick → event after
   simulated wedge.
4. **P4 — surfaces.** `clu doctor` reports active stuck-tool detections;
   `clu watch` (incl. `--task-list`) emits `tool_stuck` lines. **Closes #67.**
5. **P5 — inbox-hook delivery.** Extend `inbox_hook.py` to surface unread
   `TOOL_STUCK` events at session start with the investigate-then-recommend
   instruction block. Update `.claude/skills/clu-monitor/SKILL.md` with the
   contract. Test the hook output shape. **First half of #68 closure.**
6. **P6 — Monitor experiment (research, no production code).** Arm 2 concurrent
   Monitors on a test `clu watch` stream from this session; trigger `/clear`
   (interactive — I'll ask the operator); observe whether the Monitor survives,
   gets disconnected, or stays orphaned. Document findings in
   `docs/operations.md`. Outcome dictates P7.
7. **P7 — long-running Monitor delivery (conditional).** If P6 confirms
   Monitor survives the needed lifecycle: ship `clu watch --operator` filter
   + session-start hook arming + SKILL contract. **Closes #68.** If P6
   shows fatal lifecycle issues: file a Claude Code feedback item, document
   inbox-hook as sole delivery, **still closes #68** with the inbox-hook path
   the issue explicitly allows.
8. **P8 — manual e2e on HealthData.** Dispatch a phase that wedges (or use a
   crafted `xcodebuild` that sleeps); confirm `clu doctor` shows it, watch
   emits `tool_stuck`, inbox-hook surfaces it at next session start (and
   Monitor if shipped). Document the verified flow in `docs/operations.md`.
