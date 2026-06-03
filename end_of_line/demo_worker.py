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
import sys
import time
from collections import namedtuple
from pathlib import Path

from end_of_line import state as st
from end_of_line.top import PROJECTS_ROOT, encode_project_dir

# The four demo personalities. Order is the dashboard order the operator sees.
SCENARIOS = ("busy", "idle", "block", "dead")

# Per-step actions the loop dispatches on.
ACT_WRITE = "write"  # append a step of records + heartbeat (alive, producing)
ACT_QUIET = "quiet"  # heartbeat only (alive, but transcript ACT climbs)
ACT_BLOCK = "block"  # call `clu block` (opens a blocker, releases claim), then stop
ACT_DEAD = "dead"  # exit with no callback — orphan the claim for dead-PID detection

# How many steps idle works before going quiet, and how many block/dead work
# before their lifecycle event — small so the dashboard shows real activity
# first, then the scenario's defining behavior.
_IDLE_WRITE_STEPS = 2
_PRE_LIFECYCLE_STEPS = 2

# Live-demo pacing. Heartbeat + write every 5s keeps `busy` looking active
# (fresh ACT) and stays well under the ~25-min stalled threshold. The step
# ceiling bounds a forgotten worker to ~1h; the normal exit is `clu demo`
# teardown killing the pgroup, not reaching the cap.
DEFAULT_STEP_SECONDS = 5.0
DEFAULT_MAX_STEPS = 720

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


def scenario_action(scenario: str, step: int) -> str:
    """Pure: what the worker does at `step` for `scenario` (one of ACT_*).

    Unknown scenarios behave like `busy` so a typo still produces a live row.
    """
    if scenario == "idle":
        return ACT_WRITE if step < _IDLE_WRITE_STEPS else ACT_QUIET
    if scenario == "block":
        return ACT_WRITE if step < _PRE_LIFECYCLE_STEPS else ACT_BLOCK
    if scenario == "dead":
        return ACT_WRITE if step < _PRE_LIFECYCLE_STEPS else ACT_DEAD
    return ACT_WRITE


def command_template(scenario: str, *, python: str | None = None) -> str:
    """The `.orchestrator.json` `dispatch.command` template for a demo plan.

    `{plan_slug}` is a bare, space-bounded positional so the supervisor's #83
    cmdline-marker reaper recognizes the live worker — a slug buried in a longer
    token reads as 'dead' and gets killed. `{session_id}` opts the dispatcher
    into generating + stamping a session id, giving the locator a deterministic
    transcript filename. dispatch.py substitutes the `{...}` fields at spawn.
    """
    python = python or sys.executable
    return (
        f"{python} -m end_of_line.cli demo-worker {{plan_slug}} "
        f"--phase {{phase_id}} --token {{token}} --project {{project}} "
        f"--session-id {{session_id}} --scenario {scenario}"
    )


def _cli_runner(argv: list[str]) -> int:
    """Default callback runner: invoke a `clu` subcommand in-process.

    The demo worker is already a `clu` process, so calling `main` directly
    exercises the real token-validated callback (heartbeat/block) without a
    subprocess. Imported lazily to avoid a cli<->demo_worker import cycle.
    """
    from end_of_line.cli import main

    return main(argv)


def _heartbeat_argv(plan: str, phase_id: str, token: str, project: str) -> list[str]:
    return ["heartbeat", "--project", project, "--plan", plan, "--phase", phase_id, "--token", token]


def _block_argv(plan: str, phase_id: str, token: str, project: str) -> list[str]:
    return [
        "block",
        "--project", project,
        "--plan", plan,
        "--phase", phase_id,
        "--token", token,
        "--question", "Demo blocker: which way should the demo worker go?",
        "--option", "left",
        "--option", "right",
    ]


def run_worker(
    plan: str,
    phase_id: str,
    token: str,
    scenario: str,
    *,
    project: Path | str,
    session_id: str,
    projects_root: Path | str = PROJECTS_ROOT,
    max_steps: int = DEFAULT_MAX_STEPS,
    step_seconds: float = DEFAULT_STEP_SECONDS,
    clock=st.utcnow,
    sleep=time.sleep,
    runner=None,
) -> int:
    """Run one synthetic demo worker to completion or to its scenario's exit.

    Writes a real-format transcript under `projects_root` so `clu top`/`clu serve`
    render it, and heartbeats via `runner` (default: the in-process `clu`
    callback) so the lease stays live. `project` doubles as the callback
    `--project` target and the transcript cwd (the demo uses no worktrees).

    Scenario exits: `busy`/`idle` run the full `max_steps` (the operator's
    teardown is the normal stop); `block` opens a blocker and returns; `dead`
    returns with no callback so dead-PID detection flags it red.
    `clock`/`sleep`/`runner` are injectable so tests run with no wall-clock,
    no real sleep, and no subprocess.
    """
    runner = runner or _cli_runner
    project = str(project)
    path = transcript_path(project, session_id, projects_root)
    for step in range(max_steps):
        action = scenario_action(scenario, step)
        if action == ACT_BLOCK:
            runner(_block_argv(plan, phase_id, token, project))
            return 0
        if action == ACT_DEAD:
            return 0
        if action == ACT_WRITE:
            append_records(
                path,
                build_records(scenario, step, cwd=project, session_id=session_id, now=clock()),
            )
        runner(_heartbeat_argv(plan, phase_id, token, project))
        sleep(step_seconds)
    return 0
