# unregister-archived — `clu unregister --all-archived` for ghost cleanup

Closes [#12](https://github.com/smabe/end-of-line/issues/12). Today
`clu list` keeps showing plans whose master files have been archived
to `docs/history/plans/`. After every post-ship pass, the operator
manually runs `clu unregister --project P --plan S` per ghost. This
plan ships a batch flag that walks the registry and unregisters every
entry whose master file no longer exists.

## Goal

```
$ clu unregister --all-archived --dry-run
Would unregister:
  /Users/smabe/projects/end-of-line  →  triage-issues
  /Users/smabe/projects/end-of-line  →  bundle-recovery
  ...
$ clu unregister --all-archived
Unregistered 5 plans:
  /Users/smabe/projects/end-of-line  →  triage-issues
  ...
```

Auto-prune-on-tick (option 2 in the issue) is **out of scope**. Ship
the batch flag; defer the hot-path coupling unless ghost lists keep
accumulating.

## Locked design (do NOT re-litigate)

- **Subcommand shape**: extend the existing `unregister` subparser
  with `--all-archived`. When set, `--project` and `--plan` (currently
  `required=True` via `add_common`) become optional/forbidden.
- **Implementation**: argparse can't easily flip `required` on
  pre-added args. Two clean options — pick the simpler at write time:
  - **(a)** Stop using `add_common` for `unregister`; declare its own
    args with `--project` and `--plan` both optional, then validate in
    `cmd_unregister`: if `--all-archived` set → `--plan` forbidden, else
    `--plan` required.
  - **(b)** Keep `add_common` for the existing flow; add a separate
    early-return branch in `cmd_unregister` that intercepts
    `--all-archived` before `cfg`/`state_path` are even built. (Means
    the dispatcher signature for `cmd_unregister` needs adjustment —
    since `--all-archived` doesn't need a single `cfg`.)
  - **Recommendation**: go with (a). Cleaner, simpler test surface.
- **What "archived" means**: the master plan file at
  `<project_root>/<plan_dir>/<plan_slug>.md` no longer exists. We do
  NOT inspect `docs/history/plans/`. Filesystem absence at the
  registered path is the signal.
- **`--dry-run`** prints what *would* be removed; no mutation. Exits
  `ExitCode.OK`.
- **`--all-archived`** without `--dry-run` mutates and prints what
  was removed. Exits `ExitCode.OK` even when nothing was removed (it's
  a successful no-op, not an error).
- **Walk source**: `registry.entries()` for the full list. Per entry,
  resolve the master path via the per-project config (mirror the
  resolution `load_entry_state` already does at `registry.py:85-88`,
  but for the master file path, not the state file).
- **Helper extraction**: if the path-resolution logic is reusable, add
  it to `registry.py` (e.g. `master_plan_path(entry) -> Path | None`).
  If only one caller wants it, inline in `cmd_unregister`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| impl | `unregister-archived-impl.md` | Reshape `unregister` subparser; new `--all-archived` and `--dry-run` flags; per-entry master-file existence check; new tests in `tests/test_registry.py` (or a new `tests/test_unregister_archived.py`); `docs/operations.md` post-ship section mentions the new flag. | 2h |

## Failure modes to anticipate

- **Project root no longer exists.** A registered project whose
  directory was deleted from disk. Treat as "archived" — unregister
  it. The existing `load_entry_state` already returns None on missing
  project; mirror its tolerance.
- **`.orchestrator.json` missing or malformed.** If you can't load
  the project config, you can't resolve the master path. Skip + log
  in the dry-run output (do NOT unregister silently — operator should
  see the entry and decide). Don't crash.
- **`--all-archived` + `--plan`** combined. Reject with
  `ExitCode.GENERIC` and a clear message: "`--all-archived` is
  mutually exclusive with `--plan`."
- **Empty registry.** `--all-archived` on empty registry exits OK
  with `Unregistered 0 plans` (or `(nothing to unregister)`).
- **Concurrent mutation.** `_mutate` already takes the registry lock.
  Do all removals inside one `_mutate` window so the operation is
  atomic from the operator's POV.
