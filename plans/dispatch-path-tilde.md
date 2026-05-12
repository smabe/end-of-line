# dispatch-path-tilde — expand `~` in `dispatch.path` segments

Closes [#15](https://github.com/smabe/end-of-line/issues/15). Tiny
ergonomic win: let operators write `~/.local/bin:/usr/bin` in
`.orchestrator.json`'s `dispatch.path` instead of the fully-resolved
absolute form. Per-segment `os.path.expanduser` at config load time.

## Goal

After this plan ships, `dispatch.path: "~/.local/bin:/opt/homebrew/bin:/usr/bin"`
in any project's `.orchestrator.json` resolves to expanded segments
when the supervisor builds the worker subprocess `env`. The "no tilde"
caveat in the docs goes away.

## Locked design (do NOT re-litigate)

The full design is in the GitHub issue. Summary:

- **Per-segment expansion**, not whole-string. A `:` inside `$HOME` is
  theoretical but cheap to handle right.
- **Where**: `end_of_line/config.py`'s `load_project_config`, when
  reading the `dispatch.path` field. Not at use site in `dispatch.py`
  — keep dispatch.py oblivious; expansion is a load-time concern.
- **Empty string stays empty** ("don't set PATH override"). The
  expansion happens only when the raw value is truthy.
- **Absolute paths pass through unchanged** — `os.path.expanduser` is
  a no-op when the segment doesn't start with `~`.

## Sessions index

| Session | Plan file | Scope | Effort |
|---|---|---|---|
| impl | `dispatch-path-tilde-impl.md` | Per-segment `~` expansion in `load_project_config`. New tests in `tests/test_config.py`. Drop the "no tilde" caveat in `docs/operations.md` and `README.md`. | 30m |

## Failure modes to anticipate

- **`$HOME` not set in worker subprocess.** `os.path.expanduser("~/foo")`
  on macOS falls back to `pwd.getpwuid(os.getuid()).pw_dir` if `$HOME`
  is missing — fine. Don't add belt-and-suspenders.
- **Existing absolute-path configs.** Must keep working unchanged. The
  test must explicitly cover an all-absolute path string round-tripping.
- **Empty segments.** A trailing `:` or double `::` in the raw string
  would create empty segments after split. `os.path.expanduser("")`
  returns `""` — safe. Don't filter empties; preserve the operator's
  literal value.
