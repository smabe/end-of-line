# lease-reliability-effort-parser — permissive Effort-string parser (#58 part 1/3)

You are phase `effort-parser` of the `lease-reliability` plan. Ship a
pure parser `parse_effort_minutes(raw: str) -> int | None` covering
every Effort shape the operator's plans use. No callers wired yet —
`ttl-storage` does that.

## Locked decisions (do NOT re-litigate)

See `plans/lease-reliability.md`. Summary:

- New pure function `parse_effort_minutes(raw: str) -> int | None` in `end_of_line/plan_parser.py`.
- Shapes accepted: `3h`, `1.5h`, `90min`, `30min`, `2-3h`, `1.5-2h` (range → upper bound).
- Case-insensitive on unit; whitespace tolerant.
- Malformed → return `None`. Do not raise.
- `Phase` dataclass unchanged; `parse_sessions_index` callers unchanged.

## Read first

- `end_of_line/plan_parser.py` (entire file, 92 lines) — the `Phase` dataclass and `parse_sessions_index`. The `effort` field is already parsed at line 74; your function converts that string to minutes.
- `tests/test_plan_parser.py` if it exists, or `tests/` for an analogous test layout. Mirror existing patterns.

## Produce

1. **Failing tests first.** Add tests to `tests/test_plan_parser.py` (create if missing) for `parse_effort_minutes`:
   - `test_parse_hours_integer`: `"3h" -> 180`.
   - `test_parse_hours_decimal`: `"1.5h" -> 90`.
   - `test_parse_minutes`: `"30min" -> 30`, `"90min" -> 90`.
   - `test_parse_hours_range_takes_upper`: `"2-3h" -> 180`, `"1.5-2h" -> 120`.
   - `test_parse_case_insensitive`: `"1H" -> 60`, `"45MIN" -> 45`.
   - `test_parse_whitespace_tolerant`: `" 2h " -> 120`, `"30 min" -> 30`.
   - `test_parse_malformed_returns_none`: `"" -> None`, `"abc" -> None`, `"3" -> None` (unit required), `"3 hours" -> None` (full-word unit not accepted; we only support `h`/`min`), `"-1h" -> None`.
   - `test_parse_none_input_returns_none`: handle `None` input gracefully (or document at signature level that caller must pass str — pick one and reflect in the type hint).

2. **Implementation in `end_of_line/plan_parser.py`:**
   - Add module-level regex constants:
     ```python
     _EFFORT_SINGLE_RE = re.compile(r"^(\d+(?:\.\d+)?)(h|min)$", re.IGNORECASE)
     _EFFORT_RANGE_RE = re.compile(r"^\d+(?:\.\d+)?-(\d+(?:\.\d+)?)(h|min)$", re.IGNORECASE)
     ```
     Note: range regex captures only the upper bound + unit; lower bound is non-capturing.
   - Function:
     ```python
     def parse_effort_minutes(raw: str | None) -> int | None:
         if not raw:
             return None
         s = raw.strip().replace(" ", "")
         m = _EFFORT_RANGE_RE.match(s) or _EFFORT_SINGLE_RE.match(s)
         if not m:
             return None
         value = float(m.group(1))
         unit = m.group(2).lower()
         if value < 0:
             return None
         minutes = value * 60 if unit == "h" else value
         return round(minutes)
     ```
   - Place the function below `_split_row` (or wherever fits the module's flow); export via the module's public surface (no `__all__` change needed — clu uses direct imports).

3. **Acceptance.**
   - All new `parse_effort_minutes` tests pass.
   - Full suite green: `python3 -m unittest discover -s tests`.
   - `python3 -c "from end_of_line.plan_parser import parse_effort_minutes; print(parse_effort_minutes('1.5h'))"` outputs `90`.
   - No callers wired (next phase). `grep -rn parse_effort_minutes end_of_line/` should show only the definition.

4. **Commit + complete.**
   - Structured commit: `lease-reliability: phase effort-parser — permissive Effort-string parser (#58)`.
   - Stage explicit paths: `end_of_line/plan_parser.py`, `tests/test_plan_parser.py`.
   - `clu verify` + `clu attest --simplify` per the gate.
   - `clu complete --plan lease-reliability --phase effort-parser --token <T>`.

## Failure modes to watch

- **Whitespace inside numbers.** `"1 .5h"` would be normalized by `replace(" ", "")` to `"1.5h"` — acceptable. `"1 .5 h"` becomes `"1.5h"` — also acceptable. If you want to be strict, drop the `replace(" ", "")` and rely on the regex strictness; either approach is fine but match the test expectations.
- **Decimal precision.** `round()` on `1.5 * 60 = 90.0` is exact; on `0.7h` you get `42` (close enough). Don't overthink — minutes-precision is the contract.
- **Range parsing ambiguity.** `"2-3h"` interprets the `2` as hours by elision; the regex enforces that. Don't try to support `"30-45min"` separately — the operator's plans use h-ranges in practice.
- **Future Effort shapes.** Don't add shapes speculatively (no `1d`, no `1w`). YAGNI; the operator can extend the parser if a new shape shows up.
