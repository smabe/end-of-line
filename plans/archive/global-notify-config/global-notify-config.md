# global-notify-config

## Goal
Add a machine-wide `~/.config/clu/config.json` holding shared notify channels
(+ quiet_hours) that every registered project inherits, so one Discord/iMessage
bot setup applies across all projects instead of being copy-pasted into each
project's `.orchestrator.json`. Per-project config still wins where it speaks up.

## Non-goals
- **No `clu config --show-origin` / provenance dump.** Prior art (git) recommends
  one, but it's a new CLI surface with no caller yet; the merge itself is the
  feature. Parking-lot candidate if "why is global ignored" debugging pain appears.
- **Only `notify.channels` + `notify.quiet_hours` globalize.** `dispatch`,
  `test_command`, `plan_dir`, `quality`, `auto_archive`, `inbound_auto_tick` stay
  per-project. *Why this exclusion is safe:* those keys are inherently
  project-specific (dispatch command, test command, repo layout differ per repo) —
  there is no "share one value across all projects" use case for them, unlike notify
  credentials which are genuinely machine-wide. Excluding them opens no race/ordering
  hazard because each project already reads its own value independently.
- **No `[]`-means-suppress-all semantics.** A local `channels: []` (or absent)
  inherits global. The single suppress mechanism is the per-kind `{kind, enabled:false}`
  mask. *Why:* two suppress mechanisms (empty-list AND mask) is redundant; the user
  chose masking, and there are only two kinds to mask if a project wants full silence
  (or use `clu --no-notify`).
- **No change to the project file's unguarded `json.loads` (config.py:274).** A
  malformed `.orchestrator.json` still fails loud — it's a required file. Only the
  *optional* global file fails open. The asymmetry is intentional, not an oversight.
- **No CLI to scaffold/edit the global file.** Hand-create `~/.config/clu/config.json`
  for v1.
- **No new XDG semantics.** `global_config_path()` mirrors `registry_path()`
  (registry.py:32-37) exactly, including its existing non-guard of relative
  `XDG_CONFIG_HOME` — don't diverge from the sibling helper.

## Files to touch
- `end_of_line/config.py` —
  1. `_validate_channel` (176-191): exempt `enabled:false` channels from the
     required-field check (182-186) so a `{kind, enabled:false}` mask stub validates.
  2. New `global_config_path()` — mirrors `registry_path()`: `$XDG_CONFIG_HOME/clu/
     config.json` (fallback `~/.config/clu/config.json`), calls `assert_xdg_safe`.
  3. New `_load_global_notify()` → `(channels: tuple[ChannelSpec,...], quiet_hours)`.
     Reads the global file if present; catches `(OSError, json.JSONDecodeError)` and
     returns `((), None)` on missing/empty/malformed (fail-open). Validates global
     channels via the same `_validate_channel`.
  4. `load_project_config` (269-303): after normalizing local channels (existing
     281-285 block, incl. legacy `imessage.to`), merge global-as-base / local-overrides
     by `kind`; resolve quiet_hours local-else-global.
- `tests/test_config_global.py` — new file, TDD coverage (see Done criteria).
- `tests/test_config_channels.py` — **added at review (Scope Check):** `ChannelsMigrationTestCase`
  is non-isolated and asserts channel counts; once `load_project_config` reads the global
  file it reads the operator's real `~/.config/clu/config.json`. Isolate its XDG.
- `docs/operations.md` — new "Global notify config (all projects)" section after the
  per-channel setup sections (~line 806, after the Discord setup block).

**XDG-dedup refactor (added at review — its own commit):** the `XDG_CONFIG_HOME`-vs-`~/.config`
base resolution is duplicated across 8 sites (incl. the new `global_config_path`). Extract
`clu_config_dir()` into `end_of_line/_xdg_guard.py` and rewire the exact-pattern sites:
`config.py`, `registry.py`, `monitor.py`, `notify_imessage.py`, `inbox.py`,
`hooks/clu_session_start.py`, `hooks/clu_inbox_surface.py`. Pure refactor (XDG-honoring sites
only); the drifted `Path.home()/.config` hardcodes in `notify_imessage_inbound.py` /
`notify_discord*.py` are EXCLUDED — they don't honor XDG today and converting them is a
behavior change needing its own pass. Full suite is the regression guard + a unit test for
`clu_config_dir()`.

