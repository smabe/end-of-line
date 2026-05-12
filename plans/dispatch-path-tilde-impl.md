# dispatch-path-tilde-impl — per-segment `~` expansion at config load

You are the only phase of the `dispatch-path-tilde` plan. Closes
GitHub issue [#15](https://github.com/smabe/end-of-line/issues/15).

The design pass is done. Read the master plan for context, then do
exactly what's below. Do not redesign or scope-creep.

## Locked decisions (do NOT re-litigate)

- **Where to expand**: `end_of_line/config.py`, `load_project_config`,
  inside the block that reads `dispatch.path`. NOT at the call site in
  `dispatch.py`.
- **Per-segment** (`:`-split), not whole-string.
- **Empty string** → leave empty (no PATH override).
- **Absolute / non-tilde segments** → unchanged (`os.path.expanduser`
  is a no-op for those).

## Read first

- `end_of_line/config.py` — `load_project_config` and the `Dispatch`
  dataclass (or whatever holds `path`). Find where `dispatch.path` is
  read from the JSON dict.
- `tests/test_config.py` — existing test patterns for `load_project_config`.
  Mirror the fixture style.
- `end_of_line/dispatch.py:144-145` and `:226-227` — confirm the
  consumer just does `cfg.dispatch.path` and passes it through to
  `subprocess.Popen(env={..., "PATH": cfg.dispatch.path})`. No changes
  here.
- `docs/operations.md` and `README.md` — search for the "no tilde" or
  "absolute paths only" caveat to drop.

## Produce

1. **TDD: failing tests first.** Add to `tests/test_config.py`:

   - `test_dispatch_path_expands_tilde` — config with
     `"path": "~/.local/bin:/usr/bin"` resolves to a `Dispatch.path`
     where the first segment is `os.path.expanduser("~/.local/bin")`
     and the second is `/usr/bin`.
   - `test_dispatch_path_absolute_unchanged` — config with
     `"path": "/foo:/bar"` resolves to exactly `"/foo:/bar"`.
   - `test_dispatch_path_empty_stays_empty` — config with `"path": ""`
     (or no `path` field) resolves to `""` / falsy. Confirms the
     "don't set PATH override" branch in dispatch.py still triggers.
   - `test_dispatch_path_mixed_segments` — config with
     `"path": "~/foo:/bar:~/baz"` resolves correctly: first and third
     expanded, second unchanged.

   Run the suite — all four new tests must FAIL.

2. **Implement the expansion** in `load_project_config`:

   ```python
   raw_path = disp.get("path", "") or ""
   if raw_path:
       raw_path = ":".join(
           os.path.expanduser(seg) for seg in raw_path.split(":")
       )
   ```

   Substitute `disp` for whatever the existing variable name is.
   `os` is probably already imported; if not, add it.

3. **Run the suite — all green.**

4. **Drop the "no tilde" caveat:**
   - `docs/operations.md` — find the section that documents
     `dispatch.path` (likely under "Worker PATH" or "dispatch
     configuration"). Remove or rewrite the warning to say tilde is
     expanded at load time.
   - `README.md` — same drill in the "Configure a project" section.

5. **`/simplify`** — diff is small, but run it. If clean, skip; if it
   surfaces something, fix and re-test.

6. **Commit** with the project's structured format. Title:
   `dispatch-path: expand ~ in segments at config load`. Reference
   `closes #15` in the body.

## Verification before `clu complete`

Per CLAUDE.md mandate #9: re-run `python3 -m unittest discover -s tests`
right before calling `clu complete`. Report pass/fail in the
completion summary, including the final test count.

## Acceptance (mirror back in completion summary)

- [ ] `dispatch.path: "~/.local/bin:/usr/bin"` → expanded segments
- [ ] Absolute paths pass through unchanged
- [ ] Empty string stays empty (don't-override branch preserved)
- [ ] All four new tests pass; full suite green
- [ ] `docs/operations.md` + `README.md` no longer warn against tilde
- [ ] One commit, with `closes #15` in body
