# drift-check-vendored-skills

## Goal
Make `clu doctor`'s skill-drift check stop flagging VENDORED bundled skills
(`plan`, `brainstorm` — copies clu ships but isn't canonical for) while keeping
genuine staleness detection for clu-native skills. Today the check treats all 7
`BUNDLED_SKILLS` uniformly, so a user's own richer `/plan` trips a misleading
"differ from the bundle" warning whose `clu install-skill --force` suggestion
would clobber their copy with clu's.

## Diagnosis
- **Hypothesis:** `_print_skill_drift_health` (cli.py:2654) iterates the whole
  `BUNDLED_SKILLS` tuple with no native/vendored distinction, so any installed
  vendored skill whose bytes differ from clu's bundle is reported as drift.
- **Falsifiable test:** install a `~/.claude/skills/plan/SKILL.md` that differs
  from the bundled copy, run `clu doctor`; if `plan` appears under "differ from
  the bundle", the hypothesis holds. This is the first TDD test below.
- **Test result:** confirmed by read this session — `_print_skill_drift_health`
  (cli.py:2640-2672) has no vendored guard; the loop at cli.py:2654 covers every
  member of `BUNDLED_SKILLS`. The failing test is written first at implement-time
  to make the confirmation executable.

## Non-goals
- **Don't change `cmd_install_skill` / `--only` / `--list`** (cli.py:2295-2312):
  vendored skills stay installable + listable; only the DRIFT WARNING changes.
  Safe asymmetry because install/list are user-initiated actions, not unsolicited
  warnings — the misleading-noise problem is specific to the drift printer, so
  suppressing there and nowhere else is exactly scoped to the harm.
- **Don't add an in-file sentinel/marker** to the SKILL.md files — the in-file
  header is already an unreliable discriminator (clu-phase + clu-monitor are
  native but lack the "canonical copy is end_of_line" header). Classification
  stays an explicit constant.
- **Don't touch the bundled `plan` content** (shipped last commit, `2db4ba3`) or
  any other part of the `doctor` command.
- **No new dependency** — cli.py already imports `hashlib` (line 26); stdlib only.

## Work
- `end_of_line/cli.py`
  - Add `VENDORED_SKILLS = frozenset({"brainstorm", "plan"})` adjacent to
    `BUNDLED_SKILLS` (currently cli.py:2223-2231), with a comment: clu bundles
    these as a convenience but is NOT their canonical source, so an installed
    copy differing from clu's bundle is the expected steady state, not drift —
    re-syncing would clobber the user's own copy.
  - In `_print_skill_drift_health` (loop at cli.py:2654), `continue` when
    `name in VENDORED_SKILLS`.
- `tests/test_skill_drift.py` — add (mirroring the existing
  `_install`/`_bundled`/`_doctor` helpers, HOME redirected per-test):
  - `test_vendored_skill_not_flagged`: install a differing `plan`; assert it does
    NOT appear under "differ from the bundle".
  - `test_vendored_differs_native_in_sync_is_quiet`: differing `plan` + in-sync
    `clu-phase` → no drift section at all.
  - `test_vendored_skills_subset_of_bundled`: assert `VENDORED_SKILLS` ⊆
    `BUNDLED_SKILLS` (guards a typo that would silently never match).

## Decisions & findings

### Decision: classify with an explicit `VENDORED_SKILLS` set, not an in-file marker  *(status: active)*
- **Rationale:** `BUNDLED_SKILLS` is already the source-of-truth tuple the drift
  loop iterates; a sibling `frozenset` partitions it at one glance with no new
  read of file contents. Lowest conceptual load for a fixed 7-item enumeration.
- **Alternatives considered:** (a) grep an in-file sentinel comment — rejected:
  the "canonical copy is end_of_line" header is NOT a reliable discriminator
  (clu-phase + clu-monitor are native but lack it), and a marker can be stripped
  on re-sync; (b) `.gitattributes` / manifest list — rejected: drifts
  independently from the file tree, overkill for 7 in-repo files. (Prior art:
  linguist `linguist-vendored`, go `vendor/modules.txt`, `@generated` sentinels
  — all win at scale / external provenance, not for a fixed in-repo list.)
- **Evidence:** cli.py:2223-2231 (tuple), cli.py:2654 (drift loop), header-probe
  this session (clu-phase + clu-monitor are native yet lack the header).

### Decision: vendored-skill drift is silent, not soft-reported  *(status: active — see approval question)*
- **Rationale:** the drift mechanism exists for clu-NATIVE staleness (the #75
  heartbeat-loop incident was clu-phase; docstring cli.py:2641-2648). clu isn't
  canonical for vendored skills, so "yours differs from ours" is not actionable —
  suppress it rather than emit a softer non-actionable line.
- **Alternatives considered:** a separate "vendored skill X differs (expected)"
  line — rejected as noise that re-introduces the confusion for the common case
  (user maintains their own copy on purpose).
- **Evidence:** cli.py:2641-2648 docstring (the mechanism's stated purpose).

## Failure modes to anticipate
- **Public user who installed `plan` FROM clu loses the update signal** when
  clu's bundle later advances. Accepted: vendored skills are a convenience
  bundle; the drift mechanism targets clu-native staleness. Documented in the
  constant's comment.
- **`VENDORED_SKILLS` rots** if a future vendored skill is bundled but not added
  to the set → it'd be wrongly flagged. Mitigated by adjacency + comment; the
  subset test catches typos (not omissions, but an omission just keeps the old
  over-warning behavior, which is safe-by-default).
- **Over-suppression** if a native skill is wrongly added to `VENDORED_SKILLS` →
  genuine staleness missed. Mitigated: the set is exactly `{brainstorm, plan}`;
  existing `test_drift_flagged` (clu-phase) + `test_only_drifted_skill_named`
  (clu-plan) still assert native skills ARE flagged.
- **Test HOME isolation:** new tests must reuse `_install`/`_bundled`/`_doctor`
  (HOME redirected per-test) so they never read the real `~/.claude`.

## Done criteria
- `VENDORED_SKILLS` exists adjacent to `BUNDLED_SKILLS` with the rationale comment.
- `_print_skill_drift_health` skips vendored skills; native skills unchanged.
- A differing installed `plan` / `brainstorm` produces NO drift warning; a
  differing `clu-phase` / `clu-plan` still does.
- `cmd_install_skill` list / `--only` / install still cover all 7 skills (unchanged).
- New tests green; full suite green (`python3 -m unittest discover -s tests`).

## Parking lot
(empty)
```
