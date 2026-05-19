# reliability-batch-2 — heartbeats + worktree-context + verify-opt-out

Second batch of follow-ups from the icloud-container-sync incident
(2026-05-19). The first batch (`lease-reliability` + `watch-bootstrap-active`)
closed #57, #58, #62, and #63. This plan closes the remaining three
issues filed against that same incident: #59 (heartbeats), #60
(worktree handoff context), #61 (verify-required opt-out).

Single-file plan — worked through phase-by-phase ourselves, not
dispatched via clu. The SKILL.md churn across three phases would be
unsafe to parallelize per `[[feedback_clu_queue_concurrent]]` + the
SKILL.md drift mode noted in #64.

## Ordering rationale

- #59 first (cheapest; gives real-time observability for #60/#61 work).
- #60 second (#57's reap is prerequisite; landed last batch).
- #61 last (most SKILL.md churn — minimize rebases).

## Phase 1 — heartbeat-ticker (#59)

**Goal:** workers ping `clu heartbeat` every ~2min during long phases
so `clu status` stops mis-reporting STALLED on healthy long-running
work.

**Locked decisions:**

- **Clu-side: no changes.** `cmd_heartbeat` already calls
  `state.record_heartbeat` which correctly stamps
  `current_claim.last_heartbeat_at` (state.py:484). The entire bug
  is that the worker never calls heartbeat — this is a SKILL.md fix.
- **Pattern:** background subshell loop in the worker prompt:
  ```bash
  ( while :; do
      clu heartbeat --plan <slug> --phase <id> --token <T> >/dev/null 2>&1
      sleep 120
    done ) &
  HEARTBEAT_PID=$!
  trap "kill $HEARTBEAT_PID 2>/dev/null" EXIT
  ```
- **2min interval:** well inside the default `stalled_heartbeat_minutes: 10`
  threshold; loose enough that state.json writes don't pile up.
- **EXIT trap:** ensures the ticker cleans up if the worker exits
  abnormally. Workers historically forget background cleanup; the
  trap goes IN the snippet, not as a separate "remember to kill the
  ticker" instruction.
- **Placement:** add the snippet near the top of `/clu-phase`
  SKILL.md, before the "Read the sub-plan" step. Workers should arm
  the ticker as their first action so heartbeat freshness covers the
  entire phase including sub-plan reading.
- **No conditional logic** — every worker arms the ticker on every
  attempt. Cheap and simple.

**Files touched:** `end_of_line/skills/clu-phase/SKILL.md`

**Acceptance:**
- SKILL.md contains the background-ticker snippet with EXIT trap.
- `clu install-skill --force --only clu-phase` deploys to
  `~/.claude/skills/clu-phase/SKILL.md`.
- Manual smoke: dispatch a phase with a 5min sleep; `clu status`
  shows `Heartbeat: <2m ago` continuously, never "STALLED".
- Existing tests pass (no clu-side code changed).

**Done:** structured commit, push, advance to phase 2.

## Phase 2 — dispatch-context-block (#60 part 1)

**Goal:** attempt-N workers see a "Previous attempt state" block in
their prompt summarizing what the prior attempt left in the worktree.

**Locked decisions:**

- **Site:** `end_of_line/dispatch.py` — the prompt-assembly function
  that produces the `claude --print '...'` command argument. Before
  the existing prompt is constructed, check
  `data["current_claim"]["attempts"]` (or whichever field carries
  attempt number). If `> 1`, prepend a block.
- **Block contents:**
  - Attempt number + prior-attempt termination reason (from last
    `lease_expired` / `claim_force_released` event).
  - `git -C <worktree> status --short`
  - `git -C <worktree> diff --stat HEAD`
  - `git -C <worktree> log --oneline HEAD ^<base_ref>` (commits
    landed by prior attempts, if any).
- **Subprocess discipline:** each `git` call has a 5s timeout. On
  timeout or non-zero exit, degrade to "(git status unavailable)"
  rather than fail dispatch. Three separate `subprocess.run` calls
  with `capture_output=True, text=True, timeout=5`.
- **Block format:** prepended verbatim to the existing worker prompt,
  separated by a clear `## Previous attempt state` header so the
  skill can reference it.
