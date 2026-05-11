# clu — End User Empath Review

You asked me not to go easy. I won't. clu is a clever piece of plumbing, but as a *daily companion* it's not there yet. The MVP works; the *relationship* between user and clu is broken in three specific places that will make you stop trusting it inside a week.

## Verdict

**Would I use this daily as-is? No.** I'd use it for one plan, get burned by one of the cliffs below, and quietly disable the cron line "until I have time to fix it." Then I'd never fix it, because by then the friction has out-competed the benefit.

The core loop is sound — fresh context per phase, file-state, cron heartbeat, blockers as first-class. That's *the right architecture*. What's missing is the **operator surface**: the dozen tiny affordances that let a human stay in a trusting relationship with an autonomous thing running on their machine.

## The Three Cliffs (must-fix before v0.1 is daily-usable)

### Cliff 1 — The silent-stuck problem (scenarios 8, 12)

**The moment of dread:** You start a plan at 9am. At noon you check `clu status`. It says `Active: a-foundation (lease 12:24, attempt 1)`. Cool. At 3pm you check again. Same line. *Is the worker happily grinding, or did Claude segfault 90 seconds in and nothing's been alive since?* You have no idea. The state file doesn't distinguish "worker is healthy and thinking" from "worker died and the lease just hasn't expired yet."

A 30-minute lease with no heartbeat means up to 30 min of "looks fine, is dead." A user who's been burned by this twice will compulsively re-check status, which defeats the entire point of walking away.

**Fix (user-visible):** Workers ping `clu heartbeat` every couple minutes during their session. `clu status` shows `Active: a-foundation (last heartbeat 47s ago, lease 12:24)`. If heartbeat goes stale before the lease, status shows `stalled` in yellow. That's the difference between "I trust this" and "I babysit this."

Also: when status shows `halted`, the **reason** must be on the first screen, not buried in events. `Halted: phase a-foundation, 3/3 attempts failed (last exit code 1 at 14:22)`. Right now you grep events. That's a cliff.

### Cliff 2 — The "I can't answer in bed" problem (scenarios 1, 2, 13)

**The moment of dread:** 11:14pm. Phone buzzes. *"clu: blocked on watch-start-workout — Snapshot includes startDate or only kind?"* You're in bed. You don't remember what the phase even is. You can't tap to see the context or the options. Your choices: get up and open the laptop, ignore and feel guilty, or kill the notification and forget. Tomorrow morning the 24h SLA fires and the plan halts because you were *asleep*.

This single failure mode will train the user to dread notifications, which means they'll mute them, which means clu becomes useless.

**Fix (user-visible):**
- **Quiet hours by default.** `notify.quiet_hours: ["22:00", "08:00"]` in `.orchestrator.json`. Blockers during quiet hours queue silently and fire one digest at the start of the active window.
- **SLA clock pauses during quiet hours.** A 24h SLA that ticks through your sleep is just a 16h SLA that happens to fire at the worst moment.
- **The notification must contain the question, options, and a one-tap reply path.** iMessage is the right channel here — you already have the MCP wired. The notification *is* an iMessage from a `clu` handle; you reply `0` or `1` and a tiny listener calls `clu answer`. No SSH, no Termius, no laptop. That's the difference between "tolerable" and "I love this."

Until that exists, the 11pm scenario alone disqualifies daily use.

### Cliff 3 — The fleet-blindness problem (scenarios 3, 10, 11)

**The moment of dread:** You wake up Monday. Three plans across two projects. You open terminal. `clu status --project ... --plan ...` Wait, what was the slug? Was it the workout one or the watch one? You `ls` the plans dir. You type the command. You read four lines. Now do it twice more for the others. By plan #3 you've decided clu is "more friction than it saves."

`clu status` is built around the *single-plan* case. Reality is N plans across M projects, and the tool has no opinion about the fleet.

**Fix (user-visible):**
- **`clu` with no args** = a fleet dashboard. One line per known plan across all configured projects. Status, current phase, blocker count, last activity. Picks up `.orchestrator.json` from a registry (`~/.config/clu/projects.toml` or a discovery walk).
- **`clu pause` / `clu resume`** (with optional `--all` or `--plan`). Sets a `paused_until` timestamp in state, or globally in `~/.config/clu/clu.toml`. Cron still fires but the tick is a no-op. *Heavy hammer for "chill for 4 hours" is the current "disable cron" workaround — that's a cliff.*
- **`clu list`** as the most basic "what do I have." You can almost steal `status` for this; just make plan/project optional and have it list when omitted.

