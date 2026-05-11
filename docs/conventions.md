# Conventions

clu is a small package with a few load-bearing rules that aren't visible
from any one file. Skip them and you'll either silently break a
projection, leak state into the host filesystem, or hand a worker a
forged-token escape hatch. Read this before changing logic; refer back
to it during review.

Reference cross-links: [`contract.md`](contract.md) owns the state
schema and worker callback shape; [`reference.md`](reference.md) owns
per-module API; [`architecture.md`](architecture.md) owns the process
model. This doc owns the project-private *policies* that those
references don't capture.

## TDD discipline

Every logic change starts with a failing test. New behavior, bug fix,
refactor that crosses a public surface — all of it. The pattern is AAA
(arrange / act / assert), one assertion per behavior, no shared
fixtures between unrelated cases. Tests live in `tests/test_*.py` and
the suite is `unittest`, not pytest (`python3 -m unittest discover -s
tests`).

`tests/test_worker_callbacks.py::WorkerCallbackTestCase.setUp` is the
canonical factory template: it spins a `tempfile.TemporaryDirectory`,
calls `tests.isolate_registry(self, tmp_path)`, lays out a minimal
`plans/<plan>.md`, `git init`s a real repo so SHA validation can pass,
runs `main(["init", ...])` to write the state file, and finally calls
`st.claim_phase(data, "a", lease_minutes=30)` to mint a token. Any new
test that needs a "phase under an active claim" should follow that
shape — git init, isolate registry, init plan, claim phase. Copy it
rather than improvising; the dance is load-bearing because the worker
callbacks check the live claim, the SHA against `git cat-file`, and the
schema version of the state file in that order.

After a multi-file change, run the whole suite (`python3 -m unittest
discover -s tests`) before commit. A green subset can hide a broken
projection — `completed_phase_ids` is the obvious example, since it
reads the event log and a typo'd event type compiles fine but
silently drops phases.

## `/simplify` after non-trivial work

Once a change is green, run `/simplify` before committing anything
bigger than a typo or a rename. The skill reviews the diff for reuse,
quality, and efficiency, and either fixes the issues in place or
flags them. The Day-1 pass through the security commits collapsed nine
near-identical error sites into the `_die(ExitCode.X, msg)` helper,
cut suite runtime roughly in half by removing redundant fixture
churn, and surfaced two genuine bugs in passing. The rule of thumb:
if the diff touched more than one file or added more than ~30 lines,
`/simplify` pays its own rent. It's the loop that keeps the codebase
from drifting toward "every command does the same dance in slightly
different ways."

## Structured commit format

Commit messages have a fixed shape — title under 70 chars, then four
sections, then the trailer:

```
Title under 70 chars

Why
<1-3 sentences on motivation, not changelog>

What's new
- <user-visible behavior>

Under the hood
- <implementation notes>

Tests
<count / what's covered>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

The format exists because future-you (or a phase worker on Day 6)
will read `git log --oneline` and need to know *why* a change shipped,
not just what changed. The diff already tells you what. See `2ab6711`
("Day 2.7: clu pause / resume / retry lifecycle commands") for a
representative example. The `Tests` line is non-negotiable — if a
commit has no test coverage, write that explicitly ("Tests: none —
docs-only change") rather than omitting the section.

## `ExitCode` enum, never bare ints

CLI commands return integers, but those integers are never written as
literals. The `end_of_line.cli.ExitCode` IntEnum names every code
clu can return:

| Code | Name | Meaning |
|---:|---|---|
| 0 | `OK` | Success |
| 1 | `GENERIC` | Catch-all error |
| 2 | `INVALID_SLUG` | Slug failed `validate_slug` |
| 3 | `BAD_SHA` | `git cat-file -e <sha>` rejected the commit |
| 4 | `CLAIM_MISMATCH` | Token / phase didn't match the live claim |
| 5 | `SPAWN_CAP` | Per-phase spawn cap exceeded |
| 6 | `UNKNOWN_TASK` | `task-done` referenced a task id with no live record |
| 7 | `STATUS_TRANSITION` | `pause / resume / retry` against a status that doesn't allow it |

Failures go through the `_die` helper:

```python
return _die(ExitCode.CLAIM_MISMATCH, str(exc))
```

`_die` prints `error: <msg>` to stderr and returns the int form of the
enum. Don't `print` + `return 4` by hand — the enum is the contract
between the CLI and the supervisor (which inspects exit codes from
worker callbacks), and a bare int hides the meaning at the call site.

## Worker callback contract

Every worker-side CLI command — `complete`, `block`, `spawn`,
`task-done`, `heartbeat` — takes `--token`, and that token MUST match
the `claimed_by` on `current_claim` *and* the `--phase` MUST match the
claim's phase. The check lives in `state.assert_claim_match`, which
raises `ClaimMismatch` on either mismatch. Forged or stale tokens
exit `ExitCode.CLAIM_MISMATCH` (4) and never touch the state file.

The decorator pattern in `cli.py` is:

```python
@_translate_claim_mismatch
def cmd_complete(args):
    with st.mutate(state_path) as data:
        st.assert_claim_match(data, args.token, args.phase)
        ...
