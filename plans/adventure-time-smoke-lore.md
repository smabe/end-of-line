# adventure-time-smoke-lore — world + production facts

You are phase `lore` of the `adventure-time-smoke` plan. Web-search for
3–5 facts about Adventure Time's world (Land of Ooo, Mushroom War) and
production history, then APPEND a `## World & Production` section to
`adventure-time-facts.md`.

## Produce

1. **Web search** — 2–3 queries (e.g. "Adventure Time Land of Ooo
   Mushroom War", "Adventure Time production history Pendleton Ward",
   "Adventure Time Cartoon Network episode count seasons"). Pick concrete
   findable info.

2. **Append to `adventure-time-facts.md`** at repo root:
   - Add a `## World & Production` section after the existing
     `## Characters` section (phase `characters` wrote it).
   - 3–5 bullet-pointed facts. Each one sentence, concrete. Cover at
     least one world-building note + one production note.
   - One closing line citing where you got the info.

3. **Acceptance.**
   - `adventure-time-facts.md` has BOTH `## Characters` (from prior
     phase) AND `## World & Production` (yours).
   - Full suite green (regression guard).
   - No other files modified.

4. **Commit + complete.**
   - Title: `adventure-time-smoke: phase lore — world + production facts`
   - Stage: `adventure-time-facts.md` only.
   - `clu complete --plan adventure-time-smoke --phase lore --token <T>`

## Failure modes to watch

- **Section ordering** — `## World & Production` must come AFTER
  `## Characters`, not before. Append, don't prepend.
- **WebSearch refused.** Fall back to training-data knowledge
  (Mushroom War backstory, premiered Dec 2010 on Cartoon Network, 10
  seasons / 283 episodes, ended Sept 2018, distant-future Earth setting,
  Pendleton Ward → Adam Muto showrunner transition).
- **`adventure-time-facts.md` missing** — phase `characters` is
  supposed to create it. If for some reason it's absent, create it with
  a title line first, then append your section.
