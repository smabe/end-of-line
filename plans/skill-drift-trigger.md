# skill-drift-trigger — clu keeps its own installed skills current

## Phase map  *(every phase, one block — the ARC and the GATES, never the work detail)*

**Phase p1 — isolate `HOME` in the test harness**  *(prerequisite gate)*
- Enters when: start here. Nothing that writes under `~/.claude/` may land before this.
- Done signal: a **synthetic** write to `Path.home()` inside a test provably lands in a temp dir (no `init` path writes under `~/.claude/` yet — p4 creates that), and a canary file in the developer's real `~/.claude/skills/` exists before AND after a full suite run with an unchanged hash.
- If it fails: the plan stops. Every later phase writes to the home directory; without isolation the suite corrupts the developer's machine on every run.
- Shard: `plans/skill-drift-trigger-p1.md`

**Phase p2 — `skill_sync.scan()`, pure and symlink-aware**
- Enters when: p1 committed.
- Done signal: `clu doctor` reports drift from `scan()` rather than its own fused logic, and `scan()` classifies a symlinked, a dangling, and an unreadable install correctly where the old check silently skipped all three.
- If it fails: no gate — fix-forward. Nothing writes yet.
- Shard: `plans/skill-drift-trigger-p2.md`

**Phase p3 — provenance: shipped fingerprint manifest + install record**  *(decides what may be overwritten)*
- Enters when: p2 committed; `scan()` exists and returns a status per skill.
- Done signal: `scan()` classifies a copy as `recognized` or `foreign`, where recognized means its hash matches either clu's own install record or a fingerprint of a version clu previously shipped.
- If it fails: the plan stops before p4. Without a working recognized/foreign split, the repair path in p4 cannot honour the operator's decision and would overwrite hand edits.
- Shard: `plans/skill-drift-trigger-p3.md`

**Phase p4 — repair, wired into `init` and `queue add`**
- Enters when: p3 committed; the recognized/foreign split is live.
- Done signal: a staled but recognized skill is silently brought current at `clu init` and `clu queue add`, a foreign copy is reported and left byte-identical, and a symlinked target is refused.
- If it fails: no gate — this is the last phase; revert the wiring and the plan ships nothing user-visible.
- Shard: `plans/skill-drift-trigger-p4.md`

## Status & cold-start  *(which phase is NEXT)*
**Approval: APPROVED 2026-08-19**
**Authored at: a624085**

Both open decisions were approved as drafted (operator, 2026-08-19, "go ahead and dispatch" with both surfaced and unamended): the symlink-keyed write-safety check replaces the name-based exclusion, and the summary line ships with the wording shown in p4.

Verification pass 2026-08-19 — 57/57 claims checked (41 resolve · 7 do not · 6 partially · 0 uncheckable · 3 uncited) · 27 done criteria across 4 shards and 19 interface entries covered · 6 stated rules read against their mechanisms, 10 characterizations, 3 cross-shard restatements · 13 fixed · 2 promoted to approval · 1 refuted (cited) · 0 uncheckable.

Fixed, by name: p1's locked decision pointed `HOME` at the same directory as `XDG_CONFIG_HOME`, which makes `clu_config_dir()` a child of home and trips `assert_xdg_safe` on every config write under test mode — re-probed here (same-dir raises, sibling clean) and changed to `tmp_path / "home"`; p1's done signal keyed on `main(["init", ...])` writing under `~/.claude/`, which nothing does yet, rewritten against a synthetic write; p2's `Consumes:` called `BUNDLED_SKILLS` a `frozenset` when it is a tuple; p4's `Consumes:` gave `cmd_init` one argument when it takes three, and omitted `installed_record` and `SkillStatus.provenance` that p4's own rules turn on; the master and p1 named different env keys for the same patch block; the master's file overview credited `tests/test_skill_drift.py` to p3, which never touches it, instead of p4, which does, and omitted `tests/test_home_isolation.py` entirely; the master cited `cli.py:4510` for a claim that comment does not make; the `env-inject-91` citation was restated as adjacent rather than identical in both the master and p4; three line hints corrected (`2389`→`2390-2391`, `2643`→`2648-2653`, and the two install-write ranges separated); p3's manifest currency guard existed only as a failure mode and became Work, and its sidecar schema was unstated; five vacuous criteria that asserted agreement without asserting presence were split into presence-then-agreement (p1 canary, p2 doctor, p3 manifest, p4 docs surface, p4 idempotence); two failure modes were unlooked-up facts — the doctor exact-stdout risk (no such assertion exists) and the skill-reload hedge (documented, and documented the opposite way) — both restated; two uncited claims (the spec-kit/dpkg comparison, the reuse-threshold negative) relabelled as unverified rather than presented as evidence.

