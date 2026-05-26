"""Phase A of skill-drift-detection: argparse-derived verb lint.

Walks every bundled `end_of_line/skills/*/SKILL.md` and asserts that
every `clu <verb>` mention inside a fenced code block names a verb
the live `clu --help` reports.

Two failure classes:

- **Unknown verb**: SKILL.md mentions `clu nonexistent-verb` — almost
  always a typo or a verb that got renamed/removed and the SKILL.md
  wasn't updated.
- **Deprecated verb**: SKILL.md mentions a verb whose argparse help
  text starts with `DEPRECATED` — the verb still works but the skill
  should be steering callers to the replacement (e.g. `clu integrate`
  → `clu ship` / `clu validate`).

Prose mentions outside fenced blocks are intentionally exempt: a
sentence like "deprecated alias `clu integrate`" needs to name the
old verb to explain the migration.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "end_of_line" / "skills"


def _live_verbs() -> tuple[set[str], set[str]]:
    """Return (valid_verbs, deprecated_verbs) parsed from `clu --help`."""
    result = subprocess.run(
        [sys.executable, "-m", "end_of_line.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    text = result.stdout
    usage_match = re.search(r"\{([a-z0-9,_-]+)\}", text)
    if not usage_match:
        raise AssertionError(
            "Could not parse verb list from `clu --help` output. "
            "Has the argparse output format changed?"
        )
    verbs = set(usage_match.group(1).split(","))

    deprecated: set[str] = set()
    for verb in verbs:
        pattern = rf"^    {re.escape(verb)}\s+(.+?)(?=^    \S|\Z)"
        m = re.search(pattern, text, re.DOTALL | re.MULTILINE)
        if m and m.group(1).strip().upper().startswith("DEPRECATED"):
            deprecated.add(verb)

    return verbs, deprecated


_BASH_FENCE_LANGS = {"bash", "sh", "shell", "console"}


def _clu_verbs_in_fences(skill_md: Path) -> list[tuple[int, str]]:
    """Find every `clu <verb>` mention inside bash code fences.

    Returns a list of (line_number, verb) tuples. Only fences tagged
    `bash`/`sh`/`shell`/`console` are linted — untagged fences and
    `python`/`markdown`/`jsonc`/etc fences hold illustrative content
    (Monitor calls, example markdown, JSON) where `clu` may appear
    inside string literals and matching would false-positive.

    Skips:
    - prose mentions outside fenced blocks (may discuss old names)
    - `clu --foo` (the next token is a flag, not a verb)
    """
    out: list[tuple[int, str]] = []
    in_bash_fence = False
    for lineno, line in enumerate(skill_md.read_text().splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if in_bash_fence:
                in_bash_fence = False
            else:
                info = stripped[3:].strip().lower()
                in_bash_fence = info in _BASH_FENCE_LANGS
            continue
        if not in_bash_fence:
            continue
        for m in re.finditer(r"\bclu\s+([a-z][a-z0-9_-]*)\b", line):
            out.append((lineno, m.group(1)))
    return out


class TestSkillLint(unittest.TestCase):
    def test_skill_fences_only_reference_known_verbs(self):
        valid, _deprecated = _live_verbs()
        violations: list[tuple[str, int, str]] = []
        for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            for lineno, verb in _clu_verbs_in_fences(skill_md):
                if verb not in valid:
                    rel = skill_md.relative_to(REPO_ROOT)
                    violations.append((str(rel), lineno, verb))
        if violations:
            lines = ["Unknown `clu <verb>` mentions in SKILL.md fences:"]
            for path, lineno, verb in violations:
                lines.append(f"  {path}:{lineno}  clu {verb}")
            lines.append("")
            lines.append(f"Known verbs ({len(valid)}): {sorted(valid)}")
            self.fail("\n".join(lines))

    def test_skill_fences_avoid_deprecated_verbs(self):
        valid, deprecated = _live_verbs()
        if not deprecated:
            self.skipTest(
                "No deprecated verbs in `clu --help`; nothing to lint against."
            )
        violations: list[tuple[str, int, str]] = []
        for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            for lineno, verb in _clu_verbs_in_fences(skill_md):
                if verb in deprecated:
                    rel = skill_md.relative_to(REPO_ROOT)
                    violations.append((str(rel), lineno, verb))
        if violations:
            lines = ["Deprecated `clu <verb>` mentions in SKILL.md fences:"]
            for path, lineno, verb in violations:
                lines.append(f"  {path}:{lineno}  clu {verb}  (deprecated)")
            lines.append("")
            lines.append(f"Deprecated verbs: {sorted(deprecated)}")
            lines.append(
                "Prose may reference old names for migration context; "
                "fences should use the current verb."
            )
            self.fail("\n".join(lines))

    def test_live_verbs_returns_nonempty_known_subset(self):
        """Smoke test: the parser itself works on the current argparse output."""
        valid, deprecated = _live_verbs()
        self.assertGreater(len(valid), 10, "Expected >10 known verbs")
        self.assertIn("init", valid)
        self.assertIn("ship", valid)
        self.assertIn("complete", valid)
        self.assertTrue(
            deprecated.issubset(valid),
            "Deprecated verbs must be a subset of known verbs",
        )


class TestVerbExtractor(unittest.TestCase):
    """Verify the fence-parser catches the two drift classes we built it for."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "FAKE_SKILL.md"
        path.write_text(body)
        return path

    def test_bash_fence_clu_verb_is_extracted(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp),
                "intro prose\n\n```bash\nclu ship --plan x --yes\n```\n",
            )
            self.assertEqual(_clu_verbs_in_fences(path), [(4, "ship")])

    def test_untagged_fence_is_skipped(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp),
                'Monitor block:\n\n```\n  description="clu operator dashboard",\n```\n',
            )
            self.assertEqual(_clu_verbs_in_fences(path), [])

    def test_python_fence_is_skipped(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp),
                '```python\ncmd = "clu init --plan x"\n```\n',
            )
            self.assertEqual(_clu_verbs_in_fences(path), [])

    def test_prose_mention_outside_fence_is_skipped(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp),
                "Use `clu integrate` was the old way — migrate to clu ship.\n",
            )
            self.assertEqual(_clu_verbs_in_fences(path), [])

    def test_multiple_verbs_on_one_line(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                Path(tmp),
                "```bash\nclu init && clu queue add\n```\n",
            )
            self.assertEqual(_clu_verbs_in_fences(path), [(2, "init"), (2, "queue")])


if __name__ == "__main__":
    unittest.main()
