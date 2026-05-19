# attestation-gate-schema-config — state slot + quality config block

You are phase `schema-config` of the `attestation-gate` plan. Add the
`attestations` slot on `current_claim` and the `quality` config block
to `.orchestrator.json` parsing. No behavior change yet — gates and
callbacks land in later phases.

## Locked decisions (do NOT re-litigate)

See `plans/attestation-gate/attestation-gate.md`. Summary:

- `attestations` is a **map** (`{verify?, simplify?}`), not parallel
  fields. Each entry is `{"at": ISO8601_Z, "commit_sha": str}`.
- `attestations` is **lazy-init** — not in the default `current_claim`
  shape from `claim_phase`. The first stamp adds the key. This keeps
  pre-upgrade state files clean.
- `quality.verify_command` falls back to top-level `test_command`.
  Resolution helper lives on `ProjectConfig` (`resolved_verify_command()`).
- `quality.simplify_threshold` defaults to `{"files": 1, "lines": 30}`.
  Helper: `simplify_threshold_or_default()` returning a tuple.
- Schema version NOT bumped. Verify `st.load` tolerates the new field
  before assuming. If it rejects unknown fields → bump SCHEMA_VERSION
  + minimal migration. Likely not needed.

## Read first

- `end_of_line/state.py:67-80` — `SCHEMA_VERSION`, defaults section.
- `end_of_line/state.py:200-220` — `empty_state` shape; confirm
  `current_claim: None` baseline (no need to change).
- `end_of_line/state.py:350-380` — `claim_phase` (where
  `current_claim` is constructed). Confirm we don't need to seed
  `attestations` here — it lazy-inits.
- `end_of_line/state.py:430-460` — `release_claim` (clears claim).
  Attestations naturally die with the claim — no extra cleanup.
- `end_of_line/config.py` (whole file ~170 lines) — match the
  `DispatchSpec` / `NotifySpec` patterns for `QualitySpec`.
- `end_of_line/config.py:67-103` — `ProjectConfig` dataclass; add
  `quality` field + resolution helpers.
- `end_of_line/config.py:137-173` — `load_project_config` parser;
  add quality block parsing.
- `tests/test_config.py:88-100` — `test_command` test patterns; mirror
  for the new fields.

## Produce

1. **Failing tests first.** New file `tests/test_attestations.py`:
   - `test_stamp_attestation_adds_verify_key` — call helper with
     `kind="verify"`, `commit_sha="abc123"`. Assert
     `claim["attestations"]["verify"] == {"at": ..., "commit_sha": "abc123"}`.
   - `test_stamp_attestation_adds_simplify_key` — same for `simplify`.
   - `test_stamp_attestation_lazy_inits_map` — claim without
     `attestations` key → helper creates the key.
   - `test_stamp_attestation_overwrites_existing` — re-stamping
     same kind replaces the entry (no append).
   - `test_stamp_attestation_iso8601_z_format` — `at` field matches
     `r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"`.
   - `test_release_claim_drops_attestations` — `release_claim` clears
     the whole `current_claim` including any attestations.

   Augment `tests/test_config.py`:
   - `test_quality_block_default_when_absent` — no `quality` key →
     `cfg.quality.verify_command is None` and
     `cfg.quality.simplify_threshold is None`.
   - `test_quality_verify_command_loaded` —
     `{"quality": {"verify_command": "make test"}}` →
     `cfg.quality.verify_command == "make test"`.
   - `test_quality_simplify_threshold_loaded` —
     `{"quality": {"simplify_threshold": {"files": 3, "lines": 50}}}` →
     `cfg.quality.simplify_threshold == {"files": 3, "lines": 50}`.
   - `test_resolved_verify_command_prefers_quality_block` — both
     `test_command` (top-level) and `quality.verify_command` set →
     returns the quality block value.
   - `test_resolved_verify_command_falls_back_to_test_command` —
     only `test_command` set → returns it.
   - `test_resolved_verify_command_returns_none_when_neither_set` —
     neither field → returns None.
   - `test_simplify_threshold_or_default_returns_default` — no
     override → returns `(1, 30)`.
   - `test_simplify_threshold_or_default_returns_override` — override
     → returns the configured tuple.

