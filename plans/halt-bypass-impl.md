# halt-bypass-impl — worker sub-plan

You are running as a phase worker via the `/clu-phase` skill on plan
`halt-bypass`, phase `impl`. The prior phase (`design-block`) captured
a user decision about whether halt notifications should bypass quiet
hours. Your job is to implement whichever path they chose.

## Step 1: Find the decision

Read the state file at the path your skill received. Find the answered
blocker from phase `design-block`. The `answer` field holds the option
the user picked, as the resolved text string (e.g. `"Bypass quiet hours
(loud at 3am)"`).

```bash
python3 -c "
import json
data = json.load(open('<state_file>'))
for b in data['blockers']:
    if b['phase_id'] == 'design-block' and b.get('answer') is not None:
        print(b['answer'])
"
```

If you find no answered blocker on `design-block`, something is wrong
(the prior phase shouldn't have completed without one). `clu block`
with a question asking the user how to proceed, and stop.

## Step 2a: If they chose "Bypass quiet hours …"

Edit `end_of_line/notify.py`. Locate the line:

```python
QUIET_HOURS_BYPASS_KINDS: frozenset[str] = frozenset()
```

Replace with:

```python
QUIET_HOURS_BYPASS_KINDS: frozenset[str] = frozenset({KIND_HALTED})
```

Then add a unit test in `tests/test_notify.py` that asserts
`notify.notify(spec_with_quiet_hours, KIND_HALTED, body, now=quiet-time)`
returns `True` (delivers despite quiet hours), while the same call with
`KIND_BLOCKER` still returns `False`.

Run the full test suite — `python3 -m unittest discover -s tests`.
All tests must pass before committing.

Commit (follow the project's structured commit format in CLAUDE.md).
Capture the SHA with `git rev-parse HEAD`.

## Step 2b: If they chose "Stay gated …"

No code change needed. The current behavior already matches this
choice. Do NOT make a commit. Skip to step 3 and call `clu complete`
with no `--commit` flags.

## Step 3: Complete

```
clu complete --project <project_root> --plan halt-bypass \
    --phase impl --token <token> [--commit <sha>]
```

Pass `--commit <sha>` only if step 2a's commit landed. If step 2b
(no-code-change), call complete with no commit flag.

## Constraints

- Don't touch anything outside `end_of_line/notify.py` and `tests/test_notify.py`.
- Don't refactor surrounding code.
- Don't change behavior for any kind other than `KIND_HALTED`.
- If the test suite was red before your changes, that's a separate
  problem — `clu block` to surface it rather than committing on red.
