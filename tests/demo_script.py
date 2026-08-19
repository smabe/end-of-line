"""The scripted demo sequence — one plan's full lifecycle, deterministically.

Later phases of the sqlite-migration plan all need to replay the *same* fleet
run and compare what came out: p3 proves the DB-backed engine emits what the
JSON backend emitted, p6 proves the restructured tick keeps the sequence, p7
proves `clu watch` is byte-identical at the end. None of them can define the
sequence — a golden is only worth anything if every side runs the identical
script — so it lives here, once.

The script drives one plan (`demo-block`, three phases, the worker parked at
phase `c`) through: dispatch -> heartbeats -> blocker -> operator answer ->
blocker consumed -> re-dispatch -> completion -> plan done. It is built on the
real `clu demo` machinery (`end_of_line.demo` scaffolds and pre-completes,
`end_of_line.demo_worker` heartbeats and blocks) so what the golden captures is
clu's actual pipeline, not a hand-rolled imitation of it.

**Determinism.** Everything that would otherwise vary is pinned:

- the plan slug, phase ids, blocker id, question and answer are all fixed;
- the worker's sleep is a no-op and its step count is exactly enough to reach
  the block, so no wall clock is consulted;
- `tick_on_action` is turned off in the scaffolded config, because a detached
  background tick would race the script and land events in a random order;
- the real worker spawn is stubbed, so no subprocess exists to be slow.

The one thing that genuinely differs run to run is timestamps — and `clu
watch`'s default projection never prints them, which is why the golden is
captured from that projection rather than from `--json`.

Not named `test_*`, so unittest discovery leaves it alone: it is a fixture,
not a test. `tests/test_demo_script.py` is what exercises it.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from unittest import mock

from end_of_line import demo, demo_worker, watch
from end_of_line import state as st
from end_of_line.cli import main as cli_main
from end_of_line.config import CONFIG_FILENAME, load_project_config

# The `block` scenario is the one whose demo layout already tells a whole story:
# three phases with the first two pre-completed, so the run exercises a mid-plan
# claim, a blocker, and a plan-completing final phase.
SCENARIO = "block"
SLUG = f"{demo.DEMO_SLUG_PREFIX}{SCENARIO}"
PHASE = "c"
BLOCKER_ID = "q-1"
ANSWER_INDEX = "0"
SESSION_ID = "demo-script-session"

# The pre-migration capture: what `clu watch` emitted for this script while the
# JSON backend was still the engine. p3 and p7 diff their runs against it.
GOLDEN_PATH = Path(__file__).with_name("goldens") / "watch-demo.txt"

# One more step than the block scenario needs to reach its blocker, so the
# worker heartbeats its way there and then blocks — and stops.
_WORKER_MAX_STEPS = demo_worker._PRE_LIFECYCLE_STEPS + 1


def golden_lines() -> list[str]:
    """The recorded golden, as a line list ready to compare with a live run."""
    return GOLDEN_PATH.read_text().splitlines()


def scaffold_project(root: Path) -> Path:
    """Scaffold + init the demo plan under `root`. Returns the project root.

    Split from `run_sequence` because `clu watch` has to be watching a state
    file that already exists — a path that is absent when the stream starts is
    dropped from the poll set and never picked up again.
    """
    (plan,) = demo.scaffold([SCENARIO], root=root)
    _disable_tick_on_action(plan.project_root)
    with contextlib.redirect_stdout(io.StringIO()):
        _run(
            [
                "init",
                "--no-notify-prompt",
                "--project",
                str(plan.project_root),
                "--plan",
                SLUG,
            ]
        )
    return plan.project_root


def _disable_tick_on_action(project_root: Path) -> None:
    """Turn off the detached post-action tick for this project.

    `clu block` and `clu complete` each fire a background `clu tick` when
    `tick_on_action` is on (the default). That subprocess would tick the same
    plan concurrently with the script's own ticks, so events would interleave
    differently on every run. The script ticks explicitly instead.
    """
    path = project_root / CONFIG_FILENAME
    cfg = json.loads(path.read_text())
    cfg["tick_on_action"] = False
    path.write_text(json.dumps(cfg, indent=2))


def state_path_for(project_root: Path) -> Path:
    return load_project_config(project_root).state_path(SLUG)


def _run(argv: list[str]) -> None:
    """Run a clu command and fail loudly if it did not succeed.

    Every command here is load-bearing for the golden. An unchecked non-zero
    exit would drop the lines that command was supposed to produce, and the
    only symptom later phases would see is a golden diff — which reads as "the
    storage swap changed the output" when the truth is "`clu answer` stopped
    parsing these arguments". Naming the command that failed costs one line.
    """
    rc = cli_main(argv)
    if rc:
        raise RuntimeError(f"demo script: `clu {argv[0]}` exited {rc}")


def _tick(project_root: Path) -> None:
    """One supervisor tick with the real worker spawn stubbed out."""
    with mock.patch("end_of_line.dispatch.dispatch_for_tick"):
        _run(["tick", "--project", str(project_root), "--plan", SLUG])


def _live_token(project_root: Path) -> str:
    claim = st.load(state_path_for(project_root)).get("current_claim")
    if not claim:
        raise RuntimeError("demo script: expected a live claim after the dispatch tick")
    return claim["claimed_by"]


def run_sequence(project_root: Path, *, projects_root: Path) -> None:
    """Drive the scaffolded plan through its whole lifecycle.

    `projects_root` is where the synthetic worker's transcript lands; pass a
    temp dir so nothing touches `~/.claude/projects`.
    """
    state_path = state_path_for(project_root)

    # Phases a and b are pre-completed, exactly as `clu demo up` does it, so
    # the tick below claims phase c rather than phase a.
    _, done_ids = demo._phase_layout(SCENARIO)
    demo._prefill_completed(state_path, done_ids)

    with contextlib.redirect_stdout(io.StringIO()):
        # Dispatch: claims phase c and stamps `phase_started`.
        _tick(project_root)
        token = _live_token(project_root)

        # The synthetic worker: heartbeats through its work steps, then opens
        # the blocker and releases the claim.
        demo_worker.run_worker(
            SLUG,
            PHASE,
            token,
            SCENARIO,
            project=project_root,
            session_id=SESSION_ID,
            projects_root=projects_root,
            max_steps=_WORKER_MAX_STEPS,
            sleep=lambda _seconds: None,
        )

        # The operator answers; the next tick marks it consumed and re-opens
        # the lane; the tick after that re-dispatches the phase.
        _run(["answer", "--plan", SLUG, ANSWER_INDEX])
        _tick(project_root)
        _tick(project_root)
        token = _live_token(project_root)

        # The worker finishes. The quality gates are skipped explicitly: the
        # throwaway demo project is not a git repo, so there is no HEAD for a
        # verify or simplify stamp to be measured against.
        _run(
            [
                "complete",
                "--project",
                str(project_root),
                "--plan",
                SLUG,
                "--phase",
                PHASE,
                "--token",
                token,
                "--skip-verify",
                "--skip-simplify",
            ]
        )

        # Every phase is done — this tick is the one that says so.
        _tick(project_root)


def capture_watch_lines(root: Path, *, projects_root: Path) -> list[str]:
    """Run the whole script under `clu watch` and return the lines it emitted.

    The sequence runs inside `stream_loop`'s `_before_first_tick` seam so the
    stream takes its baseline snapshot on a freshly-initialized plan and then
    sees every event the script produces — which is what makes a single poll
    tick enough, and what keeps the capture free of `time.sleep`.
    """
    project_root = scaffold_project(root)
    sink = io.StringIO()
    watch.stream_loop(
        [state_path_for(project_root)],
        sink=sink,
        poll_interval=0.0,
        max_ticks=1,
        _before_first_tick=lambda: run_sequence(project_root, projects_root=projects_root),
    )
    return sink.getvalue().splitlines()
