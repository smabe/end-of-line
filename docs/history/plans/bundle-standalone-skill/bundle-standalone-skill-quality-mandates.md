# bundle-standalone-skill-quality-mandates — bake quality into the skill

You are phase `quality-mandates` of the `bundle-standalone-skill`
plan. Edit `end_of_line/skill/SKILL.md` to add a "Quality mandates"
section so every worker, on every project that uses clu, upholds the
same minimum bar. These mandates are project-agnostic — project-
specific rules continue to live in each project's CLAUDE.md.

## Read first

- `end_of_line/skill/SKILL.md` — the canonical skill, just landed by
  phase 1. This is what you edit.
- The skill already covers: the four arguments protocol, the sacred
  `complete`/`block` contract, the resume-after-answer pattern,
  step-by-step protocol, common pitfalls, "block don't bail". Don't
  duplicate those — add the quality mandates as a NEW top-level
  section.
- The Day-1-through-3.5 commits on `main` are the lived example of
  these mandates in practice. Reference patterns from there if you
  need concrete prior art (e.g. `git log --oneline --all -30`).

## Produce

Add a new section to `end_of_line/skill/SKILL.md` titled **"Quality
mandates"**. Suggested placement: after "Step-by-step protocol" and
before "Common pitfalls" — mandates are work-discipline, pitfalls are
debug-discipline.

The section must cover **all eight** of these mandates. Each gets a
bolded one-liner heading + a 1-2 sentence explanation. **Do not**
expand into prose paragraphs — workers will skim. Tight is the goal.

1. **TDD when modifying logic.** Failing test first, then minimal
   implementation. Skip TDD only for pure refactor / config / docs /
   content edits. The project's CLAUDE.md tells you what test
   framework to use.

2. **Review after non-trivial diffs.** If the diff spans more than
   one file or ~30 lines, run the project's review command
   (`/simplify`, project-local equivalent, or a self-review pass).
   Look specifically for: rule-of-three extraction opportunities,
   dead code, copy-paste from sibling phases.

3. **Structured commit messages.** Title (one line) / Why (the
   motivation) / What's new (the surface) / Under the hood (the
   non-obvious choices) / Tests (count, what's covered) /
   `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
   trailer. Commit messages outlast the code; treat them as primary
   documentation.

4. **Stage explicit paths.** `git add <path1> <path2> ...`, never
   `git add -A` or `git add .`. The former forces you to think about
   what you're including; the latter is how secrets leak.

5. **External tools need absolute paths or `command -v` fallbacks.**
   Worker subprocess PATH is not the operator's shell PATH —
   LaunchAgent contexts and headless `claude --print` invocations
   inherit a minimal environment. If you shell out to `gh`, `pipx`,
   `pip`, or any user-installed tool, resolve the absolute path
   first or use `command -v <tool> || echo /known/fallback`.

6. **Read existing helpers before inventing new ones.** Grep first.
   If you'd write a function whose 80%-overlap twin already exists,
   use the existing one. Project-level rule-of-three may already
   have extracted what you need; check.

7. **Honor the project's CLAUDE.md.** That file is the project-
   specific layer of these mandates. Naming conventions, exit-code
   patterns, event constants, files to avoid — all there. Read it
   before your first commit on a project.

8. **The completion summary is load-bearing.** When you call `clu
   complete`, your final message to the operator is the only signal
   they have about what shipped. Mention:
   - What actually committed (SHA).
   - What tests pass (count + delta).
   - Anything you tried that didn't work and that the operator
     should know about (e.g. "couldn't run `gh issue close` because
     the binary wasn't on PATH; operator should close manually").
   Silence on a failure mode reads as "everything went fine," which
   is worse than admitting a small thing didn't.

## Constraints

- **One section, no scattered edits.** Don't sprinkle these mandates
  through the existing skill body — that's harder to maintain and
  harder for new workers to absorb. One named section, scannable.
- **Don't duplicate the sacred contract.** The skill already has a
  prominent "must call complete or block" rule at the top. Don't
  restate it in the mandates section.
- **Don't introduce project-specific examples.** This skill ships in
  the clu package; it loads on EVERY project that uses clu. Examples
  must be generic. "If you shell out to `gh`" not "since end-of-line
  uses gh for issue closes".
- **Don't add a 9th mandate.** Eight is enough; bloat dilutes signal.
  Surface candidates via `clu block` if you think one is genuinely
  missing.

## Done

```bash
clu complete --project /Users/smabe/projects/end-of-line \
    --plan bundle-standalone-skill --phase quality-mandates \
    --token <token> --commit <sha>
```

No GitHub issue to close.

## Escape hatch

`clu block` if:
- A mandate in the list contradicts something the skill already says.
  Surface the conflict; we'd need to either revise the existing
  language or drop the mandate.
- You discover a 9th genuinely-universal mandate while writing.
  Don't unilaterally add — propose with options.
