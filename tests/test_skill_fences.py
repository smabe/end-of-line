"""Phase B of skill-drift-detection: executable bash fences.

Walks every bundled `end_of_line/skills/*/SKILL.md`, finds bash fences
preceded by a `<!-- skilltest -->` HTML-comment marker, and runs each
in a sandboxed tmpdir under `bash -eo pipefail`. Exit 0 = pass; non-zero
= the fence references a flag/verb/output the live clu CLI no longer
matches and the SKILL.md needs updating.

The marker is opt-in by design: most bash fences in SKILL.md contain
placeholders (`<slug>`, `<phase-id>`) and can't be executed verbatim.
Tagging is reserved for fences that are runnable as-is.

Sandbox: `HOME` and `XDG_CONFIG_HOME` are redirected to the tmpdir so
`clu install-hook` / `clu install-skill` style commands write inside
the sandbox, not the operator's real config.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "end_of_line" / "skills"

_MARKER = "<!-- skilltest -->"
_FENCE_RE = re.compile(r"^```([a-zA-Z]*)\s*$")
_FENCE_TIMEOUT_SECONDS = 30


def _tagged_fences(skill_md: Path) -> list[tuple[int, str]]:
    """Return (fence_open_line, body) for every `<!-- skilltest -->` bash fence.

    The marker must appear on its own line, immediately followed (after
    any blank lines) by an opening ```bash fence. The body is everything
    between the open and close fences, joined with newlines.
    """
    lines = skill_md.read_text().splitlines()
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != _MARKER:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            break
        m = _FENCE_RE.match(lines[j])
        if not m or m.group(1).lower() != "bash":
            i = j
            continue
        body_start = j + 1
        k = body_start
        while k < len(lines) and not lines[k].startswith("```"):
            k += 1
        body = "\n".join(lines[body_start:k])
        out.append((j + 1, body))
        i = k + 1
    return out


def _all_tagged_fences() -> list[tuple[Path, int, str]]:
    out: list[tuple[Path, int, str]] = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        for lineno, body in _tagged_fences(skill_md):
            out.append((skill_md, lineno, body))
    return out


class TestSkillFences(unittest.TestCase):
    def setUp(self):
        if not shutil.which("clu"):
            self.skipTest(
                "`clu` not on PATH — install with `pipx install -e .` to "
                "run the fence harness against the live binary."
            )

    def test_at_least_one_fence_is_tagged(self):
        """Prevent silent disablement — if every tag is removed, this fails."""
        tagged = _all_tagged_fences()
        self.assertGreaterEqual(
            len(tagged),
            1,
            "No `<!-- skilltest -->` fences found in any bundled SKILL.md. "
            "Phase B has no coverage. Tag at least one runnable bash fence "
            "to seed the convention.",
        )

    def test_all_tagged_fences_run_green(self):
        failures: list[tuple[str, int, int, str]] = []
        for skill_md, lineno, body in _all_tagged_fences():
            with tempfile.TemporaryDirectory() as tmp:
                env = os.environ.copy()
                env["HOME"] = tmp
                env["XDG_CONFIG_HOME"] = os.path.join(tmp, ".config")
                env.pop("CLAUDE_PROJECT_DIR", None)
                result = subprocess.run(
                    ["bash", "-eo", "pipefail", "-c", body],
                    cwd=tmp,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=_FENCE_TIMEOUT_SECONDS,
                )
                if result.returncode != 0:
                    rel = str(skill_md.relative_to(REPO_ROOT))
                    failures.append((rel, lineno, result.returncode, result.stderr))
        if failures:
            lines = ["Tagged bash fences failed under sandbox:"]
            for path, lineno, code, stderr in failures:
                lines.append(f"  {path}:{lineno}  exit={code}")
                if stderr:
                    snippet = stderr.strip().splitlines()[-1][:240]
                    lines.append(f"    stderr (last line): {snippet}")
            self.fail("\n".join(lines))


class TestTaggedFenceExtractor(unittest.TestCase):
    """Self-tests for the fence parser — synthetic SKILL.md fixtures."""

    def _write(self, body: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
        )
        tmp.write(body)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def test_marker_then_bash_fence_extracted(self):
        path = self._write(
            "prose\n\n<!-- skilltest -->\n```bash\nclu doctor\n```\n"
        )
        fences = _tagged_fences(path)
        self.assertEqual(fences, [(4, "clu doctor")])

    def test_marker_with_blank_lines_before_fence_works(self):
        path = self._write(
            "<!-- skilltest -->\n\n\n```bash\nclu doctor\n```\n"
        )
        fences = _tagged_fences(path)
        self.assertEqual(fences, [(4, "clu doctor")])

    def test_marker_followed_by_non_bash_fence_is_skipped(self):
        path = self._write(
            "<!-- skilltest -->\n```python\nprint(1)\n```\n"
        )
        self.assertEqual(_tagged_fences(path), [])

    def test_untagged_bash_fence_is_skipped(self):
        path = self._write("```bash\nclu doctor\n```\n")
        self.assertEqual(_tagged_fences(path), [])

    def test_multiple_tagged_fences_in_one_file(self):
        path = self._write(
            "<!-- skilltest -->\n```bash\nclu doctor\n```\n"
            "intermission\n"
            "<!-- skilltest -->\n```bash\nclu list\n```\n"
        )
        self.assertEqual(
            _tagged_fences(path),
            [(2, "clu doctor"), (7, "clu list")],
        )

    def test_multiline_fence_body_preserved(self):
        path = self._write(
            "<!-- skilltest -->\n```bash\nset -x\nclu doctor\nclu list\n```\n"
        )
        fences = _tagged_fences(path)
        self.assertEqual(fences, [(2, "set -x\nclu doctor\nclu list")])


if __name__ == "__main__":
    unittest.main()
