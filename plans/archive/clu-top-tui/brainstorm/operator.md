# clu top redesign — Operator POV

I run this all day. Two physical setups, two different jobs. The
redesign only earns its keep if it makes BOTH better without breaking
the thing today's flat table already nails: instant trust that workers
are alive and moving.

## 1. Wide-short strip (A) — what I need per worker at a glance

Docked under coolant on the big monitor, I'm not interacting, I'm
*scanning*. Per worker I need, in order:

1. **Identity** — project/plan·phase. One glance, I know which row is which.
2. **Liveness verdict** — a single fused health glyph, not four numbers
   I have to mentally AND together. Today I read PID + HB + ACT
   separately; in a strip I want one column that's green/amber/red
   computed from (alive? heartbeat fresh? transcript moving? command
   stuck?). The raw clocks belong in drill-down, not the strip.
3. **ACT** — last transcript activity age. This is the real "is it
   doing things" clock (top.py:237 comment agrees). If this climbs,
   something's wrong even if PID says ok.
4. **COMMAND** with the `*` running marker — what it's doing *right now*.
5. **SAYING** — truncated is fine here; it's the vibe check.

The "something's wrong" signal I must NOT miss: **a worker that's
PID-ok but ACT-stale** — alive process, zero transcript progress. That's
the wedged-worker case the whole watchdog family (MEMORY: wedge-watchdogs,
worker-watchdog) exists to catch. PID `dead` is obvious; the silent
wedge is the dangerous one. The strip must make stale-ACT scream.

## 2. Phone drill-down (B) — ranked verbose metrics

In bed, tiny screen, I've picked ONE worker. What I want, ranked:

1. **Full SAYING** — untruncated. This is the #1 reason I open it on the
   phone. The row clips it; I want the whole last assistant line.
2. **Recent transcript tail** — last 3-5 assistant/tool turns, not just
   the latest. Tells a story: "ran tests → failed → editing → re-running."
3. **Time-on-phase + lease remaining** — RAN plus `lease_expires`
   countdown (claim already carries it, state.py:713). "Is this about to
   lease-expire on me?"
4. **Attempts** — `claim.attempts` (state.py:716). Attempt 3 of 3 means
   it's about to hit max and halt. Critical, currently invisible in top.
5. **Files touched this phase** — not just the last write; the list.
6. **Token spend / $ for this phase** — usage is already extracted
   (top.py:218, `tokens`), just not surfaced.
7. **git diff stat** — lines/files changed. Nice, lower priority; it's a
   shell-out and I mostly trust the transcript.

## 3. New modular-pane metrics, ranked by value

1. **Fleet-summary header** — `N running · N blocked · N dead · oldest-ACT`.
   The single most valuable new thing. On the strip it's the one line I
   actually read first; on the phone it's the "is everything fine?" answer
   before I drill.
2. **Token cost per worker + $/phase** — data's already there. Turns "is
   this worker burning money in a loop?" from invisible to obvious.
3. **Phase progress (X of N)** — from the plan's sessions index. "impl is
   phase 4 of 6" gives the row meaning a bare phase-id can't.
4. **Files-written list** (not just last) — cheap, high signal for "what
   did it actually touch."
5. **Time-in-current-tool** — how long the `*`-running command has been
   running. A `pytest` stuck 8m is a different alarm than one at 3s.
6. **$/hour burn / tokens-min** — fun, but derived noise for me. Low.
7. **Commits made** — I mostly learn this at ship time. Low.

## 4. Same UI for both, or two modes?

Two modes, **default per machine**. The strip and the phone are genuinely
different jobs — density+status vs depth-on-one. The existing compact vs
detail toggle (the `w` key) is already the seed. I want:
- **strip mode** — fused health glyph, fleet header, max rows, no detail
  pane. Default on the desk machine.
- **full mode** — list + a detail pane for the arrow-selected worker.
  Default over SSH.
