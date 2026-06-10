# basedpyright-drain-gate — pin, hard-fail the canary, enforce the gate (#89)

You are phase `gate` of the `basedpyright-drain` plan, the last phase. The
repo is at basedpyright zero when you start (tests-drain-rest's acceptance).
You deliver, as one commit: the exact-version pin, the canary promotion from
advisory to hard fail, the enforced worker gate, and the docs — closing #89.

## Locked decisions (do NOT re-litigate)

See `plans/basedpyright-drain.md`. Summary:
- **Pin `basedpyright==1.39.7`** in pyproject's dev extra (replacing
  `>=1.39`): the error set varies across versions; the canary builds a
  fresh venv, so a float reintroduces surprise reds. Keep `ruff>=0.15`
  untouched.
- **Canary** (`scripts/canary.sh:76-80`): delete the advisory block and the
  GH #89 comment; basedpyright joins the `|| fail basedpyright` pattern used
  by ruff/install/tests.
- **Enforced gate**: local untracked `.orchestrator.json` gets
  `quality.verify_command = "basedpyright && python3 -m unittest discover -s tests"`.
  `test_command` stays pure-suite. `clu verify` runs sandbox-exempt so this
  works in hardened workers. The config file is NOT committed — the
  committed artifacts are pyproject, canary.sh, docs.
- **Docs**: operations.md gets the gate description (what runs where:
  canary venv pinned, verify_command enforced, pipx local floats);
  conventions.md quality-gate area gets one cross-link line.

## Read first

- `plans/basedpyright-drain.md` `## Findings log` — REQUIRED: prior phases
  may have logged version-skew stragglers or rationale'd ignores the docs
  should mention.
- `scripts/canary.sh` — the `fail` helper + existing gate lines (74-81).
- `pyproject.toml:23-26` — dev extra.
- `docs/operations.md` "Hardened worker dispatch" + canary section;
  `docs/conventions.md` quality-gate area.
- `.orchestrator.json` at the canonical root (current verify_required +
  test_command shape; `Bash(basedpyright *)` already in --allowedTools).

## Produce

1. **Validation first (this phase's TDD analog):**
   - Scratch venv: `python3 -m venv /tmp/bp-gate-venv && pip install -e
     ".[dev]"` with the NEW pin → `basedpyright` exit 0 on 1.39.7. Any
     1.39.7-only stragglers: fix them in this commit (expected zero-to-few;
     same idioms as the drain phases).
   - `bash -n scripts/canary.sh` after the edit.

2. **Implementation**: pin + canary block + local verify_command edit + docs.

3. **Acceptance.**
   - Scratch-venv basedpyright (pinned 1.39.7) exit 0.
   - `scripts/canary.sh` carries no "advisory" / "#89" vocabulary
     (grep-verified) and fails hard on basedpyright.
   - `clu verify` against a throwaway phase claim... is not available to you
     directly — instead run the new verify command line verbatim in the
     worktree and confirm exit 0; the NEXT plan's verify runs prove it live.
   - Full suite green.

4. **Commit + attest + complete.**
   - Findings: none expected; log 1.39.7 deltas if any appeared.
   - Structured commit: `basedpyright-drain: phase gate — pin 1.39.7, canary
     hard-fail, enforced verify gate (closes #89)`.
   - Stage explicit paths: `pyproject.toml`, `scripts/canary.sh`,
     `docs/operations.md`, `docs/conventions.md` (+ master if findings).
   - After the commit:
     - `clu verify --plan basedpyright-drain --phase gate --token <T>`
     - `clu attest --simplify --plan basedpyright-drain --phase gate --token <T>`
   - `clu complete --plan basedpyright-drain --phase gate --token <T>`.
   - Completion summary MUST remind the operator: `pipx upgrade
     basedpyright` (or `pipx install basedpyright==1.39.7 --force`) so the
     local CLI matches the pin, and the canary's next weekly run is the
     first hard-gated one.

## Failure modes to watch

- **Editing canonical-root `.orchestrator.json` from a worktree** — use the
  absolute path; never `cd` out and `git commit` (silent-clobber rule).
  Preserve all existing keys (dispatch command/allowlist/path, notify,
  quiet_hours) — you are ADDING `quality.verify_command`, nothing else.
- **Gate ordering**: make the verify_command edit AFTER the scratch-venv
  validation passes — a broken gate line would refuse every subsequent
  plan's verify on this host.
- **Version skew vs the drain phases** — they ran 1.39.6 locally; you
  validate on the pinned 1.39.7. Deltas are yours to fix, not bounce.
- **Sandbox suite caveat** (env-inject-91 findings): judge green by
  `clu verify`; the in-sandbox ~30 environment failures are known and not
  yours.
