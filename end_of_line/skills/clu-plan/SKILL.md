---
name: clu-plan
description: Author a clu-format plan (master + sub-plan files) ready for `clu init` + `clu queue add` dispatch. Use when the user wants to scope a feature for clu-orchestrated execution, mentions queueing plans, or says "plan this for clu". For non-clu projects, falls back to /plan with a pointer.
user_invocable: true
---

<!--
Bundled with clu so /clu-plan installs are self-contained. The canonical
copy is end_of_line/skills/clu-plan/SKILL.md in the clu repo. To replace
this bundled copy with a symlink to your own version, run
`clu install-skill --only clu-plan --force` after putting your SKILL.md
at ~/.claude/skills/clu-plan/SKILL.md.
-->

## You are the clu-plan authoring skill

clu's dispatch contract requires a master plan file with a `## Sessions
index` markdown table whose rows declare each phase, PLUS one sub-plan
file per phase. The sub-plan is the worker's brief — what to read, what
to build, when to call `clu complete`. A `/plan`-style single file
fails `parse_sessions_index()` and the supervisor errors `no Sessions
index in plans/<slug>.md`.

This skill produces both: the master AND every sub-plan, in the format
that workers dispatched via `claude --print '/clu-phase ...'` can act on.

## When to use vs. when to refuse

**Use** when:
- User invokes `/clu-plan` directly.
- User asks to "scope this for clu", "plan a clu drain", "queue this
  up", or similar.
- User describes a multi-issue batch they want clu to drain
  autonomously.

**Refuse** (and point at `/plan`) when:
- The project doesn't have `.orchestrator.json` at its root. clu plans
  only make sense in clu-managed projects.
- The user's intent is a single solo human-authored plan with no
  intention to dispatch via clu (e.g. "make me a plan I'll work on
  manually"). The clu format has extra overhead; `/plan` is lighter.

Detection: `test -f .orchestrator.json && echo OK`. If absent, say:
> This project doesn't have `.orchestrator.json` — clu isn't managing
> it. Use `/plan` instead (it produces a generic single-file plan).
> If you want to make this a clu project first, run `clu init --project
> . --plan <slug>` to bootstrap, then re-invoke `/clu-plan`.

## Workflow

### Step 1: Gather inputs

Ask only what you can't infer:

- **Plan slug** — kebab-case, matches `^[a-z0-9][a-z0-9_-]{0,63}$`. If
  the user gave one, validate it; if not, propose based on the goal
  ("scope a queue + worktree refactor" → `queue-worktree-refactor`).
- **Goal / scope** — what is this plan trying to accomplish? Pull from
  conversation context if it's been discussed; otherwise ask in one
  sentence.
- **GitHub issues to close** — list, optional. Worker uses these in
  commit messages (`closes #N`).
- **Phase breakdown** — how should the work split? If the user has a
  proposed split, use it. Otherwise propose one: smallest-first,
  each phase = one issue OR one cohesive commit, each phase has
  TDD-able acceptance criteria. Single-phase plans are fine when the
  scope is small — clu still requires the Sessions index with one row.

#### Phase granularity

Each phase has ~30–60s of overhead (cold-context worker ramp +
subprocess startup; push-dispatch since #52 closed the cron-tick gap
between phases) before any real work happens. Don't phase finer than
the work justifies.

Rules of thumb:

- **Collapse one-function helpers into their first caller.** If the
  helper is <50 LOC and only used by the next phase, it belongs in
  that phase.
- **A "meaningful commit" isn't a single function.** It's a
  minimum-viable slice that's TDD-able and reviewable on its own —
  a function plus its first caller usually qualifies.
- **Target 3–6 phases for typical features.** 7+ is fine when each
  phase is a genuinely independent slice (notify-multi-channel was 7,
  clu-ship was 8); treat it as a smell to re-check, not a hard cap.
- **Phase when there's a forcing function:** schema bumps, API surface
  changes that other plans queue against, config fields workers need
  to read in earlier phases.