```

`@_translate_claim_mismatch` catches a leaked `ClaimMismatch` and
turns it into `_die(ExitCode.CLAIM_MISMATCH, ...)` so command bodies
don't repeat the same try/except. New worker commands should reuse
this decorator and let the exception propagate. The reason this is
load-bearing: workers are spawned as `claude --print` subprocesses
that have shell access to the project. The token check is the entire
security boundary between a well-behaved worker and a misbehaving one
(or a malicious shell on the same machine). Skip it on one command
and the boundary is gone.

## Slug validation

`state.validate_slug` is the path-traversal guard. Both
`args.plan` (from operator commands) and any `phase_id` parsed out of
the master plan must pass through it before they touch a filesystem
path. The regex is `^[a-z0-9][a-z0-9_-]{0,63}$` — lowercase
alphanumerics, hyphens, underscores, up to 64 chars, must start with
alphanumeric. That rules out `..`, `/`, absolute paths, leading
dots, and any whitespace or Unicode trickery that might survive a
naive `Path(...)` join.

A bypassed slug is how `clu init --plan ../../../etc/passwd` would
write outside `plans/.orchestrator/`. There is no other defense — the
state file path is built by concatenation, and Python's path
operations will happily accept `..`. The rule: any new code path that
takes a plan or phase id from outside the trust boundary (CLI args,
plan markdown, iMessage reply) must call `validate_slug(s, kind=...)`
before joining it into any path. Don't add an "internal" call site
that skips this — there's no way to prove a string never came from
outside.

## Event type constants

Every event written to the log uses an `EVENT_*` constant from
`state.py`:

```python
EVENT_PHASE_STARTED       = "phase_started"
EVENT_PHASE_COMPLETED     = "phase_completed"
EVENT_PHASE_BLOCKED       = "phase_blocked"
EVENT_LEASE_EXPIRED       = "lease_expired"
EVENT_BLOCKER_ANSWERED    = "blocker_answered"
EVENT_BLOCKER_CONSUMED    = "blocker_consumed"
EVENT_BLOCKER_SLA_EXCEEDED = "blocker_sla_exceeded"
EVENT_PHASE_MAX_ATTEMPTS  = "phase_max_attempts"
EVENT_TASK_SPAWNED        = "task_spawned"
EVENT_TASK_COMPLETED      = "task_completed"
EVENT_PLAN_COMPLETED      = "plan_completed"
EVENT_DISPATCH_FAILED     = "dispatch_failed"
EVENT_PHASE_STALLED       = "phase_stalled"
EVENT_PAUSED              = "paused"
EVENT_RESUMED             = "resumed"
EVENT_RETRY_REQUESTED     = "retry_requested"
```

Never write a raw string. Projections like
`state.completed_phase_ids(data)` and `state.latest_event(data,
type=EVENT_PAUSED)` filter by exact type match, so a typo
(`"phase_complete"` instead of `"phase_completed"`) compiles, passes
type checking, and silently produces wrong answers — the phase looks
unfinished forever and the supervisor re-dispatches it. The constant
is the single source of truth; renaming a constant is the *only*
correct way to rename an event type, and it triggers a search for
every read site.

## Test isolation for the host registry

`clu init` writes a host-level entry to `~/.config/clu/registry.json`
so the fleet view (`clu`) can find every plan across every project.
That same code path runs in tests when a case calls `main(["init",
...])` — and without isolation, those test runs pollute the
operator's real registry with bogus `tmp/...` entries.

The mandatory helper is:

```python
from tests import isolate_registry

class MyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        isolate_registry(self, self.project)
        ...
