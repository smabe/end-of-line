# false-alarms-p4 — hook-installed predicate: derive it from the file that decides it

You are phase `p4` of the `false-alarms` plan. clu currently answers "is the monitor hook installed?" from a marker row in its own database, which is a write-only cache of a fact that lives in `~/.claude/settings.json`. On this machine they are diverged right now: the entry is present in `settings.json` and the marker table is empty. This phase makes the divergence unrepresentable. One commit.

*(That the hook also RUNS on this machine was observed directly — a `SessionStart` hook fired at the start of the drafting session — but note the predicate this phase builds proves presence in the file, not execution. Those are different claims and the phase is careful not to conflate them; see the failure mode about `disableAllHooks` below.)*

## Locked decisions (do NOT re-litigate)

See the master `plans/false-alarms.md`. The decisions binding this phase:

- **`settings.json` is the source of truth**, because it is what Claude Code reads and therefore what decides whether the hook fires. The marker is demoted to install metadata (when, and into which settings file) — the one thing the file genuinely cannot supply.
- **The predicate becomes PER-SURFACE.** The SessionStart operator dashboard and the opt-in `--inbox` UserPromptSubmit surface are two different hooks and must have two different answers. Today one "does any marker row exist" predicate answers for both, so an inbox-only row reports the dashboard as installed, and `clear_marker` deletes ALL rows so uninstalling one surface wipes the answer for the other.
- **The inbox surface stays installed-on-request and intact.** `clu install-hook` installs the SessionStart dashboard only; `--inbox` revives the UserPromptSubmit surface. Its code stays on disk and dormant. This phase changes how its installed-ness is *reported*, and does not retire, delete, or re-enable anything.
- **Matching is by BASENAME, not absolute path.** Absolute-path matching is why a clu reinstalled to a new venv path silently appends a DUPLICATE hook entry instead of recognising the existing one, and why uninstall then orphans the old entry.
- **Three states, not two: present / absent / unreadable.** A locked or malformed `settings.json` must not be reported as "not installed" — that is the same conflation as today's degraded-DB-read path, moved to a new file.
- **Fail soft, always.** A missing, unreadable, or malformed `settings.json` must never break clu's startup path. Every existing reader of that file already honours this.

## Work

> **Line hints re-anchored at `95a2d82` (after p1, p2 and p3 shipped).** Anchor on the SYMBOL; these are secondary. Three phases have landed in `cli.py` and `state.py` since this shard was written, and the `cli.py` hints below are the ones that moved most:
> `monitor.load_marker` → `monitor.py:40` · `monitor.is_scheduled` → `monitor.py:58` (both unmoved) ·
> `_maybe_print_monitor_tip` → `cli.py:82` (unmoved) with call sites now at `cli.py:2208` (`cmd_init`) and `cli.py:3772` (`cmd_queue_add`) — the shard says `:2177` and `:3741` ·
> `_hook_settings_path` → `cli.py:2699` (shard says `:2668`) · `_entry_command` → `cli.py:2729` (shard says `:2699`) · `_INBOX_HOOK_BASENAME` → `cli.py:2750` (shard says `:2721`) ·
> `cmd_install_hook` → `cli.py:2784` (shard says `:2753`) · `cmd_uninstall_hook` → `cli.py:2863` ·
> `_print_quiet_hours_coverage_health` → `cli.py:3302`, with its basename derivation at `:3324` (shard says `:3287-3297`).
>
> **Two doc citations resolve elsewhere than the shard says — both claims are real, the numbers are not:**
> the `UserPromptSubmit` mislabel is `docs/contract.md:391`, not `:347` (and note `:420` ALSO says `UserPromptSubmit`, correctly — that one describes the inbox surface, which genuinely is a `UserPromptSubmit` hook, so do not "fix" it). The non-TTY refusal claim is `docs/operations.md:1579`, not `:1552-1554`.
>
> **Also new since this shard was written:** p3 added a `quiet-span` worker callback and p1 added four `worker_idle_*` config thresholds. Neither touches this phase's surfaces, but `cli.py` is a shared file — expect the hunks around your edits to look unfamiliar.

- `end_of_line/monitor.py` — replace `is_scheduled` (`:58-59`) with per-surface derivation reading `settings.json`. Keep `load_marker` for install metadata; retire the "NO rows means not installed" contract in the module docstring (`:12`), which is the claim that encoded the bug. Note the module docstring also calls the marker "advisory, never load-bearing" — that was false the moment `_maybe_print_monitor_tip` branched on it, and it becomes true again with this change.

  ```python
  # Three states so "cannot tell" never reads as "not installed".
  class HookState(Enum): PRESENT; ABSENT; UNREADABLE
  def hook_state(surface: Surface, settings_path: Path | None = None) -> HookState
  ```

