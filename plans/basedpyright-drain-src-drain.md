# basedpyright-drain-src-drain — 31 source errors, real guards

You are phase `src-drain` of the `basedpyright-drain` plan. You deliver, as
one commit: `basedpyright` reporting zero errors in `end_of_line/` (tests/
errors remain — later phases). These 31 are the ones that can be latent real
bugs; every fix is a deliberate guard decision, not a silencing.

## Locked decisions (do NOT re-litigate)

See `plans/basedpyright-drain.md`. Summary:
- Local narrowing only: guards, early returns, intermediate-variable
  `assert`, `cast()`/`# pyright: ignore[rule]` only for true checker
  limitations with a one-line rationale. NO signature changes, NO new
  TypedDicts/dataclasses for state shapes (flag candidates in Findings log).
- `webserver.py:479`: None token = DENY path, never an assert.
- For each fix, decide: is the None path reachable? Reachable → real guard
  with chosen behavior (+ regression test). Unreachable by construction →
  narrowing assert with the invariant named in the assert message or a
  trailing comment.

## Read first

- `plans/basedpyright-drain.md` `## Findings log` — empty if first.
- Run `basedpyright --outputjson` yourself for the live list; the planning
  snapshot (2026-06-10, 1.39.6) had these 31, by file:
  - `cli.py` 5 — 2977 (Connection|None), 4534-4536 (Path|None / str|None
    into resolve/answer_blocker), 4960 (Optional subscript)
  - `notify_discord_inbound.py` 5 — 87 (getitem overload), 138-142
    (None.name / Path|None / str|None into __init__)
  - `top.py` 5 — 194-211 (None.get + dict|None into renderer)
  - `notify_imessage_inbound.py` 4 — 289, 299 (Unknown|None unpacks)
  - `dispatch.py` 2 — 283, 286 (Optional subscript)
  - `watch.py` 3 — 446 (param shadowing), 479 (TextIO|None sink), 540
    (Any|None key)
  - `webserver.py` 3 — 479 (compare_digest str|None ×2), 577 (host
    str|bytes|bytearray)
  - `top_registry.py` 2 — 319 (object→int|float|None), 471 (max over
    list[Unknown|None])
  - `cross_plan_rules.py` 1 — 369; `demo.py` 1 — 96 (Literal list return);
    plus cli.py's redeclaration sibling in watch.py
- `tests/` files covering each touched module — add regression tests beside
  existing patterns where a guard changes reachable behavior.

## Produce

1. **Failing tests first** for every fix where the None path is REACHABLE
   (the guard changes behavior). Type-only narrowing of unreachable paths
   needs no new test — basedpyright + existing suite are the acceptance.

2. **Implementation**: the 31 fixes per Locked decisions.

3. **Acceptance.**
   - `basedpyright --outputjson` → zero errors with file under
     `end_of_line/` (tests/ errors untouched — do NOT fix test files in this
     phase; disjoint file sets keep phases reviewable).
   - No NEW errors introduced anywhere (compare total count: should be
     planning-snapshot 188 minus exactly the source errors you cleared).
   - Full suite green.

4. **Commit + attest + complete.**
   - Findings: log any fix that smelled like it wants a signature change or
     schema typing (candidate refactors), and any 1.39.6-vs-newer behavior
     surprises — the gate phase pins 1.39.7.
   - Structured commit: `basedpyright-drain: phase src-drain — 31 source
     errors get real guards (#89)`.
   - Stage explicit paths: the touched `end_of_line/*.py` + any new/updated
     test files (+ master if findings logged).
   - After the commit:
     - `clu verify --plan basedpyright-drain --phase src-drain --token <T>`
     - `clu attest --simplify --plan basedpyright-drain --phase src-drain --token <T>`
   - `clu complete --plan basedpyright-drain --phase src-drain --token <T>`.

## Failure modes to watch

- **Assert-to-silence on a reachable path** is the failure this phase exists
  to avoid — an assert that fires in production is a crash you authored.
  When in doubt, guard + early return and log/notify per the module's
  existing error style.
- **Count drift**: if your local basedpyright reports a different total than
  188, the version moved — proceed against the live list, note the delta in
  Findings.
- **Sandbox suite caveat** (from env-inject-91 findings): the full suite RUN
  INSIDE the sandbox shows ~30 environment failures (socket.bind, pgroup
  signals). `clu verify` is sandbox-exempt and authoritative — judge "suite
  green" by it, and say so in the completion summary.