- **Suppressed for attempt 1** — no prior state.
- **Included even when worktree is clean** — explicit "clean" message
  so the worker has confirmation rather than wondering if the block
  failed silently.

**Files touched:**
- `end_of_line/dispatch.py` — new helper `_prev_attempt_block(worktree, base_ref, attempt, last_event)` + integration into the prompt path.
- `tests/test_dispatch_attempt_context.py` — NEW.

**Acceptance:**
- New helper tests: mocked git outputs → block contains expected
  sections; attempt-1 dispatch → no block; git-timeout → graceful
  degradation message.
- Manual smoke: write a tiny synthetic state.json with `attempts=2`
  and a real worktree path; call the helper; verify output shape.
- Full suite: 1081 baseline + 4–6 new tests.

**Done:** /simplify if diff >1 file or ~30 lines, structured commit,
push.

## Phase 3 — dispatch-context-skill (#60 part 2, closes #60)

**Goal:** `/clu-phase` SKILL.md tells workers to read the
"Previous attempt state" block first when present.

**Locked decisions:**

- One paragraph added near the top of SKILL.md (after the heartbeat-
  ticker snippet from phase 1, before "Read the sub-plan"):
  > If a `## Previous attempt state` block precedes this prompt,
  > read it first. It describes what the prior attempt left in the
  > worktree. Decide based on the sub-plan whether to keep/continue/
  > reset those edits. Reset is
  > `git -C <worktree> reset --hard <base-ref> && git -C <worktree>
  > clean -fd` — only if the edits don't align with the sub-plan.
- **No worker logic** — just a "read this first" instruction.

**Files touched:** `end_of_line/skills/clu-phase/SKILL.md`

**Acceptance:**
- SKILL.md has the new paragraph.
- `clu install-skill --force --only clu-phase` deploys.
- Suite unchanged.

**Done:** structured commit, push, close #60.

## Phase 4 — verify-opt-out-schema (#61 D)

**Goal:** `.orchestrator.json` accepts `quality.verify_required: bool`
(default `True`). When `false`, `cmd_complete` skips the verify-
attestation refusal gate.

**Locked decisions:**

- **Config schema:** add `verify_required: bool = True` to
  `QualitySpec` in `end_of_line/config.py`. Parser reads
  `raw["quality"].get("verify_required", True)` with type coercion;
  validate is `bool`.
- **`cmd_complete` branch:** when `cfg.quality.verify_required is
  False`, skip the verify-attestation refusal path. Emit a one-time
  `verify_policy_skipped` event (not per-phase) — track via a
  field on the plan state or detect via "first complete without
  verify stamp in this plan" semantics.
- **Decision on the one-time-event mechanic:** simplest path is to
  emit the event ONCE PER PHASE that completes under the opt-out
  (not literally "one time per plan"). The audit trail is still
  bounded — once per phase, not once per verify-call — and the
  schema is simpler. Operator feedback from #61's comment: the
  audit clutter is "real but cosmetic" — acceptable.
- **New event constant:** `EVENT_VERIFY_POLICY_SKIPPED =
  "verify_policy_skipped"` in state.py. Fields: `phase`,
  `plan_slug` (to differentiate from `verify_skipped` which is
  per-skip-flag).

**Files touched:**
- `end_of_line/config.py` — `QualitySpec.verify_required`.
- `end_of_line/state.py` — new event constant.
- `end_of_line/cli.py::cmd_complete` — branch on `verify_required`.
- `tests/test_verify_opt_out.py` — NEW.

**Acceptance:**
- `cmd_complete` under `verify_required: true` (default) behaves
  unchanged.
- `cmd_complete` under `verify_required: false` no longer refuses
  without verify stamp; emits `verify_policy_skipped` event.
- Full attestation suite still green (1081 baseline preserved).
- Tests: 4–5 new in `test_verify_opt_out.py`.

**Done:** /simplify if needed, structured commit, push.

## Phase 5 — init-stub (#61 A — the load-bearing fix)

**Goal:** `clu init` writes a commented-out `quality` block stub when
authoring `.orchestrator.json`, surfacing the field at config time.

**Locked decisions:**

