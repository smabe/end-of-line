# env-inject-91-inject — dispatcher-side CLU_* env injection (#91)

You are phase `inject` of the `env-inject-91` plan. You deliver, as one
commit: `build_worker_env` injecting `CLU_PLAN/CLU_PHASE/CLU_TOKEN/CLU_PROJECT`
into the worker process env at dispatch, plus removal of the dead worker-side
export step from the clu-phase SKILL.md. This restores `tool_stuck` coverage
for every headless worker (it has been silently dark for ALL `--print`
workers — predates #90).

NOTE: you are running under the hardened #90 dispatch (dontAsk + allowlist +
sandbox). If a command you legitimately need is denied, `clu block` with the
specifics — do not work around the sandbox.

## Locked decisions (do NOT re-litigate)

See `plans/env-inject-91.md`. Summary:

- `build_worker_env(cfg, *, plan_slug=None, phase_id=None, token=None)` —
  optional kwargs; when provided, return `{**os.environ, CLU_PLAN: ...,
  CLU_PHASE: ..., CLU_TOKEN: ..., CLU_PROJECT: str(cfg.project_root)}` merged
  with the existing PATH override behavior. Cfg-only calls keep today's exact
  semantics INCLUDING the `None`-to-inherit return (cmd_doctor's probe and its
  "(source: inherited)" display must not change).
- `dispatch_for_tick` passes plan_slug, `result.phase_id`, `result.token`;
  `dispatch_repair_worker` passes NOTHING — repair workers have no claim or
  token; the activity hook's `[ -n "$CLU_TOKEN" ]` short-circuit is correct
  for them. Record this exclusion + rationale in the docstring (#91
  acceptance criteria).
- The hook command and `activity_hook.py` are NOT touched — they already read
  `$CLU_*` from the hook subprocess env (probe-verified 2026-06-10 on claude
  2.1.170: Popen-set vars reach `--settings`-defined PreToolUse hooks in
  `--print`, every Bash call).
- SKILL.md: delete step 2b's export block (SKILL.md:112-116), replace with a
  one-liner that the dispatcher provides these vars; rewrite the line-230
  "Forgetting the activity-hook env exports" failure-mode bullet to describe
  the new contract (e.g. "running a phase outside clu dispatch → hook
  short-circuits, by design"). Purge the export vocabulary:
  `grep -n "export CLU_" SKILL.md` must end with zero hits.

## Read first

- `plans/env-inject-91.md` `## Findings log` — empty if you're first.
- `end_of_line/dispatch.py:114-123` (`build_worker_env`), `:194-201` (render
  site — every value you need is in scope), `:241-256` (Popen consumption),
  `:325-362` (`dispatch_repair_worker`'s identical pattern).
- `end_of_line/activity_hook.py:44-79` — the CLI-args contract you are NOT
  changing.
- `end_of_line/skills/clu-phase/SKILL.md:112-116, 230, 273, 282` — every
  CLU_* reference.
- `tests/test_doctor.py:43-55` — existing `build_worker_env` tests to extend.

## Produce

1. **Failing tests first** (extend `tests/test_doctor.py`'s
   build_worker_env block or the dispatch tests' home — follow where the
   existing ones live):
   - kwargs provided → returned dict carries all four CLU_* keys with exact
     values, `os.environ` merge preserved, PATH override composes (path set +
     kwargs → both present).
   - kwargs provided + NO path override → still returns a dict (not None)
     with CLU_* present.
   - cfg-only call, no path → returns None (regression pin for doctor).
   - dispatch-level: dispatch_for_tick's Popen receives env with CLU_* set
     (mirror however existing dispatch tests capture popen_kwargs);
     dispatch_repair_worker's env carries NO CLU_* keys.

2. **Implementation.**
   - `end_of_line/dispatch.py`: extend `build_worker_env` + the
     `dispatch_for_tick` call site. Repair site untouched except it now flows
     through the same function signature (no kwargs).
   - `end_of_line/skills/clu-phase/SKILL.md`: step 2b removal + bullet
     rewrite per Locked decisions. Renumber/reflow only as far as needed; do
     not restructure unrelated steps.
   - `docs/reference.md`: update the dispatch.py section's
     `build_worker_env` line with the env-injection contract + repair
     exclusion.

3. **Acceptance.**
   - All new tests green; full suite green
     (`python3 -m unittest discover -s tests`).
   - `grep -rn "export CLU_" end_of_line/skills/clu-phase/SKILL.md` → no hits.
   - Live one-shot proof (cheap, do it): from a scratch dir, run
     `CLU_TOKEN= python3 -m end_of_line.activity_hook --start-bash ...` is NOT
     the test — instead verify the seam directly:
     `python3 -c "from end_of_line import dispatch, config; ..."` building env
     with kwargs and asserting the four keys. Print the result into your
     completion summary.

4. **Commit + attest + complete.**
   - Findings: if hardened-dispatch friction (denials, sandbox surprises)
     surfaced, log a dated bullet in the master's `## Findings log` — that
     bullet IS #90 dogfood evidence the operator wants.
   - Structured commit: `env-inject-91: phase inject — dispatcher-side CLU_*
     env injection (closes #91)`.
   - Stage explicit paths: `end_of_line/dispatch.py`,
     `end_of_line/skills/clu-phase/SKILL.md`, `docs/reference.md`, the test
     file(s) (+ master if findings logged).
   - After the commit:
     - `clu verify --plan env-inject-91 --phase inject --token <T>`
     - `clu attest --simplify --plan env-inject-91 --phase inject --token <T>`
   - `clu complete --plan env-inject-91 --phase inject --token <T>`.

## Failure modes to watch

- **Do not put `$CLU_*` expansions in any worker-facing Bash example** — the
  dontAsk heuristic can DENY custom-var-bearing commands (probe 2026-06-10),
  and Bash-tool shells may not promote inherited vars
  (anthropics/claude-code#32512). Hook-side expansion only.
- **`build_worker_env` signature is an API hotspot** — cmd_doctor and
  dispatch_repair_worker call it; keyword-only kwargs with None defaults keep
  both call sites working unchanged. Run the full suite, not a subset.
- **SKILL.md editing under drift guard** — same as #90's hb-daemon phase: do
  NOT run `clu install-skill` mid-phase; the doctor drift flag afterward is
  expected (operator re-syncs post-ship).
- **You are the #90 dogfood** — if the sandbox/allowlist blocks something the
  phase genuinely needs, that's a finding, not an obstacle to hack around:
  `clu block` with the exact denied command.
