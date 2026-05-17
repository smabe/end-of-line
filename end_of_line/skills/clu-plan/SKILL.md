<!--
Bundled with clu so /clu-plan installs are self-contained. The canonical
copy is end_of_line/skills/clu-plan/SKILL.md in the clu repo. To replace
this bundled copy with a symlink to your own version, run
`clu install-skill --only clu-plan --force` after putting your SKILL.md
at ~/.claude/skills/clu-plan/SKILL.md.
-->

---
name: clu-plan
description: Author a clu-format plan (master + sub-plan files) ready for `clu init` + `clu queue add` dispatch. Use when the user wants to scope a feature for clu-orchestrated execution, mentions queueing plans, or says "plan this for clu". For non-clu projects, falls back to /plan with a pointer.
user_invocable: true
---

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

### Step 3: Draft master + sub-plan files IN MEMORY

**Do not write to disk yet.** Draft all files in the conversation as
markdown code blocks. The operator-approval mandate from the user's
CLAUDE.md applies: novel plan files require `ship` from the operator
before they land on disk.

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

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests` (or this
  project's primary check).
- Structured commit format (Title / Why / What's new / Under the hood /
  Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan <slug> --phase <id> --token <T>` with the
  worker token on success.

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

4. **Commit + complete.**
   - Structured commit: `<slug>: phase <phase-id> — <scope> (#issue
     if applicable)`.
   - Stage explicit paths: `<file1>`, `<file2>`, `<test file>`.
   - `clu complete --plan <slug> --phase <phase-id> --token <T>`.

## Failure modes to watch

- **<phase-specific gotcha>** — <explanation + mitigation>
- **<another>** — <...>
```

### Step 4: Show all files and await `ship`

After drafting all files in memory, present them to the operator with
this exact framing:

> Here's the plan — master + N sub-plan files. Read them over and say
> `ship` to write + queue, or tell me what to change.

Then **wait**. Do not write to disk. Silence is not approval. If the
operator returns with edits, apply them to the in-memory draft and
re-show.

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

5. **Confirm to the operator** with the dispatched state:
   ```bash
   clu queue list --project .
   clu list  # fleet view
   ```

If the operator only wants the files authored (not queued yet), stop
after step 1. Don't run `clu init` without explicit operator intent.

## Critical rules

- **Every sub-plan ends with `clu complete --plan ... --phase ...
  --token <T>`.** That's the worker's exit contract (per `/clu-phase`
  SKILL.md and the project CLAUDE.md mandate `--token on every worker
  callback`). Omitting it = lease-expiry = halt.
- **Phase IDs and plan slugs must match `^[a-z0-9][a-z0-9_-]{0,63}$`.**
  `st.validate_slug` enforces this. Sub-plan filenames derive phase
  IDs by stripping `<plan-slug>-` from the basename.
- **Each phase = one commit, one `/simplify`, one suite-green run, one
  `clu complete`.** Don't batch phases. The cadence is the contract.
- **Operator-approval mandate (user CLAUDE.md) applies.** Novel plan
  files require `ship` from the operator before they land on disk.
  Silence is not approval.
- **Plan files commit + push to main BEFORE `clu init --worktree`.**
  Otherwise the worker worktree branches off a HEAD that doesn't have
  the plan files. (Real friction documented in commit `0d8e6d0` —
  cost a worktree round-trip to fix mid-pipeline.)
- **Per-project queue: cron pops one head per tick.** Multiple plans
  with worktrees can run concurrently (each on its own branch).
  The queue's at-most-one-pop-per-tick rule means three queued plans
  drain over three cron ticks, not all-at-once.
- **For ALGORITHMIC plans** (signals: cites a paper, uses a constraint
  solver, implements physics/integrator/control loop), include the
  inner-loop-specialist research from `/plan`'s step 7.5 BEFORE
  drafting. The four required questions (canonical implementation
  inside the inner loop / what fails without surrounding solver
  structure / minimum executable test / load-bearing details absent
  from API docs) carry over verbatim.

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

## Per-phase done checklist
- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green.
- Structured commit format.
- Stage explicit paths.
- Call `clu complete --plan auth-cleanup --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| timeout | `auth-cleanup-timeout.md` | Session timeout config + 401-on-expire (#100) | 1h |
| rotation | `auth-cleanup-rotation.md` | 24h token rotation + 5min grace (closes #100 #101) | 2h |
```

**Sub-plan** (`plans/auth-cleanup-timeout.md`):
```markdown
# auth-cleanup-timeout — session timeout (#100)

You are phase `timeout` of the `auth-cleanup` plan. Add a configurable
session timeout that returns 401 on expired sessions, prompting the
client to re-login.

## Locked decisions (do NOT re-litigate)
See `plans/auth-cleanup.md`. Summary:
- 30-min default via `AUTH_SESSION_TIMEOUT` env var.
- `/auth/refresh` returns 401 when age > timeout.

## Read first
- `server/auth.py:45-80` — current `Session` dataclass.
- `server/auth.py:120-140` — `validate_session` body.
- `tests/test_auth.py` — existing patterns.

## Produce
1. **Failing tests first.** New `test_session_expires_after_timeout`
   and `test_session_within_timeout_validates`.
2. **Implementation.**
   - `server/auth.py`: read `AUTH_SESSION_TIMEOUT` at module load,
     default 1800 (30 min). In `validate_session`, check
     `age > timeout` and return 401 before the existing checks.
3. **Acceptance.** Both new tests green; existing 47 auth tests still
   pass.
4. **Commit + complete.**
   - `auth-cleanup: phase timeout — session timeout + 401 on expire (#100)`
   - `clu complete --plan auth-cleanup --phase timeout --token <T>`

## Failure modes to watch
- **Time mocking** — use `freezegun` or `unittest.mock.patch('time.time')`;
  don't rely on `time.sleep`.
- **Timezone bugs** — store session timestamps as UTC; the existing
  Session dataclass uses `datetime.utcnow()` (verify).
```

The second sub-plan (`auth-cleanup-rotation.md`) follows the same
shape. After both are authored:

```bash
git add plans/auth-cleanup*.md
git commit -m "plans: author auth-cleanup batch (closes #100 #101)"
git push origin main
clu init --project . --plan auth-cleanup --worktree --no-claude-md
clu queue add --project . auth-cleanup
clu queue list --project .
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
