# skill-drift-trigger-p3 — provenance: shipped fingerprint manifest + install record

You are phase `p3` of the `skill-drift-trigger` plan. This phase makes "did clu write this copy?" a decidable question, so that p4 can update stale copies without destroying a copy someone edited by hand. It ships as one commit and still writes nothing to `~/.claude/`.

## Locked decisions (do NOT re-litigate)
See the master `plans/skill-drift-trigger.md`. The decisions binding this phase:
- **Provenance lives beside the skills, never inside them.** Claude Code documents a free-form `metadata` frontmatter map that third-party tooling may use, so a marker in the SKILL.md is permitted — but it would make the installed copy differ from the bundled copy by construction, so every skill would report drift forever. The record goes in a sidecar under `clu_config_dir()`.
- **Recognition needs a hash HISTORY, not one hash.** A copy is recognized when its hash matches clu's own install record OR any fingerprint of a version clu previously shipped. One hash can only say "differs", which is exactly the ambiguity the operator's decision rejects.
- Unrecognized is reported, never prompted on. `clu init` and `clu queue add` run in scripts and under cron.

## Work

**Carried in from p2 (shipped `22ac6d0`).** `skill_sync` now exports `installed_path(name)`, `bundled_bytes(name)` and `is_writable_target(target)` beyond p2's declared `Produces:` line — use them rather than re-deriving. `scan()` raises `ValueError` for a name outside `BUNDLED_SKILLS`. `BUNDLED_SKILLS` is imported lazily INSIDE `scan()` because `cli` imports this module; a module-scope import cycles, and p3 extends the same module. **And resolve any temp `HOME` before asserting on writability** — on macOS `$TMPDIR` sits under `/var`, a symlink, so an unresolved temp home makes every target read `writable=False`.

**Carried in from p1 (shipped `853a7f4`).** The test harness now patches `HOME` for you: `CluTestCase` points it at `tmp_path / "home"`, and `isolate_registry` patches it too — its signature is now `isolate_registry(testcase, tmp_path, home: Path | None = None)`. Any test class in this phase should subclass `CluTestCase` (or call `isolate_registry`) and **not** re-patch `HOME` itself. p1 also found three production constants that bind `Path.home()` at IMPORT time — `top.py:34`, `notify_imessage_inbound.py:24` and `:25` — which no `setUp` patch can reach; if this phase touches any of them, an env patch will not isolate it.

- `scripts/gen_skill_manifest.py` — new. Walks `git log` for each `end_of_line/skills/*/SKILL.md`, collects the SHA-256 of every committed version, and writes `end_of_line/skills_manifest.json`. Run by a human when skills change; not run at install time.

  ```python
  # {"clu-plan": ["<sha256>", ...], ...} — newest first, deduped.
  # Hash the FILE CONTENT (git show <rev>:<path> | sha256), not the git blob
  # id: blob ids include a header, so they will not match a hash taken of a
  # file on disk. This is the one detail that makes the manifest useless if
  # got wrong, and it fails silently — every copy reads as foreign.
  ```

- `end_of_line/skills_manifest.json` — new, generated. Ships in the package.

- `pyproject.toml` — add `skills_manifest.json` to `[tool.setuptools.package-data]`'s `end_of_line` list (currently `skills/*/SKILL.md`, `hooks/*.py`, `web/*.html`, `web/*.png`, `worker-settings.template.json` at line 21). Without this the manifest is absent from a wheel install and every copy reads as foreign — the feature silently does nothing for exactly the users it is for.

- `end_of_line/skill_sync.py` — add recognition to the scan.

  ```python
  # SkillStatus gains:
  provenance: Literal["recognized", "foreign", "absent"]
  # recognized  := hash matches the install record, or any manifest fingerprint
  # foreign     := installed, hash matches neither
  # absent      := nothing installed
  def record_install(name: str, digest: str) -> None: ...   # writes the sidecar
  def installed_record() -> dict[str, str]: ...             # returns the "skills" map only,
                                                            # name -> hex digest; the version
                                                            # wrapper is handled inside
  ```
  Sidecar path: `clu_config_dir() / "installed-skills.json"`. `clu_config_dir()`
  is at `end_of_line/_xdg_guard.py:17` and is already redirected in tests by
  `isolate_registry`. Schema — a flat object, skill name to lowercase hex
  SHA-256 of the bytes clu last wrote, with a schema version alongside it so a
  future shape change is detectable rather than silently misread:
  ```json
  {"schema_version": 1, "skills": {"clu-plan": "<64-hex>", "clu-phase": "<64-hex>"}}
  ```