## Merge algorithm (the load-bearing detail)
```python
# local_channels already includes legacy-imessage normalization (existing block)
local_kinds = {c.kind for c in local_channels}
channels = tuple(g for g in global_channels if g.kind not in local_kinds) + tuple(local_channels)
```
- Keeps ALL local channels (no collapse of multiple same-kind locals — avoids a
  back-compat regression).
- Drops a global channel only when the project overrides that `kind`.
- A `{kind:"discord", enabled:false}` local stub overrides global discord and is then
  filtered out by the existing downstream `c.enabled` checks → net effect: that kind is
  silenced. No separate "pop" branch needed.
- quiet_hours: `local_quiet if valid 2-tuple else global_quiet` (mirrors the existing
  line-298 2-tuple collapse).

## Precedence table (verified against config.py:281-285)
| Local `channels` | Local legacy `imessage.to` | Global file | Result |
|---|---|---|---|
| present | — | any | global base, local overrides by kind |
| absent | present | any | global base + normalized local imessage override |
| absent | absent | present | global channels as-is |
| absent | absent | absent | `()` — byte-identical to today |

## Failure modes to anticipate
- **Malformed/empty global `config.json` crashes every project load.** A freshly
  `touch`ed file is empty → `json.loads("")` raises `JSONDecodeError`. The global
  loader MUST catch `(OSError, json.JSONDecodeError)` and fail open to `((), None)`.
- **Merge collapses two same-kind local channels.** Avoided by the
  drop-global-by-kind + keep-all-local algorithm above; a naive `{c.kind: c}` dict
  would regress this. Test guards it.
- **Mask stub `{kind, enabled:false}` raises in `_validate_channel`.** Fixed by the
  enabled-exempt reorder; test asserts no raise + a disabled ChannelSpec results.
- **Legacy-imessage project silently loses global discord.** Legacy `imessage.to`
  must normalize into a local channel and then merge with global (not bypass it) —
  otherwise the "one bot everywhere" promise breaks for old configs. Test the
  absent-channels + legacy-to + global-discord path explicitly.
- **quiet_hours precedence wrong** — local absent must fall through to global, not
  silently `None`.
- **Test isolation leak** — `global_config_path()` must resolve under
  `XDG_CONFIG_HOME` so `CluTestCase` isolates it; a test reading the operator's real
  `~/.config/clu/config.json` would be non-hermetic. `assert_xdg_safe` + CluTestCase
  cover writes; reads resolve under the temp dir.
- **Back-compat drift** — with NO global file, every existing config (channels form,
  legacy form, empty form) must load byte-identical to today. This is the primary
  regression surface; full suite is the guard.

## Done criteria
- A global `config.json` with a discord channel is inherited by a project whose
  `.orchestrator.json` has no discord channel (unit test + a live `clu notify-test
  --channel discord` run from a second project root).
- Local same-kind channel overrides global; new local kind adds alongside global;
  `{kind, enabled:false}` mask disables a global kind (three unit tests).
- quiet_hours local-wins-else-global (unit test).
- Missing/empty/malformed global file → load succeeds, global ignored (unit test).
- With no global file: legacy `imessage.to`, explicit `channels`, and `channels:[]`
  all behave exactly as before (unit tests) + full existing suite green.
- `docs/operations.md` has the global-config section with the precedence table, the
  `chmod 600 ~/.config/clu/config.json` step, and a "creds live in global only, never
  re-embed per project; ~/.config is not a git repo so this is safer than the
  per-project file" note. (Secrets decision: plaintext + chmod 600, matching clu's
  existing 0o600 state files — no env/keychain/file-ref indirection in v1.)
- Full suite green; report pass count (currently 1464 per memory — expect +~8).

## Parking lot
(empty)
```
```

## Phases
1. **Validation exemption** — `_validate_channel` exempts `enabled:false` from
   required-field check. TDD: mask stub validates to a disabled ChannelSpec; a fully
   specified disabled channel still validates; an *enabled* channel missing required
   fields still raises. `/code-review` → test → commit.
2. **Global layer + merge** — `global_config_path()`, `_load_global_notify()`, merge +
   quiet_hours precedence in `load_project_config`. TDD: the full precedence table,
   override/add/mask, fail-open, back-compat no-global. `/code-review` → test → commit.
3. **Docs** — `docs/operations.md` global-config section + precedence table + creds
   note. Single-file doc edit (cycle's trivial-diff escape may skip `/code-review`).
   Commit.