- `end_of_line/cli.py` — **share the basename matcher with the existing derivation, do not add a fourth one.** `_print_quiet_hours_coverage_health` (`:3287-3297`) already derives hook-installed from `settings.json` by basename, and `_INBOX_HOOK_BASENAME` (`:2721`) already exists with a comment explaining exactly why basename beats absolute path. That is one decision — "is this hook in settings.json" — with two consumers, so factoring it is a genuine single-source-of-truth case rather than coincidental overlap. Add the SessionStart basename — `clu_session_start.py`, the sibling of `clu_inbox_surface.py` in `end_of_line/hooks/` — beside the inbox one.

- `end_of_line/cli.py` — `_maybe_print_monitor_tip` (`:82-90`) asks the SessionStart predicate specifically, and stays silent on `UNREADABLE`. Its two call sites are `cmd_init` (`:2177`) and `cmd_queue_add` (`:3741`) — note this corrects issue #116's text, which says the nag fires on "every TTY invocation"; it does not. Keep the `isatty` guard: worker-driven `clu queue add --token` depends on it.

- `end_of_line/cli.py` — `cmd_install_hook` (`:2753-2830`) matches existing entries by basename, so a moved clu recognises its own hook instead of appending a duplicate; and dedupes any duplicates a previous install already left. `cmd_uninstall_hook` matches the same way, and clears only the marker keys for the surface being removed rather than all rows.

- `end_of_line/skills/clu-monitor/SKILL.md` — the "marker rows in clu's host database are the source of truth for 'is the hook already installed'" claim is now false; correct it. Step 1's check currently reaches past the CLI into a private module (`python3 -c "from end_of_line import monitor; print(monitor.load_marker())"`) — point it at a supported CLI surface instead, so the skill cannot inherit an internal representation change again. Keep the description's "idempotent — checks first and short-circuits" behaviour and the retired-inbox wording.

- `docs/contract.md` — `:347` still describes the installed hook as a `UserPromptSubmit` hook; it is SessionStart. Document the marker's demotion to metadata.

- `docs/operations.md` — `:1552-1554` claims `clu install-hook` "refuses to run in non-TTY contexts". There is no `isatty` check in `cmd_install_hook` (the only `isatty` calls in `cli.py` are at `:86,196,241,2537`). Either implement the refusal or delete the claim — do not leave a documented guard that does not exist.

- `tests/test_monitor.py` / `tests/test_cli_hints.py` / `tests/test_install_hook.py` — derived-predicate cases: hook present + marker empty → installed, no tip (the live divergence); marker present + entry absent → NOT installed (the reverse case, which today is silently trusted); unreadable settings → `UNREADABLE`, no tip, no crash; an inbox-only install does not report the dashboard as installed. **`tests/test_cli_hints.py:112` currently suppresses the dashboard tip by writing an INBOX marker row** — that test encodes the conflation and must be corrected, not preserved.

- `end_of_line/cli.py` — **`--check` added at execution, operator-confirmed at the p4 review gate.** The Work list requires the skill's step-1 check to point at "a supported CLI surface" and none existed: `clu doctor` refuses without a project `.orchestrator.json`, and every other hook path mutates. A read-only reporter had to exist for the described work to be possible. Operator chose to keep the `install-hook --check` spelling over a separate `clu hook-status`.
- `docs/architecture.md` — **added at execution.** Said the marker "records the install for idempotency", which the code now contradicts; the plan-level criterion requires a cold reader of this file to state installed-ness correctly.
- `docs/_outline.md` — **added at execution.** Its one-line `monitor.py` description named only the marker.
- `end_of_line/skills_manifest.json` — **added at execution.** The currency guard hard-fails when a bundled SKILL.md changes without it (same as p2 and p3).
- `tests/test_xdg_guard.py`, `tests/test_monitor_migration.py`, `tests/test_watch_tip.py`, `tests/test_doctor.py`, `tests/test_session_start_hook.py` — **added at execution.** Every one either called the deleted `is_scheduled` or asserted through it; `test_watch_tip` was additionally seeding an install marker to silence a tip the marker no longer silences.