Persist the default in `clu_config_dir()/top.json` (the column-sizing plan
already establishes that file, clu-top-column-sizing.md:36). I should
never set a flag to get the right mode on a given box.

## 5. Selection on a phone — arrow keys are pain

iOS SSH arrow keys are genuinely miserable. Least-painful, in order:
1. **Number keys jump** — `1`-`9` selects that worker, Enter/`d` expands.
   I usually have ≤5 workers; one keypress to the one I want beats arrowing.
2. **Single key to cycle** — `Tab` (or `j`) advances selection, wraps.
   No arrows at all.
3. Arrow keys as a fallback only. Never the primary path.
Whatever's chosen must work with the existing `getch` loop — no escape
sequences I have to thumb-type.

## 6. The killer feature — what makes me alt-tab instead of `tail -f`

The **fused health verdict + fleet header**: one glance answers "are all
my workers alive and moving, and is any one wedged/burning/near-expiry?"
Logs can't do that across N concurrent workers in N worktrees. The
independent-transcript check (top.py docstring) is already clu top's
superpower — workers can't lie about ACT/COMMAND/WROTE because it's read
from the harness's own transcript, not the LLM's self-report. The
redesign should lean ALL the way into that: surface the wedge before I'd
ever notice it in a log.

## 7. Noise I do NOT want in the strip

- Four separate liveness clocks (RAN/ACT/HB/PID) as columns — collapse to
  one glyph + ACT.
- Token/$ numbers in the strip — drill-down only; they jitter and pull my
  eye for no glance value.
- git diff stat in the strip — too heavy, too noisy.
- Full untruncated SAYING in the strip — that's literally what full mode
  is for.
- Per-tool timing in the strip. Detail only.

## 8. Alerts — yes, flag visually (read-only is fine)

The strip is read-only but a visual alarm is exactly right:
- **Color**: red for dead/wedged, amber for amber-health, green normal.
- **`!` prefix** on a wedged/blocked/dead row so it reads even without
  color (some SSH clients mangle color).
- **Move-to-top / sort-by-severity** so a sick worker floats up where I'll
  see it without scanning. This is the one I'd want most — I shouldn't have
  to find the bad row.

## 9. Defaults out of the box (no config)

- **Wide-short geometry** → strip mode: fleet header + fused-health +
  identity + ACT + COMMAND + truncated SAYING. Severity-sorted.
- **Tall-narrow geometry** → full mode: compact list (identity + health +
  ACT) on top, detail pane for the selected worker below.
- Auto-detect from terminal dimensions on launch; the per-machine default
  in `top.json` overrides. Value with zero flags is the bar.

## 10. `--cols` configurability — honest effort

I will configure columns **once, ever**, then never touch it. I want
**great defaults + 2-3 named presets** (`strip`, `full`, `wide`) on a
cycle key far more than a `--cols name:40` DSL. The column-sizing plan's
preset-cycle key (clu-top-column-sizing.md:35) is the part I'd use daily;
the declarative spec is the part I'd set on day one and forget. Don't
over-invest in the DSL; invest in the presets being right.

## 11. Dealbreakers — what kills the redesign vs today's table

1. **Losing at-a-glance density.** If panes/borders/chrome eat vertical
   space and I see fewer workers than the flat table does, I revert. The
   table's whole value is N-workers-one-screen.
2. **Slower / flickery refresh.** Today it's pure-stdlib and snappy. If
   modular panes add latency or curses flicker on a 1.5s loop, dead on
   arrival.
3. **Mandatory interaction to see status.** If I must arrow-select to
   learn a worker is wedged, it failed — the strip must answer "all fine?"
   passively, no keypress.
4. **Config required for value.** If the default geometry is wrong and I
   have to write a `--cols` spec to get a usable view, I won't bother.
5. **Breaking the strip-under-coolant dock** — if it can't render sane in
   a wide-short geometry, I lose use case (A) entirely.
