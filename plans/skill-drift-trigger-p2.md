# skill-drift-trigger-p2 — `skill_sync.scan()`, pure and symlink-aware

You are phase `p2` of the `skill-drift-trigger` plan. This phase extracts skill-drift detection out of the doctor printer into a module that returns data, and fixes the three cases the current check silently mis-reports. It writes nothing to disk — repair arrives in p4. It ships as one commit.

## Locked decisions (do NOT re-litigate)
See the master `plans/skill-drift-trigger.md`. The decisions binding this phase:
- Detection, policy, and formatting are separated. `scan()` returns a list of status records and prints nothing; `cmd_doctor` becomes a formatter over that list. The current `_print_skill_drift_health` fuses all three and returns `None`, which is why `init`/`queue add` could not consume it without capturing stdout.
- **Safety is keyed on the filesystem, not on a name list.** A target is unsafe to write when the leaf OR any parent component is a symlink. The hardcoded `VENDORED_SKILLS` name set (`end_of_line/cli.py:2293`) stops being the *write-safety* mechanism this phase; it may remain as a separate *ownership* signal. Same wording as the master's Non-goal — neither says the set is deleted.
- One `SKILL.md` per skill is the packaged unit (`pyproject.toml:21`). `scan()` compares that one file and asserts the invariant rather than walking directories.

## Work

**Carried in from p1 (shipped `853a7f4`).** The test harness now patches `HOME` for you: `CluTestCase` points it at `tmp_path / "home"`, and `isolate_registry` patches it too — its signature is now `isolate_registry(testcase, tmp_path, home: Path | None = None)`. Any test class in this phase should subclass `CluTestCase` (or call `isolate_registry`) and **not** re-patch `HOME` itself. p1 also found three production constants that bind `Path.home()` at IMPORT time — `top.py:34`, `notify_imessage_inbound.py:24` and `:25` — which no `setUp` patch can reach; if this phase touches any of them, an env patch will not isolate it.

- `end_of_line/skill_sync.py` — new module. A frozen dataclass per skill plus a pure `scan()`.

  ```python
  @dataclass(frozen=True)
  class SkillStatus:
      name: str
      target: Path              # ~/.claude/skills/<name>/SKILL.md
      placement: Literal["file", "link", "absent", "broken", "unreadable"]
      content: Literal["in_sync", "differs", "unknown"]
      writable: bool            # False when leaf or any parent is a symlink

  def scan(names: Iterable[str] | None = None) -> list[SkillStatus]: ...
  ```
  `placement` is decided with `is_symlink()` BEFORE `exists()`, because
  `exists()` follows links and reports a dangling link as absent — the bug at
  `end_of_line/cli.py:2817`. `writable` walks the parents: for
  `~/.claude/skills/plan/SKILL.md` the symlink is the parent directory, so a
  leaf-only check reports it writable when it is not (probed this session).
  Bundled bytes come from `importlib.resources.files("end_of_line")` —
  the canonical read, no `as_file()` needed.

- `end_of_line/cli.py` — `_print_skill_drift_health` becomes a formatter over `scan()`. The skill-name registry it iterates is `BUNDLED_SKILLS`, a tuple of seven names at `cli.py:2275-2283`. It keeps its current quiet-when-clean contract and its single call site in `cmd_doctor`, and gains lines for the three newly-visible cases (`broken`, `unreadable`, and a `link` placement). Its bare `except OSError: continue` (`cli.py:2822`) is deleted — an unreadable install becomes `placement="unreadable"` and is reported, not silently treated as in sync.

- `tests/test_skill_sync.py` — new. Table-driven over the five placements and three content states, under p1's harness `HOME`. (Confirmed in p1: `tests/test_skill_drift.py:38` already redirects `HOME` to `self.tmp_path / "home"` — the exact value the harness now sets, so its per-test patch is redundant rather than conflicting.) Includes the three cases the old check got wrong: a dangling symlink, a symlinked parent directory, and a file whose read raises.

- `tests/test_skill_drift.py` — existing tests move onto `scan()`. `test_not_installed_is_quiet` keeps its meaning and is now expressed as `placement == "absent"`.

- Consumes: `BUNDLED_SKILLS: tuple[str, ...]`, `cmd_doctor(args) -> int`
- Produces: `SkillStatus` (frozen dataclass, fields above), `scan(names: Iterable[str] | None = None) -> list[SkillStatus]`