Refuted: the gh-aw delete-then-write incident is real but Windows-specific file locking, so it stays as illustration of the shape and the decision rests on `state.save_atomic` instead — cited in p4.

Promoted to approval: the symlink-vs-name exclusion change, and the wording of the user-visible summary line. Both are listed under Open decisions below.


**p1 SHIPPED** — `853a7f4`.

**Spec check at p1** — work items 3/3 evidenced · interface conforms (`real_home_canary(testcase: unittest.TestCase) -> Path` shipped as declared) · 2 files unclaimed by the Work list, both reported by the worker as required by the described work and recorded as a planning defect in p1's shard · +3 files added at review (`tests/test_home_isolation.py` audit tightening, `tests/__init__.py`), re-evidenced

Downstream sweep at p1 — p2 1 item corrected (the `test_skill_drift.py:38` note, now a confirmed fact rather than a prediction) + carry-in note added · p3 clean, carry-in note added · p4 clean, carry-in note added · code: p1 pinned `HOME` suite-wide and changed `isolate_registry`'s signature to take an optional `home`; no earlier phase of this plan has shipped source for it to obsolete, and the one thing it made redundant — `test_skill_drift.py:38`'s per-test patch — sets an identical value, so it is dead weight rather than a false constraint and p2 owns removing it. No constraint required promotion to a downstream Done criterion; the sweep's findings were informational.

NEXT phase is **p2**. Read `plans/skill-drift-trigger-p2.md` FIRST — it is the self-sufficient packet for that phase.

The three decisions binding p2, pulled inline so a compaction that drops the shard still leaves them visible:
- Detection, policy and formatting split apart: `scan()` returns data and prints nothing; `cmd_doctor` becomes a formatter over it.
- **Write-safety is keyed on the filesystem, not on the `VENDORED_SKILLS` name list** — a target is unsafe when its leaf OR any parent is a symlink. The name set survives only as an ownership signal.
- One `SKILL.md` per skill is the packaged unit; `scan()` compares that one file and asserts the invariant rather than walking directories.

p1's decisions, kept for the record:
- `HOME` is patched into `tests/__init__.py`'s existing `mock.patch.dict` block in `CluTestCase.setUp`, alongside `CLU_TEST_MODE` and the `COOLANT_*` keys — not via a new fixture, and not per-test. (`XDG_CONFIG_HOME` lives in a separate block inside `isolate_registry`, not this one.)
- **`HOME` points at `self.tmp_path / "home"`, a SIBLING of the XDG dir — never at `tmp_path` itself.** Same-dir makes `clu_config_dir()` a child of `Path.home()`, and `assert_xdg_safe` then raises on every clu config write under `CLU_TEST_MODE=1`. Probed this session: same-dir raises, sibling is clean.
- p1 ships with a canary assertion that fails loudly if a future test writes to the real home, because the failure this phase prevents is silent by nature.

## Non-goals

