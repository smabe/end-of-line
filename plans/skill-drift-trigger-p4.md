# skill-drift-trigger-p4 — repair, wired into `init` and `queue add`

You are phase `p4` of the `skill-drift-trigger` plan. This phase adds the write path and calls it from the two commands a person runs before autonomous work starts. It is the phase that makes the feature live, and the first one that modifies anything under `~/.claude/`. It ships as one commit.

## Locked decisions (do NOT re-litigate)
See the master `plans/skill-drift-trigger.md`. The decisions binding this phase:
- **Repair only what `scan()` calls `recognized` and `writable`.** A `foreign` copy is reported and left byte-identical. A target whose leaf or any parent is a symlink is refused and reported — never written, not even with a warning. The existing `cli.py:2394-2399` warn-then-write-through behaviour is exactly what this phase must not reproduce.
- **The check fires at `clu init` and `clu queue add` only.** Not at dispatch, not on the supervisor tick.
- **Never from a worker.** `cmd_queue_add` early-returns to `_cmd_queue_add_worker` when `--token` is present; repair goes AFTER that branch so a worker calling `queue add` mid-plan never rewrites the operator's skills. The nearest recorded rule is `plans/archive/env-inject-91/env-inject-91-inject.md:112-114`, which tells a *worker* not to run `clu install-skill` mid-phase — adjacent rather than identical, so this decision rests on the ordering hazard itself as well as on that line.
- **Before `_spawn_post_action_tick`.** That call (`end_of_line/cli.py:3299`) detaches a tick that can dispatch a worker immediately; repairing after it races a worker that has already read the stale skill.
- `cmd_install_skill` is NOT reused as the repair path. It prompts on a TTY for a `~/.claude/CLAUDE.md` edit and `_die`s with `STATUS_TRANSITION` on a regular-file collision, either of which would turn a convenience refresh into a hard failure of `clu queue add`.

## Work

**Carried in from p3 (shipped `6777324`).** Three constraints, the first two promoted to Done criteria below:
- **A "stale but repairable" fixture must be RECORDED, not just written.** Arbitrary stale bytes now classify `foreign`, which is the copy repair must leave alone — so a repair test built by writing bytes asserts the opposite of what it means to. Install and record it, or use a real shipped fingerprint.
- **Manual CLI verification needs a resolved temp home** — build it with `cd $(mktemp -d) && pwd -P`. On macOS `$TMPDIR` sits under `/var`, a symlink, so an unresolved home puts every skill in the can-not-repair path and nothing ever looks repairable.
- **Batch the sidecar write if repair updates several skills.** `record_install()` takes a cross-process lock and rewrites the whole sidecar per call; that is fine for the rare explicit install, but p4 puts it on `clu init` and `clu queue add`.
New surface available: `load_manifest() -> tuple[dict, str | None]`, `shipped_fingerprints()`, `digest()`, `record_path()`, `installed_record()`, `record_install()`, `MANIFEST_FILENAME`.

**Carried in from p2 (shipped `22ac6d0`).** Two constraints, both promoted to Done criteria below rather than left as prose:
- **Gate repair on `writable`, never on `placement`.** On a real machine the symlink is the skill DIRECTORY, so the leaf is a regular file and `placement` is `"file"` while `writable` is `False`. A placement-keyed guard never fires on the hazard this plan exists to prevent.
- **Resolve the temp `HOME` in any test that asserts a repair HAPPENED.** On macOS `$TMPDIR` sits under `/var`, itself a symlink, so an unresolved temp home makes every target `writable=False` and a repair assertion silently passes over a repair that never ran.
Also available from p2 beyond its declared `Produces:` line: `installed_path(name)`, `bundled_bytes(name)`, `is_writable_target(target)`. `scan()` now raises `ValueError` for a name outside `BUNDLED_SKILLS`.

**Carried in from p1 (shipped `853a7f4`).** The test harness now patches `HOME` for you: `CluTestCase` points it at `tmp_path / "home"`, and `isolate_registry` patches it too — its signature is now `isolate_registry(testcase, tmp_path, home: Path | None = None)`. Any test class in this phase should subclass `CluTestCase` (or call `isolate_registry`) and **not** re-patch `HOME` itself. p1 also found three production constants that bind `Path.home()` at IMPORT time — `top.py:34`, `notify_imessage_inbound.py:24` and `:25` — which no `setUp` patch can reach; if this phase touches any of them, an env patch will not isolate it.

