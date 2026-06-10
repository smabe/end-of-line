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
