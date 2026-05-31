# clu cmdline-slug anchor (close #76)

## Goal
Replace the unanchored `cmdline_match in <ps-output>` substring guard with a
single shared token-boundary predicate, so the PID-reuse guard stops
false-matching on slug prefixes (`w1` matching `w1-foo`) and incidental
substrings (log paths, attempt-context filenames). Closes #76.

## Diagnosis  *(bug)*
- **Hypothesis:** `claim_worker_alive` (`state.py:313`, `return cmdline_match in
  result.stdout`) and `reap_orphan_pgroup` (`state.py:379`, `any(cmdline_match in
  cmd ...)`) both test the marker as a bare substring. When slug `w1` dies and a
  recycled PID runs plan `w1-foo`'s worker, `"w1" in "...clu-phase w1-foo a..."`
  is True → dead worker reported alive → never reaped until lease expiry.
- **Falsifiable test:** `claim_worker_alive({"pid": <live>}, cmdline_match="w1")`
  against a process whose cmdline contains only `w1-foo` returns **True** today
  (substring), should return **False** (different token). This is the first
  failing test (TDD).
- **Test result:** Confirmed by reading `state.py:313` + `:379` — both are bare
  `in` checks. The bug is the predicate, and the cmdline string is fully visible
  to the layer, so the correct fix is a better predicate (not threading new
  data). The new test will codify it red-first before the helper lands.

## Anchor design (verified this session)
- Real cmdlines place the slug as a token bounded by non-`[a-z0-9_-]` chars:
  heartbeat `--plan w1 --phase` (space), worker templates `/clu-phase w1 a` or
  `/plan w1 resume` (space), and possibly `--plan=w1` (`=`) since the worker
  template is operator-defined (`dispatch.py:170`, `shlex.quote(plan_slug)`).
- `\b` word-boundary is **wrong**: Python `\w` = `[a-zA-Z0-9_]` excludes `-`, so
  `\bw1\b` matches `w1-foo` (hyphen is a boundary) and *fails* on `w1_foo`
  (underscore is not). The slug charset `^[a-z0-9][a-z0-9_-]{0,63}$`
  (`state.validate_slug`) straddles the `\w`/`\W` line.
- **Correct predicate:** `re.search(rf"(?<![a-z0-9_-]){re.escape(marker)}(?![a-z0-9_-])", cmdline)`.
  Negative lookbehind/lookahead on the slug's own alphabet. Rejects `w1` in
  `w1-foo` (next char `-` in charset) and `w1_foo` (`_` in charset); accepts
  `--plan=w1` (`=` not in charset), ` w1 `, `/clu-phase w1`, and slug at
  string start/end. Multi-token test markers (`/clu-phase foo bar`) match
  because the boundary is only checked at the marker's two ends.

## Non-goals
- **PID start-time / creation-time composite identity** (the `psutil` root-fix
  for true PID reuse). *Why safe to exclude:* this addresses a different,
  rarer bug — PID reuse to a process whose cmdline legitimately carries the
  *same* slug token — not the prefix/substring collision #76 names. It needs a
  new claim state field + portable stdlib start-time capture (no `psutil`; the
  project is zero-dep), which is its own design pass. The anchor fully closes
  every false-negative #76 enumerates.
- **PPID checking** — orphan reparenting to launchd breaks it; out of scope.
- **Renaming `cmdline_match` or changing caller signatures.** All callers
  (`supervisor.py:642/665`, `cli.py:4052`, `reap_claim`→`state.py:855`,
  `is_zombie_state`→`state.py:879`) already pass `plan_slug` correctly; only the
  internal match predicate changes. *Why safe:* the parameter's value is
  unchanged; only how it's compared changes.

## Files to touch
- `end_of_line/state.py` — add module-level `_cmdline_marker_present(cmdline:
  str, marker: str) -> bool` (the lookaround predicate); swap the bare `in` at
  `:313` (`claim_worker_alive`) and `:379` (`reap_orphan_pgroup`); update the
  docstrings on `claim_worker_alive` (`:278-279`), `_pgroup_member_cmdlines`
  (`:319-320`), and `reap_orphan_pgroup` (`:362-364`) to say "as a whole token",
  dropping the word "substring".
- `tests/test_state.py` — add `claim_worker_alive` tests: prefix collision
  (`w1` vs `w1-foo` → False), `_`-collision (`w1` vs `w1_foo` → False),
  `=`-separator still matches (`--plan=w1` → True); keep the existing
  multi-token-marker hit/mismatch tests green.
- `tests/test_reap_orphan_pgroup.py` — add a prefix-collision test: a group
  whose only member cmdline contains `w1-foo` is **not** signaled when
  `cmdline_match="w1"` (asserts `cmdline_mismatch=True`, no signal).

## Failure modes to anticipate
- Using `\b` instead of charset lookaround — silently reintroduces the exact
  bug for hyphenated slugs and breaks underscore slugs. (The named trap.)
- Whitespace-only boundary (` slug `) would false-*negative* on `--plan=slug`
  templates — must use the charset lookaround, not `\s`.
- `re.escape` must wrap the marker (slugs are safe, but test markers contain
  `/` and spaces) — forgetting it turns `/clu-phase foo bar` into a bad regex.
- `ps` rendering of embedded newlines in the `REAP_PG_MARKER_12345` fixture —
  if a boundary assertion fails there, the fixture marker (not the predicate)
  is what to adjust; the production contract is lowercase slugs.
- Stale "substring" wording left in docstrings → concept rot for the next
  reader (CLAUDE.md: purge deleted concepts from comments).
- Forgetting one of the two call sites — `claim_worker_alive` is the exposed
  path (single `ps -p`, no pgid scope), but `reap_orphan_pgroup` must change
  too or the contract is split.

## Done criteria
- `_cmdline_marker_present` exists; both `:313` and `:379` call it; no bare
  `cmdline_match in` remains in `state.py` (grep-clean).
- New tests prove: `w1` does not match `w1-foo` or `w1_foo`; `--plan=w1` still
  matches; the reap path does not signal a `w1-foo`-only group for `w1`.
- Existing `test_cmdline_match_hit/mismatch` and `test_cmdline_match_reaps/
  mismatch` stay green.
- Full suite green (1459 → 1459 + new tests), reported as N/N.
- No "substring" wording remains in the three touched docstrings.

## Parking lot
(empty)
