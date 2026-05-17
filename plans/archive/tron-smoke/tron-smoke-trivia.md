# tron-smoke-trivia — google Tron facts and commit them

You are phase `trivia` of the `tron-smoke` plan. This is a touch task
to validate `clu watch` end-to-end — the watch stream is the real
deliverable, the file is just proof the worker ran.

## Produce

1. **Web search** for "Tron 1982 film trivia facts" (or equivalent
   query — pick whichever returns useful behind-the-scenes info).
   Use the `WebSearch` tool if available, otherwise `WebFetch` on a
   well-known source. Three or four searches is fine; don't go
   spelunking.

2. **Write `tron-facts.md`** at the repo root with:
   - A one-line title.
   - 5–8 bullet-pointed facts about the film. Each fact one sentence,
     concrete (year, person, technical claim). No fluff like "Tron
     was groundbreaking" without a specific reason.
   - One closing line citing where you got the info (e.g.
     "Sources: en.wikipedia.org/wiki/Tron, ...").

3. **Acceptance.**
   - `tron-facts.md` exists at repo root, 5–8 facts, sources cited.
   - Full suite green (regression guard, even though no logic
     changed): `python3 -m unittest discover -s tests`.
   - No other files modified.

4. **Commit + complete.**
   - Title: `tron-smoke: phase trivia — tron facts smoke`
   - Body: 1-2 lines on what the smoke validated.
   - Stage: `tron-facts.md` only. NOT `git add -A`.
   - `clu complete --plan tron-smoke --phase trivia --token <T>`

## Failure modes to watch

- **Web search refused / no results.** Fall back to your training-
  data knowledge of Tron 1982 and cite the year/director/known
  facts (Steven Lisberger directed, released 9 July 1982, Disney,
  Wendy Carlos scored, MAGI / Information International / Robert
  Abel & Associates / Digital Effects for CGI, Cindy Morgan + Jeff
  Bridges + Bruce Boxleitner cast). Just commit and complete —
  the smoke is about the watch stream firing, not perfect citations.
- **`tron-facts.md` already exists.** Overwrite. This is a smoke
  test; idempotent.
- **/simplify** — doesn't apply (single-file content commit, no
  logic).
