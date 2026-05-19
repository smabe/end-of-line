# gate-worktree-head — HEAD resolution must follow the worker into the worktree (closes #56)

The attestation-gate (shipped #55) captures HEAD from
`cfg.project_root` — the canonical repo, not the worker's worktree.
In worktree-mode dispatch (clu's default), worker commits land on
the `clu/<slug>` branch in `<canonical>-<slug>/`, while canonical
sits at `main`. Stamps record canonical HEAD; the gate compares
canonical-HEAD against canonical-HEAD; the worker's actual work is
invisible.

Evidence: phase 4 + 5 of #55 itself shipped with `simplify_stamped`
events at `commit_sha=b4e14e37` — canonical main's pre-dispatch
HEAD, not the worker's commits (`2d5a857`, `f7e9bde`). The gate
accepted those stamps and let the phases ship without ever seeing
the actual diff.

One phase. Helper in `state.py` + 4 callsite swaps in `cli.py`
(`cmd_verify`, `cmd_attest`, `cmd_complete`, `_compute_phase_diff`)
+ worktree-mode tests in the 3 existing test files.

## Locked design decisions

### Helper lives in `state.py`
`get_worktree(data)` already lives there. The new helper
`_claim_git_root(data, cfg)` reuses it. cli.py imports state as
`st`, so callers spell it `st.claim_git_root(...)`. Public name (no
leading underscore) since it's cross-module.

### Behavior
```python
def claim_git_root(data: dict, cfg: ProjectConfig) -> Path:
    """Return the git context for the active claim.

    Worktree-mode plans dispatch into a per-plan worktree on a
    `clu/<slug>` branch; worker commits land there, not in the
    canonical repo. Falls back to canonical when no worktree.
    """
    wt = get_worktree(data)
    if wt and wt.get("path"):
        return Path(wt["path"])
    return cfg.project_root
```

Returns `Path`, not `str` — matches the `_resolve_ref` parameter
type. Always returns a usable path (no None case).

### Call site swaps
All four use the same shape: `_resolve_ref(cfg.project_root, ...)`
→ `_resolve_ref(st.claim_git_root(data_snap, cfg), ...)` or
equivalent. `cmd_complete` already has `data_snap` in scope.
`cmd_verify` and `cmd_attest` need to load state before calling
`_resolve_ref` (they currently do it the other way around; the
order swap is part of the fix).

### `_compute_phase_diff` fix
`_compute_phase_diff(project_root, base_sha)` runs
`git -C <project_root> diff --numstat <base_sha>..HEAD`. The
`HEAD` here resolves in the canonical repo's git context — same
bug. Change signature to take `git_root: Path` (rename param) and
have `cmd_complete` pass `st.claim_git_root(data_snap, cfg)`
instead of `cfg.project_root`.

### Non-worktree mode unchanged
When the plan was init'd without `--worktree`, `get_worktree`
returns None, helper falls back to canonical, behavior is
identical. Existing non-worktree tests should pass unmodified.

### Test fixtures
The 3 worktree-mode tests need a tmp git repo + `git worktree add`
+ a fake `current_claim` with the worktree record. Existing fixture
helpers in `tests/` (look for `_init_tmp_repo` or similar) likely
cover the repo-init half. Worktree setup is small (~5 lines).

## Non-goals

- **Refactoring `_resolve_ref` itself.** Its `project_root` param
  is fine — the callers are wrong, not the helper.
- **Adding worktree-aware-ness to other clu CLI commands.** Only
  the three gate-related commands matter; other commands (clu
  blockers, clu doctor, etc.) work against canonical state by
  design.
- **Catching the case where the worktree directory was deleted
  out-from-under a live claim.** That's `clu worktree gc` territory.
  The helper trusts that if `get_worktree(data)` returns a record,
  the path is still valid.
- **Backfilling the broken stamps from #55.** Those phases already
  shipped; the stamps are historical noise in the state file.

## Files touched

- `end_of_line/state.py` — P1 modified — public helper
  `claim_git_root(data, cfg) -> Path` next to `get_worktree`.
  **API hotspot:** new public state-module function; tests import
  it.
- `end_of_line/cli.py` — P1 modified — 4 call sites:
  - `cmd_complete` (line ~3439): `_resolve_ref(cfg.project_root, "HEAD")` → uses helper
  - `cmd_verify` (line ~3795): same
  - `cmd_attest` (line ~3826): same
  - `_compute_phase_diff` call in `cmd_complete` (line ~3454): pass `git_root` from helper
  - `_compute_phase_diff` signature (line ~3374): rename param `project_root` → `git_root`
- `tests/test_cmd_verify.py` — P1 NEW test — worktree-mode round-trip.
- `tests/test_cmd_attest.py` — P1 NEW test — same shape.
- `tests/test_complete_refusal.py` — P1 NEW test — stale-stamp refusal in worktree mode.

## Per-phase done checklist

- TDD: failing tests first.
- `/simplify` after if diff >1 file or ~30 lines.
- Full suite green: `python3 -m unittest discover -s tests`.
- Structured commit format (Title / Why / What's new / Under the hood / Tests / `Co-Authored-By:` trailer).
- Stage explicit paths (no `git add -A`).
- Call `clu complete --plan gate-worktree-head --phase fix --token <T>`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| fix | `gate-worktree-head-fix.md` | Helper + 4 callsite swaps + 3 worktree-mode tests | 2h |