- **`cmd_init` change:** when `.orchestrator.json` doesn't exist OR
  when invoked with new `--upgrade-orchestrator` flag, write a stub
  with a commented `quality` block:
  ```jsonc
  {
    "plan_dir": "plans",
    "dispatch": { ... },
    "test_command": "...",
    // Uncomment to enable independent verify of test runs before clu complete.
    // For projects that test via MCP tools (not shell), prefer:
    //   "quality": { "verify_required": false }
    // "quality": {
    //   "verify_command": "your test command here",
    //   "verify_timeout_seconds": 1800
    // }
  }
  ```
- **JSONC format:** the `//` comments require relaxed JSON parsing.
  Check whether the existing config parser handles this — if not,
  add a stripping pass (regex-strip `//`-to-EOL outside string
  literals before `json.loads`). If too invasive, fall back to
  writing the stub as a sibling `.example` file with the comments,
  and the real `.orchestrator.json` stays strict JSON.
- **`--upgrade-orchestrator` flag:** new CLI flag on `clu init`.
  When present, read existing config, detect missing `quality`
  block, write a backup `.orchestrator.json.bak`, then write the
  augmented config. Idempotent.
- **NEW vs existing:** if the file exists and `--upgrade-orchestrator`
  is NOT passed, `cmd_init` should NOT silently mutate the file
  (existing behavior). The upgrade is opt-in.

**Files touched:**
- `end_of_line/cli.py::cmd_init` — stub authoring + upgrade flag.
- `end_of_line/config.py` — JSONC parsing if we go that route.
- `tests/test_init_orchestrator_stub.py` — NEW.

**Acceptance:**
- Fresh `clu init` on a project without `.orchestrator.json` writes
  one with the commented quality block.
- `clu init --upgrade-orchestrator` on an existing project without
  `quality` writes a backup + augments the config.
- `clu init` (no flag) on an existing project is unchanged
  (regression guard).
- 5–6 new tests.

**Done:** /simplify if needed, structured commit, push.

## Phase 6 — skill-verify-branch (#61 part 3, closes #61)

**Goal:** `/clu-phase` SKILL.md skips the `clu verify` step when the
project has opted out via `verify_required: false`.

**Locked decisions:**

- Add a conditional to the SKILL.md's verify section:
  > If `quality.verify_required` is `false` in `.orchestrator.json`
  > (or the field is absent and you're confident the project tests
  > via MCP tools), skip the `clu verify` call. The worker's
  > in-session test run + commit message is the audit trail.
- **No file-read in SKILL.md** — instructional only. The worker
  decides based on the project's `.orchestrator.json` it can see in
  the worktree.
- **`docs/conventions.md`:** add a paragraph on when to use
  `verify_required: false` (MCP-tested projects, not "I'm in a
  hurry").

**Files touched:**
- `end_of_line/skills/clu-phase/SKILL.md` — conditional skip note.
- `docs/conventions.md` — opt-out guidance.

**Acceptance:**
- SKILL.md has the conditional verify-skip note.
- `clu install-skill --force --only clu-phase` deploys.
- `docs/conventions.md` has the opt-out paragraph.
- HealthDash optional dogfood: set `verify_required: false` in its
  `.orchestrator.json`; next plan's worker skips verify cleanly.
- Suite unchanged.

**Done:** structured commit, push, close #61.

## Wrap

After phase 6:
- `clu install-skill --force --only clu-phase` to deploy the
  updated worker skill.
- Verify all three issues closed: #59 (closed by phase 1's commit),
  #60 (closed by phase 3's commit), #61 (closed by phase 6's commit).
- Manual archive to `plans/shipped/reliability-batch-2.md` (or
  per #65's resolution if that ships first).
- Update memory: new project entry, prune older shipped if
  MEMORY.md > 130 lines.

## Non-goals (explicit)

- Auto-reset worktree on attempt N+1 (#60 alternative; deferred).
- Auto-answer policy for known blocker IDs (#61 option B; deferred).
- MCP-aware `verify_command` via `claude --print` subprocess (#61
  option C; cleanest long-term but non-trivial).
- Heartbeat threshold tuning — `stalled_heartbeat_minutes: 10`
  stays; once heartbeats are real, the threshold works as designed.
- Documentation overhaul beyond the targeted `conventions.md`
  additions.

## Tests baseline

Pre-batch: **1081/1081 green** (post-#63 ship at `ec6bb6d`).
Expected post-batch: **1090–1095** depending on test density.
