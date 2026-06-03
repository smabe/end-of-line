"""`clu demo-worker` — the synthetic, deterministic core of the demo fleet.

This module fabricates real-format Claude Code session transcripts so that
`clu top` / `clu serve` render live demo workers without a real LLM. The whole
point of the demo is *verifying the install*, so determinism + zero token cost +
reliability matter more than realism — but the records must still satisfy the
locator/parser contract in `top.py` exactly, or the dashboard shows empty rows.

Phase 1 is the pure core: build a step's worth of records, append them as JSONL,
and derive the transcript path. The locator (`top.locate_transcript`) accepts a
file only if an early record carries the worker's real `cwd` and is not a
sidechain — so every record here carries both. `build_records` exercises every
`top.extract_activity` branch (a Bash command, a file write, an assistant line,
token usage) so a `busy` worker lights up all four dashboard columns.

`run_worker` (the paced loop + per-scenario lifecycle) lands in phase 2.
"""

from __future__ import annotations

import json
from collections import namedtuple
from pathlib import Path

from end_of_line.top import PROJECTS_ROOT, encode_project_dir

# The four demo personalities. Order is the dashboard order the operator sees.
SCENARIOS = ("busy", "idle", "block", "dead")

# Per-scenario flavor: a distinct command + assistant line so the demo rows look
# like genuinely different workers, plus whether the step's Bash command is left
# running. `busy`/`block`/`dead` are caught mid-command (a live `*`); `idle`
# resolves its command so its row goes quiet and only ACT climbs.
_Flavor = namedtuple("_Flavor", "command saying running")
_SCENARIO_FLAVOR = {
    "busy": _Flavor("python3 -m unittest discover -s tests", "running the suite", True),
    "idle": _Flavor("tail -f build.log", "waiting on a slow build", False),
    "block": _Flavor("grep -rn TODO src/", "need a decision on the schema", True),
    "dead": _Flavor("python3 migrate.py --apply", "applying the migration", True),
}


def transcript_path(
    cwd: Path | str, session_id: str, projects_root: Path | str = PROJECTS_ROOT
) -> Path:
    """Where this worker's synthetic transcript lives.

    Reconstructs the exact path the locator globs: the lossy-encoded cwd dir
    under `projects_root`, then `<session_id>.jsonl`.
    """
    return Path(projects_root) / encode_project_dir(cwd) / f"{session_id}.jsonl"


def build_records(
    scenario: str, step: int, *, cwd: Path | str, session_id: str, now: str
) -> list[dict]:
    """The JSONL records for one step of synthetic work.

    `now` is an ISO-8601 UTC stamp (`...Z`) — it becomes each record's
    `timestamp`, which the dashboard ages into the ACT column. Every record
    carries the real `cwd` and `isSidechain: False` so `top.locate_transcript`
    confirms the file. Unknown scenarios fall back to `busy`.
    """
    flavor = _SCENARIO_FLAVOR.get(scenario, _SCENARIO_FLAVOR["busy"])
    cwd = str(cwd)
    bash_id = f"{scenario}-b{step}"
    assistant = {
        "type": "assistant",
        "timestamp": now,
        "cwd": cwd,
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"{flavor.saying} (step {step})"},
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": f"{cwd}/demo_step_{step}.py"},
                    "id": f"{scenario}-w{step}",
                },
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": flavor.command},
                    "id": bash_id,
                },
            ],
            "usage": {"input_tokens": 100 + step, "output_tokens": 40 + step},
        },
    }
    records = [assistant]
    if not flavor.running:
        # Resolve the Bash command so `command_running` reads False.
        records.append(
            {
                "type": "user",
                "timestamp": now,
                "cwd": cwd,
                "isSidechain": False,
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": bash_id, "content": "ok"}
                    ],
                },
            }
        )
    return records


def append_records(path: Path | str, records: list[dict]) -> None:
    """Append `records` as JSONL, creating parent dirs. Empty list is a no-op."""
    path = Path(path)
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