- **The Claude Code plugin route is out of scope.** Shipping clu's skills as a marketplace plugin with a `command` source would give platform-managed refresh once per session, but plugin skills are always namespaced with no documented opt-out, and worker dispatch invokes the phase skill by bare name from each project's own `.orchestrator.json` (`examples/hardened.orchestrator.json:5`). Renaming it leaves every already-initialized project's stored template invoking a name that no longer resolves — the template string is verified, the consequence is reasoning, not a cited behaviour. Operator decision, 2026-08-19, taken on the namespacing cost and the 2.1.229 version floor as well as this.
- **No check at dispatch time, and no change to the supervisor tick chain.** Operator decision, 2026-08-19: the check fires at the moments a person is present. *Peer-set safety:* dispatch and tick are peers of `init`/`queue add` as places clu could look, and excluding them is safe because they are the autonomous moments — a repair firing there would rewrite a skill under a running worker. The nearest recorded rule is `plans/archive/env-inject-91/env-inject-91-inject.md:112-114`, which tells a *worker* not to run `clu install-skill` mid-phase; that is adjacent rather than identical, so the rationale here rests on the ordering hazard itself, not on that line alone.
- **`plan` and `brainstorm` are not excluded by name.** The name-keyed `VENDORED_SKILLS` set stops being the *write-safety* mechanism and is replaced for that purpose by a filesystem-property check (it may remain as an ownership signal) (see Background findings); these two are protected because they are symlinked, and any of the other five becomes equally protected if a user symlinks it.
- **No prompting.** A foreign copy is reported, never turned into an interactive question. `clu init` and `clu queue add` run in scripts and under cron. *Operator sign-off:* the auto-update option was chosen on the stated basis that there is "no warning to miss and nothing to remember to type" (2026-08-19), which a prompt would reverse.
- **`clu-phase` is not renamed, moved, or namespaced** by any phase of this plan. *Peer-set safety:* it is singled out from the other four bundled skills because it is the only one whose name appears in machine-read config on disk — each project's `.orchestrator.json` dispatch template — so renaming it changes behaviour for existing installs in a way renaming a human-typed skill does not.
- **No retirement of `clu install-skill`.** It stays the explicit path, including its `--force` behaviour; this plan adds an automatic path beside it.

## Files touched (overview)

- `tests/__init__.py` — P1 — patch `HOME` into the existing environment patcher; add the real-home canary helper.
- `end_of_line/skill_sync.py` — P2 (new), P3, P4 — the scan/classify/repair module.
- `end_of_line/cli.py` — P2, P3, P4 — doctor consumes `scan()`; install records provenance; `init` and `queue add` call repair.
- `end_of_line/skills_manifest.json` — P3 (new) — shipped fingerprints of previously released SKILL.md versions.
- `scripts/gen_skill_manifest.py` — P3 (new) — regenerates the manifest from git history.
- `pyproject.toml` — P3 — add the manifest to `package-data`; without it the file is unpackaged and every install reads as foreign.
- `tests/test_skill_sync.py` — P2 (new), P3, P4 — the module's own tests.
- `tests/test_skill_drift.py` — P2, P4 — existing drift tests move onto `scan()`; P4 adds the init/queue-add call-site tests.
- `tests/test_home_isolation.py` — P1 (new) — the harness-isolation guard tests.
- `docs/reference.md` — P4 — per-module public surface for `skill_sync`.

## Background findings  *(cross-phase research ONLY)*

