# clu-watch-docs — file issue + sweep + close (closes #N)

You are phase `docs` of `clu-watch`. File the GitHub issue this plan
implements (scope was operator-approved live; no pre-existing
issue), update the docs library, close the issue via the commit
message.

## Locked decisions (do NOT re-litigate)

See `plans/clu-watch.md` § Phase 6. Summary:
- Worker files the issue via `gh issue create` at the start of
  this phase.
- `(closes #N)` in the final commit title triggers GitHub
  auto-close on merge.
- Docs updates: `docs/reference.md`, `docs/operations.md`,
  `README.md`.

## Read first

- `docs/_outline.md` — structural contract for the docs library.
- `docs/reference.md` — find the CLI-command list + module list.
- `docs/operations.md` — find the "Background monitoring" section
  (per memory, this is where `/clu-monitor` install lives).
- `README.md` — find the observe / monitoring section.

## Produce

1. **File the GitHub issue first** (no test for this; gh is the
   action):
   ```bash
   gh issue create \
     --title "clu watch: streaming state-event projection for AI agents" \
     --body "$(cat <<'EOF'
   Streaming projection of per-plan state-machine events for
   AI-agent consumption (Claude's Monitor tool).

   Existing surfaces:
   - \`clu status [--json]\` — single snapshot, no stream.
   - \`clu logs --follow\` — tails the worker subprocess stdout
     (raw chatter, no state-machine structure).

   Gap: no streaming projection of the per-plan \`events\` array
   (\`EVENT_PHASE_STARTED\` / \`COMPLETED\` / \`BLOCKED\` / etc.).

   See \`plans/clu-watch.md\` for the full design. 6 phases:
   events / stream / cli / tips / skill-wire / docs.

   Operator-approved scope ahead of pre-existing issue. Plan files
   committed in \`860ff78\` precursor and queue-worker-callback
   ships ahead of this.
   EOF
   )"
   ```
   Capture the returned issue number; substitute `#N` in the
   final commit title.

2. **No code tests** (docs-only phase). Run the full suite as a
   regression guard at the end.

3. **Documentation updates.**
   - `docs/reference.md`:
     - CLI section gains `clu watch` entry with arg list + exit
       codes (mirror the `clu logs` / `clu status` entries).
     - Module section gains `watch.project_event` and
       `watch.stream_loop` entries.
   - `docs/operations.md`:
     - Under "Background monitoring", add subsection "Live
       in-session feed (`clu watch`)" with the basic invocation
       example and the Monitor-tool pairing. Position adjacent to
       the inbox-hook section.
   - `README.md`:
     - Under the observability / "what's clu doing" section, add a
       paragraph introducing `clu watch` as the live-feed sibling
       to the inbox hook.

4. **Acceptance.**
   - Issue filed and number captured.
   - All four docs files updated; no broken cross-references.
   - Full suite green (regression): `python3 -m unittest discover -s tests`.
   - `grep -n "clu watch\|watch.project_event" docs/ README.md`
     confirms the new content is in.

5. **Commit + complete.**
   - Title: `clu-watch: phase docs — reference + operations +
     README sweep (closes #N)`
   - Stage: `docs/reference.md`, `docs/operations.md`,
     `README.md`.
   - `clu complete --plan clu-watch --phase docs --token <T>`

## Failure modes to watch

- **`gh issue create` fails** — auth / repo not resolved / etc.
  Surface the error verbatim and `clu block` rather than
  proceeding without an issue number. The commit closing pattern
  depends on the issue existing.
- **Symbol drift between plan and code** — if any locked design
  decision changed during implementation (e.g. function rename),
  the docs entry should reflect what shipped, not what the master
  plan said. Audit `end_of_line/watch.py` and `cmd_watch` against
  the docs entries before committing.
- **`/simplify` mandate** — docs-only doesn't qualify; skip.
- **Trying to close the issue you just filed** — verify the issue
  number with `gh issue list` or capture stdout from
  `gh issue create` before composing the commit title. The
  `(closes #N)` pattern is one-shot; getting N wrong is mildly
  embarrassing.