- `end_of_line/skill_sync.py` — add the write path.

  ```python
  @dataclass(frozen=True)
  class RepairResult:
      updated: list[str]
      refused: list[tuple[str, str]]   # (name, reason: "foreign" | "symlink" | "unreadable")

  def repair(names: Iterable[str] | None = None) -> RepairResult: ...
  ```
  One write per skill, using the repo's atomic pattern from
  `end_of_line/state.py:601-613`: `mkstemp(dir=target.parent)` → write →
  `flush` → `os.fsync` → `os.replace`. `mkstemp` MUST carry `dir=` — without it
  the temp file lands on another filesystem and `os.replace` fails. Do not copy
  `webserver.py:143-152`, which omits the fsync. After a successful write, call
  `record_install()` so the copy is recognized next time.

- `end_of_line/cli.py` — call `repair()` from two places and print a one-line summary when it did anything, staying silent when everything was already current.
  - `cmd_init`: after the project is written, before the closing tips.
  - `cmd_queue_add`: after the `--token` worker branch returns (`cli.py:3202-3210`) and before `_spawn_post_action_tick(cfg)` (`cli.py:3299`).

  Output shape — one line, no prompt:
  ```
  skills: updated clu-plan, clu-monitor · left alone: audit-skill (edited locally)
  ```

- `docs/reference.md` — add the `skill_sync` public surface: `SkillStatus`, `scan`, `repair`, `record_install`, `installed_record`.

- `tests/test_skill_sync.py` — repair tests: a recognized-stale copy is updated; a foreign copy is untouched and reported; a symlinked-parent target is refused and untouched; a write interrupted before `os.replace` leaves the original intact.

- `tests/test_skill_drift.py` — `init` and `queue add` call repair; a `--token` `queue add` does NOT.

- Consumes: `scan(names: Iterable[str] | None = None) -> list[SkillStatus]`, `record_install(name: str, digest: str) -> None`, `installed_record() -> dict[str, str]`, `SkillStatus`, `SkillStatus.provenance: Literal["recognized", "foreign", "absent"]`, `cmd_init(args, cfg: ProjectConfig, state_path: Path) -> int`, `cmd_queue_add(args) -> int`
- Produces: `RepairResult` (frozen dataclass, fields above), `repair(names: Iterable[str] | None = None) -> RepairResult`

## Decisions & findings