- Consumes: `_entry_command(entry: dict) -> str | None` (`cli.py:2699`); `_INBOX_HOOK_BASENAME` (`cli.py:2721`); `_hook_settings_path() -> Path` (`cli.py:2668`); `monitor.load_marker(path: Path | None) -> dict | None`
- Produces: `monitor.hook_state(surface, settings_path) -> HookState`; a shared basename matcher in `cli.py` consumed by both `_maybe_print_monitor_tip` and `_print_quiet_hours_coverage_health`

## Done criteria addendum  *(escalated to plan level by the p2 sweep)*

- **Every config threshold this phase adds or reads is tested at its ZERO / disabled value, and the test states which direction is safe.** Vacuous if this phase adds none — but check rather than assume, because the class has now bitten twice (p1: a minimum-sample count of `0` crashed the tick; p2: a `0` meaning "detector disabled" silently removed a suppression bound), and both times the full suite was green.
- **A note on vocabulary, carried in by the p2 sweep:** this phase's "marker" is the monitor-hook install marker row in the HOST database. p2's "marker" is the active-tool activity stamp on a claim. They are unrelated, and nothing in this shard is falsified by p2 — but the shared word is a trap for anyone reading both shards, so keep this phase's usage explicitly qualified.

## Decisions & findings

### Finding: the shared matcher could NOT live in `cli.py`, and the `Produces:` line was structurally impossible  *(status: active)*
- The Work list specifies "a shared basename matcher in `cli.py` consumed by both". `cli.py` imports `monitor` (`cli.py:51`) and `hook_state` must do the matching, so `monitor` importing `cli` would be a cycle — **verified at review** by reading both modules' imports, not by trusting the report. The matcher lives in `monitor.py` and all consumers call it there.
- **Recorded as an interface departure rather than silently rewritten.** The substance the operator approved — ONE matcher, shared, single source of truth — shipped exactly. Only its address differs, and the plan named an address that cannot hold it.

### Finding: the install path is a THIRD consumer the shard did not count  *(status: active)*
- The shard names two consumers. `cmd_install_hook` / `cmd_uninstall_hook` also need "is this entry ours" once matching is basename-based, making three. That strengthens the factoring case rather than weakening it — and it is why `_entry_mentions_hook_path` could be deleted outright rather than kept beside the new matcher.

### Finding: a MISSING settings.json is ABSENT; an unparseable one is UNREADABLE  *(status: active)*
- These are different facts and the phase splits them. No user settings file means Claude Code loads no user hooks — the absence IS the answer. Anything merely unparseable (locked file, malformed JSON, a `hooks` block of the wrong shape) is `UNREADABLE`, because "cannot tell" reading as "not installed" is the exact conflation this phase replaced.

### Finding: the two consumers map UNREADABLE in OPPOSITE directions, deliberately  *(status: active)*
- The `clu init` / `clu queue add` tip stays SILENT on `UNREADABLE`: being wrong there means nagging an operator whose hook works, which is the bug this phase exists to kill. The doctor's quiet-hours check WARNS on it: being wrong there means a blocker nobody ever sees. Cheap warning versus silent failure — they resolve the same uncertainty in opposite directions on purpose.
- Documented in both call sites and in `docs/contract.md`, because it reads as an inconsistency and is not.

### Finding: `clear_marker` deleting all rows was LATENT, not live  *(status: active)*
- `cmd_uninstall_hook` removes both surfaces in one pass, so no shipped path ever removed just one. The per-surface clear is proved at unit level instead. Its one non-obvious rule: `settings_json_path` belongs to no surface, so it leaves with the LAST surface out — otherwise a full uninstall leaves a marker row describing nothing.
- Uninstall uses one rule covering both its paths ("clear the rows of every surface `settings.json` no longer installs") rather than two, so the early-return behaviour when there is no settings file at all is not silently dropped.

### Finding: install RECOGNISES but does not rewrite  *(status: active)*
- When basename matching finds an entry at a path clu would not have chosen, the entry is left exactly as the operator has it — only the message changes, to name the path actually installed rather than clu's resolved path, which would assert something false. Duplicates prune first-wins, so the survivor is the oldest: the one whose date the install record holds.

### Finding: the zero-value criterion is genuinely vacuous here — checked, not assumed  *(status: active)*
- This phase adds no config threshold and reads none; `config.py` is absent from the diff and `monitor.py` references no config at all. The structural analogue is the three-state predicate, so `UNREADABLE` was tested in five shapes with the assertion that it is NOT `ABSENT` — which is precisely the mutation p1 and p2 were missing.