Receipt: auto-archive-on-merge (2026-05-18) was 3 phases; the first
phase added a single ~15-line helper plus 5 tests and could have
shipped inside the next phase's commit without losing TDD-ability.
Each saved phase is ~30–60s of dead time off the plan's wall clock.

### Step 2: Pre-author research (optional, scale to size)

For plans touching surfaces you haven't already read in the current
conversation: dispatch parallel `Explore` agents to map the touch
points. Skip if "Files to touch" lists ≤ 3 files and you've already
read each. Each agent's brief: file:line citations mandatory, report
under 500 words.

This is the same disciplined-exploration pattern from `/plan` step
7.5 — but for clu-plans it happens BEFORE drafting (not after first
approval), because the master's Locked-decisions section commits to
specific file paths and behaviors that need ground-truth grounding.

### Step 3: Draft all files in memory

**Do not write to disk yet.** Draft the master file AND every sub-plan
in memory — every file must be ready to write the moment the operator
says `ship`. The operator-approval mandate from the user's CLAUDE.md
applies: novel plan files require `ship` from the operator before they
land on disk.

Drafting all sub-plans up-front is mandatory even though only the
master is shown in Step 4. The worker dispatched after `clu init` will
read a sub-plan that exists or fail; you can't lazily author them on
ship.

#### Master template

