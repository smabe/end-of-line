# harden-worker-dispatch — scoped permissions + OS sandbox for clu workers (#90)

Every clu worker currently runs `--permission-mode bypassPermissions` on the host
(this repo's `.orchestrator.json` + HealthData's; clu ships no default —
`config.py:39` defaults `command` to `""`). This plan replaces that with the
layered model GH #90 scoped: `dontAsk` + scoped `--allowedTools` as friction,
the native Seatbelt sandbox as the boundary, `clu block` as the escape hatch
for denials. Ordering: first make the worker contract allowlist-survivable
(the heartbeat loop is the one piece that empirically cannot pass prefix
matching), then ship the guard + recipe, then migrate this repo and smoke it
end-to-end.

All load-bearing externals were verified empirically on 2026-06-10 against the
installed claude 2.1.170 (spike transcripts at `/tmp/clu90-spike/*.out`):

- **Denial-recovery confirmed (Test A):** under `dontAsk` in `--print`, a denied
  Bash call returns a denial message, the session continues, and later allowed
  calls run — the blocker-instead-of-wedge premise holds.
- **Heartbeat loop denied (Test B):** the SKILL.md `( trap …; while …; | tee …; ) & wait`
  compound is denied even with every inner command allowlisted — subshell/loop
  constructs don't survive permission decomposition.
- **Sandbox works headless (Test C):** `--settings` + `sandbox.enabled` in
  `--print` allows cwd + `filesystem.allowWrite` paths, blocks everything else
  with `Operation not permitted`.
- **`$VAR` expansion passes (Test D)** on 2.1.170 (upstream
  anthropics/claude-code#51001 reports it denied on some 2.1.11x builds —
  version floor goes in the docs).
- **CLI quirk:** `--allowedTools` is variadic and eats a following prompt
  argument — the recipe uses one comma-joined value.
- Docs facts (claude-code-guide, code.claude.com/docs/en/sandboxing +
  /permissions + /permission-modes): worktree workers' writes to the canonical
  shared `.git` are auto-granted by the sandbox; `sandbox.excludedCommands`
  exempts named command patterns; field consensus (Flatt Security's 8 bypasses,
  CVE-2025-66032 family, Trail of Bits claude-code-config) is that prefix
  allowlists are friction and the sandbox is the boundary.

## Locked design decisions

### Cross-phase — the permission model
- **Sandbox is the boundary, allowlist is friction.** Both ship; neither alone.
- **`clu` runs outside the sandbox** via `sandbox.excludedCommands: ["clu *"]`:
  callbacks write state at the canonical root and `clu block` spawns osascript
  (`notify_imessage.py:63`) — both outside worktree+tmp. clu is the operator's
  own token-validated CLI; exempting it keeps every callback working with zero
  clu code changes. Consequence: no `filesystem.allowWrite` entries needed in v1.
- **Worker settings live at `~/.config/clu/worker-settings.json`** (XDG via the
  existing `clu_config_dir()` in `_xdg_guard.py`, global-notify-config
  precedent), referenced by absolute `--settings` path in `dispatch.command`.
  Keeps worker policy out of operator interactive sessions and works from any
  worktree (the file is untracked; worktrees never materialize it).
