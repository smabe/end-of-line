# worker-death-visibility-foreground-gates — make a long gate runnable in the foreground, then require it

You are phase `foreground-gates` of the `worker-death-visibility` plan. You
raise the Bash-tool timeout ceiling for dispatched workers, then rewrite the
part of `/clu-phase` that lets a worker think it can start a gate in the
background and end its turn to wait. One commit; closes #106.

## Locked decisions (do NOT re-litigate)

See `plans/worker-death-visibility.md`, including its `## Assumptions` section
— both external-behavior lines below were closed by probe and you should not
re-derive them. Summary:

- The ceiling is raised via `build_worker_env` in `end_of_line/dispatch.py`,
  NOT via an `env` block in `worker-settings.json`. Whether a `--settings`
  file's env reaches the session is unproven and is issue #102's subject; the
  inherited-process-env route is probe-verified.
- **A command that overruns the ceiling is MOVED TO THE BACKGROUND, not
  killed.** Probe output: `Command did not complete within its 5s timeout and
  was moved to the background (ID: bxcd92dz5) ... You will be notified when it
  completes.` Only `sleep`-prefixed, `git`-containing and unparseable compound
  commands are stopped instead. A test gate is none of those.
- New config field `dispatch.bash_max_timeout_ms`, default `1_800_000`
  (30 minutes), against the invariant **gate duration < ceiling < lease TTL**.
  clu's own gate measures 90 seconds wall clock; the 60-minute default lease
  is the upper bound (`state.py:107`).
- `setdefault`, not assignment — and INSIDE the existing `inject` branch. The
  `None`-versus-dict return is load-bearing for `cmd_doctor` and
  `dispatch_repair_worker` in opposite directions.
- The skill prohibition covers the whole family — Monitor waits,
  `run_in_background` followed by end of turn, scheduled wakeups — AND names
  the auto-background result as a trap.
- Doc-level rather than allowlist-level, because the incident host dispatches
  with `bypassPermissions` and no `--allowedTools`.

## Read first