### Finding: running the phase's own gate had a side effect on the operator's machine  *(status: active)*
- `clu init` auto-syncs bundled skills, so exercising the gate end-to-end rewrote `~/.claude/skills/` with this phase's uncommitted `clu-monitor`. Harmless once committed, and `clu-phase` was already drifted beforehand. The scratch plan registered to do it was unregistered afterwards — **verified at review: the host registry is empty and the marker table is still empty.**
- Worth knowing before anyone runs a `clu init` gate again: it is not a read-only operation.

### Decision: derive the predicate instead of reconciling the cache  *(status: active)*
- **Rationale:** of the four candidates #116 lists, only derivation satisfies all three of its acceptance criteria. "Reconcile on read" (re-stamp the marker when the entry is present) fixes the observed direction but still TRUSTS a marker whose entry has been removed, which is the worse direction — clu stays silent about a hook that is not firing. A `clu doctor` check helps only an operator who runs `doctor`, and the nagging continues meanwhile. Deriving makes both divergence directions unrepresentable rather than detected.
- **Alternatives considered:** delete the marker table outright (recommended by one research agent). Rejected as the default because the install timestamp is the one fact `settings.json` cannot supply, and `/clu-monitor` reports it back to the operator; demoting the marker keeps that at no correctness cost. This is a reversible call — if the timestamp turns out to be unused in practice, deleting the table is strictly simpler.
- **Evidence:** `monitor.py:40-59`; `cli.py:2826-2828`; `cli.py:3287-3297` for the existing derivation precedent; the live divergence on this machine.

### Decision: per-surface predicates  *(status: active)*
- **Rationale:** the two hooks have independent lifecycles — the dashboard installs by default, the inbox only under `--inbox` — so one shared answer is wrong by construction. It is already observably wrong: `record_hook_installed` is called ONLY under `--inbox` (`cli.py:2827-2828`) while `record_session_start_installed` is called unconditionally (`:2826`), yet `is_scheduled` treats any row as an answer for both.
- **Alternatives considered:** a single predicate keyed on the SessionStart surface only (rejected — it silently drops the inbox's own installed-ness, which `--inbox` operators and the quiet-hours doctor check both need).
- **Evidence:** `cli.py:2826-2828`; `monitor.py:58-59`; `monitor.py:117-119` (`clear_marker` deletes all rows).

## Failure modes to anticipate

- **`XDG_CONFIG_HOME` isolation.** `monitor.is_scheduled` is one of three paths guarded by the XDG test harness (`tests/test_xdg_guard.py:69-74`). `_hook_settings_path` uses `Path.home()`, NOT the XDG dir — so repointing the predicate at `settings.json` escapes `CLU_TEST_MODE` isolation and a test could read or write the operator's real `~/.claude/settings.json`. Every new test must inject the settings path explicitly.
- **Reading a file on a startup path.** The predicate moves from one indexed DB read to a file read plus JSON parse, on `clu init` and `clu queue add`. Both are already slow, operator-facing commands, but the parse must fail soft rather than propagate.
- **Presence in the file is not proof the hook runs.** `disableAllHooks`, `allowManagedHooksOnly`, and hooks merged from project/local/managed settings files all mean the answer derived from the user settings file alone can still be wrong. This is strictly better than the marker, but the tip's wording should not overclaim.
- **De-duplicating existing entries mutates the operator's settings file.** That file is theirs, not clu's. Removing a duplicate clu entry is defensible; touching anything else is not.
- **Deleting `is_scheduled` breaks the `/clu-monitor` skill** if the skill is not updated in the same commit — the skill calls into the module directly today.

## Done criteria

- **Observable, on this machine, and it is the phase's gate:** with the hook present in `~/.claude/settings.json` and the `monitor` table empty — the exact state that exists right now — `clu init` prints no install tip, and the `/clu-monitor` step-1 check reports the hook as installed. Capture both outputs. Today the first nags and the second reports a clean machine.
- **Observable:** the reverse case — a marker row present with no matching entry in `settings.json` — reports NOT installed. Today it is silently trusted.
- An unreadable or malformed `settings.json` produces no tip, no traceback, and a distinct `UNREADABLE` state that is not conflated with absent.
- Installing twice from two different absolute paths leaves exactly one SessionStart entry.
- An inbox-only install does not report the dashboard hook as installed, and uninstalling one surface leaves the other's answer intact.
- No doc or skill line claims the marker is the source of truth, claims a non-TTY refusal that does not exist, or calls the SessionStart hook a `UserPromptSubmit` hook.
- Commit message carries `Fixes #116`.
- `python3 -m unittest discover -s tests` green; `clu verify` green.
