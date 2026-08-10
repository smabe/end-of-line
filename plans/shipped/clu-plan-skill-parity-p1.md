# clu-plan-skill-parity-p1 — format-contract corrections

You are phase `p1` of the `clu-plan-skill-parity` plan. Fix the places where clu-plan's SKILL.md documents contracts that the code contradicts, plus one stale citation in the SessionStart hook. One commit.

## Locked decisions (do NOT re-litigate)

See the master `plans/clu-plan-skill-parity.md`. Binding here:
- Edit the REPO copy `end_of_line/skills/clu-plan/SKILL.md` only. Do NOT run `clu install-skill` (that happens once, at ship, with `--only clu-plan`).
- No runtime code changes. `plan_parser.py` is the source of truth; the doc moves to match it, never the reverse.
- Line references below are tagged @3d51805; re-anchor by the quoted text if lines have shifted.

## Work

- `end_of_line/skills/clu-plan/SKILL.md` — three corrections:
  1. **Effort formats.** The line "Formats accepted: `45m`, `1h`, `2.5h`, or a bare integer interpreted as minutes" (near :360, in the "Sessions index is load-bearing" block) is false. `parse_effort_minutes` accepts `Nh` / `Nmin` (decimals ok, case-insensitive) and ranges `N-Mh` / `N-Mmin`; `45m` and bare integers return None, which silently falls back to the default lease — the exact mid-phase-expiry footgun the same paragraph warns about (plan_parser.py:17-18,94-104; tests/test_plan_parser.py). Rewrite the sentence to name the real formats (`1h`, `2.5h`, `90min`, `1-2h`), state that a range resolves to its UPPER bound (the range regex captures the post-dash number; probe-verified: `1-2h` → 120), and state what a non-matching value does (None → default lease TTL, no error) — naming `45m` and bare `90` as the seductive counter-examples.
  2. **Single-file-plan claim.** The intro sentence "A `/plan`-style single file fails `parse_sessions_index()` and the supervisor errors…" (near :21) is right about the outcome, wrong about the mechanism: the parser returns `[]` (plan_parser.py:3-6) and the SUPERVISOR errors `no Sessions index in <path>` at dispatch (supervisor.py:834-836). Reword to "yields no phases, and the supervisor errors `no Sessions index in plans/<slug>.md` at dispatch".
  3. **Byte-exact heading note.** The `## Sessions index` heading the note concerns sits INSIDE the fenced master-template code block, where prose cannot go — so place the note as its own short paragraph immediately AFTER the template's closing fence, ahead of the "The Sessions index is load-bearing" paragraph (probe-validated placement). Content: the heading must be byte-exact `## Sessions index` (case-sensitive, single space) — the machine-wide plan-draft gate uses that exact spelling to exempt clu masters from its write-freeze (`~/.claude/hooks/plan_draft_gate.py:934`), while clu's own parser is case-insensitive (plan_parser.py:20), so a variant spelling parses fine for clu yet loses the exemption.
- `end_of_line/hooks/clu_session_start.py` — the comment "Compressed from /clu-plan SKILL.md 'Reacting to task-list protocol notifications' (lines 327-373)" (:52-53) cites lines that are already stale (the section now sits ~:578) and will drift further this plan. Drop the line range; cite by section name only. Comment-only change — the `TASK_LIST_PROTOCOL_INSTRUCTION` string itself is untouched.

- Consumes: none
- Produces: none (documentation text only; no interface changes)

## Decisions & findings

**SHIPPED at c040966 (2026-08-10).** Worker findings, transcribed from its report:
- Every phase claim re-verified at implementation time; the supervisor error line had drifted one line to supervisor.py:836 (re-anchored by quoted text, as the shard instructed).
- The draft-gate citation was deliberately written into the SKILL.md note by path only, without `:934` — a line number into a machine-local hook file outside this repo would go stale with nothing in this repo to notice.
- Suite-reading tip: the unittest summary is invisible under `| tail` (tests print trailing stdout); `grep -E "^(Ran|OK|FAILED)"` is the reliable read.

### Decision: doc moves to parser, not parser to doc  *(status: active)*
- **Rationale:** `parse_effort_minutes`'s formats shipped in lease-reliability (#57/#58) and are test-pinned; the doc is the drifted party.
- **Alternatives considered:** widening the parser to accept `45m`/bare ints — rejected: runtime change out of this plan's scope, and bare ints are ambiguous (minutes vs hours).
- **Evidence:** plan_parser.py:17-18,94-104 @3d51805; master Non-goals ("No runtime code changes").

## Failure modes to anticipate

- Editing the installed copy `~/.claude/skills/clu-plan/SKILL.md` instead of the repo copy — the edit is invisible to git and gets clobbered at the next install.
- Touching the task-list protocol section's literal strings while editing nearby — `tests/test_task_list_skill_wire.py:16-31` pins them; run the suite.
- "Fixing" the Effort row examples in the worked example (`| timeout | … | 1h |`) — those are already valid; only the "Formats accepted" sentence lies.

## Done criteria

- Produced observable: a one-shot `python3 -c` run that calls `end_of_line.plan_parser.parse_effort_minutes` on every format string the revised sentence names (each returns the expected minutes) AND on `45m` and `90` (each returns None), output shown in the phase report.
- `grep -n "lines 327-373" end_of_line/hooks/clu_session_start.py` returns nothing.
- Full suite green: `python3 -m unittest discover -s tests`.