- `plans/worker-death-visibility.md` — the whole file, especially `## Assumptions` and `## Findings log`. You are the first phase, so the findings log is empty.
- `end_of_line/dispatch.py:120-156` — `build_worker_env`. Def at 120, docstring 127-143, body 145-156. It merges `os.environ` rather than replacing it (the #9 regression); the `inject` flag keys only off the claim kwargs; `PATH` and every `CLU_*` are assigned unconditionally at `:148-155`, so there is no `setdefault` precedent here and yours is a deliberate departure.
- `end_of_line/dispatch.py:444` — `dispatch_repair_worker`'s walrus on `build_worker_env`. This is why the `None` return must survive your edit: `None` means the repair subprocess inherits rather than receiving a frozen `os.environ` snapshot.
- `end_of_line/cli.py:2655-2656` — `cmd_doctor`'s `source = "dispatch.path" if env is not None else "inherited"`. Its docstring claims the operator sees byte-for-byte what a worker inherits; that claim is already stale for the `CLU_*` variables and your change widens it by one, so update the docstring rather than leaving it lying.
- `end_of_line/config.py:37-49` — `DispatchSpec`. Every field has a default; keep that true.
- `end_of_line/config.py:394-403` — where `DispatchSpec` is built from parsed JSON.
- `end_of_line/config.py` `_validate_stuck_tool_threshold` — **this** is the idiom to mirror, not `_validate_bool_field` (`:260`) or `_validate_ship_mode` (`:253`). It is already exactly "non-negative int or `ConfigError`", including the guard that a `bool` is an `int` in Python. Prefer renaming it to something general over adding a fourth near-identical validator; it is module-private with two call sites and no test asserts its message.
- `end_of_line/skills/clu-phase/SKILL.md:204` — the "Run FOCUSED tests during TDD iterations; the FULL suite exactly twice" mandate. Your cross-reference goes here.
- `end_of_line/skills/clu-phase/SKILL.md:210-212` — the `## Common pitfalls` heading and the "Command shapes that get DENIED under hardened dispatch" bullet. Match its shape and tone.
- `end_of_line/skills/clu-phase/SKILL.md:106` — already tells workers that shell variables do not persist across Bash calls in headless `--print` sessions. Same family of constraint; stay consistent with its wording.
- `tests/test_doctor.py:140-145` — pins "(source: inherited)". The only test module that currently imports `build_worker_env`.
- `tests/test_dispatch.py` — home of the existing `CLU_*` env-injection tests. Your ceiling tests belong beside them.
- `tests/test_skill_lint.py:93-133` — lints SKILL.md fenced `clu` verbs against live `clu --help`.

## Produce

1. **Failing tests first.**
   - `tests/test_dispatch.py`, beside the existing `CLU_*` injection tests:
     - `BASH_MAX_TIMEOUT_MS` is present in the env returned by
       `build_worker_env` for a phase dispatch, with the configured value as a
       string.
     - The default with no config key present is `"1800000"`. Build the
       expected value from `DispatchSpec()` via `dataclasses.replace` rather
       than retyping the number, so the test exercises the real default.
     - An inherited `BASH_MAX_TIMEOUT_MS` in `os.environ` is NOT overwritten.
     - A cfg-only call with no PATH override and no claim kwargs still returns
       `None`. This is the regression pin for `cmd_doctor` and
       `dispatch_repair_worker`; without it the `setdefault` can drift onto
       the unconditional path and nothing else will notice.
   - `tests/test_config.py`: a non-integer, negative, or boolean
     `dispatch.bash_max_timeout_ms` raises `ConfigError`.

2. **Implementation.**
   - `end_of_line/config.py`: add `bash_max_timeout_ms: int = 1_800_000` to
     `DispatchSpec` with a comment stating the two-sided invariant. Generalize
     the existing non-negative-int validator and use it at the
     `DispatchSpec(...)` construction site.
   - `end_of_line/dispatch.py`: inside the `inject` branch of
     `build_worker_env`, `env.setdefault("BASH_MAX_TIMEOUT_MS", str(cfg.dispatch.bash_max_timeout_ms))`.
     Extend the docstring with why it is `setdefault`, why it is inside the
     branch, and the consequence that repair workers get no ceiling.
   - `end_of_line/cli.py`: correct `cmd_doctor`'s docstring so its
     byte-for-byte claim matches what it can actually show.
   - `end_of_line/skills/clu-phase/SKILL.md`:
     - New `## Common pitfalls` bullet, **Never end your turn to wait for
       something.** State the mechanism (you are a dispatched `claude --print`
       worker; the run ends when you emit your final result, and any
       background shell is terminated about five seconds later), name the
       shapes it forbids (arming a Monitor and ending the turn,
       `run_in_background` followed by end of turn, any scheduled wakeup),
       name the consequence concretely (your work is staged and uncommitted,
       the claim is orphaned, and the operator finds out hours later), and
       give the replacement: run the gate as a blocking foreground Bash call
       with an explicit `timeout` argument.
     - **A second bullet for the trap**, because a worker can reach the fatal
       shape without choosing it: if a Bash result says the command was moved
       to the background and you will be notified, you have NOT got a result
       and no notification is coming. Re-run with a longer explicit timeout,
       or call `clu block`. Never wait.
     - State the ceiling numerically: clu-dispatched workers get
       `BASH_MAX_TIMEOUT_MS` from `dispatch.bash_max_timeout_ms`, 30 minutes
       by default; a project whose gate runs longer raises the field rather
       than reaching for a background wait.
     - Cross-reference from the full-suite mandate at line 204 — one clause,
       not a duplicated paragraph.
   - `docs/operations.md`: document the field beside the other dispatch
     fields, including both sides of the invariant and the auto-background
     behavior that motivates the lower bound.
   - `docs/reference.md`: keep `build_worker_env` and `DispatchSpec` accurate.

3. **Acceptance.**
   - All new tests green; full suite green with count and delta recorded.
     Baseline for comparison: 1968 tests before this phase.
   - `python3 -c "from end_of_line.config import DispatchSpec; print(DispatchSpec().bash_max_timeout_ms)"`
     prints `1800000`.
   - `grep -c "Monitor" end_of_line/skills/clu-phase/SKILL.md` is non-zero —
     it is 0 today, so a zero afterwards means the prohibition did not land.
   - `grep -c "background" end_of_line/skills/clu-phase/SKILL.md` covers the
     second bullet — the auto-background trap is the half a reader is most
     likely to skip, and the half that kills workers who never type "Monitor".
   - `python3 -m unittest tests.test_skill_lint` green.
   - `python3 -m unittest tests.test_doctor` green — proves the `None`-inherit
     contract survived.
   - The line-204 cross-reference exists and points at the new pitfall.
   - The prohibition names the `--print` mechanism, not just the rule. A
     worker that reads only a rule will find a way around it.

4. **Commit + attest + complete.**
   - **Record cross-phase findings** in `## Findings log` of
     `plans/worker-death-visibility.md`.
   - Commit: `worker-death-visibility: phase foreground-gates — raise worker Bash ceiling, forbid end-of-turn waits (closes #106)`.
   - Stage explicit paths: `end_of_line/config.py`,
     `end_of_line/dispatch.py`, `end_of_line/cli.py`,
     `end_of_line/skills/clu-phase/SKILL.md`, `docs/operations.md`,
     `docs/reference.md`, `tests/test_dispatch.py`, `tests/test_config.py`,
     `tests/test_doctor.py` (and `plans/worker-death-visibility.md` if you
     logged a finding).
   - After the commit:
     - `clu verify --plan worker-death-visibility --phase foreground-gates --token <T>`
     - `clu attest --simplify --plan worker-death-visibility --phase foreground-gates --token <T>`
   - `clu complete --plan worker-death-visibility --phase foreground-gates --token <T>`

## Failure modes to watch

- **You are widening a watchdog, and that is a real loss.** The 2-minute
  default cap was nominally a tool timeout, but it also bounded how long any
  single wedged command could hold a worker — a hung suite, a network call
  with no timeout, a process waiting on stdin all died in two minutes. They
  now live for thirty. Detection is partly re-established by the supervisor's
  stuck-tool detector (`stuck_tool_threshold_seconds`, 300s default,
  `supervisor.py:381`) and `_emit_worker_idle`, both of which fire well before
  30 minutes — but they NOTIFY, they do not kill. For the 5-to-30-minute
  window the recovery path is now operator-in-the-loop rather than automatic.
  The plan accepts this deliberately; do not silently widen it further.
- **Your edit to the bundled skill does not reach any worker until the
  operator reinstalls it.** `~/.claude/skills/clu-phase/SKILL.md` is a regular
  file, not a symlink, so `cmd_install_skill` refuses to overwrite it without
  `--force` (`cli.py:2387-2393`). You cannot run that yourself — it writes
  outside the repo. Say so explicitly in your completion summary: the operator
  must run `clu install-skill --only clu-phase --force`.
- **`clu doctor` will report skill drift the moment you commit**, because it
  SHA-256-compares installed against bundled (`cli.py:2824`). That is the
  guard working and the operator's cue to reinstall, not a regression.
- **`setdefault` on the unconditional path is the silent bug in this phase.**
  It passes every test you would think to write. What it breaks is
  `cmd_doctor` printing "(source: dispatch.path)" for every project without an
  override, and `dispatch_repair_worker` switching from inherit-semantics to a
  frozen `os.environ` copy. The required cfg-only-returns-`None` test is the
  only thing standing between those two and a one-line edit.
- **basedpyright rejects a `**dict` splat into `DispatchSpec`** — it infers
  `dict[str, int]` against `str | None` fields. Use `dataclasses.replace` in
  test helpers, which is better anyway because the default case then exercises
  the real dataclass default.
- **Do not raise the ceiling past the lease.** A `bash_max_timeout_ms` above
  the phase's lease TTL means the lease expires while a gate is still legally
  running, and the supervisor reaps a worker doing exactly what it was told.
  The default is deliberately half the default lease; keep the invariant in
  the comment where the next person will see it.