- `end_of_line/cli.py` — `cmd_install_skill` calls `record_install()` after each successful write, so copies written from now on are recognized by the record rather than only by the manifest. `_print_skill_drift_health` reports `foreign` copies distinctly from `differs`.

- `tests/test_skill_sync.py` — recognition tests: a copy matching the record, a copy matching an older manifest fingerprint, a copy matching neither, and a manifest that is missing entirely (must degrade to record-only, not crash). **Plus the currency guard:** a test that fails when the hash of any bundled `SKILL.md` is absent from the manifest. That test is the thing that makes `gen_skill_manifest.py` get re-run when a skill changes, so it is Work, not a hope.

- Consumes: `SkillStatus`, `scan(names: Iterable[str] | None = None) -> list[SkillStatus]`, `clu_config_dir() -> Path`, `cmd_install_skill(args) -> int`
- Produces: `record_install(name: str, digest: str) -> None`, `installed_record() -> dict[str, str]`, `SkillStatus.provenance: Literal["recognized", "foreign", "absent"]`

## Decisions & findings

### Decision: sidecar record + shipped manifest, not a frontmatter marker  *(status: active)*
- **Rationale:** a per-install stamp written into the SKILL.md changes the file, so the installed copy can never be byte-equal to the bundled one and the drift check reports permanent drift. The sidecar keeps the compared artifact untouched. The shipped manifest covers the migration case the sidecar cannot: every copy installed before this feature existed has no record, and without the manifest all of them read as foreign and are never updated.
- **Alternatives considered:** `metadata:` frontmatter — permitted by Claude Code but breaks hash equality, as above. Comparing hashes with the marker line stripped — rejected as fragile parsing of a file whose format clu does not own. A single "last shipped" hash — rejected: it cannot distinguish an old clu copy from an edit, which is the whole question.
- **Evidence:** https://code.claude.com/docs/en/skills — Frontmatter (`metadata` is free-form and Claude Code "doesn't act on its contents"); https://manpages.debian.org/bookworm/ucf/ucf.1.en.html (ucf keeps one md5sum per released version and concludes user-edited only when none match); `end_of_line/_xdg_guard.py:17`.

### Decision: the manifest is generated by a human-run script, not at build time  *(status: active)*
- **Rationale:** clu has no build step beyond packaging, and adding one for this would put git access into the install path. A checked-in generated file is inspectable in review and diffable.
- **Alternatives considered:** computing fingerprints at install time from git — rejected: an installed clu has no git checkout. A setuptools build hook — rejected as a new build dependency against a zero-dep project.
- **Evidence:** `pyproject.toml:15-21` (packaging is `setuptools.packages.find` plus `package-data`; no custom build).

### Finding: writing stale bytes no longer produces a REPAIRABLE copy  *(status: active)*
- Before provenance, any stale bytes read as `differs`. Now arbitrary stale bytes read as **`foreign`** — the leave-alone path. Two existing doctor tests were asserting a section they no longer reach, and were corrected via an `_install_as_clu` helper.
- **p4 depends on this directly:** a test that wants "stale but repairable" must install a copy AND record it (or use a real shipped fingerprint). Writing bytes alone produces the case p4 must NOT touch, so a repair test built that way asserts the opposite of what it means to.

### Finding: `tests/test_install_skill.py` isolated `HOME` but never `XDG_CONFIG_HOME`  *(status: active)*
- It subclasses plain `unittest.TestCase`. The moment install started writing a provenance sidecar under `clu_config_dir()`, that became a write into the developer's real `~/.config/clu` on any machine that sets `XDG_CONFIG_HOME` — and with no `CLU_TEST_MODE` set, `assert_xdg_safe` would not have caught it either. It failed to leak here only because that variable is unset on this machine. Routed through `isolate_registry(self, tmp/"xdg", home=tmp/"home")`.

### Finding: the generator must include the WORKING-TREE copy  *(status: active)*
- Otherwise the currency guard is unsatisfiable in a single commit: a manifest generated before committing a skill change can never contain that change, so every skill edit would need two commits with a red one in between.

### Finding: the macOS symlink trap bites at the CLI level, not only in tests  *(status: active)*
- p2 recorded it for tests. The first `clu doctor` demo put every skill in the "can't compare or re-sync" section because `$TMPDIR` lives under `/var` → `/private/var`. **Any manual p4 verification must build its temp home with `cd $(mktemp -d) && pwd -P`**, or nothing will ever look repairable.