- **Allowlist (one comma-joined arg):** `Bash(clu *)`, `Bash(git *)`,
  `Bash(python3 *)`, `Bash(gh *)`, `Bash(command -v *)`, `Edit`, `Write`,
  `TodoWrite`, `Task`, `Skill`. Bare `Edit`/`Write` is a documented v1 residual
  gap: path-scoped `Write(...)` rules silently fail in dontAsk upstream
  (anthropics/claude-code#52962), and the sandbox doesn't govern file tools.
- **Fail closed:** `failIfUnavailable: true`, `allowUnsandboxedCommands: false`;
  network `allowedDomains` only `github.com` + `api.github.com` (workers don't
  push; `gh` is best-effort already per the clu-phase SKILL.md).
- **Worker model is Fable 5** (`--model claude-fable-5` in `dispatch.command`)
  — operator decision 2026-06-10, applies to the current config swap and the
  hardened recipe.

### Phase 1 — hb-daemon
- **The heartbeat loop moves out of bash into `clu heartbeat-daemon`** (new
  `end_of_line/heartbeat_daemon.py`, paced-loop shape mirrors
  `demo_worker.py:213-258`): self-daemonizes via double-fork + setsid (no shell
  `&`, which is what made Test B fail), pings every 120s while the worker PID
  is alive, exits on worker death or on first token-rejection after claim
  release, keeps the 3-strike `notify-heartbeat-failure` self-report and the
  stderr sidecar log.
- **claim.pid semantics unchanged** — the daemon is never the claim PID
  (supervisor-lifecycle constraint: wrappers must not change claim.pid).
- **SKILL.md step 2 collapses to one flat allowlistable command**; step 2b env
  exports and all five watchdog layers' semantics unchanged. Workers must not
  reinstall the skill mid-run (doctor's drift guard flags until operator
  re-syncs).

### Phase 2 — guard-recipe
- **Doctor printer `_print_dispatch_permission_health`:** shlex-tokenize
  `dispatch.command` *and* `dispatch.repair_command` (mirror `resolved_model()`,
  `dispatch.py:95-111`), warn on `bypassPermissions` /
  `--dangerously-skip-permissions`, quiet when clean. No container detection —
  doctor runs on the host this matters on.
- **`clu init` emits the settings file if absent** (bundled
  `worker-settings.template.json` via `importlib.resources`, mirroring
  `_ensure_quality_stub` at `cli.py:1878-1899`; never overwrites) and prints
  the hardened command line. Recipe doc lands in `docs/operations.md` after
  `## Bootstrap` (~line 521), cross-linked from `docs/conventions.md`
  `## Worker callback contract` (line 115) and `docs/reference.md` dispatch
  section (line 303), including the full allowlist enumeration (#90 acceptance
  criterion 1) and the `clu block`-on-denial contract.

### Phase 3 — migrate-dogfood
- Swap this repo's (untracked) `.orchestrator.json` to the hardened command;
  add committed `examples/hardened.orchestrator.json`; update the stale
  "Worker sandbox: document-only for v0.1" bullet in CLAUDE.md's Locked config
  decisions. End-to-end smoke in a scratch project with notify masked (demo
  precedent): real `clu init` + dispatch under the hardened recipe, verify
  heartbeat-daemon + complete, plus a deliberate denial→`clu block` round-trip.
- The "dogfooded through a real multi-phase plan" criterion is satisfied by the
  *first plan queued after this ships* — #90 stays open until the operator
  confirms that run.

## Non-goals
- **HealthData migration** — configs are independent per-project files with no
  shared state, so the asymmetry is safe; its `xcodebuild`/simulator behavior
  under Seatbelt is unverified and gets its own pass after this repo's dogfood.
- **Containerized workers (layer 3)** — composable on top, no shared state with
  layers 1–2; operator decides post-ship whether to file it (per #90's own
  acceptance criteria).
- **Supervisor-side notify refactor** — unnecessary once `clu` is sandbox-exempt.
- **Upstream Write-glob scoping (#52962)** — documented residual gap, not ours
  to fix.

## Files touched
- `end_of_line/heartbeat_daemon.py` — P1 NEW — daemonized heartbeat loop
- `end_of_line/cli.py` — P1, P2 modified — API hotspots: argparse surface
  (`heartbeat-daemon` subcommand), doctor printer chain, `cmd_init` emission
- `end_of_line/skills/clu-phase/SKILL.md` — P1 modified — step 2/2b rewrite
  (drift guard will flag until reinstall)
- `end_of_line/worker-settings.template.json` — P2 NEW — bundled
  sandbox+permissions template (package-data, like `skills/`)
- `docs/operations.md`, `docs/conventions.md`, `docs/reference.md` — P1, P2
- `examples/hardened.orchestrator.json` — P3 NEW
- `CLAUDE.md` — P3 modified — Locked config decisions sandbox bullet
- `.orchestrator.json` (untracked, canonical root) — P3 local edit
- `tests/test_heartbeat_daemon.py` — P1 NEW; doctor + init emission tests — P2
  (pattern: `redirect_stdout`, per `test_doctor.py:79`)

## Per-phase done checklist
- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format; stage explicit paths.
- **Post-commit attestations:** `clu verify` then `clu attest --simplify`
  (each with `--plan harden-worker-dispatch --phase <id> --token <T>`).
- Call `clu complete --plan harden-worker-dispatch --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| hb-daemon | `harden-worker-dispatch-hb-daemon.md` | `clu heartbeat-daemon` + SKILL.md step-2 rewrite | 2.5h |
| guard-recipe | `harden-worker-dispatch-guard-recipe.md` | doctor bypass warning + settings emission + recipe docs | 2.5h |
| migrate-dogfood | `harden-worker-dispatch-migrate-dogfood.md` | config swap + hardened example + end-to-end scoped smoke | 1.5h |

## Findings log

_Empty at plan time. As phases run, the worker appends one dated bullet per
cross-phase finding — a gotcha, a spike result, an API surprise, an assumption
that turned out wrong — so a later phase doesn't rediscover it. Cite file:line._

- 2026-06-10 (hb-daemon): live fork smoke confirmed the daemon lands in its
  own process group (PGID = intermediate child's PID ≠ worker's PGID), so the
  reaper-immunity assumption holds as designed. The daemon's cmdline carries
  the plan slug AND token (`clu heartbeat-daemon ... --plan <slug> --token
  <T>`), but supervisor liveness checks (`state.claim_worker_alive`,
  `_detect_dead_pid`) only probe claim.pid — no interference. For phase 3's
  smoke: arm-time validation in `cmd_heartbeat_daemon` (cli.py) stamps the
  first heartbeat itself, so a fresh stamp right after arming proves the
  parent path, not the loop — backdate `last_heartbeat_at` and wait one 120s
  tick (or kill and check the sidecar `logs/<phase>.<token>.hb.log`) to prove
  the daemon. Both self-exit paths verified live: dead worker PID →
  `exit_worker_dead`, released claim + live PID → `exit_claim_gone`.
- 2026-06-10 (migrate-dogfood): **`dispatch.path` is REQUIRED in the hardened
  recipe.** Cron dispatch inherits the LaunchAgent's minimal PATH → bare `clu`
  exits 127 → workers fall back to the absolute path → which defeats the
  `excludedCommands: ["clu *"]` prefix match → clu runs INSIDE the sandbox →
  callbacks that write outside cwd (canonical-root state for worktree plans,
  the `~/.config/clu` inbox) die with EPERM. Live-proven both directions:
  smoke v1 (no path) logged `notify: inbox write failed (blocker): [Errno 1]`;
  smoke v2 (with path) landed the blocker in the inbox. operations.md recipe +
  `examples/hardened.orchestrator.json` updated.
- 2026-06-10 (migrate-dogfood): **`$PPID` doesn't survive scoped-permission
  dispatch** (extends Test B: variable-bearing/compound Bash calls fail prefix
  matching; workers also can't persist shell state across calls in `--print`).
  Fix shipped in this phase: `clu heartbeat-daemon --worker-pid` is now
  optional, defaulting to the claim's dispatcher-stamped PID (`cmd_heartbeat_daemon`,
  cli.py; SKILL.md step 2 rewritten flag-free). Round-3 smoke validates the
  flat no-PID arm.
- 2026-06-10 (migrate-dogfood): off-allowlist flat commands may EXECUTE inside
  the sandbox and fail at its boundary instead of being permission-denied —
  `curl https://example.com` ran and exited 56 on the network block. Same net
  containment, different mechanism than Test A's denial path. Docs note added.
- 2026-06-10 (migrate-dogfood): transient — one of two simultaneously
  dispatched sandboxed workers had Bash entirely unavailable (smoke-happy2
  attempt 1); suspected sandbox-init contention under `failIfUnavailable`.
  Defense-in-depth held: worker stopped cleanly without callbacks, the
  dead-PID watchdog re-dispatched in 3s, attempt 2 completed. Unreproduced.
- 2026-06-10 (migrate-dogfood): step 2b's `CLU_*` env exports are ineffective
  in headless workers — shell state doesn't persist across Bash calls, so the
  activity hook short-circuits and `tool_stuck` coverage is absent for ALL
  `--print` workers (pre-dates this plan). Candidate fix for a follow-up:
  dispatcher injects `CLU_PLAN/PHASE/TOKEN/PROJECT` into the worker env at
  Popen time (`build_worker_env` already merges os.environ).
- 2026-06-10 (migrate-dogfood): a project-level `.claude/skills/clu-phase/`
  copy CANNOT shadow the user-level skill of the same name (probe, claude
  2.1.170; the user-level copy won) — the smoke used a renamed project-level
  copy (`clu-phase-hardened`). Real migrations instead require `clu
  install-skill` BEFORE the next dispatch under the hardened command
  (migration-ordering note added to operations.md guard rails).
- 2026-06-10 (guard-recipe): `worker-settings.template.json` is registered in
  pyproject `[tool.setuptools.package-data]` beside `skills/`, but the live
  install is editable — `importlib.resources` resolves from the source tree,
  so wheel inclusion is only exercised by the weekly clean-clone canary. For
  phase 3's smoke: the doctor warning was live-verified against this repo's
  canonical bypass config (`_print_dispatch_permission_health`, cli.py) — it
  should disappear after the config swap; re-run `clu doctor` to confirm
  both that and that init does NOT re-emit over the operator's existing
  `~/.config/clu/worker-settings.json` (never-overwrite contract).
- 2026-06-10 (migrate-dogfood, attempt 3): headless dispatch PATH also poisons
  the TOOLCHAIN, not just `clu` resolution — bare `python3` resolved to Xcode's
  3.9 (172 import errors: `datetime.UTC` needs 3.11+) and `openssl` to Apple
  LibreSSL 3.3 (no `-ext` flag; 2 `clu serve` TLS test errors). Both vanish
  with `/opt/homebrew/bin` first on PATH. The hardened recipe's
  `dispatch.path` (homebrew first) fixes this for future workers; the swap
  also makes `test_command`'s bare `python3` resolve correctly under
  `clu verify`.
- 2026-06-10 (operator, post-mortem of migrate-dogfood attempt-1 death):
  do NOT launch the smoke dispatches or test suite as BACKGROUND tasks and
  end your turn "standing by" — a `--print` worker process EXITS when its
  final message is emitted; there is no later re-invocation. Attempt 1 died
  exactly this way (pid 12608, last log line "Standing by", no
  complete/block called; dead-PID rule released the claim). Run every smoke
  dispatch and suite run SYNCHRONOUSLY (foreground Bash, generous timeout),
  and call `clu complete`/`clu block` before your final message.
