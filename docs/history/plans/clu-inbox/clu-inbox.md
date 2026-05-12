# clu-inbox — hook+inbox in-session signaling (replaces broken /schedule mechanism)

Closes [#20](https://github.com/smabe/end-of-line/issues/20). Fixes the architectural mistake from #19's `/clu-monitor` skill. Ships in-session signaling so a Claude Code session sees clu events automatically on the user's next message — no manual summary, no per-fire cost, no remote-agent quotas.

## Goal

After this plan ships, the operator workflow becomes:

```
1. Queue plans: `clu queue add foo bar baz`
2. Walk away. Get iMessage on phone when something happens.
3. Walk back to Claude. Type literally anything ("ok", "next", "/post-ship", "hey").
4. Claude already knows: "Plan foo completed at 14:00 (commit abc). Plan bar
   halted at 14:15 (max attempts on phase impl). Want me to retry bar with
   `clu retry`, then run /post-ship for foo?"
5. Operator confirms or redirects. Zero manual context-summarizing.
```

The trick: clu writes events to `~/.config/clu/inbox/`. A UserPromptSubmit hook (installed via `clu install-hook`) reads the inbox at the start of every Claude turn, filters to events tagged with the current project_root, and emits them as a system reminder via `hookSpecificOutput.additionalContext`. Claude sees the reminder before processing the user's message.

## Locked design (do NOT re-litigate)

Verified end-to-end via claude-code-guide consultation (2026-05-12). Full transcript in #20's body. Summary:

- **Hook event**: `UserPromptSubmit`. Fires before Claude processes the user's prompt. Right choice — `SessionStart` is one-shot, `Stop` is post-response.
- **Hook output mechanism**: stdout JSON with shape `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "..."}}`. The `additionalContext` lands in Claude's context as a system reminder. Max 10K chars (well within our 50-event-summary budget).
- **Latency budget**: ≤500ms. Our use case (read ~10-50 small JSON files, filter, format) is comfortable.
- **Install target**: `~/.claude/settings.json` (user-level, global). Existing user hooks at the operator's machine use nested-array format (`{hooks: [{type, command, timeout}]}`); installer must preserve that format if present.
- **Failure modes**: non-zero exit shows stderr line to user but doesn't block; exit 2 blocks (don't use). `--continue` replays hook output from transcript (fine — clu events are historical facts).

### Inbox shape

- Directory: `~/.config/clu/inbox/` (unprocessed) + `~/.config/clu/inbox/processed/` (already-surfaced)
- One JSON file per event, name `<utc_iso>-<kind>-<short_id>.json`:
  ```json
  {
    "id": "evt-<8-char-hash>",
    "schema_version": 1,
    "type": "halted | blocked | plan_completed | queue_skipped | queue_corrupt | queue_repaired | queue_repair_failed | stuck_blocker | stalled_claim",
    "plan_slug": "...",
    "project_root": "/abs/path",
    "timestamp": "ISO UTC",
    "summary": "one-line human summary",
    "details": { "...kind-specific...": "..." }
  }
  ```
- Mark-and-sweep dedup: after surfacing, hook `mv`s the file to `processed/`. Operator can clear by deleting either dir.

### Marker file evolves (schema v1 → v2)

- v1 (current, broken): `{schema_version: 1, scheduled_at, schedule_id, cadence}`.
- v2: `{schema_version: 2, hook_installed_at, hook_path, settings_json_path}`.
- Migration: v1 markers treated as "needs reinstall" — `is_scheduled` returns False, `/clu-monitor` re-runs the install. No data loss (schedule_id was never used by anything other than the broken skill).

### Settings.json install semantics

