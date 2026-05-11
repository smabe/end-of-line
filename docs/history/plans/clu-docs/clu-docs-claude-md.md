# clu-docs-claude-md — rewrite CLAUDE.md + archive brainstorm

You are running as phase 6 (final) of the `clu-docs` plan. By now
`docs/architecture.md`, `docs/reference.md` (or directory),
`docs/operations.md`, `docs/conventions.md`, and `docs/contract.md`
all exist and cover what they own.

Your two jobs:

1. Rewrite `CLAUDE.md` to be concise, focused on what an AI agent
   needs to be effective on the next change, with links into `docs/`
   for depth.
2. Move `brainstorm/` to `docs/history/brainstorm/` and write a
   `docs/history/README.md` framing the archive.

## Read first

- Current `CLAUDE.md` (top to bottom)
- Every file under `docs/` (yours from prior phases plus existing
  `contract.md`)
- `brainstorm/` (the files you're about to move; skim, don't deep-read)
- `README.md` (so you don't duplicate the public-facing intro)
- `git log --oneline -15` for the recent commit hashes you'll cite
  in the status block

## Produce — Part 1: rewrite CLAUDE.md

The new CLAUDE.md is for AI agents starting a fresh session in this
repo. It must answer fast:

- What is this project, in one sentence?
- What's the stack and how do I run + test?
- What are the mandatory conventions I must not violate (one-liners
  + link to `docs/conventions.md` for the why)?
- What should I NOT do?
- What's the current status (recent commits + what's the next thing
  to pick up)?
- Where do I look for more depth?

Suggested sections, in roughly this order:

1. **`# end-of-line / clu`** — one paragraph, what it is, link to
   README for the public pitch.

2. **Stack + run/test** — Python 3.11+, stdlib-only, unittest (NOT
   pytest), `pipx install -e .` for the CLI, `python3 -m unittest
   discover -s tests` for the suite. Tight.

3. **Conventions (mandatory)** — one-line rules with a link to
   `docs/conventions.md` for full rationale on each. Cover at least:
   TDD before logic changes, `/simplify` after non-trivial work,
   structured commit format, `ExitCode` enum, worker token
   discipline, slug validation, event constants, test isolation for
   registry-touching tests, `with st.mutate()` for state mutations,
   "one tick = one action" in supervisor.

4. **What NOT to do** — short bullet list (no SwiftUI, no `git add
   -A`, no third-party deps without justification, don't break the
   one-tick contract).

5. **Where to look for depth** — a "Docs" subsection listing each
   `docs/*.md` file with a one-line description.

6. **Status (as of <date>)** — current shipped state, recent commit
   hashes for orientation, plus the "Pick up here" block flagging
   the next thing to work on. Preserve the spirit of the existing
   status block but tighten — readers shouldn't need to scroll a
   wall of "Day 2.x shipped" entries.

7. **Locked config decisions** — keep as-is, this is durable signal
   for future sessions.

8. **Sister project** pointer — keep.

Total target: well under the current ~110 lines. Probably 60-90.
Hard rule: nothing in CLAUDE.md should duplicate a fact that lives
in `docs/`. If you're writing it twice, put it in docs and link.

## Produce — Part 2: archive brainstorm/

1. Create `docs/history/` (if it doesn't exist).
2. `git mv brainstorm/* docs/history/brainstorm/` (the move should
   preserve git history).
3. Write `docs/history/README.md` (one paragraph): "These are frozen
   design rationale from before Day 1 shipped. The current code is
   the source of truth; these files exist to document why decisions
   were made the way they were, not what the code does today."

## Test before committing

Docs-only changes shouldn't touch the suite, but prove it:

```
python3 -m unittest discover -s tests
```

151 (or however many — same as before phase 1) tests must pass.

## Done

One commit covering: CLAUDE.md rewrite, the brainstorm move, and
`docs/history/README.md`. Project commit format. Then:

```
clu complete --project <project> --plan clu-docs \
    --phase claude-md --token <token> --commit <sha>
```

That's the last phase — the next tick will hit `plan_done` and
iMessage will go out.

## Pre-commit sanity checks

Before you commit, eyeball:

- The new `CLAUDE.md` line count vs the old one — should be shorter.
- The links into `docs/*.md` — they should all resolve to files that
  actually exist (every prior phase committed its file).
- `brainstorm/` is gone (moved).
- `docs/history/brainstorm/` exists with the same files.
- `docs/_outline.md` from phase 1 is still committed — leave it
  alone; it's a useful artifact of how the docs library was designed.

## If something is wrong

If the docs from prior phases are inconsistent, contradictory, or
missing material CLAUDE.md needs to link to, `clu block` with a
focused question. Don't paper over it by writing CLAUDE.md to match
broken docs.
