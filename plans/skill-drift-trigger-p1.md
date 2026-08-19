# skill-drift-trigger-p1 — isolate `HOME` in the test harness

You are phase `p1` of the `skill-drift-trigger` plan. This phase makes it impossible for the test suite to write into the developer's real home directory, so that every later phase — all of which write under `~/.claude/skills/` — is safe to run under `python3 -m unittest discover -s tests`. It ships as one commit and changes no production code.

## Locked decisions (do NOT re-litigate)
See the master `plans/skill-drift-trigger.md`. The decisions binding this phase:
- `HOME` is added to the existing `mock.patch.dict(os.environ, ...)` block in `CluTestCase.setUp` (`tests/__init__.py:97-110`), alongside `CLU_TEST_MODE` and the `COOLANT_*` keys. Not a new fixture, not a per-test decorator, not a conftest. (`XDG_CONFIG_HOME` is patched in a *separate* block inside `isolate_registry`, `tests/__init__.py:122-125` — it is not in this one.)
- **`HOME` points at a SIBLING of the XDG dir, `self.tmp_path / "home"` — never at `self.tmp_path` itself.** `isolate_registry` sets `XDG_CONFIG_HOME=tmp_path`, so `HOME=tmp_path` would make `clu_config_dir()` a child of `Path.home()` and `assert_xdg_safe` would raise on every registry, inbox, monitor, notify and worker-settings write under `CLU_TEST_MODE=1`. Probed this session — see Decisions & findings.
- This is a test-harness change only. `end_of_line/_xdg_guard.assert_xdg_safe` is NOT modified — the sibling path is what keeps it satisfied, so no production code learns about test mode.
- The phase ships an explicit canary assertion, because the failure it prevents is silent: a test that writes to the real home leaves a correct-looking green run behind.

## Work

- `tests/__init__.py` — add `HOME` to the environment patcher in `CluTestCase.setUp`, pointing at a subdirectory of the per-test temp dir, and add a `real_home_canary` helper that a guard test uses to prove the isolation holds. `self.tmp_path` is the `Path` already created in `setUp` from `tempfile.TemporaryDirectory()` (`tests/__init__.py:93-95`).

  ```python
  # inside CluTestCase.setUp's existing mock.patch.dict block:
  "HOME": str(self.tmp_path / "home"),   # SIBLING of XDG_CONFIG_HOME (= tmp_path)
  ```
  `Path.home()` resolves through `os.path.expanduser("~")`, which reads `HOME`
  first on POSIX, so this redirects every `Path.home() / ".claude" / ...` in
  production code without those call sites knowing about tests. Create the
  directory in `setUp`; `Path.home()` does not require it to exist, but the
  first write does.

- `tests/test_home_isolation.py` — new. Four tests: (1) inside a `CluTestCase`, `Path.home()` is under the temp dir, not the real home; (2) a synthetic write to `Path.home() / ".claude" / "skills" / "canary" / "SKILL.md"` lands under the temp dir, and that path under the *real* home does not appear; (3) `assert_xdg_safe(clu_config_dir() / "registry.json")` does NOT raise inside a `CluTestCase` — the regression guard for the sibling-path decision, which is the failure this phase most nearly shipped; (4) a guard that fails if `HOME` is removed from the patcher.

- **Audit which tests are actually covered.** `CluTestCase` is the harness, but not every test file subclasses it — 42 files call `main(["init", ...])` (`from end_of_line.cli import main`) and several declare bare `unittest.TestCase` classes. Enumerate them, confirm each either subclasses `CluTestCase` or isolates `HOME` itself, and close any that does neither. That closing is this phase's Work, not a follow-up. Record the count in the commit message.

