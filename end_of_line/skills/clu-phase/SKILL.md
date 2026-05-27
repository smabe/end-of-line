---
name: clu-phase
description: Execute one phase of a clu-managed plan. Invoked by clu's dispatch from cron — never by the user directly. Reads the phase's sub-plan file, does the work, and calls `clu complete` or `clu block` with the worker token before exiting. The contract is load-bearing: never exit without calling one of those two callbacks, or the plan will lease-expire and eventually halt.
user_invocable: false
---

## You are a clu phase worker

This skill is fired by `claude --print '/clu-phase <plan_slug> <phase_id> <token> <state_file>'` — clu's dispatch.command launches one Claude session per phase, in headless `--print` mode, with the working directory set to **the dispatch cwd**: the canonical project root if the plan was init'd without `--worktree`, OR the plan's worktree root (e.g. `/Users/me/projects/foo-<slug>`) if it was. **They are not always the same place.** See "Where you are" below.

You are not interacting with a human. The user is asleep / at lunch / not at their terminal. Output goes to a log file at `<project>/plans/.orchestrator/logs/<phase>.<token>.log`. The only way to communicate back to the user is via `clu block` (which sends an iMessage they can reply to).

Read this entire skill before doing anything.

## The four arguments

The skill is invoked with four positional arguments:

1. `<plan_slug>` — the clu plan slug (e.g. `multi-plan-routing`)
2. `<phase_id>` — the phase to execute (e.g. `routing-impl`)
3. `<token>` — your worker claim token (e.g. `session-abc123...`). MUST be passed to every `clu` callback. Forged tokens are rejected.
4. `<state_file>` — absolute path to the plan's state.json (e.g. `/Users/me/projects/foo/plans/.orchestrator/multi-plan-routing.state.json`). Use this to inspect prior history.

Capture them from your prompt before any other work.

## The sacred contract

**You MUST call one of these two CLI commands before exiting:**

```bash
# On success — phase done. Pass every commit SHA you made.
clu complete --project <project_root> --plan <plan_slug> \
    --phase <phase_id> --token <token> \
    [--commit <sha1> --commit <sha2> ...]

# When you need user input or hit a wall you can't break through alone.
clu block --project <project_root> --plan <plan_slug> \
    --phase <phase_id> --token <token> \
    --question "Short, specific question" \
    --option "First choice" --option "Second choice" \
    [--option "Third"] [--context "additional context"]
```

If you exit without calling one of these:
- The 60-minute lease will eventually expire (`lease_expired` event fires)
- The phase's attempts counter ticks up
- After 3 attempts, the plan halts on max-attempts and the user gets a halt iMessage
- This is the worst outcome — you've burned an attempt without progress and forced the user to clean up

**Repeat: never exit without calling complete or block.** Even if the work was a no-op (already done), call `clu complete`. Even if you're stuck and confused, call `clu block` with a clear question instead of just quitting.

## Where you are (CRITICAL — git ops vs clu ops)

clu supports per-plan git worktrees (see `clu init --worktree`). When in use, the dispatch cwd is the **worktree root** (e.g. `/Users/me/projects/foo-routing-impl`) on the plan's dedicated branch (`clu/routing-impl`). The **canonical project root** (`/Users/me/projects/foo`) is a different directory on a different branch (usually `main`).

These two roots play different roles in your work:

