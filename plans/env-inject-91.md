# env-inject-91 — dispatcher-side `CLU_*` env + doctor marker guard (#91, #83; dogfoods #90)

Headless workers have never had `tool_stuck` coverage: `/clu-phase` step 2b
tells the worker to `export CLU_*`, but env does not persist across Bash tool
calls in `--print` (documented: code.claude.com/docs/en/tools-reference
"Environment variables do not persist"), so the PreToolUse activity hook's
`[ -n "$CLU_TOKEN" ]` guard (SKILL.md:273) short-circuits on every call. Fix at
the right layer: the dispatcher knows plan/phase/token/project at Popen time —
inject them into the worker process env, which hooks inherit
(code.claude.com/docs/en/hooks "Hooks inherit the parent environment";
probe-verified live on claude 2.1.170, 2026-06-10: a Popen-set var reached a
`--settings`-defined PreToolUse hook on every Bash call of a `--print`
session, including denied calls). Phase 2 adds the sibling doctor guard from
#83: warn when `dispatch.command` can't surface the plan slug as a bounded
token, which is what keeps PID-reuse liveness checks honest.

This is the **first plan dispatched under the hardened #90 config** — its
clean completion is the dogfood evidence for closing #90 (operator-side).

Verified ground truth (2026-06-10 session):

- `build_worker_env` is the single env seam — `dispatch_for_tick` (Popen at
  `dispatch.py:241-256`) and `dispatch_repair_worker` (`dispatch.py:361-362`)
  both consume it; plan_slug / phase_id / token / project_root are all in
  scope at the render site (`dispatch.py:194-201`).
- The activity hook reads context via CLI args populated by `$CLU_*` expansion
  inside the hook command line (`activity_hook.py:44-79`, SKILL.md:273) — the
  hook command needs NO change once the process env carries the vars.
- Upstream anthropics/claude-code#40506 ("PreToolUse never fires in `-p`")
  does not reproduce on 2.1.170 (probe). #32512 (inherited vars not promotable
  as `$VAR` in the Bash tool) and our own probe (custom-var expansion can be
  permission-DENIED under dontAsk) both mean: never rely on `$CLU_*` inside
  worker Bash commands — hook-side expansion only.
- `_cmdline_marker_present` (`state.py:291-300`) is the production
  boundary-aware matcher; `claim_worker_alive` (state.py:303-342) and
  `reap_orphan_pgroup` (state.py:376-411) consume it with
  `cmdline_match = data["plan_slug"]` (supervisor.py:620, 642-646, 665).

## Locked design decisions

### Phase 1 — inject (#91)
- **`build_worker_env` grows optional keyword args** (`plan_slug`, `phase_id`,
  `token`) and injects `CLU_PLAN` / `CLU_PHASE` / `CLU_TOKEN` / `CLU_PROJECT`
  (project from `cfg.project_root`) merged over `os.environ`. Cfg-only calls
  (cmd_doctor's PATH probe) keep today's exact behavior including the
  `None`-to-inherit branch — doctor's "(source: inherited)" display must not
  change.
- **Both dispatch sites pass what they have**: `dispatch_for_tick` passes all
  three; `dispatch_repair_worker` passes NONE — repair workers carry no
  claim/token, and the hook's empty-token short-circuit is the correct
  behavior for them. Documented exclusion per #91 acceptance criteria; the
  rationale lives in the `build_worker_env` docstring.
- **SKILL.md step 2b is deleted**, replaced by one line documenting that the
  dispatcher provides the env. The line-230 failure-mode bullet and every
  other `export CLU_*` reference go with it — grep-verified purge of the
  vocabulary.
- **No `CLAUDE_ENV_FILE` / SessionStart mechanism** — workers never need
  `$CLU_*` in their own shell commands; only hooks read them.

### Phase 2 — marker-doctor (#83)
- **`_print_dispatch_marker_health(cfg)`** mirrors
  `_print_dispatch_permission_health` (cli.py:2708-2731): quiet when clean,
  findings then remediation pointer. Check = render `dispatch.command` with a
  sentinel slug through the same `.format(...)` placeholder set as
  `dispatch.py:194-201`, then `_cmdline_marker_present(rendered, sentinel)`.
  Reusing the production matcher catches both a missing `{plan_slug}` and an
  unbounded one (e.g. `x{plan_slug}y`); shlex.quote in the render is fine —
  quotes are non-slug chars, valid boundaries.
- **Template render may KeyError on templates missing other placeholders** —
  format with a defaultdict-style safe map or catch and treat as unparseable
  (quiet skip, like `resolved_model`'s tolerance). Unparseable ≠ warning.
- **`repair_command` is excluded**: marker checks only run against
  phase-worker claims (supervisor.py:620, 642-646, 665); repair workers carry
  no claim. One-sentence asymmetry rationale goes in the printer docstring.

## Non-goals
- **No hook-command or `activity_hook.py` changes** — probe-verified working
  once the env arrives; CLI-args contract stays.
- **Repair-worker env injection** — excluded with the rationale above (no
  claim/token exists; short-circuit is correct).
- **Closing #90 in-band** — workers close #91/#83 via commit messages; the
  operator closes #90 after confirming this plan ran clean under the hardened
  dispatch.

## Files touched
- `end_of_line/dispatch.py` — P1 — API hotspot: `build_worker_env` signature
  (callers: dispatch_for_tick, dispatch_repair_worker, cmd_doctor, tests)
- `end_of_line/skills/clu-phase/SKILL.md` — P1 — step 2b removal (drift guard
  flags until operator reinstalls)
- `end_of_line/cli.py` — P2 — `_print_dispatch_marker_health` inserted in the
  cmd_doctor chain next to `_print_dispatch_permission_health` (cli.py:2672)
- `docs/reference.md` — P1, P2 — dispatch env contract + doctor printer lines
- `docs/operations.md` — P2 — hardened-recipe guard-rails note ({plan_slug}
  is load-bearing for liveness checks)
- `tests/test_doctor.py` — P1 (build_worker_env injection), P2 (marker
  printer); dispatch Popen-env assertion in the existing dispatch test home

## Per-phase done checklist
- TDD: failing tests first.
- `/code-review` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format; stage explicit paths.
- **Post-commit attestations:** `clu verify` then `clu attest --simplify`
  (each with `--plan env-inject-91 --phase <id> --token <T>`).
- Call `clu complete --plan env-inject-91 --phase <id> --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| inject | `env-inject-91-inject.md` | `build_worker_env` CLU_* injection + SKILL.md step-2b removal (#91) | 2h |
| marker-doctor | `env-inject-91-marker-doctor.md` | sentinel-render doctor warning for unbounded plan-slug (#83) | 1h |

## Findings log

_Empty at plan time. Workers append one dated bullet per cross-phase finding
(gotcha, spike result, API surprise, wrong assumption) with file:line._