### Finding: the sidecar key is `schema_version`, not `version`  *(status: active)*
- The shard specified `{"version": 1, ...}`, which is not the repo-wide convention (`dispatch.py:49`, `monitor.py:41`, `inbox.py:67` all use `schema_version`) and would have left the file unreadable by `state.locked_json`. Corrected during the review pass, while the file is new and nothing is in the field.

### Finding: an unreadable manifest is not the same as an absent one  *(status: active — found at review)*
- Both degraded to `{}`, and the corrupt case fails in the worst direction: every copy clu wrote reads as `foreign`, doctor calls the operator's untouched file a local edit, and p4 would decline to repair it. Probed. Split so absent stays silent and correct while unreadable returns a reason doctor prints. The same collapse existed per-entry and for a future-schema sidecar.

### Finding: p1 shipped a regression this phase found and fixed  *(status: active)*
- `tests/test_doctor.py`'s resolver test reached `DEFAULT_CHAT_DB`, bound from `Path.home()` at IMPORT time — the exact hazard p1 recorded. p1's suite-wide `HOME` redirect made it resolve to a temp path with no database, so doctor took the "chat.db inaccessible" branch and never reached the resolver. It passed under `unittest discover` only because an earlier module imported `notify_imessage_inbound` while `HOME` was still real, and failed standalone and under `partest`. Bisected: clean at `96b886c`, failing from `853a7f4`. Fixed with a synthetic chat.db and a module-attribute patch. **The gate did not catch this** — `discover` order masked it.

### Finding: two unlisted files, and the plan's own audit was wrong about one  *(status: active)*
- `tests/test_skill_drift.py` and `tests/test_install_skill.py`, both required by the described work. The master's verification record concluded that this shard "never touches" `test_skill_drift.py` and re-credited it to p4 — **that conclusion was wrong**: p3 changes how doctor classifies a differing copy, which re-classifies the fixtures those tests depend on, so p3 cannot avoid the file.

## Failure modes to anticipate

- **Git blob id used instead of content hash.** `git hash-object` prepends a header, so blob ids never match a SHA-256 taken of the file on disk. Every copy reads as foreign, the feature does nothing, and nothing errors. Assert in a test that a known committed version's fingerprint matches a hash computed from the working-tree file at that revision.
- **The manifest is forgotten when a skill changes.** The version just shipped is not in the manifest, so a user who installed it reads as foreign forever. Mitigate with a test that fails when the current bundled SKILL.md's hash is absent from the manifest — that test is the thing that makes the script get re-run.
- **`package-data` omission.** Covered by a Work item, but it is worth stating twice: a wheel install without the manifest degrades silently to record-only, which for existing users means never.
- Editable install: `importlib.resources.files()` resolves into the working tree, so a dirty checkout produces a "bundled" hash that is in no manifest. On the developer's own machine this correctly reads as foreign; do not add an exemption for it.
- The sidecar is written by clu and read by clu, but two clu processes can race (a `queue add` while a `doctor` runs). Write it with the repo's atomic pattern (`end_of_line/state.py:601-613`), not `write_text`.
- A user who runs two different clu versions against one home directory gets a record from the newer and a manifest from the older. Recognition must be a membership test across both sources, never an equality test against the record alone.

## Done criteria

- The suite passes: `python3 -m unittest discover -s tests`, full count reported.
- **Observable, produced and measured:** generate the manifest, then take a SKILL.md as committed at an older revision, hash it, and confirm `scan()` classifies a copy with that content as `recognized`. Quote the revision, the hash, and the classification. Then modify one byte and confirm the same copy classifies as `foreign`. Both results in the commit message.
- **The manifest is non-empty for every bundled skill**, and the count of fingerprints per skill is stated. A skill with zero fingerprints is a generation bug, not an empty history. (`audit-skill` has the shortest history — one commit — so it is the case most likely to look like a bug while being correct.)
- **The install path records provenance:** run `clu install-skill --only clu-reply --force` against a temp `HOME`, then read the sidecar and confirm it now carries that skill's name and the hash of the bytes just written. Quote the sidecar contents before and after.
- **`clu doctor` distinguishes `foreign` from `differs` in its output** — stale a skill to an older shipped version (reads `recognized` + `differs`) and separately hand-edit one (reads `foreign`), run doctor once per state, and quote both lines. Two different states producing one indistinguishable message is the failure this criterion exists to catch.
- The current bundled version of every skill appears in the manifest — demonstrated by the guard test, run and quoted.
- `provenance` has three values; each is produced by a named input in a test and each is asserted. State what a copy that is `absent` reports for `content`, so the interaction between the two fields is pinned rather than incidental.
- A wheel built from this tree contains `skills_manifest.json` — build one, list its contents, and quote the line. A passing test suite does not prove packaging.