## The Nuisances (defer but track)

- **Cron-catchup notification flurry (scenario 7):** Coalesce within a tick window. If the supervisor dispatches+blocks+notifies inside one 30-second window, send *one* notification ("Plan moved A→B, B blocked, see clu status"), not three. Tolerable for now if Cliff 2 is fixed, because the iMessage channel naturally batches.
- **First-install hell (scenario 6):** `clu doctor` is *exactly* the right shape. PATH check, dispatch dry-run, notification adapter ping, state-dir writeable, `claude` resolvable, cron line syntactically valid. Defer until you've onboarded a second project — the bug surface is real but you personally won't hit it again.
- **Mid-flight intervention (scenario 4):** `clu skip --phase X` and `clu force-complete --phase X --commit <sha>` cover this. Hand-editing state.json is fine for v0.1 since this is rare; promote it when you do it twice.
- **Halted-plan recovery (scenario 5):** `clu retry` (resets attempts, clears `current_claim`) is a 5-line command and should ship with Cliff 1's fix because they hit at the same moment. Cheap.
- **Un-complete a phase (scenario 14):** Append-only event log makes this philosophically uncomfortable, but `clu rewind --to-phase X` that writes a `phase_rewound` event and recomputes derived state is the honest move. Defer — this is "twice a year" territory.
- **Victory ping (scenario 9):** Yes, please. One iMessage when `plan_completed` fires, with the commit chain. The cost of *omitting* this is the user never gets the dopamine that pays for the boredom of waiting. Cheap, high-ROI, but not a cliff.

## Fine-as-is (engineering would over-build)

- **Schema migrations** — halt on mismatch is correct. Don't build silent migrations.
- **Parallel scheduling within a plan** — sequential is right. Fan-out by running multiple cron lines is the correct mental model.
- **Per-project notification adapters** — a global iMessage channel is enough. Don't build a plugin system before you have two users.
- **A daemon instead of cron** — cron is the right call. Resist the urge to write a launchd plist with state.

## The Joys (keep)

- **`clu init` + cron line + walk away** is genuinely magical *when it works*. The promise is real.
- **Spawned tasks blocking plan completion** is the cleanest "tech debt never escapes" mechanic I've seen — that alone is worth shipping.
- **Append-only event log** means every weird moment is forensically debuggable. Keep this religion.
- **Workers are stateless / cold context** — this is the architectural insight that makes the whole thing work. Don't let some future "session reuse" optimization tempt you.

## The Trust Loop

The user walks away from clu **iff** all four of these are true at any given moment:

1. **I know clu is alive.** (Heartbeat visible in status; stale heartbeats surface.)
2. **I know clu will reach me when it needs me.** (Notification I can actually act on, from a channel I check.)
3. **I know clu will NOT reach me when it doesn't.** (Quiet hours; no flurry; SLA respects sleep.)
4. **I can ask "what's happening" in one keystroke.** (`clu` with no args = fleet view.)

clu today has #1 partially (you can see a claim, but not freshness), #2 not at all (stderr stub), #3 not at all (no quiet hours), and #4 not at all (requires --plan + --project). **Three of the four are missing.** That's why the answer to "would I use this daily" is no — not because the engine is wrong, but because the operator surface assumes the user is sitting at the terminal, and the whole *point* is that they aren't.

## Concrete UX additions, ranked by ROI

1. **iMessage notification adapter with reply→answer round-trip.** Fixes scenarios 1, 2, 7, 9, 13. Highest ROI in the whole list — unlocks daily use.
2. **Quiet hours + SLA-pause-during-quiet.** Tiny config, massive trust dividend.
3. **`clu` (no args) fleet dashboard + project auto-discovery.** Solves the slug-amnesia tax.
4. **Worker heartbeat + `clu status` shows freshness + stalled state.** Kills the silent-stuck dread.
5. **Halt-reason as first-class field in status.** One line, huge clarity win.
6. **`clu retry` and `clu pause`/`clu resume`.** Cheap commands that close real recovery gaps.
7. **Victory ping on plan completion.** Pure dopamine, near-zero cost.
8. **`clu doctor`.** Defer until second project onboarding.
9. **`clu rewind` / `clu force-complete`.** Defer until you actually need them twice.

Ship 1–4 and clu becomes the thing you described when you started building it. Everything else is polish.

— End of line.