### Decision: `os.replace` onto the leaf, never unlink-then-write  *(status: active)*
- **Rationale:** `os.replace` onto a path that is itself a symlink replaces the link rather than writing through to its destination, and it leaves no window in which the file is absent. `cmd_install_skill`'s `unlink()` + `write_bytes()` has both problems, and the field evidence is that delete-then-write is how self-updaters leave installations unrecoverable.
- **Alternatives considered:** reusing `cmd_install_skill`'s write — rejected for the reasons in Locked decisions. `shutil.copy2` — rejected: not atomic.
- **Evidence:** `end_of_line/state.py:601-613` (the repo's atomic pattern); `end_of_line/cli.py:2410-2416` (unlink-then-write); https://github.com/github/gh-aw/issues/27272 (a self-upgrade that `RemoveAll`s its directory, hits a locked file, and leaves the extension unrecoverable) — note that incident is Windows file-locking and clu is macOS-only, so it is illustrative of the delete-then-write shape rather than a hazard clu inherits. The decision rests on `state.save_atomic` being the repo's own established pattern.

### Decision: silent on success, one line when it acted  *(status: active)*
- **Rationale:** the operator's decision was that this should not be something to notice or act on. A line every run trains people to ignore it, which is how the drift went unseen for eight days in the first place.
- **Alternatives considered:** always printing a status line — rejected as noise. Printing nothing ever — rejected: overwriting a file with no record of it is worse than a line, and a refused `foreign` copy MUST be reported or the user never learns their edit is being skipped.
- **Evidence:** operator decision, 2026-08-19 (auto-update chosen specifically so there is "no warning to miss").

## Failure modes to anticipate

- **Repair placed above the `--token` branch** (`cli.py:3201-3210`), so a worker rewrites the operator's skills mid-phase. Named in Locked decisions and tested for; it is the highest-consequence placement error in this phase.
- **Repair placed after `_spawn_post_action_tick`**, so the detached tick dispatches a worker that reads the stale skill. Silent: the update happens, the worker still ran on the old copy, and everything looks correct afterwards.
- **Editable install writes a dirty working tree into the user's home.** Under `pipx install -e`, `importlib.resources.files()` resolves into the checkout, so uncommitted skill edits get installed. On a maintainer's machine that is a real hazard, not a theoretical one — decide explicitly whether repair refuses when the resolved bundle path is inside a git working tree with modifications, and record the answer here.
- A live Claude Code session has the skill in context. The docs are explicit that changes ARE picked up in-session: "When you add, edit, or remove a skill under `~/.claude/skills/` … Claude Code picks up the change within the current session, without a restart" (https://code.claude.com/docs/en/skills — Live change detection). What is undocumented is only whether a skill ALREADY pulled into a conversation's context is re-read. So do not tell users a restart is required; say the change is picked up, and treat the already-loaded case as unknown rather than as broken.
- Two clu processes repair concurrently. `os.replace` is atomic per file, so the loser's write wins whole rather than interleaving; the sidecar record needs the same atomic treatment or it can be truncated.
- A `queue add` that repairs and then fails validation leaves the skills updated and no plan queued. Acceptable and worth stating: repair is idempotent and independent of the queue operation, so it must not be rolled back.
- The refusal path stays quiet because `writable` is computed once at scan time and the filesystem changes before the write. Re-check immediately before `os.replace`, not only in `scan()`.

## Done criteria

- **The repairable fixture is proven repairable before the repair is asserted.** State the `provenance` value the fixture produced BEFORE calling `repair()`; if it reads `foreign`, the test is exercising the leave-alone path and the criterion is not met however green it looks.
- **The manual end-to-end run uses a resolved temp home.** Quote the home path used and its `writable` value; an unresolved `/var/...` path makes every skill unrepairable and the run proves nothing.

- **Repair is gated on `writable`, and a test proves a placement-keyed guard would not do.** Build the real-machine shape — a skill whose parent DIRECTORY is a symlink, leaving `placement == "file"` — stale it, run `repair()`, and show the file behind the symlink is byte-identical afterwards. State the `placement` and `writable` values the fixture produced; a fixture reporting `placement == "link"` is testing the wrong shape.
- **Every repair test resolves its temp `HOME` before asserting.** For one such test, state the `writable` value observed with the home unresolved and with it resolved. If they are both `False`, the test is asserting nothing and the criterion is not met.

- The suite passes: `python3 -m unittest discover -s tests`, full count reported.
- `clu verify` passes, including basedpyright.
- **Observable, produced and measured — the end-to-end run on this machine.** Stale an installed clu skill by writing an older committed version over it; run `clu queue add`; then confirm the file's SHA-256 now equals the bundled copy's and quote the summary line clu printed. Then hand-edit a second installed skill; run `clu queue add` again; confirm that file's SHA-256 is unchanged from the edit and that it was named as left alone. Quote all four hashes.
- **The symlink refusal is demonstrated, not asserted.** Point a test's temp `HOME` at a skill whose parent directory is a symlink into a second temp directory, run `repair()`, and show the file behind the symlink is byte-identical afterwards. This is the case the existing installer warns about and writes through anyway, so a passing unit test that never creates a real symlink does not cover it.
- **A worker-mode `queue add` performs no write.** Run `clu queue add --token ... --plan ... --phase ...` against a stale skill and confirm the file is unchanged.
- **Repair runs before the detached tick.** Demonstrate by ordering, not by inspection: assert in a test that the repair call site precedes `_spawn_post_action_tick` in `cmd_queue_add`'s execution, e.g. by recording call order with a patch on both.
- **Idempotence is demonstrated from a known-stale start, not from whatever the machine holds.** Stale a skill deliberately, run `clu init`, and confirm it printed the skills line naming that skill; run `clu init` again and confirm it printed no skills line and the file is unchanged. Both runs printing nothing would satisfy a weaker wording while proving nothing happened at all.
- `docs/reference.md` lists the `skill_sync` public surface. Assert each side separately: the doc entry names `SkillStatus`, `scan`, `repair`, `record_install`, `installed_record`; each of those five symbols exists in the shipped module; and the signatures in the doc match the shipped ones. An entry matching a module that shipped nothing passes a match-only check.
