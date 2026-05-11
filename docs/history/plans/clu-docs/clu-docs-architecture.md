# clu-docs-architecture — write docs/architecture.md

You are running as phase 2 of the `clu-docs` plan. Phase 1 produced
`docs/_outline.md` defining what each docs file owns; you write the
architecture doc per that contract.

## Read first

- `docs/_outline.md` — your structural contract from phase 1
- `README.md` — already has a "How it works" section; use it as the
  starting voice but expand to full 1-page depth
- `docs/contract.md` — for boundaries (don't duplicate state schema or
  callback contract)
- `end_of_line/supervisor.py`, `end_of_line/state.py`,
  `end_of_line/dispatch.py`, `end_of_line/notify.py`,
  `end_of_line/notify_inbound.py`, `end_of_line/cli.py` — enough to
  ground every claim you make

## Produce

`docs/architecture.md` — a 1-page system shape doc. About 150-300
lines, no more. Cover:

1. **The big picture.** One paragraph: what clu is, what problem it
   solves, where it fits (cron + Claude + file state). Already in the
   README intro — adapt it, don't copy verbatim.

2. **Process model.** What runs where:
   - cron-fired `clu tick` (supervisor)
   - the inbound iMessage LaunchAgent (long-running poller)
   - the worker (one `claude --print` per phase)
   - the operator (a human with `clu status` and a phone)

3. **One-tick decision flow.** The supervisor's priority chain
   (`supervisor.tick`): lease release → stalled → SLA → blocker resume
   → terminal idle → active claim idle → dispatch → all-done →
   fallthrough idle. Use a bulleted list, not prose; this is how
   readers find the rule when debugging "why didn't this tick advance?"

4. **Data flow on a typical happy path.** From "operator runs
   `clu init`" → "cron fires `clu tick --dispatch`" → "supervisor
   dispatches phase" → "worker callback fires `clu complete`" → "next
   tick picks up the following phase." A diagram in ASCII or a
   numbered sequence works.

5. **Blocker round-trip.** Separate sub-section because it's the most
   non-obvious flow: worker `clu block` → iMessage outbound → operator
   replies on phone → inbound poller routes → `clu answer` → next tick
   consumes → worker re-dispatched with answer in state.

6. **Where things are NOT documented here.** Two-line "see also":
   - per-module API → `reference.md`
   - state schema and event types → `contract.md`
   - install + LaunchAgents → `operations.md`
   - project conventions → `conventions.md`

## Constraints

- Don't duplicate `contract.md` (no state schema, no callback table).
- Don't duplicate `reference.md` (no per-module API). Mention modules
  by name when describing flow, but don't enumerate functions.
- Don't duplicate `operations.md` (no install steps, no FDA grant).
- 1 page = ~150-300 lines including blank lines + the diagram. If
  you're tempted to add a 6th section, push back — that's reference
  material in disguise.

## Done

Run the full test suite first to make sure nothing broke — should
be a no-op for a docs-only change but worth proving:

```
python3 -m unittest discover -s tests
```

If 151 tests pass, commit per the project's structured commit format
and call:

```
clu complete --project <project> --plan clu-docs \
    --phase architecture --token <token> --commit <sha>
```

If the suite is red for any reason, `clu block` with a clear
question rather than committing on red.