- Idempotent. Detect existing entry by absolute hook_path match.
- Preserve existing format: nested-array `{matcher?, hooks: [{type, command, timeout?}]}` (operator's current style) OR flat-array `{type, command}` (alternate style the docs show).
- Atomic write: `tmp + fsync + rename` (same primitive used elsewhere in clu).
- On malformed settings.json: refuse to install with a clear message — don't try to repair.

### Two new notification kinds

Gap-fills from prior conversation:
- **Stuck blocker re-ping** (`stuck_blocker`): supervisor tick checks blockers with `consumed: false` AND `(now - created_at) > 30min` AND `(now - last_repinged_at or created_at) > 30min`. Fires notify + inbox event. Stamps `last_repinged_at`. Continues every 30min until consumed.
- **Stalled claim transition** (`stalled_claim`): supervisor tick detects `current_claim` with `lease_expires < now` AND `status == RUNNING`. Fires notify + inbox event ONCE per transition (stamp `stalled_notified: true` on the claim to prevent re-firing on subsequent ticks).

### Out of scope (explicit)

- LaunchAgent-based monitor (deferred; iMessage covers most "operator fully offline" cases).
- Pluggable notification backends (#11).
- Custom MCP server for true push-into-session (would require Claude session to subscribe; hook pull pattern is simpler).
- Cross-machine inbox reconciliation.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| inbox-hook | `clu-inbox-inbox-hook.md` | New `end_of_line/inbox.py` primitive (write/read/mark_processed/list_for_project). New `end_of_line/hooks/clu_inbox_surface.py` script. New CLI: `clu install-hook` / `clu uninstall-hook` (idempotent, format-preserving). `notify.py` wires to inbox alongside iMessage send. Skill rewrite: workflow becomes "run `clu install-hook`". Marker schema v1 → v2 with migration. ~17 new tests covering primitives, hook script, install/uninstall, settings.json format preservation, marker migration. | 3.5h |
| gap-notifications | `clu-inbox-gap-notifications.md` | Two new notification kinds: `stuck_blocker` (30-min interval re-ping until consumed) + `stalled_claim` (one-shot on transition). Supervisor tick chain extension at appropriate priority slots. State.py: `last_repinged_at` on blockers, `stalled_notified` on claims. New event kinds: `EVENT_STUCK_BLOCKER_REPINGED`, `EVENT_STALLED_NOTIFIED`. ~8 tests. | 1.5h |
| docs | `clu-inbox-docs.md` | Rewrite `docs/operations.md` § Background monitoring (the section shipped in #19's docs phase describes the broken mechanism). Rewrite `docs/contract.md` schemas (inbox JSON + marker v2). Update README. Refresh project CLAUDE.md status section. Add a manual smoke step ("queue a smoke plan, wait for completion, type 'ok' in Claude, verify the inbox event surfaces"). | 1h |

Total est: ~6h across 3 sessions.

## Failure modes to anticipate

- **Settings.json hook format inconsistency.** The operator's existing settings.json uses nested-array format. The Claude Code docs show both nested AND flat formats are valid. Installer MUST detect the existing format and preserve it. Test both branches explicitly.
- **Hook script latency under load.** If the inbox accumulates 100s of events (operator AFK for a week), the hook could exceed 500ms. Mitigation: cap the listing to the most recent N events (e.g. 20), surface "+ 47 older events" as a footer. Test the cap.
- **Hook script crash → user sees stderr.** Errors don't block (good), but a noisy crash message would startle the operator. Wrap the script in a try/except that logs to a dedicated log file and exits 0 cleanly on any exception. Better to silently fail than alarm.
- **`additionalContext` exceeds 10K chars.** Same mitigation as the latency case (cap event count). Detect at write time and truncate the summary with "(+ N more events not shown — run `clu inbox` to see all)".
- **Stuck-blocker re-ping spam during quiet hours.** The existing `notify.in_quiet_window` already gates this for iMessage. But inbox writes happen unconditionally (the inbox is for the next Claude turn, not for waking the operator). Document the asymmetry: notifications respect quiet hours; inbox events don't.
- **Concurrent inbox writes.** Two simultaneous events from the supervisor could race on file naming. Mitigation: use a 16-char random suffix in filenames, not a monotonic counter. Race-free by construction.
- **Settings.json corruption mid-write.** Atomic tmp+rename prevents partial writes. But if the operator hand-edits during install, we could lose their edits. Mitigation: read+write under a `.lock` file (mirror state.locked_json pattern).
- **CWD detection failures in hook script.** If the user starts Claude in a non-repo directory, `git rev-parse --show-toplevel` fails. Fallback: use `os.getcwd()` for project_root match. Hook gracefully returns "no inbox events for current dir" rather than crashing.
- **`clu install-hook` run by a worker.** Workers shouldn't install hooks. Detect non-TTY context and refuse (consistent with the TTY gate from #19's CLI hints). Print "install-hook requires interactive shell" and exit non-zero.
- **Skill markdown re-installation drift.** The current `/clu-monitor` SKILL.md is the broken /schedule version. The pivot rewrites it. Operators who installed it pre-fix would re-run `clu install-skill --force` after this ships. Document in the operations.md migration note.
- **Stalled-claim detection in tick chain.** The tick chain is first-match-wins through 8 priority slots (per CLAUDE.md). Adding stalled-claim detection must NOT preempt higher-priority slots (blocker resumption, halt, etc.). Slot it between "no claim" and "dispatch" priorities. Test ordering explicitly.
- **Migration race.** If the marker is v1 and `is_scheduled()` returns False, `/clu-monitor` will try to install the hook. If install succeeds before the v1 marker is cleared, we have stale v1 sitting next to fresh v2. Mitigation: install writes a NEW marker that overwrites the v1 path atomically. Test the v1 → v2 transition.
