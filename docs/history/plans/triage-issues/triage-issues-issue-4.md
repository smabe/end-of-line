# triage-issues-issue-4 — assess #4 (replan worker callback design)

You are phase `issue-4` of the `triage-issues` plan. Read one issue,
assess it, post a single comment. Don't implement.

This issue is labeled `question` — it's a design discussion, not a
concrete feature ticket. Your triage should reflect that: the
"recommended approach" is likely "run a brainstorm pass" or "draft a
mini-spec," not "ship it."

## Read first

- `plans/triage-issues.md` — **Triage format** section.
- The issue body:
  ```
  gh issue view 4 --repo smabe/end-of-line
  ```
- Code to ground assessment:
  - `end_of_line/state.py` — `STATUS_*` constants; confirm
    `STATUS_HALTED_REPLAN` is unused (`grep STATUS_HALTED_REPLAN end_of_line/`)
  - `end_of_line/supervisor.py` — the tick chain; where halt is handled
  - `end_of_line/cli.py` — existing operator commands (`retry`, `pause`,
    `resume`); replan sits adjacent to these

## Produce

A single comment on issue #4, master plan's triage format. Because
this is a design discussion, the **Open questions** field is the
weight-bearing one — be specific about what the brainstorm needs to
answer (the four numbered questions in the issue body are a starting
point; flag any you'd add).

```bash
gh issue comment 4 --repo smabe/end-of-line --body-file /tmp/triage-4.md
```

Sign off `— triaged by clu worker (phase: issue-4, token: <first-8>)`.

## Constraints

- Read-only.
- One comment. Resume-safe.
- Don't propose an implementation in the triage comment. The point of
  a `question`-labeled issue is that the design isn't settled.
- Don't pre-rank against the other 7.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan triage-issues --phase issue-4 \
    --token <token>
```

No `--commit` flags.

## Escape hatch

`clu block` only for unparseable issue, gh failure, or obsolete issue.