```markdown
# <slug> — <one-line tagline>

<2-3 paragraph intro: what the plan does, why it matters, what's the
ordering of phases. Reference any GitHub issues it closes. If the plan
is a follow-up to a recent incident, name the incident.>

## Locked design decisions

<One subsection per phase OR per logically distinct decision area.
Each subsection: bullet list of concrete decisions, with the WHY
attached when non-obvious. The worker reads these to ground itself
on what's already settled vs. what they get to decide.>

### Phase 1 — <phase name> (#issue if applicable)
- **<decision>:** <details>
- **<another decision>:** <details>

### Phase 2 — ...

## Non-goals

- <explicit boundary>
- <natural adjacent work being deferred>
- <scope creep risk>

## Files touched

List every file the plan creates or modifies, plus API hotspots
(public function signatures, schema fields, config keys) downstream
plans might rely on. The operator scans this at queue time to spot
overlaps when scheduling parallel batches — overlapping `## Files
touched` sections mean serialize, not parallelize. Unchecked semantic
conflicts across worktrees were the canonical failure (clu #50;
`cmd_answer` argparse drift, merge SHA `1816c0f`).

- `<path/to/file>` — <P1 NEW | P1, P3 modified> — <one-line note; flag API hotspots>
- `<another path>` — <phase tags> — <note>

## Per-phase done checklist

- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests` (or this
  project's primary check).
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- **Stamp attestations AFTER the commit.** The gate compares stamp SHA
  against HEAD; pre-commit stamps go stale the moment you commit.
  - `clu verify --plan <slug> --phase <id> --token <T>` runs the
    project verify command and stamps `attestations.verify`.
  - `clu attest --simplify --plan <slug> --phase <id> --token <T>`
    stamps `attestations.simplify` (required when phase diff exceeds
    `simplify_threshold`; auto-passes below it).
- Call `clu complete --plan <slug> --phase <id> --token <T>` with the
  worker token on success. The completion gate refuses with
  `EVENT_ATTESTATION_REFUSED` + an inbox surface if stamps are missing
  or stale.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| <phase-id> | `<slug>-<phase-id>.md` | <one-line scope> | <Nh> |
| <next-phase-id> | `<slug>-<next-phase-id>.md` | ... | ... |
```

The Sessions index is load-bearing. `parse_sessions_index()` derives
phase IDs from the sub-plan filename: if the filename is
`<slug>-<phase>.md`, the phase ID is `<phase>`. Both must be valid
slugs per `st.validate_slug` regex `^[a-z0-9][a-z0-9_-]{0,63}$`.

**The `Effort` column is mechanically load-bearing, not decorative.**
`parse_effort_minutes()` reads it at `clu init` time to scale each
phase's lease TTL (default 60min × `lease_ttl_scale`, capped by
`lease_ttl_minutes`). Formats accepted: `45m`, `1h`, `2.5h`, or a
bare integer interpreted as minutes. Undersize → lease expires
mid-phase and the worker halts; oversize is fine. Estimate honestly;
a 4-hour phase tagged `1h` is a footgun. Shipped in lease-reliability
(#57/#58).

#### Sub-plan template (one per phase)

```markdown
# <slug>-<phase-id> — <one-line tagline>

You are phase `<phase-id>` of the `<slug>` plan. <1-2 sentences
describing this phase's job in concrete terms — what the worker
delivers as one commit.>

## Locked decisions (do NOT re-litigate)

See `plans/<slug>.md`. Summary:

- <bullet of phase-specific locked decisions, pulled from master>
- <another>

## Read first

- `<file:line>` — <why the worker needs this context>
- `<file:line>` — <another>
- `<existing test file>` — <pattern to mirror>

## Produce

1. **Failing tests first.** <Specific test file + test names.
   Describe the assertions concretely.>

2. **Implementation.**
   - `<file>`: <code shape — function signature, key logic, what
     existing patterns to mirror. Include code snippets when the
     shape is non-obvious.>
   - `<another file>`: <...>

3. **Acceptance.**
   - <Concrete check 1 — e.g. all N new tests green>
   - <Concrete check 2 — e.g. manual smoke command + expected output>
   - <Concrete check 3 — e.g. grep confirms no regressions>

4. **Commit + attest + complete.**
   - Structured commit: `<slug>: phase <phase-id> — <scope> (#issue
     if applicable)`.
   - Stage explicit paths: `<file1>`, `<file2>`, `<test file>`.
   - **After the commit** (HEAD must be the SHA being attested):
     - `clu verify --plan <slug> --phase <phase-id> --token <T>`
     - `clu attest --simplify --plan <slug> --phase <phase-id> --token <T>`
   - `clu complete --plan <slug> --phase <phase-id> --token <T>`.

## Failure modes to watch

- **<phase-specific gotcha>** — <explanation + mitigation>
- **<another>** — <...>
```

### Step 4: Present the master only and await `ship`

After drafting all files in memory, present **only the master file**
to the operator with this exact framing:

> Here's the master — N sub-plan files drafted alongside it in memory.
> Read the master (locked decisions, non-goals, Sessions index) and
> say `ship` to write + queue, or tell me what to change. If you want
> to see a specific sub-plan before shipping, name it and I'll expand
> it inline.

Then **wait**. Do not write to disk. Silence is not approval.

Sub-plans are intentionally NOT dumped in chat by default. The design
judgment lives in the master (locked decisions, non-goals, Sessions
index); sub-plans are derivative worker-facing detail bounded by those
decisions and are rarely the thing that flips an approval. Pre-rendering
a 7-sub-plan dump is the slowest part of a clu-plan conversation and
mostly doesn't change the operator's decision.

If the operator asks to see a specific sub-plan, expand THAT one inline
— don't volunteer the others. If the operator returns with edits to
the master, apply them to the in-memory draft (including propagating
any locked-decision changes into the affected sub-plans) and re-show
the master.

### Step 5: On `ship`, write files + optionally init/queue

When the operator says `ship` (or equivalent):

1. **Author the plan files in a single tight pipeline.** Write all
   master + sub-plan files via `Write` tool calls in one assistant
   turn. Don't pause between them — the queue-pop-mid-author feedback
   memory documents the failure mode where `clu` cron pops position 1
   before the operator finishes adding positions 2-N.

2. **Plan files MUST land on the OPERATOR's main checkout.** When
   `clu init --worktree --branch` runs, it branches off current HEAD
   — if the plan files are on a different branch, the worker worktree
   won't see them. Commit + push to main BEFORE `clu init`.

3. **Run `clu init` per plan (if the operator wants queueing now):**
   ```bash
   clu init --project . --plan <slug> --worktree --no-claude-md
   ```
   `--worktree` isolates each plan's worker on its own branch. Use
   `--no-claude-md` if the project's CLAUDE.md is already set up to
   avoid the prompt (most operators).

4. **Run `clu queue add` in ONE call** (atomic per the queue-ux-hardening
   ship):
   ```bash
   clu queue add --project . <slug-1> <slug-2> <slug-3>
   ```

5. **Confirm to the operator** with the dispatched state. Both
   `clu init` and `clu queue add` print a one-line resolved-model
   summary (worker-model-line #51) — surface it to the operator if
   they're choosing between sonnet/opus for this run:
   ```bash
   clu queue list --project .
   clu list                              # fleet view (snapshot)
   clu watch --all --task-list           # fleet stream (alt to list)
   ```

6. **Arm live progress monitoring** via the Monitor tool — only when
   the SessionStart hook hasn't already done it. The hook
   (`end_of_line/hooks/clu_session_start.py`) auto-arms one
   `--task-list` Monitor per active plan on every fresh session in a
   clu-managed cwd, and the hook docstring guarantees idempotency
   (won't double-arm if one is already in flight). So the manual
   block below is the fallback for the "just queued this in the
   current session" case — the hook hasn't fired yet because no new
   session has opened. After `/clear` or a fresh session, the hook
   does it for you.
   ```
   Monitor(
       description="clu <slug> phase progress",
       persistent=True,
       timeout_ms=3600000,
       command="clu watch --project . --plan <slug> --task-list"
   )
   ```
   Each state transition (phase started/completed/blocked/halted)
   arrives as a notification, so you see what clu is doing without
   polling. The operator's UserPromptSubmit hook handles AFK surfacing
   separately; this is the at-desk live feed.

   **Cross-plan wedge events** (`tool_stuck`, `phase_blocked`,
   `attestation_refused`, `stalled_claim_notified`) stream on a
   different filter — `clu watch --all --operator` — armed once per
   session by the user-CLAUDE.md SessionStart instruction (operator
   dashboard, #70). It's complementary to per-plan `--task-list`,
   not redundant: `--operator` is host-wide wedge surfacing,
   `--task-list` is per-plan execution progress.

7. **Tear down the Monitor when the plan completes.** The single
   teardown trigger is `TASK_UPDATE task=<slug> status=completed`
   with NO `parent=` field — that's the whole-plan completion event
   (emitted on `EVENT_PLAN_COMPLETED`), not a phase event. When that
   line arrives, call `TaskStop` on the Monitor's task_id. Without
   teardown the watch is a zombie stream that survives `/clear`
   (because of `persistent: True`) and pollutes future sessions
   with leftover monitors. Don't tear down on `status=in_progress
   msg="paused"` — paused plans can be resumed, and you'd lose the
   live feed for the rest of the run. Defensive: if `clu watch`
   processes are already running at session start that you didn't
   start yourself, those are leftovers from a prior session — you
   can't `TaskStop` them (task_ids don't persist across sessions),
   so kill the underlying PIDs.

### Reacting to task-list protocol notifications

With `--task-list`, the Monitor stream emits two line shapes:

- `TASK_CREATE task=<id> [parent=<slug>] status=pending` —
  bootstrap lines, one per plan + phase, arrive together within
  ~200ms at startup. The parent line (plan itself, `task=<slug>`,
  no `/phase`) has NO `parent=` field. Child lines
  (`task=<slug>/<phase>`) always carry `parent=<slug>`.
- `TASK_UPDATE task=<id> [parent=<slug>] status=<state> msg="<one-liner>"` —
  fired as state transitions happen. `<state>` is one of
  `pending` / `in_progress` / `completed`. `parent=` is present
  on phase-scoped events and absent on plan-scoped events
  (`plan_completed`, `paused`, `resumed`).

**On the bootstrap batch (TASK_CREATE lines):** call `TaskCreate`
once with all matching tasks, ALL `status=pending`. The TaskCreate
API is flat — there's no `parent_id` field — so signal the
hierarchy visually in each child's `subject`:

- Parent (`task=<slug>`, no `parent=`): `subject = <slug>`.
- Child (`task=<slug>/<phase>`, with `parent=<slug>`):
  `subject = "└ <phase>"` — that's a U+2514 box-drawing
  character, single ASCII space, then the phase id. Lock this
  glyph verbatim so every plan renders the same tree.

**On each TASK_UPDATE:** call `TaskUpdate` matching by `task=`
(the task_id). **Do NOT re-set `subject`** — only update
`status` and `description` / `activeForm` to the `msg` content.
Re-setting the subject would either churn the row text or strip
the `└ ` glyph mid-stream and visually un-nest the tree. The
`msg` field carries the human-readable transition reason
(e.g. `"BLOCKED b-12 — should I proceed with X?"` or
`"HALTED (max attempts on foundation)"`) — surface significant
msgs to the operator via PushNotification when the user would
want to act now (halts, blockers).

**Out-of-order arrivals:** if a `TASK_UPDATE` arrives for a
task_id you haven't seen a `TASK_CREATE` for (race condition,
rare), buffer it ~1s and retry. If still no matching task,
create it on-the-fly with the update's status — apply the
same nesting convention: if the line carries `parent=<slug>`,
prefix the subject with `└ ` and the phase id.

**Non-`TASK_*` lines:** the snapshot baseline (`[snapshot] slug:
status, active=...`) and any text-mode lines that leak through
are operator-context only — ignore in the TaskCreate flow.

If the operator hasn't installed the new skill content yet
(`clu install-skill --force --only clu-plan`), the auto-arm
reverts to text mode and notifications won't have the protocol
prefix — fall back to free-text interpretation.

If the operator only wants the files authored (not queued yet), stop
after step 1. Don't run `clu init` without explicit operator intent.

## Critical rules

- **Every sub-plan ends with `clu complete --plan ... --phase ...
  --token <T>`.** That's the worker's exit contract (per `/clu-phase`
  SKILL.md and the project CLAUDE.md mandate `--token on every worker
  callback`). Omitting it = lease-expiry = halt.
- **Attestation gate (#55) must be satisfied BEFORE `clu complete`.**
  Sub-plans must include, AFTER the commit and BEFORE complete, both
  `clu verify --plan ... --phase ... --token <T>` (runs project
  verify command, stamps `attestations.verify@HEAD`) and
  `clu attest --simplify --plan ... --phase ... --token <T>` (stamps
  `attestations.simplify@HEAD`). The gate compares stamp SHA against
  HEAD; stale or missing stamps refuse completion with
  `EVENT_ATTESTATION_REFUSED` + an inbox surface. Skip flags exist
  (`--skip-verify`, `--skip-simplify`) but emit audit events — use
  only with operator approval.
- **Phase IDs and plan slugs must match `^[a-z0-9][a-z0-9_-]{0,63}$`.**
  `st.validate_slug` enforces this. Sub-plan filenames derive phase
  IDs by stripping `<plan-slug>-` from the basename.
- **Each phase = one commit, one `/code-review`, one suite-green run, one
  `clu complete`.** Don't batch phases. The cadence is the contract.
- **Operator-approval mandate (user CLAUDE.md) applies.** Novel plan
  files require `ship` from the operator before they land on disk.
  Silence is not approval.
- **Master plans MUST declare `## Files touched`.** List every
  created + modified path with the phase tag, plus API hotspots
  (function signatures, schema fields, config keys). The operator
  uses this at queue time to spot overlaps and serialize conflicting
  plans before they ship — unchecked semantic conflicts across
  worktrees were the canonical failure (clu #50; `cmd_answer`
  argparse drift across plan-locator + blocker-lifecycle, merge SHA
  `1816c0f`). The dry-merge gate (#50) is the safety net; this
  section is the prevention.
- **Plan files commit + push to main BEFORE `clu init --worktree`.**
  Otherwise the worker worktree branches off a HEAD that doesn't have
  the plan files. (Real friction documented in commit `0d8e6d0` —
  cost a worktree round-trip to fix mid-pipeline.)
- **Per-project queue is concurrent, not sequential.** Cron pops one
  head per tick (~30s), but a popped plan dispatches on its own
  worktree and runs alongside any prior plans still in flight. Three
  queued plans = three concurrent workers ~60s apart, NOT one-after-
  another. See "Sequential queue execution requires waiting" below
  before queueing plans that touch overlapping files.
- **For ALGORITHMIC plans** (signals: cites a paper, uses a constraint
  solver, implements physics/integrator/control loop), include the
  inner-loop-specialist research from `/plan`'s step 7.5 BEFORE
  drafting. The four required questions (canonical implementation
  inside the inner loop / what fails without surrounding solver
  structure / minimum executable test / load-bearing details absent
  from API docs) carry over verbatim.

### Sequential queue execution requires waiting

`clu queue add` schedules a plan to dispatch on the next cron tick —
typically ~30s later. If a prior plan is still running on a different
worktree, both run **concurrently**. This is safe in isolation but
fails when both plans touch the same file:

- Plan A modifies `foo.py` on branch `clu/plan-a`.
- Plan B (queued before A finished) modifies `foo.py` on branch
  `clu/plan-b`, branched off pre-A-merge main.
- When A merges first, B's diff still doesn't include A's changes →
  merge conflict at integration time, or worse, silent semantic drift
  (the `cmd_answer` argparse incident, merge SHA `1816c0f`).

The dry-merge gate (#50) catches conflicts before B lands but doesn't
recover the wasted worker time. Best to serialize at queue time when
overlap is foreseeable.

**If you want sequential execution** (B starts off post-A-merge main):

1. Author + commit + push plan files for both A and B.
2. `clu init` only A; when the worker reaches DONE, the operator runs
   `clu ship --plan A --yes` (or just `clu ship --plan A` to preview
   first). Mode comes from `.orchestrator.json`'s `dispatch.ship_mode`
   (default `direct`; `as_pr` opens a GitHub PR instead).
3. After `clu ship` lands A on origin/main, `auto_archive_rule` cleans
   up A's worktree on the next tick; `clu init` B off post-merge main.
4. (Optional) `clu queue add` B at step 3 if you want supervisor
   dispatch instead of running it immediately.

**If you want concurrent execution** (default `clu queue add` of both):

- Verify both masters' `## Files touched` sections are disjoint —
  including indirect touches like shared helpers, schema fields, and
  config keys.
- If they overlap, fall back to the serial flow above.

The 2026-05-19 `watch.py` incident (#62 salvage) is the canonical
failure: two plans queued back-to-back, both modified
`end_of_line/watch.py`, second worker had to be paused and its work
salvaged into a one-phase recovery plan.

### Post-worker integration: `clu ship`

Once a plan reaches `STATUS_DONE`, the operator lands it on main
with **`clu ship`** — one verb, one approval. Two modes; the
project's `.orchestrator.json` `dispatch.ship_mode` picks the default:

- **`ship_mode: "direct"`** (default): `clu ship --plan X --yes`
  validates (dry-merge + suite), checks out main, merges (FF-first
  then merge-commit fallback), pushes origin main, and triggers an
  immediate tick so `auto_archive_rule` cleans up the worktree
  without waiting for cron. The feature branch is NOT pushed to
  origin (main carries the work; archive drops the local branch
  shortly after). Set `keep_remote_branches: true` in
  `.orchestrator.json` to preserve the remote ref and have ship
  push the branch alongside main.
- **`ship_mode: "as_pr"`**: `clu ship --plan X --yes` opens a
  GitHub PR (via `gh pr create`) with the plan body as the PR body,
  stamps `state.ship_pending`, and exits. The operator clicks
  merge on GitHub; `auto_archive_rule` picks up cleanup when
  origin/main advances. Use when CI != local suite (iOS,
  heavyweight CI) or when the operator wants async approval.

Batch form: `clu ship --all-done --yes` ships every DONE plan with
an unmerged branch in one invocation. Per-plan failures are logged
and don't halt the batch.

Preview form: drop `--yes` to see the action list without applying.
Validate-only form: `--check`.

Flag overrides config: `clu ship --plan X --as-pr --yes` (or
`--direct --yes`) forces a mode for one-off ships.

When a plan hits DONE, the supervisor emits `KIND_READY_TO_SHIP`
to the inbox with the exact copy-paste command — operators get a
one-line surface in the channel they already watch (iMessage,
Discord, clu-watch).

**Do NOT use `clu integrate`** — it's now a stderr-warning
deprecation alias for `clu validate` (which is the dry-validate
path `clu ship --check` uses). The verb 'integrate' never updated
main; the rename was the canonical clu-ship.md cleanup.

## Worked example

A 2-issue batch where issues #100 and #101 both touch `auth.py`:

**Master** (`plans/auth-cleanup.md`):
```markdown
# auth-cleanup — close #100 + #101 (smaller diffs, same surface)

Two issues that batch cleanly because both touch
`server/auth.py` and neither introduces new module-level deps.
Smallest-first.

## Locked design decisions

### Phase 1 — #100 (session timeout)
- **Default timeout:** 30 min, configurable via `AUTH_SESSION_TIMEOUT`
  env var.
- **Refresh path:** `/auth/refresh` returns 401 if session age >
  timeout; client retries with re-login.

### Phase 2 — #101 (token rotation)
- **Rotation interval:** every 24h.
- **Old token grace period:** 5 min after rotation before invalidation.

## Non-goals
- Don't migrate the bcrypt → argon2 hash (filed as #102).
- Don't add admin override for the timeout (per security review).

## Files touched
- `server/auth.py` — P1, P2 modified — adds timeout + rotation. API hotspot: `validate_session` signature, `Session` dataclass.
- `tests/test_auth.py` — P1, P2 modified — new tests for both phases.

## Per-phase done checklist
- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green.
- Structured commit format; stage explicit paths.
- **Post-commit attestations:** `clu verify` then `clu attest --simplify`
  (each with `--plan auth-cleanup --phase <id> --token <T>`).
- Call `clu complete --plan auth-cleanup --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| timeout | `auth-cleanup-timeout.md` | Session timeout config + 401-on-expire (#100) | 1h |
| rotation | `auth-cleanup-rotation.md` | 24h token rotation + 5min grace (closes #100 #101) | 2h |
```

Why two phases and not one combined commit? Each phase closes an
independent GitHub issue with its own acceptance criteria and its own
deployment risk (timeout misconfig vs. rotation race) — the forcing
function for phasing is "reviewable + revertable on its own", which
each issue satisfies. By contrast, a hypothetical helper `_clear_session(token)`
used only by phase 1 would NOT warrant its own phase: it would collapse
into the timeout phase's commit.

Both sub-plan files (`plans/auth-cleanup-timeout.md` and
`plans/auth-cleanup-rotation.md`) are drafted in memory alongside the
master — each following the sub-plan template above (Locked decisions /
Read first / Produce / Failure modes / `clu complete` exit) — but are
NOT shown in chat. The master's Sessions index names them; the operator
can ask to expand either inline before shipping.

Operator says `ship`. Both sub-plans get written from in-memory drafts
in the same write pipeline as the master:

```bash
git add plans/auth-cleanup*.md
git commit -m "plans: author auth-cleanup batch (closes #100 #101)"
git push origin main
clu init --project . --plan auth-cleanup --worktree --no-claude-md
clu queue add --project . auth-cleanup
clu queue list --project .
```

Then arm live monitoring:
```
Monitor(
    description="clu auth-cleanup phase progress",
    persistent=True,
    timeout_ms=3600000,
    command="clu watch --project . --plan auth-cleanup --task-list"
)
```

## Notes on integrations with other skills

- **`/plan`** is the project-agnostic generic version. Don't replace
  it — it remains the right tool for solo human-authored plans in
  any project.
- **`/clu-phase`** is the worker skill that reads each sub-plan and
  executes it. The sub-plan format you produce here is what
  `/clu-phase` consumes.
- **`/clu-monitor`** installs the in-session inbox hook. After
  queueing plans with this skill, remind the operator to run
  `/clu-monitor` if `~/.config/clu/monitor.json` is missing or v1.
- **`/brainstorm`** is for divergent design exploration BEFORE
  scoping. If the operator hasn't committed to an approach yet,
  suggest `/brainstorm` first, then `/clu-plan` once decisions land.
