# worker-path-config-docs — operations.md + contract.md + close #9

You are phase `docs` of the `worker-path-config` plan. Phases `env`
(already done by the time you run) and the config-field phase (commit
`2f9316b`) have shipped the implementation. Your job is the documentation
and closing issue #9.

## Locked decisions (do NOT re-litigate)

See `plans/worker-path-config.md`. Summary:

- Field is `dispatch.path` (string, optional, empty=inherit).
- Operators set absolute paths; no tilde expansion.
- Example value to recommend:
  `/opt/homebrew/bin:/usr/local/bin:/Users/<u>/.local/bin:/usr/bin:/bin`
  — covers Homebrew (Apple Silicon + Intel), pipx, system.

## Read first

- `docs/operations.md` — find the troubleshooting section (likely
  near the bottom). The new entry slots in there. Read the existing
  troubleshooting tone and match it.
- `docs/contract.md` — find the section that documents the config
  schema (the `.orchestrator.json` shape, where `dispatch.command` is
  listed). Add `dispatch.path` to the same table/section. If
  `dispatch.command` is NOT documented in `docs/contract.md`, skip
  this file — do not invent a new section just for `path`.
- GH issue #9: `gh issue view 9 --repo smabe/end-of-line` —
  re-read the AC. The relevant ones for this phase:
  - Decide path: BOTH was chosen (config-driven + docs)
  - Docs-only line in `docs/operations.md` troubleshooting block
- `plans/worker-path-config.md` — the master, for context.

## Produce

1. **Add the troubleshooting entry to `docs/operations.md`.** Format
   should match existing troubleshooting entries in the file. Content
   target (adapt the prose to the file's voice):

   > **Symptom:** worker log shows `<tool>: command not found`
   > (typical: `gh`, `pipx`, anything from `~/.local/bin`)
   > **Cause:** worker subprocess inherits a sparse PATH from the
   > LaunchAgent that dispatched it — `claude --print` doesn't get
   > the operator's shell PATH.
   > **Fix:** set `dispatch.path` in `.orchestrator.json` to an
   > absolute, colon-separated PATH:
   > ```json
   > "dispatch": {
   >   "command": "...",
   >   "path": "/opt/homebrew/bin:/usr/local/bin:/Users/<u>/.local/bin:/usr/bin:/bin"
   > }
   > ```
   > When `path` is set, clu passes `env={**os.environ, "PATH": ...}`
   > to the worker `subprocess.Popen`. Empty or absent = inherit the
   > parent env (current behavior).

2. **Add `dispatch.path` to `docs/contract.md`** alongside
   `dispatch.command` if and only if the config schema is documented
   there. Same row/table style as the existing entry. A one-liner
   description like:
   > `dispatch.path` (optional string, default `""`) — colon-separated
   > PATH for worker subprocesses. When set, passed via `env=PATH=...`
   > to `Popen`. Use absolute paths only (no tilde expansion).

3. **Run the test suite.** Docs-only edits should not break tests,
   but per CLAUDE.md mandate #9 re-run from a clean process:
   `python3 -m unittest discover -s tests`. Confirm the count
   matches phase 2's result (expected 229).

4. **Commit.** Title:
   `worker-path-config phase 3: docs for dispatch.path + close #9`.
   Use the structured format (Title / Why / What's new / Under the
   hood / Tests / Co-Authored-By trailer). Add `Fixes #9` to the
   commit body so GitHub auto-closes the issue when pushed.

5. **Verify issue #9 closes.** After the commit lands, the next
   `git push` triggers GitHub to close #9. Don't try to close #9 by
   shelling out to `gh issue close` (that's exactly the bug pattern
   we're documenting). The `Fixes #9` trailer is processed by
   GitHub-side machinery, no PATH dependency.

6. **Call `clu complete` with the worker token.** Summary should
   include the commit SHA, the test count after the suite re-run, and
   one-line confirmation that #9 has the `Fixes` trailer attached.
   Per mandate #9, re-run the test suite from a clean process right
   before calling `complete`.

## Failure modes to watch for

- **`docs/contract.md` may not document the schema.** If you search
  it for `dispatch.command` and find nothing, the schema lives
  elsewhere or is implicit. Don't invent a section — skip the
  `contract.md` edit, note it in the commit message, and move on.
- **Markdown table alignment.** If `operations.md` uses fenced tables
  with column alignment, match the style exactly (don't break
  pipe-table rendering).
- **`Fixes #9` casing.** GitHub accepts `fixes`, `Fixes`, `closes`,
  `Closes`, `resolves`. Use `Fixes #9` to match this project's prior
  trailers if any exist; otherwise the project hasn't shipped issue
  closures yet via commit trailers and either form is fine.
- **Quiet hours.** Per the locked config in CLAUDE.md, quiet hours
  are 22:00–08:00 — that affects iMessage notifications, not your
  ability to commit. Proceed regardless of clock time.

## Done criteria for this phase

- `docs/operations.md` has a troubleshooting entry for the
  `command not found` symptom referencing `dispatch.path`.
- `docs/contract.md` lists `dispatch.path` alongside
  `dispatch.command` IF the schema is documented there; otherwise
  noted in the commit body as "schema not in contract.md, deferred".
- Full test suite green from a clean process.
- One commit, structured message, `Fixes #9` trailer present.
- `clu complete` called with SHA + count summary.
