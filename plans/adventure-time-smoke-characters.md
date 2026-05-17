# adventure-time-smoke-characters — main-cast facts

You are phase `characters` of the `adventure-time-smoke` plan. Web-search
for 4–6 facts about Adventure Time's main characters and write them to
`adventure-time-facts.md` at the repo root.

## Produce

1. **Web search** — 2–3 queries via the `WebSearch` tool (e.g. "Adventure
   Time main characters facts", "Marceline backstory creator", "Finn the
   Human Jake the Dog production"). Pick whichever returns concrete,
   citable info.

2. **Write `adventure-time-facts.md`** at repo root with:
   - A one-line title (`# Adventure Time — Smoke Test Facts`).
   - `## Characters` section with 4–6 bullet-pointed facts. Each fact is
     one sentence, concrete (year, voice actor, plot detail, behind-the-
     scenes note). Cover at least 3 distinct characters.
   - Note: phase `lore` will append `## World & Production` later — keep
     your section self-contained, don't pre-write that heading.

3. **Acceptance.**
   - `adventure-time-facts.md` exists at repo root with `## Characters`
     section, 4–6 facts.
   - Full suite green (regression guard): `python3 -m unittest discover
     -s tests`.
   - No other files modified.

4. **Commit + complete.**
   - Title: `adventure-time-smoke: phase characters — main-cast facts`
   - Stage: `adventure-time-facts.md` only.
   - `clu complete --plan adventure-time-smoke --phase characters --token <T>`

## Failure modes to watch

- **WebSearch refused / no results.** Fall back to your training-data
  knowledge of the show (Pendleton Ward creator, Finn/Jake/Bubblegum/
  Marceline/BMO/Ice King cast, post-apocalyptic premise, Cartoon
  Network 2010–2018). The smoke is about the protocol firing — perfect
  citations are nice-to-have, not required.
- **`adventure-time-facts.md` already exists.** Overwrite. This is a
  smoke test; idempotent.