- Consumes: `CluTestCase.setUp() -> None`, `isolate_registry(testcase: unittest.TestCase, tmp_path: Path) -> None`, `assert_xdg_safe(path: Path) -> None`, `clu_config_dir() -> Path`
- Produces: `real_home_canary(testcase: unittest.TestCase) -> Path` *(phase-local — no later phase consumes it; it exists for this phase's own guard test)*

## Decisions & findings

### Decision: `HOME` is a sibling of the XDG dir, not the same directory  *(status: active)*
- **Rationale:** `assert_xdg_safe` raises when the path it is given resolves to somewhere under `Path.home()`. With `XDG_CONFIG_HOME` and `HOME` both pointing at `tmp_path`, `clu_config_dir()` becomes `tmp_path/clu` — under home — so the guard fires on writes it was written to permit. Pointing `HOME` one level deeper puts the config dir outside home again and the guard returns via its "not under home, safe" branch.
- **Alternatives considered:** modifying `_xdg_guard` to exempt test mode — rejected: it puts a test concept into a production guard, and the guard's whole purpose is to fire under `CLU_TEST_MODE=1`. Patching `XDG_CONFIG_HOME` elsewhere — rejected: `isolate_registry` is used independently by tests that do not want `HOME` touched.
- **Evidence:** `end_of_line/_xdg_guard.py:33-48` (the guard: `resolved.relative_to(home)` → raise; `except ValueError: return  # not under home, safe`). **Probe run this session**, three cases against `assert_xdg_safe(clu_config_dir()/"registry.json")` with `CLU_TEST_MODE=1`: `HOME=<real>` → OK; `HOME=<tmp>` (same dir as `XDG_CONFIG_HOME`) → `RuntimeError`; `HOME=<tmp>/home` (sibling) → OK.

### Finding: `init` does not write under `~/.claude/` today  *(status: active)*
- Every `Path.home()` reference in `end_of_line/cli.py` (2359, 2382, 2419, 2429, 2816) sits in `cmd_install_skill`, the hook-settings path, or the drift printer. `cmd_init` writes only the project root and `clu_config_dir()`. So this phase's guard cannot be written against `main(["init", ...])` — there is no home write to catch yet; p4 creates it. The canary test therefore performs a **synthetic** write to `Path.home()`, which is what makes the guard meaningful before the thing it guards exists.

### Finding: the fix had to be bigger, and the audit item is why  *(status: active)*
- 31 of the 34 uncovered files already call `isolate_registry`, so `HOME` was patched **there** rather than duplicated into 31 `setUp`s. `isolate_registry` existed for the parallel reason (`cmd_init` writing the real `~/.config/clu/registry.json`), and p4 makes `init` write the real `~/.claude/skills/` the same way. Only two files needed direct edits.
- `isolate_registry` takes its own temp dir for `HOME` by default rather than a subdirectory of the caller's `tmp_path`: callers routinely pass a **git project root** (`test_verify_opt_out`, `test_worktree_cleanup`, `test_worker_callbacks`), and a home inside a worktree would surface in the `git status` clu's own quality gates read. `CluTestCase` passes its own `tmp_path / "home"` explicitly.

### Finding: three production constants bind `Path.home()` at IMPORT time  *(status: active)*
- `end_of_line/top.py:34` (`PROJECTS_ROOT`), `end_of_line/notify_imessage_inbound.py:24` (`DEFAULT_CHAT_DB`), and `:25` (`LEGACY_SEEN_PATH`). **No `setUp`-time `HOME` patch can reach any of them.**
- The third is the sharp one: `read_inbound_state` calls `_drop_legacy_seen` (`notify_imessage_inbound.py:332`), which **`unlink()`s** that path and swallows the `OSError` — a real-home *deletion* this phase's mechanism cannot prevent. Probed across the full suite: **0 attempts today**, because the one class reaching it passes an explicit `legacy_path`. Latent, not live. Any future test calling `read_inbound_state()` or `poll_once()` on defaults deletes the operator's real `~/.clu/seen_msg_rowid`. If a later phase needs this closed, the fix is a module-attribute patch, not an env patch.

### Finding: two test files were reading the operator's real global config  *(status: active)*
- `tests/test_config.py` and `tests/test_notify_inbound.py`'s poll tests resolved the real home to load `~/.config/clu/config.json`, which exists on this machine — so their assertions depended on operator machine state. Closed as part of the audit. **Neither file was in this phase's `## Work` list: a planning defect.** The Work item said to close any uncovered class, and these were the only ones left resolving the real home that were not deliberate read-only path assertions; the plan should have named them.

### Finding: the standing audit shipped too narrow, and review caught it  *(status: active)*
- As first written it only recognised `main([...])` with a **literal** list, so the **17** classes that build argv in a variable (`tests/test_queue_add.py:197`, `tests/test_fleet.py:135`, others) were invisible to it. Unreadable argv now counts as home-touching rather than as safe. Measured before and after: 63 classes seen → 80.
- It also treated any class whose source merely contained the text `"HOME"` as isolated, which `assertNotIn("HOME", env)` satisfies while patching nothing. Now an AST check for `HOME` as a `patch.dict` key.
- All 17 newly-visible classes turned out to be isolated already — the worker's manual audit was complete; its automated encoding was not. That gap is exactly what would have rotted.

### Finding: no escape via `pwd`, and the per-test patch agrees  *(status: active)*
- Every subprocess spawn inherits the patched environment; `test_skill_fences` (the only test invoking the installed `clu` binary) already sets both `HOME` and `XDG_CONFIG_HOME` for its child.
- `tests/test_skill_drift.py:38` already redirects `HOME` to `self.tmp_path / "home"` — the **exact value** the harness now sets. Its per-test patch applies after `setUp` and wins; both values are identical, so ordering is moot. It is now redundant rather than conflicting.

## Failure modes to anticipate

- A test outside `CluTestCase` calls `main(["init", ...])` and writes to the real home once p4 lands; the suite stays green and the developer's machine is modified. The audit Work item is what catches it.
- Patching `HOME` breaks tests that depend on the real home. **Count them rather than guessing** — run the suite immediately after the one-line patch, before writing any new test, and record the number that fail. Fix each by pointing at the temp dir, never by exempting the class.
- `Path.home()` is resolved at import time somewhere, so patching `HOME` in `setUp` arrives too late. Probe before assuming; if true the fix is a module-level patch, not abandoning the approach.
- macOS resolves `/var` → `/private/var`, so comparing the patched `HOME` against an observed path as strings fails while isolation is working. Compare resolved paths.
- A test spawns clu as a subprocess; the patched env passes down, but a subprocess that derives home from `pwd` rather than `HOME` escapes isolation. Check whether any test spawns clu as a subprocess.
- `tests/test_skill_drift.py:38` already patches `HOME` per-test. The harness patch must not fight it — a per-test patch applied after `setUp` wins, which is fine, but confirm the ordering rather than assuming.

## Done criteria

- The suite passes: `python3 -m unittest discover -s tests`, full count reported.
- **Observable, produced and measured:** write a canary file into the developer's real `~/.claude/skills/` with known content; record its SHA-256; run the full suite; re-read it. Assert three things separately, because a criterion that only asserts agreement passes when both sides are missing: the canary file **exists** before the run, it **exists** after, and the two hashes are **equal**. Also assert no new paths appeared under the real `~/.claude/skills/`. State both hashes in the commit message.
- **The guard-interaction regression test passes and is shown to be load-bearing:** `assert_xdg_safe(clu_config_dir() / "registry.json")` does not raise inside a `CluTestCase`. Demonstrate it can fail by temporarily setting `HOME` to `self.tmp_path` instead of the sibling, running the test, and quoting the `RuntimeError`; then restore. State both pass counts.
- **Probe result recorded:** the claim that `Path.home()` follows the patched `HOME` is confirmed by a run inside a `CluTestCase`, with the observed path quoted in `## Decisions & findings`.
- The audit of `main(["init", ...])` callers is complete: the count of files checked and the count that needed closing are both in the commit message.
- `tests/test_home_isolation.py` fails if `HOME` is removed from the patcher — demonstrate by removing it, running the file, and restoring. State both pass counts.
