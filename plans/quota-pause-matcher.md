# quota-pause-matcher — quota signature table + reset-time parser

You are phase `matcher` of the `quota-pause` plan. You deliver a new pure-function module `end_of_line/quota.py` — quota-message classification and reset-time parsing — plus its test file, as one commit. No call sites yet; later phases wire it in.

## Locked decisions (do NOT re-litigate)

See `plans/quota-pause.md`. Summary:

- New module `end_of_line/quota.py`, stdlib-only (`re`, `datetime`, `zoneinfo` — first zoneinfo import in the codebase).
- `classify_quota(tail: str) -> QuotaMatch | None` where `QuotaMatch` is a small dataclass/NamedTuple carrying `signature: str` (e.g. `"session_limit"`, `"usage_credits"`) and `line: str` (the matched line, for events/notify).
- `parse_reset(line: str, now: datetime) -> datetime | None` — aware UTC result; `now` must be an aware datetime (tests pin it; no module-level "now" calls inside parsing logic).
- Signature table is hard-coded, grows via PR only, first match wins — mirror the systemic table's style and comments at `end_of_line/dispatch.py:77-88`.
- Bucketing by parseability: callers treat `parse_reset(...) is None` as the stuck bucket. This phase only provides the functions.
- Weekly/date reset forms (`resets Mon 12:00am`, `resets Oct 31, 9am`) deliberately return `None` from `parse_reset` — do NOT attempt to parse them.

## Read first

- `plans/quota-pause.md` `## Findings log` — empty if you're first (you are, unless re-dispatched).
- `end_of_line/dispatch.py:72-88` — the existing systemic signature table; mirror its shape, comment style, and "grows via PR only" discipline.
- `end_of_line/state.py:442-466` — `_now_utc`, `utcnow`, `parse_iso` helpers; your output datetimes must be aware-UTC to interoperate.
- `tests/__init__.py` — `CluTestCase` base; this phase's tests are pure (no registry/dispatch), so `isolate_registry` is not needed.

## Produce

1. **Failing tests first** — `tests/test_quota.py`, classes `ClassifyQuotaTests` and `ParseResetTests` (AAA style). Cover at minimum:
   - Verbatim observed lines (must classify, must parse):
     - `You've hit your session limit · resets 1:50am (America/New_York)`
     - `You're out of usage credits · resets 12:30pm (America/New_York)`
   - Wording variants: `weekly limit`, `Opus limit`, `Sonnet limit`; `You've used` / `You're out of extra usage` prefixes.
   - Separator variants: `·` (U+00B7), `∙` (U+2219), `|`, `-`.
   - Time variants: no minutes (`resets 12pm (America/Los_Angeles)`), uppercase `AM`, no timezone parens (assume local tz), 24h form `resets 22:30 (UTC)`.
   - Rollover: now = 23:00 ET, `resets 1:50am (America/New_York)` → tomorrow 05:50 UTC. Same-day: now = 09:00 ET, `resets 12:30pm` → today 16:30 UTC.
   - Unparseable forms → `parse_reset` returns `None` but `classify_quota` still matches: `resets Mon 12:00am`, `resets Oct 31, 9am`.
   - Negative cases: a benign traceback, the existing `rate limit` wording (stays systemic, must NOT match quota), empty string.
   - Multi-line tail: signature buried mid-tail among other output still matches.

2. **Implementation** — `end_of_line/quota.py`:
   - Module docstring stating the contract: classification feeds attempt forgiveness + project pause (#94); table grows via PR.
   - Signature regexes + ordered table of `(name, compiled_re)`; `classify_quota` scans the tail, returns first match with the matched line extracted.
   - `parse_reset`: extract the `resets <time> [(tz)]` fragment from the matched line; `strptime` with `%I:%M%p` / `%I%p` / `%H:%M` attempts in order; tz = `ZoneInfo(parens)` if present (catch `ZoneInfoNotFoundError` → return `None`) else system local; next-occurrence rollover (candidate ≤ now → +1 day); return `.astimezone(timezone.utc)`. Default fold handling — no DST fold logic (locked: ≤1h error twice a year is acceptable).
   - Constants for later phases live here too: `PAUSE_BUFFER_SEC = 120`, `CANARY_WINDOW_SEC = 180`, `QUOTA_FILE_NAME = "quota.json"` (P2/P3 import them; defining now avoids a P2 churn commit).

3. **Acceptance.**
   - All new tests green; full suite green (`python3 -m unittest discover -s tests`).
   - `python3 -c "from end_of_line import quota"` imports cleanly with no side effects.
   - Both verbatim observed lines classify AND parse to the correct UTC instants in tests.

4. **Commit + attest + complete.**
   - Record cross-phase findings (if any) in `plans/quota-pause.md` `## Findings log` — e.g. a message variant the regexes couldn't cover cleanly.
   - Structured commit: `quota-pause: phase matcher — quota signature table + reset parser (#94)`.
   - Stage explicit paths: `end_of_line/quota.py`, `tests/test_quota.py` (+ `plans/quota-pause.md` if you logged a finding).
   - After the commit: `clu verify --plan quota-pause --phase matcher --token <T>`, then `clu attest --simplify --plan quota-pause --phase matcher --token <T>`.
   - `clu complete --plan quota-pause --phase matcher --token <T>`.

## Failure modes to watch

- **Unicode separators in source vs. log encoding** — the log is read with `errors="replace"` upstream (`dispatch.py:171`), so a mangled separator byte can become U+FFFD. Make the separator class tolerant (`[·∙•|\-�]` or equivalent) or test a replaced-char line.
- **`%p` on non-C locales** — `strptime` AM/PM matching is locale-dependent in theory; CPython lowercases both sides so `am`/`AM` both work on this host (verified at plan time). Don't add locale calls; just keep the test coverage.
- **Overmatching** — the quota table must not swallow lines the systemic table owns (`rate limit`, `401 Unauthorized`). The negative tests are the guard.