```

It patches `XDG_CONFIG_HOME` to point at the per-test temp directory
and auto-restores via `addCleanup`. Every test that touches
`registry.register` (directly or transitively through `main(["init",
...])`, `main(["register", ...])`, or the fleet view) needs this
call in `setUp`. Forgetting it doesn't fail the test — it just
quietly writes to your real config dir, and you discover it later
when `clu` lists a dozen phantom plans.

## Atomic state mutations

Reads and writes to a state file go through `state.mutate`:

```python
with st.mutate(state_path) as data:
    st.assert_claim_match(data, token, phase)
    st.append_event(data, EVENT_PHASE_COMPLETED, phase_id=phase)
    st.release_claim(data, expected_token=token, expected_phase=phase)
```

The context manager takes a `flock` on `<state>.lock`, loads JSON,
yields the dict for mutation, and writes back atomically on exit
(temp file + `os.replace`). Don't `st.load(path)` and then
`st.save_atomic(path, data)` as two separate calls when you need both
— another tick or worker can interleave between them and the second
write clobbers their progress. `mutate` is the lock window, and the
lock window is the point.

The escape hatch is `state.locked(path)`, which gives you the raw
file lock without auto-save. Reserve it for the rare case where you
need to coordinate two state files (e.g. cross-plan operations) under
one lock; for the common case, always `mutate`.

## Load-bearing invariants

A small checklist that captures clu's security and correctness
posture in one place:

- **Token on every worker callback.** `complete / block / spawn /
  task-done / heartbeat` validate `--token` against the live claim.
  No exceptions.
- **Slug regex on every external input.** Plan slugs and phase ids
  from CLI args, plan markdown, and iMessage replies pass through
  `validate_slug` before any path join.
- **Lockfile `O_NOFOLLOW`.** `state.locked` opens the lock file with
  `O_NOFOLLOW` so a symlinked lock can't redirect the flock onto
  another process's file. Don't reopen the lock anywhere else.
- **Schema version check.** `state.load` raises
  `SchemaVersionMismatch` if the file's `schema_version` differs
  from `state.SCHEMA_VERSION`. Bump the constant whenever you change
  the JSON shape, even for additive changes that "look" backwards
  compatible — the explicit fail is better than a half-migrated
  state file in production.
- **Per-phase spawn cap.** `task-done` enforces a default cap of 10
  spawned tasks per phase (configurable via `.orchestrator.json`).
  An unbounded spawn loop is the easiest way for a misbehaving
  worker to burn LLM budget.

## What NOT to do

- **No SwiftUI / iOS code.** clu is pure Python. The `/review` skill
  is HealthData's mandatory gate for SwiftUI changes; it doesn't
  apply here. If you find yourself wanting to import an iOS-specific
  helper into clu, you're in the wrong repo.
- **No `git add -A`.** Stage explicit paths. The repo has a habit of
  picking up `.orchestrator/` state files, log directories, or
  worktree artifacts during development; `-A` swallows them all and
  forces a follow-up `git reset` that future-you will forget about.
  `git add end_of_line/cli.py tests/test_x.py` is verbose for a
  reason.
- **No third-party deps.** stdlib has everything clu needs. The whole
  package — supervisor, state, dispatch, iMessage poller, fleet view
  — runs on Python 3.11+ with zero `pip` dependencies, and the test
  suite is `unittest`. Adding a dep widens the install surface,
  invites supply-chain headaches, and almost always replaces ten
  lines of stdlib with a hundred lines of dependency. Bring a real
  justification (and a real benchmark) if you propose one.
- **One tick = one action.** `supervisor.tick` walks an eight-priority
  chain and the first match wins; the function returns after the
  single action and never does two things in one tick. If a tick
  *needs* to do two things (release a stale claim *and* dispatch the
  next phase), that's two ticks — the next 5-minute cron firing will
  pick up where this one left off. The invariant is what keeps the
  decision logic provably terminating and the event log linear.

## For AI agents

In practice, almost every contributor to clu is either an operator
running an interactive Claude session against the repo, or a worker
spawned by `/clu-phase` against one sub-plan. These conventions apply
equally in both modes. The worker contract layered on top —
`--token` on every callback, `clu complete --commit <sha>` or `clu
block --question ... --option ...` before exit, no silent process
death — is documented in [`contract.md`](contract.md) and the
`/clu-phase` skill itself. The rules in *this* doc don't change
based on who's at the keyboard. A worker that skips `/simplify`,
writes a raw event-type string, or `git add -A`s its branch is just
as wrong as an operator who does the same. Follow them either way.
