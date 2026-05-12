# triage-issues-issue-7 — assess #7 (systemic-failure detection)

You are phase `issue-7` of the `triage-issues` plan. Read one issue,
assess it, post a single comment. Don't implement.

**Note:** This issue has the most live "ammo" of the eight — we hit
this exact failure mode twice during Day-3 (PATH bug + budget cap).
That's worth weighting in priority and is concrete evidence for the
"how often does this fire" question.

## Read first

- `plans/triage-issues.md` — **Triage format** section.
- The issue body:
  ```
  gh issue view 7 --repo smabe/end-of-line
  ```
- Code to ground effort/risk:
  - `end_of_line/dispatch.py` — the fast-fail detection already in
    place (post-spawn rc check, log path)
  - `end_of_line/state.py` — status transitions (`paused` vs `halted`);
    where to add a `STATUS_PAUSED_SYSTEMIC` or just reuse `paused` with a reason
  - `plans/.orchestrator/clu-docs.state.json` — read the real
    `dispatch_failed` events in there; they're the canonical examples
    of the failure signatures this issue wants to pattern-match

## Produce

A single comment on issue #7, master plan's triage format. In your
**Recommended approach**, mention the live evidence (Day-3 hit it
twice) — that's the kind of signal that promotes an issue.

```bash
gh issue comment 7 --repo smabe/end-of-line --body-file /tmp/triage-7.md
```

Sign off `— triaged by clu worker (phase: issue-7, token: <first-8>)`.

## Constraints

- Read-only.
- One comment. Resume-safe.
- Don't pre-rank against the other 7 (synthesis does that).

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan triage-issues --phase issue-7 \
    --token <token>
```

No `--commit` flags.

## Escape hatch

`clu block` only for unparseable issue, gh failure, or obsolete issue.
