# session-watch-dirs — registry-independent session discovery

## Status
**Approval: APPROVED 2026-06-28**  — implemented, suite 1964 green, basedpyright clean, xhigh /code-review applied (below). Ready to commit + ship.

### xhigh /code-review outcome
Applied: (1) `load_session_dirs` now skips NON-ABSOLUTE entries (`""`/relative would resolve against clu's cwd) and is loud on a non-list value; (2) extracted `_read_global_config()` — one fail-open reader (loud on bad JSON / non-object) shared by `_load_global_notify` + `load_session_dirs`; (3) extracted `top.matches_project_filter(root, project_filter)` — the `--project` resolve-compare now single-sourced across the curses + feed paths; (4) unified the container type on `Sequence[str]` end-to-end (dropped the tuple↔list churn); (5) purged the stale "registered" wording from `gather_session_rows`/`gather_rows` docstrings; (6) `clu demo --serve` intentionally omits `session_dirs` (synthetic-showcase isolation) — now commented.
Verified, not changed: `.resolve()` matches the cwd Claude records for real (non-symlinked) project roots (empirically confirmed: in-transcript cwd `==` `Path(cwd).resolve()` for this project) — the symlink edge stays the documented caveat.
Declined (rationale): per-call `.resolve()` in `matches_project_filter` — N is a handful of roots on a 1.5s poll, realpath cost is noise, not worth splitting the helper API; `roots` list (not set) in the feed resolver — first-match-returns makes overlap harmless, and the prior session-activity review removed a `seen` set from this exact function as conceptual load.

Single-phase. Not started. Builds on the just-shipped `session-activity` (commits `2cbb203 → 16cdb62`, archived `plans/shipped/session-activity*.md`). Line hints measured at `2d4c7c7`; re-anchor by symbol.

## Goal

Let `clu top` + `clu serve` surface non-worker Claude sessions in a configured set of directories (`session_dirs` in the machine-wide `~/.config/clu/config.json`), independent of whether those dirs have registered clu plans. Today discovery only scans `registry.entries()` roots; with the registry empty between plan batches, the operator's own interactive sessions never show.

## Non-goals

- **Not honoring `CLAUDE_CONFIG_DIR` or the `~/.claude/projects` → `~/.config/claude/projects` migration.** clu hardcodes `PROJECTS_ROOT = ~/.claude/projects` (top.py:30) for ALL discovery (workers included); changing *where transcripts live* is a separate cross-cutting concern. *Safe asymmetry:* this plan only adds *which cwds* to watch — the projects-root location is untouched for every code path, so no ordering/state coupling, only which rows appear.
- **Not scanning subdirectories of a `session_dir`.** Exact-root match only (cwd-confirm against the configured path), same as registry roots today. *Safe asymmetry:* display-only; a session in a subdir simply doesn't appear — no state coupling.
- **No `--session-dir` CLI flag.** Config-only for v1; a flag can layer on later.
- **session_dirs is NOT auto-loaded inside `gather_rows`.** It's threaded as a param from the CLI layer (matching how `projects_root`/`project_filter` are passed), so the discovery functions stay testable with explicit inputs and don't couple to the global config file.
- **No existence validation at config-load.** Missing/renamed dirs are skipped at scan time (`gather_session_rows`'s existing `if not d.is_dir(): continue`, top.py:451) — matches the community skip-and-combine norm; a config full of stale paths must not break the poll.

## Work

- **`end_of_line/config.py`** — new `load_session_dirs(path: Path | None = None) -> list[str]`. Mirrors `_load_global_notify` (config.py:296-320): read `global_config_path()` (config.py:274), fail open to `[]` on missing (silent) / malformed (stderr). Reads the top-level `session_dirs` key (the existing loader only reads `notify`, so a new read path is required — config.py:314). Each entry `os.path.expanduser` + `Path(...).resolve()` → `str` (to match the resolved absolute cwd Claude records in-transcript, exactly as registry roots are stored resolved at config.py:324); non-str entries + per-entry errors skipped; dedup preserving order.
  ```python
  def load_session_dirs(path: Path | None = None) -> list[str]:
      p = path or global_config_path()
      try: raw = json.loads(p.read_text())
      except OSError: return []
      except (json.JSONDecodeError, ...) as exc: print(..., file=sys.stderr); return []
      out, seen = [], set()
      for e in raw.get("session_dirs", []) if isinstance(raw, dict) else []:
          if not isinstance(e, str): continue
          try: r = str(Path(os.path.expanduser(e)).resolve())
          except (OSError, ValueError): continue
          if r not in seen: seen.add(r); out.append(r)
      return out
  ```
- **`end_of_line/top.py`** — `gather_rows` gains `session_dirs: list[str] | None = None`. After building the registry `roots` set (top.py:497-505), union in each `session_dir` that passes the SAME `project_filter` gate already applied to registry roots (top.py:491-495): when `--project X` is set, only a session_dir whose `Path(d).resolve() == Path(project_filter).resolve()` is included. `gather_session_rows` (top.py:427) is UNCHANGED — it already takes a `roots` set, cwd-confirms each file (`_confirms`, the dedup-vs-claims, the freshness gate all still apply). Thread `session_dirs` through `run()` (top.py:1034) → `render_once()` (top.py:799) → `_run_curses()` (top.py:973), all of which call `gather_rows`.
- **`end_of_line/cli.py`** — `cmd_top` (cli.py:4197) loads `config.load_session_dirs()` and passes `session_dirs=` to `top.run(...)`. `cmd_serve` (cli.py:4208) loads it and passes to `build_config(...)`.
- **`end_of_line/webserver.py`**:
  - `ServeConfig` (webserver.py:~234) gains `session_dirs: tuple[str, ...] = ()`.
  - `build_config` (webserver.py:273) gains a `session_dirs` param, stored on the returned `ServeConfig` (security logic untouched).
  - `workers_json(*, project_filter, include_transcript, session_dirs)` (webserver.py:354) forwards `session_dirs` to `gather_rows`. The handler `_dispatch` passes `cfg.session_dirs`.
  - `resolve_session_transcript(proj, sid, *, project_filter, projects_root, session_dirs)` (webserver.py:524) — candidate roots become the registry roots (via `_project_entries`, webserver.py:482) UNION the `session_dirs` whose basename `== proj` and that pass `project_filter`; the exact-`<sid>.jsonl` + freshness + `_confirms` check (added in session-activity) runs against each. `feed_json` (webserver.py:556) gains `session_dirs` and forwards it; the handler passes `cfg.session_dirs`.
- **`docs/operations.md`** — document `session_dirs` under "Global notify config (all projects)" (operations.md:1213) — rename/extend the section to cover the non-notify global key, with the `{"session_dirs": ["/abs/path", ...]}` shape + the "watches sessions in these cwds without a registered plan" semantics. **`docs/reference.md`** — note `config.load_session_dirs` in the `config.py` section (reference.md:145) and the `session_dirs` threading in the `top.py`/`webserver.py` sections.
- **`tests/`** — `test_config_global.py`: `load_session_dirs` reads + expands + resolves + dedups; missing/malformed → `[]`; non-str entries skipped. `test_top.py`: `gather_rows(session_dirs=[X])` with an EMPTY registry surfaces a fresh session whose cwd is `X` (the headline behavior); `--project` scoping filters session_dirs; a session_dir that's also registered isn't doubled (set union). `test_webserver.py`: `resolve_session_transcript` resolves a `sid` in a session_dir with no registry entry; feed routes.

## Decisions & findings

### Decision: thread `session_dirs` as a param, don't auto-load in `gather_rows`
- **Rationale:** the established pattern resolves config/args at the CLI layer and passes explicit values down (`projects_root`, `project_filter` — top.py:478); `gather_rows` reads no config today. Auto-loading the global file inside `gather_rows` would couple the pure discovery function to `~/.config/clu/config.json` and force every unit test to set up XDG-isolated global config. Threading keeps the functions testable with explicit inputs.
- **Alternatives considered:** load inside `gather_rows`/`resolve_session_transcript` (fewer touch points, but couples discovery to the config file + breaks the explicit-param test pattern). Rejected.
- **Evidence:** `gather_rows` signature top.py:478-483; `projects_root`/`project_filter` explicit-param convention; config-at-CLI absence in `cmd_top` (cli.py:4197-4205) and `cmd_serve` (cli.py:4208-4234).

### Decision: `session_dirs` is a top-level key in the global config, loaded by a new `load_session_dirs`
- **Rationale:** it's host-scoped (which of THIS machine's sessions to surface), not per-plan — so the machine-wide `~/.config/clu/config.json` is the home, not a per-project `.orchestrator.json`. The existing `_load_global_notify` reads only the `notify` block (config.py:314) and the parser is permissive (unknown keys dropped), so the key needs its own read path; mirror the loader's fail-open shape.
- **Evidence:** `_load_global_notify` config.py:296-320; permissive `raw.get(...)` parsing; `global_config_path` config.py:274-280; `clu init` does not emit config.json (operator hand-authors), so no template change needed.

### Decision: resolve configured paths to absolute (expanduser + resolve)
- **Rationale:** `gather_session_rows` cwd-confirms each transcript by string-comparing the in-file `cwd` to the root (`_confirms`). Claude records the resolved absolute cwd; registry roots are stored resolved (config.py:324). A configured `~/projects/x` must resolve to the same string or every session there is rejected. Symlink caveat: if Claude recorded an unresolved symlinked path, `resolve()` could mismatch — same edge the registry already has; accept it.
- **Evidence:** `_confirms` cwd string-equality (top.py:80-83 family, session-activity); registry resolves at register time (config.py:324).

## Failure modes to anticipate

- **Configured path doesn't match the in-file cwd** (trailing slash, symlink, unresolved path) → the session silently doesn't appear. `resolve()` + expanduser handles the common cases; symlinked-cwd mismatch is the residual edge (note in docs).
- **`project_filter` interaction:** if `--project X` is set, a machine-wide `session_dir` for a DIFFERENT project must NOT leak in. Apply the same resolve-compare gate registry roots get — test it.
- **Dedup vs a registered project:** a `session_dir` that is also a registered root must not double-list (one as worker/session, one as session). Set union dedups the roots; the claim-path dedup still suppresses live workers. Test both.
- **Stale/huge config:** dozens of dead dirs in `session_dirs` → each is a `stat`+`is_dir` skip per poll (cheap, already guarded). No cap for v1; note if the list is large.
- **Empty/missing config (the common case):** `load_session_dirs()` returns `[]`; behavior is exactly today's (registry-only). The whole feature is inert until the operator opts in.
- **basename collision in the feed:** two `session_dirs` (or a session_dir + a registered project) with the same basename and a request for `proj=<that basename>` — the exact-`<sid>.jsonl` + `_confirms` check disambiguates (the file only exists+confirms under its true root), same guarantee the registry path already relies on.

## Done criteria

- With an EMPTY registry, setting `session_dirs: ["/abs/project"]` in the global config makes a fresh Claude session whose cwd is that path appear as a `sess` row in `clu top` and `clu serve`, and clicking it streams its feed (incl. `agent` events).
- `--project X` scopes session_dirs the same as registry roots; a session_dir that's also registered isn't doubled.
- Absent/empty/malformed config → `[]`, behavior identical to today (no regression).
- `load_session_dirs` unit-tested; `gather_rows`/`resolve_session_transcript` session_dirs paths tested.
- Full suite green (`python3 -m unittest discover -s tests`); `clu verify` clean (basedpyright); `/code-review` run on the diff; docs updated.

## Parking lot
(empty)
