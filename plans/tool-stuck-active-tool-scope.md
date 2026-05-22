# tool-stuck-active-tool-scope

## Goal
Replace `_emit_stuck_tool`'s "any descendant of the worker pid" walk
with "descendants spawned during the active Bash tool call," gated by
an activity marker the worker stamps via Claude Code's PreToolUse /
PostToolUse hooks. MCP servers spawned at session start are inherently
excluded — they're older than every tool-call window.

## Diagnosis
Same as the false-positive repro: `_emit_stuck_tool` walks every
descendant of `claim.pid` and applies a pure elapsed/cpu filter,
making no distinction between session-level MCP servers and active-
tool-call subprocesses. Confirmed by reading supervisor.py:329-396.

## Non-goals
- Don't touch the elapsed (300s) or CPU (5s) thresholds.
- Don't auto-kill — detection-only stays the v1 mandate (#67).
- Don't try to detect wedges *inside* MCP-server subtrees. If a real
  MCP gets a tool call and its bun child wedges, lease expiry catches
  it. We're not building per-MCP stuck-detection.
- Don't migrate existing in-flight state.json. The new marker is a
  new claim field; absent = no active tool call → no emissions.
- Don't auto-install the Claude Code hooks for operators. The hook is
  documented in the clu-phase SKILL; operators wire it themselves
  once. (Similar to how `clu install-skill` is opt-in.)
- Subagent tool calls (Task → Bash inside a subagent) are out of scope
  for v1. Lease expiry handles those. See failure modes.

## Files to touch
- `end_of_line/cli.py` — new `p_activity` subparser with two mutex
  flags `--start-bash` / `--end-bash` plus the standard worker callback
  args (`--plan`, `--phase`, `--token`, `--project`). Dispatch to
  `cmd_activity_start` / `cmd_activity_end`. Token-validated like every
  other worker callback.
- `end_of_line/state.py` — `current_claim.active_tool_started_at:
  ISO8601 | None` slot. Tiny helper pair: `mark_active_tool_start(claim,
  at)` and `clear_active_tool(claim)`. Document the slot in the schema
  comments next to `attestations` and `stuck_tool_emitted_at`.
- `end_of_line/supervisor.py` — rewrite `_emit_stuck_tool`'s descendant
  loop:
  ```
  active_at = claim.get("active_tool_started_at")
  if not active_at:
      return  # No active Bash call → nothing to be stuck in.
  active_age_s = (now - parse_iso(active_at)).total_seconds()
  for d in descendants:
      if d.elapsed_seconds > active_age_s + DRIFT_S:
          continue  # Older than the active call — session infra.
      ...existing elapsed/cpu/dedup checks...
  ```
  Delete `STUCK_TOOL_IGNORE_PATTERNS` and the `ignore_patterns` kwarg
  threading — the new filter subsumes them. `DRIFT_S = 5` for clock
  skew tolerance.
- `end_of_line/skills/clu-phase/SKILL.md` — three additions:
  1. Export `CLU_PLAN`, `CLU_PHASE`, `CLU_TOKEN`, `CLU_PROJECT` in the
     setup block (alongside the existing `STATE` / `WORKTREE_ROOT`
     exports) so hooks have the context they need.
  2. New section documenting the Claude Code hook snippet (PreToolUse
     + PostToolUse, matcher `Bash`, `clu activity --start-bash` /
     `--end-bash`, guarded with `[ -n "$CLU_TOKEN" ]` so non-clu
     sessions short-circuit).
  3. Update the gotcha list: "Worker without the hook installed → no
     `tool_stuck` events. Lease expiry (60 min) is still the safety
     net. Install the hook via the snippet above to enable early
     detection."
- `end_of_line/cli.py` (`clu doctor` → `_print_stuck_tool_health` at
  cli.py:2013, the second `walk_worker_tree` caller) — rewrite its
  descendant filter to use the same `active_tool_started_at` window
  the supervisor does. Add a new line: warn if the active claim is
  past `stuck_tool_threshold_seconds` without ever having
  `active_tool_started_at` set. Catches workers that forgot the hook.