| You need to... | Use |
|---|---|
| `git add` / `git commit` / `git diff` — code changes for the phase | **`$WORKTREE_ROOT`** (= your current `pwd`). The dispatch put you here on the right branch. |
| Read or modify files for this phase's diff | **`$WORKTREE_ROOT`** (relative paths work — your cwd IS the worktree). |
| Run `clu complete / block / heartbeat / prior-blocker` — pass `--project ...` | **`$PROJECT_ROOT`** (canonical). clu's state file + registry live here, not in the worktree. |
| Read `$STATE` directly (it's an absolute path) | No `cd` needed. |

**The silent-clobber failure mode:** if you `cd $PROJECT_ROOT && git commit`, your commit lands on the canonical project's current branch (usually `main`) instead of `clu/<slug>`. clu has no way to detect or correct this — the commit looks successful, `clu complete` accepts it, but the next phase dispatches off a stale branch tip and the operator has to reconcile branches by hand. **This actually happens** — see issue #36 for the post-mortem. Don't be the worker that recurs it.

Compute and emit both roots at the start so this is visible in your log:

```bash
WORKTREE_ROOT=$(pwd)
PROJECT_ROOT=$(cd "$(dirname "$STATE")/../.." && pwd)
if [ "$WORKTREE_ROOT" != "$PROJECT_ROOT" ]; then
    echo "dispatched in worktree: $WORKTREE_ROOT"
    echo "canonical project root: $PROJECT_ROOT"
fi
```

If `WORKTREE_ROOT != PROJECT_ROOT`, treat it as a flashing-red sign: **every `git` command stays in `$WORKTREE_ROOT`.** If you `cd` for any reason (to read a canonical-only file, run a clu CLI command), either `cd "$WORKTREE_ROOT"` back before the next git op, or use `git -C "$WORKTREE_ROOT" ...` explicitly. Verify by running `git rev-parse --abbrev-ref HEAD` immediately before any `git commit` — the answer MUST be the `clu/<slug>` branch, not `main`.

## Resume-after-answer

If a prior blocker on this phase has been answered, clu has re-dispatched you to continue. Ask clu:

```bash
if answer=$(clu prior-blocker --project "$PROJECT_ROOT" --plan "$PLAN" --phase "$PHASE"); then
    echo "resuming with prior answer: $answer"
fi
```

`clu prior-blocker` exits 0 and prints the answer text on stdout when an answered blocker exists for the phase; exits non-zero (no output on stdout) when there isn't one.

If you see an answered blocker, that means: you asked a question previously, the user replied, and now you're resuming with their choice in hand. Use the answer to inform the rest of the work, then complete (or block again on the next thing).

## Step-by-step protocol

1. **Capture the four arguments** from the prompt. Call them `PLAN`, `PHASE`, `TOKEN`, `STATE`. Compute both roots (see "Where you are" above):
   ```bash
   WORKTREE_ROOT=$(pwd)
   PROJECT_ROOT=$(cd "$(dirname "$STATE")/../.." && pwd)
   ```
   If they differ, you are in a worktree. Git ops stay in `$WORKTREE_ROOT`; `clu --project` calls take `$PROJECT_ROOT`.

2. **Arm the heartbeat ticker.** Long phases that don't ping `clu heartbeat` look identical to hung workers — `clu status` reports `STALLED` and the supervisor's gap-fill notifications fire. Start a background loop that pings every 2 minutes, tied to the worker's parent PID so it self-terminates if the worker is SIGKILLed/OOMed (issue #72). The EXIT trap is the fast-cleanup path for graceful exits. The loop counts consecutive failures: on the 3rd in a row (~6 min) it calls `clu notify-heartbeat-failure` so the operator learns the loop is broken before lease expiry surfaces it. stderr from each `clu heartbeat` call is tee'd to a sidecar log for post-mortem inspection:
   ```bash
   WORKER_PID=$PPID
   FAILS=0
   ERRLOG="$(dirname "$STATE")/logs/heartbeat-errors.$PLAN.$PHASE.log"
   mkdir -p "$(dirname "$ERRLOG")"
   ( while kill -0 $WORKER_PID 2>/dev/null; do
       if clu heartbeat --project "$PROJECT_ROOT" --plan "$PLAN" \
               --phase "$PHASE" --token "$TOKEN" >/dev/null 2>>"$ERRLOG"; then
           FAILS=0
       else
           FAILS=$((FAILS + 1))
           if [ "$FAILS" -eq 3 ]; then
               clu notify-heartbeat-failure --project "$PROJECT_ROOT" \
                   --plan "$PLAN" --phase "$PHASE" --token "$TOKEN" \
                   --log "$ERRLOG" >/dev/null 2>&1 || true
           fi
       fi
       sleep 120
     done ) &
   HEARTBEAT_PID=$!
   trap "kill $HEARTBEAT_PID 2>/dev/null" EXIT
   ```
   The 2-minute interval is well inside the heartbeat threshold (derived from lease TTL: `min(25, max(15, lease_ttl//2))`, so 25 min at the default 60-min lease) and loose enough to not flood state.json writes. **Both terminators are load-bearing**: `kill -0 $WORKER_PID` catches death-by-signal where the EXIT trap doesn't fire (SIGKILL, OOM, crash); the EXIT trap catches graceful exits faster than the next `sleep 120` would notice. Supervisor-side detection (`_detect_dead_pid`) is the third layer that catches the worst case where both fail. The 3-strike self-report adds a fourth layer: if all three of those silently miss a wedge, the operator gets an iMessage when the bash loop itself stops landing heartbeats. A fifth layer, `_emit_worker_idle` (supervisor-side, wedge-watchdogs phase 2), catches the orthogonal "wedged mid-API-stream" case: PID alive, no Bash tool active, CPU ≤1% over ≥10min, no open Anthropic socket. That's the failure mode the existing four didn't catch.

2b. **Export the activity-hook environment.** The stuck-tool detector (`clu doctor`, supervisor gap-fill) scopes its process-tree walk to descendants spawned during the current Bash tool call. That window is stamped by a Claude Code PreToolUse/PostToolUse hook that calls `clu activity --start-bash` / `--end-bash`. The hook reads its context from env, so export the four vars here so they propagate to child processes (including Claude Code and its hooks):
   ```bash
   export CLU_PLAN="$PLAN" CLU_PHASE="$PHASE" CLU_TOKEN="$TOKEN" CLU_PROJECT="$PROJECT_ROOT"
   ```
   The hook itself is operator-installed (one-time, in `~/.claude/settings.json` or per-project `.claude/settings.json` — see "Activity hook (operator setup)" at the bottom of this SKILL). Workers without the hook installed produce zero `tool_stuck` events; lease expiry is the safety net.

3. **Check for resume**: inspect `$STATE` for an answered blocker on `$PHASE`. If found, factor the answer into your plan for this run.

4. **Read the master plan** at `$WORKTREE_ROOT/plans/$PLAN.md` (the plan files live on your branch in the worktree — same content as `$PROJECT_ROOT/plans/`, but reading from your worktree keeps you anchored). Find the row in `## Sessions index` whose phase id matches `$PHASE` (the parser strips the master-stem prefix from the plan_file basename — see `end_of_line/plan_parser.py`). The row's "Plan file" cell points to the sub-plan markdown.

5. **Read the sub-plan** at `$WORKTREE_ROOT/plans/<plan_file>`. This is the scope. Do exactly what it says. Don't scope-creep.

6. **Check for previous-attempt context.** When `$PHASE` is on attempt > 1, the dispatcher writes a sidecar describing what the prior attempt left in the worktree:
   ```bash
   CTX="$(dirname "$STATE")/logs/attempt-context.$PLAN.$PHASE.md"
   if [ -f "$CTX" ]; then
       cat "$CTX"
   fi
   ```
   The block names the attempt number, the termination reason for the prior attempt (lease expired / operator force-released / blocked / etc.), uncommitted changes (`git status --short`), the diff stat against HEAD, and any commits already landed by prior attempts. **Read it before doing any work.** It tells you whether prior progress is on disk waiting to be continued, or whether the worktree drifted and needs a reset. Reset is `git -C "$WORKTREE_ROOT" reset --hard <base_ref> && git -C "$WORKTREE_ROOT" clean -fd` — only if the prior edits don't align with the sub-plan. Otherwise inspect and continue from where the prior attempt left off. If the file doesn't exist, this is attempt 1 — proceed from scratch.

7. **Do the work.** Use the editing/test/commit tools you have. Follow the project's CLAUDE.md (TDD, /code-review after non-trivial work, structured commit messages, etc.). When you commit, capture the SHA — `git rev-parse HEAD` after each `git commit`. **Before every `git commit`, verify the branch:** `git rev-parse --abbrev-ref HEAD` should print `clu/$PLAN` (NOT `main`). If it prints `main`, you've drifted out of the worktree — `cd "$WORKTREE_ROOT"` and re-stage before committing.

8. **Decide the exit path**:
   - Work is done and tests are green → `clu complete --commit <each SHA>`
   - Need a decision from the user → `clu block` with a focused question + 2-4 options
   - Hit a wall you can't resolve (corrupt state, missing dependency, contradictory requirements) → `clu block` with a question that surfaces the wall

9. **Call the callback and exit.** Output of the callback is logged. Exit code 0 from the callback means clu accepted it. The EXIT trap from step 2 kills the heartbeat ticker automatically.

## Pre-complete callbacks (mandatory)

Before calling `clu complete`, you MUST attest that the project's
quality mandates passed. clu refuses `complete` with
`STATUS_TRANSITION` if either stamp is missing or stale (i.e. a
commit landed after the stamp).

**Almost always** — re-run verification, then stamp:
```bash
clu verify --project "$PROJECT_ROOT" --plan "$PLAN" \
    --phase "$PHASE" --token "$TOKEN"
```
This runs `quality.verify_command` (or `test_command`) and stamps
`attestations.verify` on rc=0. On rc!=0 the command fails — fix
the breakage, commit, re-run `clu verify`.

**Exception — projects that opted out.** If
`.orchestrator.json` has `"quality": {"verify_required": false}`,
skip the `clu verify` step entirely. `cmd_complete` won't refuse
on a missing verify stamp under this policy, and `clu` records an
audit event (`verify_policy_skipped`) so the bypass is logged.
The in-session test run (which you already did before committing,
per the project's TDD mandate) plus the commit message's "Tests:"
line are the audit trail. This opt-out is intended for projects
whose authoritative test runner is an MCP tool (e.g. Xcode
`test_sim`) or anything else clu can't reasonably re-run from a
shell — see `docs/conventions.md` for the policy rationale. The
simplify mandate is unaffected.

**If your diff exceeds threshold** (>1 file OR ~30 lines by
default; per-project override in `.orchestrator.json:quality.simplify_threshold`)
— run `/code-review`, then stamp:
```bash
clu attest --simplify --project "$PROJECT_ROOT" --plan "$PLAN" \
    --phase "$PHASE" --token "$TOKEN"
```
clu cannot run `/code-review` itself — it's a Claude-side review
skill. The attestation is your word that you ran it.

**Stamps go stale.** Each stamp records the HEAD SHA at attest-time.
If you commit AFTER stamping, the stamp is stale and `clu complete`
refuses. Order: do the work, run /code-review, commit, run tests,
`clu verify`, `clu attest --simplify`, `clu complete`. If you
need to commit a fix after stamping, re-stamp.

**Skip flags exist but are operator-owned.** `clu complete
--skip-verify` and `--skip-simplify` bypass each gate but emit
audit events. Workers should not use these — if you think a phase
legitimately needs a skip, `clu block` with the situation instead.

## Quality mandates

These mandates apply on every project that uses clu. The project's CLAUDE.md adds project-specific rules on top (naming, exit-code patterns, event constants, files to avoid); read it before your first commit.

- **TDD when modifying logic.** Failing test first, then the minimal implementation that turns it green. Skip TDD only for pure refactor, config, docs, or content edits. The project's CLAUDE.md names the test framework.

- **Review after non-trivial diffs.** If the diff spans more than one file or ~30 lines, run the project's review pass (`/code-review`, a project-local equivalent, or a deliberate self-review). Look specifically for rule-of-three extraction opportunities, dead code, and copy-paste from sibling phases. Stamp via `clu attest --simplify` after running /code-review, or complete will refuse.

- **Structured commit messages.** Title (one line) / Why (motivation) / What's new (the surface) / Under the hood (the non-obvious choices) / Tests (count + what's covered) / `Co-Authored-By:` trailer naming the model you're running (e.g. `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`). Commit messages outlast the code — treat them as primary documentation.

- **Stage explicit paths.** `git add <path1> <path2> ...`, never `git add -A` or `git add .`. Explicit staging forces you to think about what you're including; the blanket forms are how secrets and stray artifacts leak in.

- **External tools need absolute paths or `command -v` fallbacks.** Worker subprocess PATH is not the operator's shell PATH — LaunchAgent and headless `claude --print` contexts inherit a minimal environment. Before shelling out to `gh`, `pipx`, `pip`, or any user-installed tool, resolve the absolute path or use `command -v <tool>` with a known fallback.

- **Read existing helpers before inventing new ones.** Grep first. If you'd write a function whose 80%-overlap twin already exists, use the existing one — project-level rule-of-three may already have extracted what you need.

- **Honor the project's CLAUDE.md.** It's the project-specific layer of these mandates: naming conventions, exit-code patterns, event constants, files to avoid. Read it before your first commit on a project, and re-read when you're unsure.

- **Re-run verification right before complete.** The project's primary check — test suite, build, lint, whichever is authoritative — must pass at the moment you exit, not just at some point earlier in the phase. Run it from a fresh process before calling `clu complete` so you're verifying the post-edit state, not stale memory of an earlier run. Record the exact result (test count + delta, lint clean, build green) and put it in the completion summary. A wrong "tests passed" claim is the single fastest way to lose operator trust; a worker that consistently re-verifies and reports honestly is the foundation everything else builds on. `clu verify` does this for you AND stamps; running the test suite manually and skipping `clu verify` will still leave `complete` refused.

- **The completion summary is load-bearing.** When you call `clu complete`, your final message to the operator is the only signal they have about what shipped. Mention what actually committed (SHA), the verification result from the mandate above (count + delta), and anything you tried that didn't work and the operator should know about (e.g. "couldn't run `gh issue close` because the binary wasn't on PATH; operator should close manually"). Silence on a failure mode reads as "everything went fine," which is worse than admitting a small thing didn't.

## Common pitfalls

- **Passing the wrong token**: tokens are validated against the live claim. If you pass anything other than the `$TOKEN` arg, `clu` rejects with `CLAIM_MISMATCH` (exit 4) and your phase is stuck. Always use `--token "$TOKEN"`.

- **Forgetting `--project`**: every `clu` worker callback takes `--project <root> --plan <slug>`. Pass them.

- **Asking too many questions**: blocker → iMessage to the user. Each blocker pauses the plan until they answer. Batch decisions if you can; ask once with multiple options rather than three times in sequence. **Don't `clu block` for things you can decide yourself** (variable names, file organization, test placement). Block only for decisions that change scope or require their context.

- **Calling complete without any commits**: legal but suspicious. If the phase scope said "implement X" and you produced no commits, you probably should have blocked. Acceptable for phases that are pure validation / smoke-tests / no-op verifications.

- **Failing SHA validation**: `clu complete --commit <sha>` runs `git cat-file -e <sha>` against the project repo. If the SHA doesn't exist (typo, didn't actually commit), exit code 3 (`BAD_SHA`) and the phase doesn't release. Always pass SHAs that are actually in the repo.

