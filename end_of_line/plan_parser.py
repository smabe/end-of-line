"""Parse the /plan skill's master-plan markdown.

Contract: a multi-session plan has a `## Sessions index` table whose rows
declare each phase. We extract phase id + plan file + scope + effort. For
single-phase plans (no Sessions index), return [] — the caller decides
whether to synthesize one phase from the master itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import state as st

_EFFORT_SINGLE_RE = re.compile(r"^(\d+(?:\.\d+)?)(h|min)$", re.IGNORECASE)
_EFFORT_RANGE_RE = re.compile(r"^\d+(?:\.\d+)?-(\d+(?:\.\d+)?)(h|min)$", re.IGNORECASE)

_SESSIONS_HEADER_RE = re.compile(r"^##\s+Sessions?\s+index\s*$", re.MULTILINE | re.IGNORECASE)
_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|\s*$")
# `[text](target)` — natural Sessions-index navigation syntax. Author intent
# is the bracketed text (the displayed filename); target may diverge but the
# parser keys off the file-name derivation, so prefer text. #44.
_MD_LINK_RE = re.compile(r"^\[(.+?)\]\([^)]*\)$")


@dataclass
class Phase:
    id: str
    plan_file: str
    scope: str
    effort: str


def parse_sessions_index(plan_path: Path) -> list[Phase]:
    text = plan_path.read_text()
    match = _SESSIONS_HEADER_RE.search(text)
    if not match:
        return []

    master_stem = plan_path.stem
    lines = text[match.end() :].splitlines()
    phases: list[Phase] = []
    in_table = False
    seen_separator = False

    for line in lines:
        stripped = line.strip()
        if not in_table:
            if stripped.startswith("##"):
                break
            if stripped.startswith("|"):
                in_table = True
            else:
                continue
        if in_table:
            if not stripped:
                break
            if stripped.startswith("##"):
                break
            if _SEPARATOR_RE.match(stripped):
                seen_separator = True
                continue
            if not seen_separator:
                continue
            cells = _split_row(stripped)
            if len(cells) < 4:
                continue
            raw = cells[1].strip()
            link_match = _MD_LINK_RE.match(raw)
            if link_match:
                raw = link_match.group(1).strip()
            plan_file = raw.strip("`")
            scope = cells[2].strip()
            effort = cells[3].strip()
            basename = Path(plan_file).stem
            if basename.startswith(master_stem + "-"):
                phase_id = basename[len(master_stem) + 1 :]
            else:
                phase_id = basename
            st.validate_slug(phase_id, kind="phase_id")
            phases.append(
                Phase(
                    id=phase_id,
                    plan_file=plan_file,
                    scope=scope,
                    effort=effort,
                )
            )
    return phases


def parse_effort_minutes(raw: str | None) -> int | None:
    if not raw:
        return None
    s = raw.strip().replace(" ", "")
    m = _EFFORT_RANGE_RE.match(s) or _EFFORT_SINGLE_RE.match(s)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    minutes = value * 60 if unit == "h" else value
    return round(minutes)


def _split_row(row: str) -> list[str]:
    inner = row.strip().strip("|")
    return [c.strip() for c in inner.split("|")]