- Tests:
  - `tests/test_activity_callback.py` (new) — start/end mutate state;
    token-validated; wrong token rejects with `ClaimMismatch`; idempotent
    (start over an active marker overwrites, doesn't error); lock-
    acquisition timeout fires cleanly (2s ceiling).
  - `tests/test_supervisor_stuck_tool.py` (existing — ~9 test methods
    need updating to inject `active_tool_started_at` in setup, since
    the new filter requires it to even consider descendants). Add the
    failing-first test that builds a ps fixture with the user's
    reported MCP shapes + `active_tool_started_at = 60s ago` +
    descendants with elapsed=400 (session age) → assert 0 events.
    Green-path test: same fixture + a wedged xcodebuild descendant
    spawned 305s ago + `active_tool_started_at = 320s ago` → assert
    1 event for xcodebuild only. Edge: descendant at the drift
    boundary.
  - `tests/test_state_stuck_tool.py` (existing — dedup map tests).
    The dedup helpers themselves don't change; tests stay green. Add
    helpers for `active_tool_started_at` if introduced (see "Files to
    touch" → state.py).

## Failure modes to anticipate
- **Worker without the hook installed.** Active marker never gets
  stamped → `_emit_stuck_tool` always returns early → zero `tool_stuck`
  events. This is a regression from current behavior (which fires
  false-positively but does fire on real wedges). Mitigation: lease
  expiry (60 min) is the same safety net the user's repro relied on
  anyway — all three phases completed inside their leases. `clu doctor`
  surfaces "no activity markers seen this session" to nudge installation.
- **Subagent Bash calls escape the marker.** Claude Code's Task tool
  spawns subagents in isolated context windows. Per the documented
  hook contract, subagent hooks do NOT automatically inherit the
  parent shell's env vars. The `[ -n "$CLU_TOKEN" ]` guard
  short-circuits the hook → subagent Bash calls don't stamp the
  marker → wedges inside subagents aren't caught by `tool_stuck`.
  Lease expiry remains the safety net for subagent wedges. Acceptable
  v1 limitation. Cross-reference the subagent gotcha in the
  clu-phase SKILL update.
- **Hook command hang freezes the worker's Bash call.** Claude Code
  documents no timeout for command hooks. If `clu activity` blocks
  on state-file lock contention (supervisor tick holding the lock,
  another hook acquiring it, etc.) the worker's Bash invocation
  hangs until the hook returns. Mitigation: `clu activity` MUST
  attempt the lock with a short timeout (2 seconds) and exit cleanly
  (non-zero) if it can't acquire; combined with the snippet's
  `|| true` this means a contended hook silently drops the marker
  update rather than blocking the worker.
- **Hook exit-2 trap.** Per the contract, a PreToolUse hook exiting
  2 *blocks* the tool call entirely. A bug in `clu activity` that
  emits exit 2 by mistake would freeze every Bash call in the
  worker. The `|| true` in the snippet defends against this. Document
  in the SKILL that the trailing `|| true` is load-bearing, not
  cosmetic — a future contributor must not "tidy up" the snippet by
  removing it.
- **Hook fires in non-clu Claude Code sessions.** `~/.claude/settings.json`
  is global; the snippet runs for every session on the machine.
  Guard: `[ -n "$CLU_TOKEN" ]` short-circuits when CLU_TOKEN is
  unset. For operators who don't want the global install, document
  the alternative: per-project `.claude/settings.json` (committed)
  or `.claude/settings.local.json` (gitignored). Hooks merge across
  scopes; both fire if both exist.
- **Hook fails silently.** Snippet uses `... 2>/dev/null || true` so a
  clu CLI error or stale lock doesn't block the worker's Bash call.
  Tradeoff: a silent miss leaves the marker stale. `clu doctor` catches
  the chronic case via the "no markers" warning.
- **Stale `active_tool_started_at` from a crashed Bash call.** If
  PostToolUse never fires (worker killed mid-tool), the marker stays
  set. Two mitigations:
  - PreToolUse always *overwrites* the marker — the next Bash call
    resets the window. So stale markers self-heal on the next call.
  - On lease expiry / claim release, `current_claim` is wiped entirely
    (already happens), so the marker dies with the claim.
- **Concurrent Bash calls (subagents).** Claude Code's Task tool can
  run subagents that fire their own Bash calls in parallel with the
  parent's. Subagent hooks inherit the parent's `CLU_TOKEN` from env
  → they stamp the same claim's marker → last-writer-wins. Two cases:
  - Subagent Bash finishes before parent Bash: parent's
    PostToolUse(end) wipes the marker prematurely. Window: parent's
    next PreToolUse re-stamps. Brief gap but self-healing.
  - Subagent Bash wedges while parent Bash finishes: parent fires
    PostToolUse(end), wipes the marker, the subagent's wedge becomes
    invisible to `tool_stuck`. Acceptable for v1 — lease expiry
    catches it eventually.
  - Long-term: a marker stack (`active_tool_started_at: list[iso]`)
    would handle this cleanly. Not v1.
- **Hook fires in non-clu Claude Code sessions.** Settings.json is
  global; the snippet runs for every session. Guard: `[ -n "$CLU_TOKEN" ]`
  short-circuits when CLU_TOKEN is unset (i.e. non-clu sessions).
  Verified safe.
- **State lock contention on every Bash call.** Each start/end hook
  briefly acquires the plan's state lock. Hundreds of Bash calls per
  phase × ~10ms per lock cycle ≈ seconds of total overhead. Acceptable;
  the worker is human-paced anyway.
- **Existing `tool_stuck` tests break.** The current tests construct
  claims without `active_tool_started_at` and expect emission on
  matching descendants. Under the new flow they all return early.
  Mitigation: bump tests to set the marker in setup. Mirrors how the
  heartbeat-threshold ship bumped backdate values.
- **DRIFT_S calibration.** Clock skew between the worker's marker
  stamp time and the supervisor's `now` (different process, possibly
  different time source). 5 seconds is generous for same-machine
  workers (typical clu setup). NTP-managed clocks shouldn't drift more
  than that. If we ever go cross-machine, revisit.
- **Hook env-var leakage.** `CLU_TOKEN` in settings.json hooks reads
  from env, not args. Tokens never appear in `ps` output. Good
  security posture.

## Done criteria
- `clu activity --start-bash` / `--end-bash` subcommands ship; token-
  validated; mutate `current_claim.active_tool_started_at`; lock
  acquisition has a 2-second timeout (so a contended hook drops the
  update silently rather than freezing the worker's Bash call).
- `_emit_stuck_tool` rewritten per the snippet above. Returns early
  when marker is unset. Filters descendants older than the active
  window.
- `STUCK_TOOL_IGNORE_PATTERNS` deleted (along with all references in
  `walk_worker_tree`, `_emit_stuck_tool`, and the `clu blockers` CLI
  path that also uses `walk_worker_tree`).
- Failing-first test reproduces the user's bug shape — MCP descendants
  + active marker set → 0 events.
- Green-path test: marker set, descendant spawned within the active
  window, low CPU, past elapsed threshold → 1 event.
- Edge test: descendant at exactly `active_age_s + DRIFT_S` boundary
  — drift tolerance behaves as documented.
- clu-phase SKILL updated with: env-var exports, hook snippet, and a
  gotcha line about the no-hook-installed case.
- `clu doctor` surfaces the no-marker warning.
- Full test suite green; single commit; structured message ties to
  the 12 false-positive repro receipt + cross-references the
  worker-watchdog ship (`0efd4c4..29ba1e6`) as the predecessor.

## Open questions
- **Rollout posture.** Two options:
  - **A. Hard cutover.** Ship the new flow as default; no fallback.
    Workers without hooks get zero `tool_stuck` events (lease expiry
    is the safety net). Simpler code; one fewer config flag.
  - **B. Config-gated migration.** New
    `stuck_tool_require_active_marker: bool = false` knob defaults
    to the old behavior (with false positives); operators flip to
    `true` after installing the hook. Future release flips default
    + deprecates fallback.
  - Recommend **A**: the user has one operator, hook installation is
    a one-time edit to `~/.claude/settings.json`. Flag gating adds
    permanent code surface for a single migration window. The
    false-positive repro shows the existing behavior is actively
    annoying — kill it now.
- **Test seam for the descendant ps fixture.** Existing
  `walk_worker_tree` already takes `ps_output` as a kwarg; new tests
  should reuse that, plus pass `claim` state with an injected
  `active_tool_started_at`. No new mocking infra needed.

## Parking lot
(empty)