2. **Implementation.**
   - `end_of_line/state.py` — add helper:
     ```python
     def stamp_attestation(data: dict, kind: str, commit_sha: str) -> None:
         """Stamp current_claim.attestations[kind] with HEAD SHA + now().

         Raises ValueError if no current_claim. Lazy-inits the
         attestations map. Overwrites any prior stamp for the same kind.
         """
         claim = data.get("current_claim")
         if not claim:
             raise ValueError("stamp_attestation: no current_claim")
         claim.setdefault("attestations", {})
         claim["attestations"][kind] = {
             "at": _now_iso(),
             "commit_sha": commit_sha,
         }
     ```
   - `end_of_line/config.py` — new `QualitySpec` dataclass:
     ```python
     @dataclass
     class QualitySpec:
         verify_command: str | None = None
         simplify_threshold: dict | None = None  # {"files": int, "lines": int}
     ```
   - `ProjectConfig` gains `quality: QualitySpec = field(default_factory=QualitySpec)`
     and two methods:
     ```python
     def resolved_verify_command(self) -> str | None:
         return self.quality.verify_command or self.test_command

     def simplify_threshold_or_default(self) -> tuple[int, int]:
         t = self.quality.simplify_threshold
         if t is None:
             return (1, 30)
         return (int(t.get("files", 1)), int(t.get("lines", 30)))
     ```
   - `load_project_config` — parse `quality` block. Validate:
     ```python
     def _validate_quality(raw: dict) -> QualitySpec:
         q = raw.get("quality") or {}
         vc = q.get("verify_command")
         if vc is not None and not isinstance(vc, str):
             raise ConfigError(
                 f"quality.verify_command: must be string, got {type(vc).__name__!r}"
             )
         st_raw = q.get("simplify_threshold")
         if st_raw is not None:
             if not isinstance(st_raw, dict):
                 raise ConfigError(
                     "quality.simplify_threshold: must be object with files+lines"
                 )
             for key in ("files", "lines"):
                 v = st_raw.get(key)
                 if not isinstance(v, int) or v < 0:
                     raise ConfigError(
                         f"quality.simplify_threshold.{key}: must be non-negative int"
                     )
         return QualitySpec(verify_command=vc, simplify_threshold=st_raw)
     ```

3. **Acceptance.**
   - All ~14 new tests green.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - `grep -n "stamp_attestation\|QualitySpec\|resolved_verify_command\|simplify_threshold_or_default" end_of_line/` returns ≥5 hits.
   - `python3 -c "from end_of_line.config import ProjectConfig, QualitySpec; print(ProjectConfig(project_root='/tmp').quality)"` prints `QualitySpec(verify_command=None, simplify_threshold=None)`.

4. **Commit + complete.**
   - Title: `attestation-gate: phase schema-config — attestations slot + quality config block (#55)`
   - Stage: `end_of_line/state.py`, `end_of_line/config.py`, `tests/test_attestations.py`, `tests/test_config.py`.
   - `clu complete --plan attestation-gate --phase schema-config --token <T>`.

## Failure modes to watch

- **`st.load` strictness.** If load rejects unknown fields, the
  attestations field on existing claims (none initially, but any
  future state-file mutation that produces one) won't parse. Check
  `state.py:load` — if it strips unknowns, fine; if it asserts, bump
  schema version.
- **`_now_iso` helper.** Check if `state.py` has one already (it does;
  used by event timestamps). Reuse, don't redefine.
- **`QualitySpec.simplify_threshold` as dict, not dataclass.** Keep it
  a dict for json round-trip simplicity. The `simplify_threshold_or_default`
  helper normalizes to tuple at the use-site.
- **Test isolation.** Use `tests.isolate_registry(self, tmp_path)` in
  any test that touches registry. Config tests typically don't, but
  attestation tests via `stamp_attestation` work on dicts in memory —
  no registry needed.
