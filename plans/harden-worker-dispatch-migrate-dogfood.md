# harden-worker-dispatch-migrate-dogfood — config swap + hardened example + end-to-end scoped smoke

You are phase `migrate-dogfood` of the `harden-worker-dispatch` plan. You
deliver, as one commit: the committed hardened example config + the CLAUDE.md
locked-decision update, plus (uncommitted, by design) the live migration of
this repo's `.orchestrator.json` off `bypassPermissions`, validated by an
end-to-end smoke of the full hardened stack (#90 acceptance criterion 4's
mechanics; the real-multi-phase-plan half is satisfied by the first plan
queued after this ships).

## Locked decisions (do NOT re-litigate)

See `plans/harden-worker-dispatch.md`. Summary:

- This repo's `.orchestrator.json` is GITIGNORED (`.gitignore:13`) — the swap
  is a local file edit at the CANONICAL project root (not your worktree), and
  it is NOT part of your commit. The committed artifacts are
  `examples/hardened.orchestrator.json` and `CLAUDE.md`.
- The hardened command for this repo (absolute paths, Fable 5 per operator
  decision, budget preserved from the current config):
  ```
  /Users/smabe/.local/bin/claude --print --model claude-fable-5 --permission-mode dontAsk --settings /Users/smabe/.config/clu/worker-settings.json --allowedTools "Bash(clu *),Bash(git *),Bash(python3 *),Bash(gh *),Bash(command -v *),Edit,Write,TodoWrite,Task,Skill" --max-budget-usd 20.00 '/clu-phase {plan_slug} {phase_id} {token} {state_file}'
  ```
  (One line in the JSON. `--allowedTools` must be ONE comma-joined argument.)
- `examples/hardened.orchestrator.json`: same shape with generic
  placeholders (`claude` on PATH, `/Users/<you>/...` settings path), plus the
  quiet-hours/quality blocks mirroring `examples/HealthData.orchestrator.json`
  conventions.
- `CLAUDE.md` Locked config decisions: replace the stale
  "**Worker sandbox:** document-only for v0.1" bullet with the new reality
  (scoped permissions + Seatbelt sandbox, clu sandbox-exempt, pointer to the
  operations.md recipe).
- Smoke runs in a scratch project with **notify masked** (demo-mode precedent:
  config masks global channels) so no iMessages fire.

## Read first

- `plans/harden-worker-dispatch.md` `## Findings log` — REQUIRED: both prior
  phases may have logged recipe corrections; apply them to the command above
  if they conflict (the findings log wins — note the divergence in your
  completion summary).
- `docs/operations.md` `## Hardened worker dispatch` (written by guard-recipe)
  — the recipe you are instantiating.
- `examples/HealthData.orchestrator.json` — example-file conventions.
- `end_of_line/demo.py` — how demo masks notify channels in a synthetic
  project config.
- `.orchestrator.json` at the canonical root — current command (carries the
  `--model claude-fable-5` the operator added at queue time; preserve flags
  you aren't explicitly changing).

## Produce

1. **Failing tests first** — only where testable: if you add any helper code,
   TDD it. The example JSON gets a test only if a config-validation test
   pattern already covers `examples/` (check; if none exists, the smoke is the
   acceptance and no new test file is warranted — do not manufacture a test
   that just re-reads static JSON).

2. **Implementation.**
   - `examples/hardened.orchestrator.json` (NEW, committed).
   - `CLAUDE.md` locked-decisions bullet update (committed).
   - Canonical `.orchestrator.json` swap (LOCAL, uncommitted).
   - **End-to-end smoke** (scratch dir under /tmp, notify masked):
     1. `git init` a scratch project with a trivial one-phase plan; `clu init`
        it (worker-settings.json must already exist from guard-recipe's
        emission — if you're on a machine state where it doesn't, run the
        emission path, don't hand-write the file).
     2. Dispatch a real worker under the hardened command (scratch config uses
        the same recipe). The scratch phase's sub-plan: create a file, commit,
        `clu verify`, `clu attest --simplify`, `clu complete`.
     3. Assert: plan reaches DONE; heartbeats recorded in state.json;
        `heartbeat-daemon` process exited after completion (no orphan: check
        `pgrep -f heartbeat-daemon`).
     4. Denial round-trip: a second scratch phase whose sub-plan instructs an
        off-allowlist action (e.g. `curl https://example.com`) and, on denial,
        `clu block` with a question. Assert: state carries the blocker, worker
        exited cleanly, nothing wedged.
   - `clu doctor` on THIS repo after the swap: the bypass warning from
     guard-recipe is GONE.

3. **Acceptance.**
   - Full suite green.
   - Smoke transcript (key lines) recorded in the commit body / completion
     summary: DONE state, heartbeat count, daemon exit, blocker round-trip.
   - `clu doctor` clean of the bypass warning on this repo; warning still
     fires on a synthetic bypass config (one-liner check).
   - `grep -rn "document-only" CLAUDE.md` → no stale sandbox bullet.

4. **Commit + attest + complete.**
   - Findings: log the smoke's surprises (especially any allowlist entry that
     had to be added — that's recipe drift the docs must absorb; if so, ALSO
     update `docs/operations.md` in this commit).
   - Structured commit: `harden-worker-dispatch: phase migrate-dogfood —
     hardened example + live config swap smoke (#90)`.
   - Stage explicit paths: `examples/hardened.orchestrator.json`, `CLAUDE.md`
     (+ `docs/operations.md` and the master if findings forced edits).
   - After the commit:
     - `clu verify --plan harden-worker-dispatch --phase migrate-dogfood --token <T>`
     - `clu attest --simplify --plan harden-worker-dispatch --phase migrate-dogfood --token <T>`
   - `clu complete --plan harden-worker-dispatch --phase migrate-dogfood --token <T>`.
   - Completion summary MUST state: the live config is now hardened, the first
     post-ship plan is the real-world dogfood, and #90 should stay open until
     the operator confirms that run.

## Failure modes to watch

- **You are the last phase still running under bypass dispatch** — your own
  session is proof of nothing about the hardened path; only the scratch smoke
  is. Don't claim the criterion on your own runtime.
- **Editing canonical-root files from a worktree**: `.orchestrator.json` and
  the smoke live OUTSIDE your worktree. Use absolute paths; never `cd` out and
  then `git commit` (silent-clobber failure mode, SKILL.md worktree rules).
- **Swap timing**: the canonical config edit takes effect on the NEXT dispatch
  of ANY plan on this host. Do the swap only AFTER the scratch smoke passes,
  so a recipe bug can't strand concurrent plans.
- **Nested `claude --print` in the smoke**: budget it (`--max-budget-usd` is
  already in the command) and use the dispatch env (`build_worker_env`)
  semantics — PATH inside the scratch dispatch is not your shell PATH.