- **clu never creates a symlink.** `grep -rn 'symlink_to\|os.symlink' end_of_line/` returns nothing. So the install guard's stated reason at `end_of_line/cli.py:2390-2391` — refusing a target that is not "a symlink clu owns" — names a category that cannot exist. The branch that skips the refusal fires only on symlinks the *operator* made, which is the most permissive path guarding the most dangerous case. This is why the exclusion is re-keyed on the filesystem property.
- **A symlinked parent directory defeats leaf-level symlink checks.** For `~/.claude/skills/plan/SKILL.md` the symlink is the parent, so `target.is_symlink()` is `False` while `target.exists()` is `True` (probed this session). `cli.py:2394-2399` detects it and only warns, then `unlink()` + `write_bytes()` writes into the operator's other git repo. Any automatic path must refuse, not warn.
- **Only `SKILL.md` is packaged.** `pyproject.toml:21` ships `skills/*/SKILL.md`. A proposal to digest whole skill directories has nothing to compare against — non-`SKILL.md` files are not bundled at all. The one-file-per-skill assumption is real and undocumented; p2 records it as an asserted invariant.
- **Provenance cannot live in the SKILL.md.** Claude Code documents a free-form `metadata` frontmatter map for third-party tooling and never acts on it (https://code.claude.com/docs/en/skills — Frontmatter), so a marker there is *permitted*. It is still wrong here: writing a per-install stamp into the installed copy makes it differ from the bundled copy by construction, so every skill would report drift forever. Provenance goes in a sidecar under `clu_config_dir()` instead, leaving the files byte-identical.
- **The field pattern for old-mine-vs-user-edited is a hash history, not one hash.** Debian's `ucf` keeps one md5sum per previously released version and concludes the user edited the file only when none match (https://manpages.debian.org/bookworm/ucf/ucf.1.en.html). Single-hash tools can only say "differs". Research reported the same pattern in spec-kit (blocks, requires `--force`) and dpkg (prompts), but those two were not opened this session and are carried as unverified corroboration, not as evidence. The `ucf` manpage above is the one source actually read, and it is what grounds the operator's recognized-only decision.
- **The canonical atomic write in this repo is `state.save_atomic`** (`end_of_line/state.py:601-613`): `mkstemp(dir=target.parent)` → write → `flush` → `os.fsync` → `os.replace`. `webserver.py:143-152` uses the same shape *without* fsync — do not copy that one. `os.replace` onto a symlinked leaf replaces the link rather than writing through it, which is strictly better than unlink-then-write and leaves no window where the file is absent.
- **The reuse specialist's trigger was checked and does not fire.** `end_of_line/skill_sync.py` is a new module, but it mirrors no existing one: it follows the shape of the small single-purpose modules (`quota.py`, `coolant.py`, `inbox.py`), which exist and were read. The specialist's numeric thresholds (a block ≥30 lines, ≥3 near-verbatim methods) were NOT measured against them — the module does not exist yet to measure. If p2's implementation ends up mirroring one of those files, the trigger fires then and the recommendation is owed at that point.

## Done criteria  *(plan-level — the whole feature's exit, NOT a copy of per-phase criteria)*

- Full suite green via `python3 -m unittest discover -s tests` — the project's canonical gate, run at every phase commit and once at plan end.
- `clu verify` passes, including basedpyright, which `scripts/partest.py` does not run.
- End-to-end on a real machine, with each half asserting presence before agreement: staling an installed clu skill and then running `clu queue add` leaves that file present and byte-identical to the bundled copy, and clu printed a line naming it as updated; repeating with a hand-edited copy leaves that file present and byte-identical to the *edit*, and clu printed a line naming it as left alone. "Left alone" is the operator-facing wording; `foreign` is the internal classification — the two must not drift apart.
- The developer's real `~/.claude/skills/` is byte-identical before and after a full suite run.
- `docs/reference.md` carries the `skill_sync` public surface.

## Open decisions for approval  *(not settled — these go to the operator at the approval gate)*

- **Replacing the name-based exclusion with a symlink check.** The plan drops `VENDORED_SKILLS` as the write-safety guard and protects any skill whose path passes through a symlink instead. Drafted with the research recommendation as the default; it changes which files clu may overwrite, so it is the operator's to overturn.
- **The wording of the one-line summary.** `skills: updated clu-plan · left alone: audit-skill (edited locally)` is user-visible copy with no operator sign-off. Drafted as shown; say the word and it changes.

## Parking lot
(empty)
