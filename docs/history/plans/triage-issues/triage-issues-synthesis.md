# triage-issues-synthesis — rank all 8 triage assessments

You are phase `synthesis` of the `triage-issues` plan, running after
issue-1 through issue-8 have each posted a triage comment on their
respective GitHub issue.

Your job: read all 8 triage comments, cross-reference dependencies,
and produce a single ranked summary file the operator can read in
~3 minutes to pick the next thing to work on.

## Read first

- All 8 triage comments. Pull them with:
  ```bash
  for N in 1 2 3 4 5 6 7 8; do
      echo "=== Issue #$N ==="
      gh issue view $N --repo smabe/end-of-line --comments \
          --json comments \
          --jq '.comments[] | select(.body | startswith("## Triage assessment")) | .body'
      echo
  done
  ```
- The original issue bodies are not required reading at this point —
  the per-issue workers already distilled them. But if a triage comment
  is unclear or contradictory, the issue body is the source of truth:
  `gh issue view N --repo smabe/end-of-line`.
- `plans/triage-issues.md` — the triage rubric (P0-P3, effort sizes) so
  your ranking uses the same scale.

## Produce

A single new file at `docs/triage/2026-05-11-issue-triage.md` with this
structure:

```markdown
# Issue triage — 2026-05-11

Triaged by clu's `triage-issues` plan. 8 phases, one per open issue,
each worker posted a comment with priority / effort / risk / deps /
recommended approach. This file is the synthesis.

## Top 3 picks (ranked)

1. **#N — title** (P?, ?h effort). Why it's the top pick: 2-3 sentences
   that integrate the per-issue triage with cross-cutting signal
   (dependencies, "we hit it twice live", etc.).
2. **#N — title** (P?, ?h). Why second.
3. **#N — title** (P?, ?h). Why third.

## Full table

| # | Title | Priority | Effort | Risk | Depends on | Triage comment |
|---|---|---|---|---|---|---|
| 1 | clu logs | P? | ? | ? | — | [link] |
| ... | ... | ... | ... | ... | ... | ... |

Link the per-issue comments via `https://github.com/smabe/end-of-line/issues/N#issuecomment-<id>`.
You can get the issuecomment id from `gh issue view N --json comments --jq '.comments[-1].url'`.

## Bundling recommendation

If any 2-3 issues are small and overlap meaningfully (same file,
same review, same context window), call them out as a candidate
bundle. The Day-3 brainstorm flagged #1 + #5 + #6 as possible small
bundle candidates — assess whether that still holds after triage.

## Open design discussions

Any issues labeled `question` (i.e. #4) or where the triage flagged
"design needed before implementation" — list them here with a
one-sentence pointer to what needs to be decided.

## Recommended next move

One sentence: which issue to grab first, why, and the operator
prerequisite (e.g. "kick off as a clu plan" vs "drive manually in a
session" vs "open a brainstorm").
```

Commit with the project's structured commit format from `CLAUDE.md`
(Title / Why / What's new / Under the hood / Tests / Co-Authored-By).
Stage only the new file:

```bash
git add docs/triage/2026-05-11-issue-triage.md
git commit -m "$(cat <<'EOF'
plan-triage-issues phase synthesis: rank the 8 open issues

# Why
clu's `triage-issues` plan dispatched 8 workers, one per open
backlog issue. This commit consolidates the per-issue triage
comments into a single ranked file the operator can read in 3
minutes.

# What's new
- docs/triage/2026-05-11-issue-triage.md — top 3 picks, full
  comparison table, bundling recommendation, design-discussion
  callouts, recommended next move.

# Under the hood
Pure synthesis. No code, no tests, no state changes. The eight
authoritative triage assessments live as comments on issues
#1-#8; this file links to them.

# Tests
N/A — docs-only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Constraints

- The 8 triage comments are the source. Don't re-derive priority from
  scratch — your job is to **rank**, not re-triage. If you strongly
  disagree with a per-issue priority, you may rerank, but explain why
  in the "Why it's the top pick" rationale.
- One commit, one file.
- Don't reference the comment text verbatim in the file. Summarize and
  link.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan triage-issues --phase synthesis \
    --token <token> \
    --commit <sha>
```

## Escape hatch

`clu block` if:
- Fewer than 8 triage comments exist when you start (a prior phase
  silently no-op'd or skipped). List which issues are missing comments.
- Two triage comments are flatly contradictory in a way that affects
  the rank (e.g. both rate themselves P1 but the dependency graph
  says only one can go first).

Don't block for "I want operator sign-off on the rank." Post the
ranking; the operator reviews in the next session.
