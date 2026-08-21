# session-start-blockers-answer-scope — make a blocker addressable, and the answer scoped

You are phase `answer-scope` of the `session-start-blockers` plan. You are
making `clu answer` able to name exactly one blocker and resolve within one
project, so the next phase can display blockers without the display and the
answer path disagreeing about which one the operator meant. One commit.

## Locked decisions (do NOT re-litigate)

See `plans/session-start-blockers.md`. Summary:

- `--project` on `clu answer` stops being ignored. Passed → resolve against
  `registry.entries_for_project(root)`. Omitted → unchanged, host-wide.
- New `--blocker <q-N>`. With `--plan`, bypass the locator and call
  `plan_store.op_answer_blocker` directly. This is what allows a free-text answer.
- Scoping is by PRE-FILTERING the entry list. `route_reply`'s signature does not
  change — a reply string has no project in it, and 12 tests call it with two
  args.
- `_hydrate_open_blockers` stamps each blocker from ITS OWN `phase_blocked`
  event, not the plan's most recent one.
- The inbound pollers keep the host-wide pool.

## Read first

- `plans/session-start-blockers.md` `## Findings log` — prior phases' runtime
  findings; empty if you are first.
- `end_of_line/cli.py:1049-1064` — the `answer` argparse block. Note `--project`'s
  current help: "ignored; kept for backward compat".
- `end_of_line/cli.py:4799-4820` — `cmd_answer`. Line 4801 is the host-wide call.
- `end_of_line/state_locator.py:36-85` — `find_blocker_for_reply`; the pooling
  loop at 50-58 and the `==` row recovery at 62.
- `end_of_line/state_locator.py:122-141` — `_hydrate_open_blockers` and the
  plan-wide timestamp at 127-131.
- `end_of_line/notify_base.py:17-80` — `REPLY_RE`, `OpenBlocker`, `route_reply`.
- `end_of_line/notify_base.py:127-144` — `_pick_by_last_pinged`.
- `end_of_line/plan_store.py:1054-1080` — `op_answer_blocker`; `_resolve_answer`
  at 1043-1051 already stores a non-digit answer verbatim.
- `end_of_line/registry.py:55-57` — `entries_for_project`.
- `end_of_line/notify_imessage_inbound.py:94-111` — `_cli_dispatch`; it shells
  out to `clu answer` and already passes `--project`.
- `end_of_line/notify_inbound.py:23` — re-exports `route_reply` / `OpenBlocker`.
- `README.md:241`, `docs/operations.md:1931,2450` — they already document the
  `<id> <text|index>` form this phase makes real.
- `tests/test_state_locator.py` — the pattern to mirror for locator tests.

## Produce

1. **Failing tests first.** New `tests/test_answer_scoping.py`:
   - `test_project_scoped_answer_ignores_other_projects` — two projects each with
     an open blocker, the other pinged more recently; `clu answer --project A 2`
     answers A's, not the other's. This is the cross-repo misroute; it currently
     resolves to the wrong project.
   - `test_answer_without_project_stays_host_wide` — no `--project` → existing
     behavior, guarding the terminal and poller call shape.
   - `test_same_slug_in_two_projects_resolves_by_project` — identical slug
     registered under two roots; `--plan` alone is not enough, `--project` picks
     the right one.
   - `test_blocker_flag_addresses_one_blocker` — two open blockers on one plan;
     `--blocker q-2` answers the second.
   - `test_blocker_flag_accepts_free_text` — `--blocker q-1 "use argon2"` stores
     that string verbatim.
   - `test_sibling_blockers_do_not_deadlock_a_bare_digit` — two open blockers on
     one plan, bare digit currently returns `None` forever; after the per-blocker
     timestamp fix it resolves or reports AMBIGUOUS, never silently nothing.
   - `test_unknown_blocker_id_is_refused` — `--blocker q-99` exits non-zero with
     a clear message, does not fall through to fuzzy routing.

2. **Implementation.**
   - `end_of_line/notify_base.py`: `route_reply`'s slug branch compares the
     entry's resolved `project_root` as well as the slug. Adding a field to
     `OpenBlocker` changes dataclass equality, which `state_locator.py:62` uses
     to recover the matched row — if you add one, fix that recovery to match on
     `(plan_slug, blocker_id)` in the same commit.
   - `end_of_line/state_locator.py`: `_hydrate_open_blockers` walks events in
     reverse and takes the ts of the event whose `blocker_id` equals this
     blocker's id (events carry it — `plan_store.py:1035`), falling back to `""`.
     Give `find_blocker_for_reply` a way to receive an already-scoped entry list.
   - `end_of_line/cli.py`: add `--blocker`; rewrite `--project`'s help to say it
     scopes resolution. In `cmd_answer`, choose the entry list by whether
     `--project` was passed, and when `--plan` and `--blocker` are both present,
     call `op_answer_blocker` directly.
     - **Resolving `state_path` for the direct path:** with `--project`, load
       that project's config and take `cfg.state_path(plan)`. WITHOUT
       `--project`, there is no cwd-independent way to know which project owns
       the slug — refuse with `_die(ExitCode.UNKNOWN_TASK, …)` naming
       `--project` rather than guessing across the registry.
     - **`op_answer_blocker` raises `KeyError` on an unknown id**
       (`plan_store.py:1074`). Catch it and `_die` with a message naming the ids
       that ARE open; letting it escape gives the operator a traceback.
     - Use `_die(ExitCode.X, …)`, never a bare int.
   - Docs: `README.md:241` and `docs/operations.md:1931,2450` already promise
     this synopsis — reconcile them with what now ships.

3. **Acceptance.**
   - All 7 new tests green.
   - `python3 -m unittest discover -s tests` fully green; report the count.
   - `basedpyright` clean.
   - Smoke: `python3 -m end_of_line.cli answer --help` shows `--blocker` and a
     `--project` help string that no longer says "ignored".
   - The documented synopsis matches reality: the `clu answer` forms shown at
     `README.md:241` and `docs/operations.md:2450` are runnable as written.

4. **Commit + attest + complete.**
   - Record cross-phase findings in the master's `## Findings log` if any.
   - Commit: `session-start-blockers: phase answer-scope — scope clu answer to a project and address one blocker`.
   - Stage explicit paths.
   - After the commit: `clu verify` then `clu attest --simplify`, both with
     `--plan session-start-blockers --phase answer-scope --token <T>`.
   - `clu complete --plan session-start-blockers --phase answer-scope --token <T>`.

## Failure modes to watch

- **`OpenBlocker` equality is load-bearing.** `state_locator.py:62` recovers the
  matched row with `next((pr, sp) for pr, sp, b in all_open if b == target)`.
  Change the field set without fixing that and routing silently degrades.
- **Two different call paths, don't conflate them.** The iMessage poller reaches
  the answer path TWICE over: it calls `find_blocker_for_reply` in-process to
  pick a target (`notify_imessage_inbound.py:255`), then shells out to
  `clu answer --project … --plan …` (`:94-111`). Discord calls the locator
  directly (`notify_discord_inbound.py:136`). So a locator signature change is an
  in-process break for both pollers, while a CLI flag change only affects the
  iMessage subprocess. Default any new locator parameter rather than making it
  required, and keep the existing two-arg `route_reply` call shape.
- **Don't scope by default.** A bare `clu answer 2` in a terminal must stay
  host-wide, or you break the documented terminal path and the Discord poller.