## Decisions & findings

### Decision: write-safety is a filesystem property, not a name  *(status: active)*
- **Rationale:** the hazard is that a write resolves into a directory clu does not own. That is decided by symlinks on the path, not by which skill it is. A name list cannot protect a user who symlinks `clu-plan` into a working checkout, and needlessly excludes a user whose `plan` is a plain directory.
- **Alternatives considered:** keeping `VENDORED_SKILLS` as the guard — rejected: it is demonstrably wrong in both directions, and it currently suppresses the *warning* about the one file the install path will happily overwrite. Resolving the path and comparing against an allowed root — rejected as more machinery for the same answer, and `Path.resolve()` collapses the very distinction being tested.
- **Evidence:** `end_of_line/cli.py:2293` (the name set), `cli.py:2394-2399` (parent-symlink detected but only warned about), `cli.py:2390-2391` (the guard's stated reason, "a symlink clu owns", against `grep -rn 'symlink_to\|os.symlink' end_of_line/` returning nothing).

### Decision: `scan()` prints nothing and decides no policy  *(status: active)*
- **Rationale:** three callers need the same facts and three different behaviours — doctor reports, `init` and `queue add` repair. A printer that returns `None` forces the other two to capture stdout or duplicate the logic.
- **Alternatives considered:** a `repair: bool` flag on the existing printer — rejected under the project's own rule that an abstraction needing a mode flag to serve its callers is two functions in a trenchcoat.
- **Evidence:** `end_of_line/cli.py:2799` (returns `None`, prints), `cli.py:2681` (its single caller).

## Failure modes to anticipate

- `Path.resolve()` used anywhere in the placement decision collapses the symlink distinction the whole phase turns on. Use `is_symlink()` on each component; resolve only for display.
- A parent-directory walk that stops at `~/.claude/skills/` misses a symlinked `~/.claude` or a symlinked `~`. Walk to the filesystem root.
- `importlib.resources.files()` under this repo's editable install resolves into the working tree, so a dirty checkout makes "bundled" mean "whatever is uncommitted". Correct for detection; it becomes a real hazard in p4 when the same bytes get written. Note it here; p4 owns the mitigation.
- Deleting the `except OSError` swallow turns a previously-silent condition into output. `tests/test_doctor.py` asserts with `assertIn`/`assertNotIn` and has no exact-stdout equality assertion, so this is unlikely to break a test by construction — but a new line can still trip an `assertNotIn`. If one fires, update it; never restore the swallow.
- `cmd_doctor` dies without `.orchestrator.json` (the `_die` is at `cli.py:2648-2653`; the docstring describing it is at 2643), so drift is only ever surfaced inside an initialized project even after this phase. That is pre-existing and NOT fixed here; p4's entry points are what make the check reachable in practice.
- A skill directory containing files other than `SKILL.md` is invisible to both the packaging glob and `scan()`. Assert the invariant in a test so it fails loudly if someone adds a second file, rather than shipping a silent partial sync.

## Done criteria

- The suite passes: `python3 -m unittest discover -s tests`, full count reported.
- **Observable, produced and measured:** construct a temp `HOME` containing all five placements — a clean install, a stale install, a dangling symlink, a real file under a symlinked parent, and an unreadable file (mode `000`) — run `scan()`, and record its actual output for each. The dangling symlink reports `broken` (the old check reported it as absent), the symlinked-parent case reports `writable=False` (the old check would have written through it), and the unreadable case reports `unreadable` (the old check reported it as in sync). Quote all five records in the commit message.
- **Every field of `SkillStatus` is exercised.** `placement` has five values and `content` has three: name each one in a test and say what input produces it. A default branch that silently absorbs an unnamed state is the failure this criterion exists to prevent.
- **`clu doctor` is exercised against a known-drifted state, not against whatever the machine happens to hold.** Deliberately stale one installed skill, run `clu doctor`, and confirm the output names that skill; then restore it, run again, and confirm the drift section is silent. Quote both runs. Asserting only "silent when none differ" passes on a machine where nothing is drifted and nothing is printed.
- `_print_skill_drift_health` contains no filesystem logic: it reads `scan()` and formats. Confirm by reading the function, and state its line count before and after.
