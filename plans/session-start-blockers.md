# session-start-blockers — answer a clu blocker from the session again

Retiring the `UserPromptSubmit` inbox surface removed the only place clu ever
rendered a blocker's *options* into a Claude Code session, and the only place
that told the session how to route a reply. `clu watch --all --operator` still
streams `phase_blocked`, but the line is `slug/phase: BLOCKED q-1 — <question
truncated to 100 chars>` (`watch.py:113-119`) — no options, once, forward-only.
So "option 2" has nothing to resolve against.

Restoring it exposed three defects in the answer path that predate the
retirement, all reproduced by probe this session. `clu answer` resolves a reply
against **every registered plan on the host** (`cli.py:4801` passes
`registry.entries()`), while every display path filters by project — so a reply
typed in project A can silently answer a question in project B purely because
B was pinged more recently. `--plan` does not save you: `route_reply` matches
on slug alone, and the registry's key is `(project_root, plan_slug)`, so two
projects may share a slug. And two open blockers on one plan are stamped with
the same timestamp by construction (`state_locator.py:127-131` takes the plan's
most recent blocked event, not the blocker's), so when two siblings are both
eligible for the same digit their tiebreak cannot resolve and the reply returns
`None` — repeatably, not once. (Probed: with only one sibling eligible for that
digit it still resolves, so the deadlock needs two.)

Phase 1 is smaller than it looks: `README.md:241` and `docs/operations.md:2450`
already document `clu answer [--project P --plan S] <id> <text|index>`, a
two-positional form the CLI never grew — `cli.py:1061-1064` takes ONE
positional. Phase 1 makes the documented surface real rather than inventing one.

Phase 1 makes a blocker addressable and the answer path scoped. Phase 2 puts
open blockers, with options, into the session at start on top of that. The
order is forced: surfacing blockers before the answer path is scoped would ship
a feature whose happy path is a silent cross-repo misroute.

## Diagnosis

- **Hypothesis:** the plain-language reply affordance lived only in the retired
  inbox hook, which rendered each blocker's numbered options and instructed the
  session to call `clu answer`; nothing else puts options in FRONT OF A SESSION,
  and the CLI itself never accepted prose.
- **Falsifiable test:** call `notify_base.route_reply` with prose, a letter, and
  a two-digit index; grep every other surface for an options render.
- **Test result:** CONFIRMED, probed this session. `route_reply("yes, go with
  the bcrypt path", …)`, `("the second one", …)`, `("B", …)` and `("12", …)` all
  return `None`; only a bare single digit routes. `REPLY_RE`
  (`notify_base.py:18`) is `^\s*(?:(<slug>)\s+)?([0-9])\s*$`. The only SESSION-FACING
  options render is `clu_inbox_surface.py:212-232`, behind `--inbox`. Five
  other sites render options to a terminal or a notification
  (`cli.py:4608-4609`, `:6832`, `:6866`, `state_blocker.py:116`, `:164`) —
  none reaches a Claude session.
  Storage was never the constraint: `_resolve_answer` (`plan_store.py:1043-1051`)
  stores any non-digit answer verbatim.

## Locked design decisions

### Phase 1 — answer-scope

- **`--project` becomes load-bearing, not ignored.** The flag already exists and
  its help says "ignored; kept for backward compat" (`cli.py:1051-1055`). When
  passed, `cmd_answer` resolves against `registry.entries_for_project(root)`
  (`registry.py:55-57`, already used at `cli.py:3140` and
  `cross_plan_rules.py:61`) instead of `registry.entries()`. Omitted, behavior is
  unchanged and host-wide — a bare terminal `clu answer 2` keeps working.
- **Why revive rather than add a flag:** `notify_imessage_inbound.py:104-105`
  already passes `--project str(target.project_root)` (inside `_cli_dispatch`,
  `:94-111`). The caller resolved the
  target and told `clu answer` which project it meant; the command threw it
  away. Honoring it narrows that call to the project the poller already picked,
  so the poller gets strictly more correct with no call-site change.
- **`--blocker <q-N>` for explicit addressing.** With `--plan` and `--blocker`,
  skip `find_blocker_for_reply` entirely and call `plan_store.op_answer_blocker`
  directly — it already takes `blocker_id` and a raw answer string
  (`plan_store.py:1054-1068`). This is the path the session hook uses, and it is
  what makes a free-text answer possible: index translation stays inside the op
  and applies only to a bare digit.
- **Scoping happens by PRE-FILTERING the entry list, not inside `route_reply`.**
  `route_reply(text, blockers)` takes no project argument (`notify_base.py:54-57`)
  and 12 existing tests call it with two (`tests/test_notify_inbound.py:120-230`);
  a reply string carries a slug and nothing else, so there is no project for it
  to compare against. Filter the entries before they reach the locator and the
  same-slug collision cannot arise — probed: `alpha 2` resolves to `/p/a` when
  `/p/b` was meant, but only because both sat in one pool. The registry's key is
  `(project_root, plan_slug)` (`registry.py:68-75`), which is what makes the
  collision possible at all.
- **Per-blocker `last_notified_at`.** `_hydrate_open_blockers`
  (`state_locator.py:122-141`) stamps every open blocker with the plan's most
  recent `phase_blocked` event. The events carry `blocker_id`
  (`plan_store.py:1035`), so matching the event to its own blocker is a lookup
  fix, not a schema change. Without it, two same-plan siblings eligible for the
  same digit tie forever and no bare digit resolves.
- **Notify-channel pollers keep the host-wide pool.** An iMessage or Discord
  reply has no working directory to scope by.

### Phase 2 — surface

- **Blocker scanning bypasses the liveness gate.** `_scan_entries` treats any
  non-terminal status as live (`clu_session_start.py:117`) and `main` emits
  nothing when nothing is live (`:174`). `TERMINAL_STATUSES` contains
  `STATUS_PAUSED` (`state.py:139`), and the supervisor pauses a plan when a
  blocker breaches `blocked_question_sla_hours` (`supervisor.py:1082-1086`,
  default 24h). Gating blockers on that switch would hide the blocker that has
  waited longest — the one that most needs surfacing. Blockers get their own
  scan and their own emit condition.
- **Current project only**, via `registry.entries_for_project(cwd)`. This is
  also the cost control: the installed hook entry carries `"timeout": 5`
  (`cli.py:2730`) and `load_entry_state` opens one SQLite database per entry, so
  scanning only this project's plans bounds the walk regardless of how many
  plans exist host-wide.
- **Render plan, phase, blocker id, question, numbered options** — the id is the
  handle Phase 1 made addressable, and the options are the thing no other
  surface emits.
- **The instruction names `--blocker` and refuses to guess.** More than one open
  blocker and an ambiguous reply → ask which, never rank. Corroborated by prior
  art: LangGraph resumes interrupts by explicit id and documents index-based
  matching as fragile; OpenAI's Agents SDK scopes approvals to a `call_id`;
  LangChain's Agent Inbox makes the human select the interrupt first.
- **Budget: `MAX_BLOCKERS = 10` plus question truncation, held under 9500
  chars.** Phase 2 must DEFINE both constants in `clu_session_start.py` — it has
  none today. The similarly-named constants live in `clu_inbox_surface.py:33,36`,
  the file phase 2 is forbidden to import, and 9500 appears in this hook's tests
  only as a bare literal (`tests/test_session_start_hook.py:99,372`). The
  10,000-character cap on `additionalContext` is documented
  (https://code.claude.com/docs/en/hooks.md, JSON output): overflow is saved to a
  file and replaced with a preview plus path.
- **Emit through `hookSpecificOutput.additionalContext` only.** Emitting
  `additional_context` as well is read by Claude Code without dedup and
  duplicates the block (anthropics/claude-code#14281, obra/superpowers#648).

## Non-goals

- **Not reinstating the `UserPromptSubmit` inbox surface.** This plan replaces
  its one load-bearing job — re-asserting open blockers as state — with a state
  read at session start. The inbox's consume-once machinery (claim transaction,
  processed flag, two-session race guard) exists to stop an event being
  delivered twice; open-blocker state disappears when answered, so none of it is
  needed.
- **A blocker raised mid-session gets a NOTIFICATION but not its options.**
  The peer case to the one in scope, so it needs a rationale — and the honest
  one is narrower than the first draft claimed. `clu watch --all --operator`
  does stream `phase_blocked` live (`watch.py:113-119`), so the session learns a
  blocker exists; but that line carries NO options, which is the complaint this
  plan opens with, so "the Monitor covers it" would be false. The asymmetry is
  safe in this narrower sense: the operator is at the session when it fires,
  sees the question, and can run `clu blockers list` (`cli.py:6832` renders the
  options) or answer by id once phase 1 lands. The predates-the-session case has
  no such recovery, which is why it is the one in scope. Closing the mid-session
  gap properly means re-rendering on the SessionStart re-fires (`clear`,
  `resume`, `compact`, `fork` — https://code.claude.com/docs/en/hooks.md,
  matcher table) and widening the watch line; that is a follow-up.
- **Not scoping the iMessage / Discord inbound pollers.** They resolve a target
  before invoking `clu answer` and have no cwd to scope by; their breadth is
  deliberate. Phase 1 changes `cmd_answer` only when `--project` is passed, so
  their behavior is unchanged except where the iMessage poller already passes it
  and gets a narrower, more correct pool.
- **Not adding a `clu inbox` lister.** The inbox stays dormant; nothing in this
  plan reads it.
- **Not reconciling the install marker with settings.json.** The host database
  was recreated during the SQLite cutover, so `monitor.load_marker()` is `None`
  while the SessionStart entry is live in settings.json. That is an operational
  re-stamp (`clu install-hook` is idempotent), not a code change, and no code in
  this plan reads the marker.

## Files touched

- `end_of_line/cli.py` — P1 modified — `cmd_answer` scoping + `--blocker` flag +
  `--project` help text. **API hotspot:** `clu answer` argument surface
  (`cli.py:1049-1064`); the iMessage poller invokes it as a subprocess.
- `end_of_line/notify_base.py` — P1 read-only unless `OpenBlocker` changes —
  `route_reply` keeps its two-argument shape. **API hotspot:** the `OpenBlocker`
  field set — dataclass equality is load-bearing at `state_locator.py:62`, which
  recovers the matched row by `==`, so adding a field there is a routing change.
- `end_of_line/state_locator.py` — P1 modified — per-blocker `last_notified_at`
  in `_hydrate_open_blockers`; `find_blocker_for_reply` accepts a pre-scoped
  entry list. **API hotspot:** called by both inbound pollers.
- `end_of_line/hooks/clu_session_start.py` — P2 modified — blocker scan, render,
  instruction. **API hotspot:** installed by absolute path in the operator's
  `settings.json`; under `pipx install -e .` an edit is live machine-wide at the
  next session start, before tests or commit.
- `end_of_line/notify_inbound.py` — P1 modified — re-exports `route_reply` /
  `OpenBlocker` at `:23` to keep old import paths working; a signature change
  passes through here.
- `README.md` (`:241`), `docs/operations.md` (`:1931`, `:2450`) — P1 modified —
  they already document the `<id> <text|index>` form; phase 1 makes them true.
- `plans/session-start-blockers.md` — P1, P2 modified — both phases append to
  `## Findings log`.
- `tests/test_answer_scoping.py` — P1 NEW — scoping, `--blocker`, slug collision,
  sibling-tie regression.
- `tests/test_session_start_hook.py` — P2 modified — blocker fixtures. Currently
  contains none, so every failure mode in Phase 2 is invisible to the suite today.
- `docs/operations.md`, `docs/architecture.md`, `README.md`,
  `end_of_line/skills/clu-monitor/SKILL.md` — P2 modified — the blocker-reply
  path. `README.md` currently states the affordance is lost; Phase 2 makes that
  wrong.
- `end_of_line/skills_manifest.json` — P2 modified — regenerate after the skill edit.

## Per-phase done checklist

- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood / Tests /
  `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- **Stamp attestations AFTER the commit** — the gate compares stamp SHA against
  HEAD.
  - `clu verify --plan session-start-blockers --phase <id> --token <T>`
  - `clu attest --simplify --plan session-start-blockers --phase <id> --token <T>`
- `clu complete --plan session-start-blockers --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| answer-scope | `session-start-blockers-answer-scope.md` | Honor `--project` by pre-filtering the entry list, add `--blocker`, per-blocker timestamps | 3h |
| surface | `session-start-blockers-surface.md` | Emit this project's open blockers + options + routing instruction at session start | 2h |

## Verification record

- grounding: 57 claims checked, 47 resolve · 7 fixed (one wrong line number, one
  overstated "only options render", one overstated deadlock claim, the
  nonexistent 9500/MAX_BLOCKERS constants, a function-local `st` import, two
  off-by-one Read-first ranges) · 0 promoted · 0 refuted
- executability: 9 acceptance bullets and 14 named tests across 2 sub-plans,
  19 Read-first pointers (all resolve), 8 Files-touched entries · 6 fixed
  (unproduced char budget, uncaught `KeyError`, unsourced `state_path`
  resolution, 3 missing Files-touched paths) · 0 promoted
- coherence: 11 stated rules against their mechanisms, 6 characterizations,
  7 cross-file restatements · 2 contradictions fixed (9500-vs-10k acceptance
  ceiling; poller-reaches-the-answer-path-by-subprocess vs by-function-call)

One finding is carried to the operator rather than closed: the mid-session
Non-goal's original rationale was FALSE — it claimed the watch Monitor covers
mid-session blockers, when that line renders no options. Rewritten to the
narrower claim that survives, with the real fix named as a follow-up.

## Findings log

_(empty at plan time — workers append cross-phase findings as phases run)_
