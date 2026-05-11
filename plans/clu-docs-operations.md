# clu-docs-operations — write docs/operations.md

You are running as phase 4 of the `clu-docs` plan. The operations
doc is the "I'm setting up clu on my machine and something is wrong"
manual.

## Read first

- `docs/_outline.md` — boundary contract
- `README.md` — already has Install + Configure + LaunchAgent
  sections. Expand here, don't duplicate.
- `examples/clu.inbound.plist`, `examples/clu.tick.plist`,
  `examples/clu-tick-all.sh`, `examples/clu-phase-skill.md` — the
  template files an operator actually copies
- `end_of_line/notify_inbound.py` — for the chat.db FDA error
  signature

## Produce

`docs/operations.md` — practical, production-flavored, no design
discussion. Cover at minimum:

1. **Prerequisites.** Python 3.11+, macOS (note Linux limitation:
   no osascript outbound, no Apple-format chat.db), `claude` CLI on
   PATH for worker dispatch.

2. **Install.**
   - `pipx install -e .` (path that works on PEP 668 systems)
   - Verify `clu --help` returns
   - Find the pipx venv python (needed for LaunchAgents) — typically
     `~/.local/pipx/venvs/end-of-line/bin/python3`

3. **Full Disk Access for the inbound poller.** Step-by-step:
   - System Settings → Privacy & Security → Full Disk Access
   - Add the pipx venv python (Cmd+Shift+G to paste an exact path)
   - Why: chat.db is protected user data; LaunchAgents inherit
     limited permissions

4. **Install both LaunchAgents.** For each:
   - Where to copy the plist
   - What to edit (absolute paths — LaunchAgents don't inherit PATH)
   - `launchctl bootstrap` command
   - How to verify it loaded (`launchctl list | grep clu`)
   - Where logs go (`/tmp/clu-*.{out,err}`)

5. **First plan.** A clean walkthrough:
   - Write a master plan with a Sessions index
   - Write sub-plan files
   - `clu init --project P --plan S`
   - First tick + what to expect
   - First blocker iMessage → reply on phone → resume

6. **Troubleshooting.** Specific symptoms and what to check:
   - "Inbound poller crash-looping" → check FDA grant, check the
     stderr log
   - "Worker dispatches but never completes" → check the worker log
     under `plans/.orchestrator/logs/`, check whether the
     `/clu-phase` skill is installed
   - "iMessage notifications not arriving" → check `imessage.to` in
     `.orchestrator.json`, check quiet hours, check Messages.app is
     signed in
   - "Plan halted on max-attempts" → `clu status` for the reason,
     `clu retry --plan S` once the underlying issue is fixed
   - "Stuck claim that won't release" → `clu pause`, edit the state
     file by hand, `clu resume` (last resort)

7. **Day-to-day commands.** A small table of the operator-side CLI:
   `clu`, `clu status`, `clu pause`, `clu resume`, `clu retry`,
   `clu list`, `clu unregister`, `clu answer`. Brief — full list
   lives in `reference.md` / CLI module section.

## Constraints

- Production focus. No design or rationale discussion ("why does the
  inbound poller exist") — that belongs in `architecture.md`.
- Concrete commands, not narrative. A reader scans, copy-pastes, and
  moves on.
- Don't duplicate README's quickstart wholesale — link to README for
  the public-facing intro and treat this doc as the deeper manual.

## Done

```
python3 -m unittest discover -s tests
```

Then commit + complete with the SHA per the project's commit format.
