# session-activity-agents — decode Agent/Task tool_use into an `agent` feed kind

You are phase `agents` of the `session-activity` plan. It delivers one commit: the activity decoders learn the `Agent`/`Task` tool, so a `/code-review` fan-out or any subagent spawn shows up — in the row (`extract_activity`) and in the detail feed (`record_events`) as a new `agent` kind. Benefits workers AND sessions (shared decoders). This is the literal payoff of the user's ask: "when claude runs code-review or uses any agents, see it."

## Locked decisions (do NOT re-litigate)
See master `plans/session-activity.md`. Binding here:
- **Spawn-level only.** Decode the parent transcript's Agent/Task `tool_use` (+ enrich from the matching `tool_result`). Do NOT recurse into the child `agent-<agentId>.jsonl`. (Master non-goal: nesting interior steps is deferred.)
- New feed kind string: `agent`. Joins the existing `say`/`tool`/`write`/`result`.
- Tool names to match: both `"Agent"` and `"Task"` (the tool has been named both across versions — match either, like `_WRITE_TOOLS` is a set).

## Work
- `end_of_line/top.py`:
  - **`_AGENT_TOOLS = frozenset({"Agent", "Task"})`** module constant (beside `_WRITE_TOOLS`, top.py:33).
  - **`extract_activity`** (top.py:174): in the `tool_use` branch (top.py:202–211), add `elif name in _AGENT_TOOLS:` → set a new latest-signal, e.g. `last_agent = inp.get("subagent_type") or inp.get("description")`. Surface it in the returned dict as `last_agent` (append-only). Decide its render: simplest is to fold it into `last_command`/`last_text` semantics — RECOMMENDED: add `last_agent` to the return and let the row's SAYING/COMMAND path show e.g. `spawned: code-review` only when present (a session running code-review has no Bash, so `last_command` would be `—`; `last_agent` fills the gap). Keep it a distinct key so renderers choose.
  - Agent `input` keys are exactly `description`, `prompt`, `subagent_type` (empirically confirmed). Prefer `subagent_type` (e.g. `Explore`, `general-purpose`); for a skill fan-out like `/code-review` the subagent_type names the reviewer agent.
- `end_of_line/webserver.py`:
  - **`record_events`** (webserver.py:439 @7dbe001): in the assistant `tool_use` branch (webserver.py:467–474), add `elif name in top._AGENT_TOOLS:` → `_emit("agent", inp.get("subagent_type") or inp.get("description"))`. Every spawn emits an `agent` event (the feed keeps every occurrence, unlike the row's latest-only).
  - **Optional enrich (in-scope, cheap):** the matching `tool_result` (`user` record) carries `toolUseResult.agentType`/`status`/`totalTokens`/`totalDurationMs`. The existing `result` branch (webserver.py:475–478) already emits tool_results as `result` kind — a code-review result is large transcript text. Leave `result` as-is for v1; the `agent` spawn event is the high-value signal. (If trivial, append `status` to the agent event text — but do not parse the child file.)
- `end_of_line/web/index.html`: render the `agent` feed kind in the detail pane with distinct styling (icon/color), parallel to how `say`/`tool`/`write`/`result` are styled. Grep the feed-event renderer (the code consuming `/api/feed` events' `kind`).
- `docs/reference.md`: add `agent` to the documented feed kinds (webserver section, ~:1272–1278) and note `_AGENT_TOOLS` in the top.py section (~:1122 `extract_activity`).
- `tests/`:
  - `extract_activity`: a transcript with an Agent tool_use (`subagent_type:"Explore"`) → `last_agent == "Explore"`; Task name also matches; falls back to `description` when `subagent_type` absent.
  - `record_events`: an Agent/Task tool_use record → one `{kind:"agent", text:"Explore"}` event; multiple spawns → multiple events in order; non-agent records unaffected (existing `say`/`tool`/`write`/`result` tests stay green).

## Decisions & findings
### Decision: spawn-decode in the parent transcript, no child-file recursion  *(status: active)*
- **Rationale:** the parent's `tool_use` (and its `tool_result` enrich) already answers "what is claude running right now" — `subagent_type` + `status` + token/duration totals. Reading `agent-<agentId>.jsonl` to nest the child's own Bash/edits adds a second recursive tail with its own freshness/contamination handling for marginal display gain, and the child file is written *before* the parent's tool_use lands (orphan window) — correlation timing is fiddly. Spawn-level is the 80% at ~10% of the cost.
- **Alternatives considered:** follow `toolUseResult.agentId` → child file and nest its events — deferred to a follow-up (master non-goal); the correlation chain is recorded in master Background findings so a later phase can pick it up without re-research.
- **Evidence:** Agent `input` shape + `tool_result.toolUseResult.agentId`/`agentType`/`status` empirically confirmed (CC v2.1.174); decoder sites `extract_activity` top.py:202–211, `record_events` webserver.py:467–478 (@7dbe001).

## Failure modes to anticipate
- **Tool rename drift:** if a future CC version renames the tool again, the set misses it. Matching both `Agent` and `Task` covers the known names; a miss degrades to "no agent event," not a crash. Note the set as the one place to extend.
- **`last_agent` clobbering `last_command`:** if folded into the same field, a session that ran Bash THEN spawned an agent could show stale data. Keeping `last_agent` a separate key avoids this; renderers decide precedence. Test both-present ordering.
- **Large `description` text:** `record_events` already `_truncate`s to `FEED_TEXT_CAP`; the agent event passes through `_emit` → `_truncate`, so a giant prompt can't blow the feed. Confirm `subagent_type` (short) is preferred over `description` (long).
- **Existing feed-kind tests:** adding a branch must not reorder or drop existing emissions — run the full `record_events` test set unchanged.
- **Web renderer unknown-kind fallthrough:** if index.html's feed renderer switches on `kind` without a default, an `agent` kind could render blank — add the case AND confirm a graceful default exists.

## Done criteria
- A worker/session running `/code-review` or spawning an Agent shows an `agent`-kind event in the detail feed; `extract_activity` exposes `last_agent` on the row.
- `Agent` and `Task` both decode; `subagent_type` preferred, `description` fallback; truncation applied.
- Existing `say`/`tool`/`write`/`result` decoding unchanged (tests green).
- `docs/reference.md` lists the `agent` feed kind.
- Full suite green; `clu verify` clean (basedpyright); `/code-review` run on the diff.