- **Long phases**: the lease is 60 min by default. If your work takes longer, checkpoint by calling `clu block` with a question like "continue?" + options `["yes", "stop here"]`. The user replies, you resume on the next dispatch with their answer in hand. This is normal — phases are meant to be tick-sized, not session-sized.

- **Skipping the heartbeat ticker**: step 2 arms a background `clu heartbeat` loop for every phase. Don't skip it even for short phases — `clu status` mis-reports `STALLED` the moment the phase exceeds the heartbeat threshold (`min(25, max(15, lease_ttl//2))`, ~25 min by default) without a heartbeat, and the supervisor's gap-fill notification fires. The 2-min ticker is cheap; the `kill -0 $WORKER_PID` loop condition + EXIT trap clean it up automatically (issue #72 — old EXIT-only contract missed SIGKILL/OOM cases).

- **Forgetting the activity-hook env exports**: step 2b exports `CLU_PLAN` / `CLU_PHASE` / `CLU_TOKEN` / `CLU_PROJECT`. Without those, the Claude Code PreToolUse hook (if installed) reads empty strings and short-circuits → `tool_stuck` detection silently disables. The phase still works; you just lose the early-warning signal for wedged Bash subprocesses. `clu doctor` flags the missing marker.

## Example invocations

End of a happy path that made one commit:
```bash
clu complete --project /Users/me/projects/end-of-line \
    --plan multi-plan-routing --phase routing-impl \
    --token session-abc123 \
    --commit 7f3a8d2c
```

Opening a blocker for a design decision:
```bash
clu block --project /Users/me/projects/end-of-line \
    --plan halt-bypass --phase design \
    --token session-def456 \
    --question "Halt notifications: bypass quiet hours or stay gated?" \
    --option "Bypass (loud at 3am)" \
    --option "Stay gated (deferred until 8am)" \
    --context "Day 2.9 gated them; 3am halts currently sit silent until morning."
```

## Activity hook (operator setup)

One-time install. The hook tells the supervisor when a Bash tool call is
in flight, which lets `tool_stuck` detection scope its process-tree walk
to subprocesses spawned during the active call — Claude Code's
session-level MCP servers stop generating false positives.

Add to `~/.claude/settings.json` (global, fires for every Claude Code
session machine-wide) OR per-project `.claude/settings.json` (committed)
OR `.claude/settings.local.json` (gitignored). Hooks merge across scopes
— pick the scope that fits how you run clu.

```jsonc
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{
          "type": "command",
          "command": "[ -n \"$CLU_TOKEN\" ] && python3 -m end_of_line.activity_hook --start-bash --project \"$CLU_PROJECT\" --plan \"$CLU_PLAN\" --phase \"$CLU_PHASE\" --token \"$CLU_TOKEN\" 2>/dev/null || true"
        }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{
          "type": "command",
          "command": "[ -n \"$CLU_TOKEN\" ] && python3 -m end_of_line.activity_hook --end-bash --project \"$CLU_PROJECT\" --plan \"$CLU_PLAN\" --phase \"$CLU_PHASE\" --token \"$CLU_TOKEN\" 2>/dev/null || true"
        }]
      }
    ]
  }
}
```

The `python3 -m end_of_line.activity_hook` entry point imports only
`end_of_line.state` (vs `clu activity` which imports the full
orchestrator surface). At ~37ms per call vs ~62ms for `clu activity`,
the savings add up over hundreds of Bash invocations per phase. The
full `clu activity --start-bash / --end-bash` subcommand still works
unchanged — operators with the older snippet don't need to update.

Three parts of the snippet are load-bearing:

- **`[ -n "$CLU_TOKEN" ]` guard** — short-circuits the hook in non-clu
  Claude Code sessions (no env vars exported → no-op). Without this
  guard, every Bash call in every session tries to run `clu activity`
  with empty arguments and clutters the log.
- **Trailing `|| true`** — Claude Code treats hook exit 2 as
  *blocking*; the tool call gets rejected. A bug or transient failure
  inside `clu activity` exiting 2 would freeze every Bash call. `|| true`
  forces exit 0 unconditionally. Do not "tidy up" by removing it.
- **`2>/dev/null`** — silences hook stderr in the transcript when
  `clu activity` errors (e.g. stale token after lease expiry); the
  worker shouldn't see the noise.

Subagent (Task tool) Bash invocations are NOT covered: Claude Code's
subagent contexts don't inherit parent env, so `CLU_TOKEN` is unset
inside subagent hooks → they short-circuit. Lease expiry remains the
safety net for wedges inside subagents.

## When in doubt: block, don't bail

The single most important rule is at the top. If something is unclear, ambiguous, or you can't proceed for any reason — `clu block` is your escape hatch. It's far better to send the user a confused-but-specific iMessage than to silently exit and force the supervisor to halt the plan on its own.
