"""clu CLI entry point.

Subcommands (orchestrator-side):
  tick      — one supervisor tick (cron target)
  status    — show current state
  init      — bootstrap state.json for a plan

Subcommands (worker-side, called by phase-runner sessions):
  complete  — mark current phase complete + record commits
  block     — record a blocker question + release claim
  answer    — answer a pending blocker (user-side)
  spawn     — append a dynamic task (e.g. /simplify finding)
  heartbeat — stamp last_heartbeat_at so the supervisor knows the worker
              is still alive (called every ~2 min by the worker)

Worker-side commands require `--token` matching the live claim. Tokens come
from `{token}` in the dispatch command template.
"""
from __future__ import annotations

import argparse
import functools
import json
import subprocess
import sys
from enum import IntEnum
from pathlib import Path

from . import fleet, notify, registry, state as st
from .config import ProjectConfig, load_project_config
from .supervisor import ACTION_NOTIFY_KIND, tick


class ExitCode(IntEnum):
    OK = 0
    GENERIC = 1
    INVALID_SLUG = 2
    BAD_SHA = 3
    CLAIM_MISMATCH = 4
    SPAWN_CAP = 5
    UNKNOWN_TASK = 6
    STATUS_TRANSITION = 7


def _die(rc: ExitCode | int, msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return int(rc)


def _translate_claim_mismatch(fn):
    """Turn a leaked ClaimMismatch into ExitCode.CLAIM_MISMATCH.

    Every worker-side command does the same dance — try the claim check,
    catch ClaimMismatch, call _die. The decorator keeps the command bodies
    focused on the work and forces a uniform exit-code for forged callers.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except st.ClaimMismatch as exc:
            return _die(ExitCode.CLAIM_MISMATCH, str(exc))
    return wrapper


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clu", description="End of Line — plan orchestrator (clu CLI)"
    )
    # required=False so bare `clu` falls through to the fleet view — the
    # daily-driver entry point. `clu list` keeps the dumb name+root listing
    # for scripting that needs no projection.
    sub = parser.add_subparsers(dest="cmd", required=False)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--project", type=Path, required=True,
            help="Project root (contains .orchestrator.json)",
        )
        p.add_argument("--plan", required=True, help="Plan slug")

    p_tick = sub.add_parser("tick", help="Run one supervisor tick")
    add_common(p_tick)
    p_tick.add_argument(
        "--dispatch", action="store_true",
        help="Actually spawn worker via configured dispatch.command",
    )

    p_init = sub.add_parser("init", help="Bootstrap orchestrator state for a plan")
    add_common(p_init)

    p_register = sub.add_parser(
        "register",
        help="Add a (project, plan) pair to the host registry (auto-runs on init)",
    )
    add_common(p_register)

    p_unregister = sub.add_parser(
        "unregister", help="Remove a (project, plan) pair from the host registry",
    )
    add_common(p_unregister)

    sub.add_parser("list", help="List all registered plans on this host")

    p_status = sub.add_parser("status", help="Show current state")
    add_common(p_status)
    p_status.add_argument(
        "--json", action="store_true", help="Dump raw state JSON",
    )

    p_pause = sub.add_parser("pause", help="Pause the plan (operator)")
    add_common(p_pause)
    p_pause.add_argument(
        "--reason", default="", help="Why paused (recorded in event log)",
    )

    p_resume = sub.add_parser(
        "resume", help="Resume a paused plan (operator). Use `retry` for halted.",
    )
    add_common(p_resume)

    p_retry = sub.add_parser(
        "retry",
        help="Clear max-attempts on the halted phase and resume (operator)",
    )
    add_common(p_retry)
    p_retry.add_argument(
        "--phase",
        help="Phase to clear attempts on. Defaults to the most-recent halt.",
    )

    p_answer = sub.add_parser("answer", help="Answer a pending blocker")
    add_common(p_answer)
    p_answer.add_argument("blocker_id")
    p_answer.add_argument(
        "answer", help='Answer text or option index ("0", "1", …)',
    )

    p_spawn = sub.add_parser(
        "spawn", help="Append a dynamic follow-up task to the plan",
    )
    add_common(p_spawn)
    p_spawn.add_argument("--token", required=True, help="Worker claim token")
    p_spawn.add_argument("--source", default="manual")
    p_spawn.add_argument("--phase", required=True, help="Phase that spawned this task")
    p_spawn.add_argument("--title", required=True)
    p_spawn.add_argument("--description", default="")

    p_complete = sub.add_parser(
        "complete", help="Worker marks a phase complete",
    )
    add_common(p_complete)
    p_complete.add_argument("--token", required=True, help="Worker claim token")
    p_complete.add_argument("--phase", required=True)
    p_complete.add_argument(
        "--commit", action="append", default=[], dest="commits",
        help="Commit SHA produced by this phase (repeatable, validated against git)",
    )

    p_task_done = sub.add_parser(
        "task-done", help="Mark a spawned task done (user or worker)",
    )
    add_common(p_task_done)
    p_task_done.add_argument("task_id", help="Spawned task id (e.g. task-1)")
    p_task_done.add_argument(
        "--force", action="store_true",
        help="Skip claim check (user-initiated cleanup)",
    )
    p_task_done.add_argument(
        "--token", default="",
        help="Worker claim token if invoked by a phase-runner",
    )
    p_task_done.add_argument(
        "--commit", action="append", default=[], dest="commits",
    )

    p_heartbeat = sub.add_parser(
        "heartbeat",
        help="Worker pings to prove it's still alive (stamps last_heartbeat_at)",
    )
    add_common(p_heartbeat)
    p_heartbeat.add_argument("--token", required=True, help="Worker claim token")
    p_heartbeat.add_argument("--phase", required=True)

    p_block = sub.add_parser(
        "block", help="Worker reports a blocker + releases claim",
    )
    add_common(p_block)
    p_block.add_argument("--token", required=True, help="Worker claim token")
    p_block.add_argument("--phase", required=True)
    p_block.add_argument("--question", required=True)
    p_block.add_argument(
        "--option", action="append", default=[], dest="options",
        help="Answer option (repeatable)",
    )
    p_block.add_argument("--context", default="")
    p_block.add_argument(
        "--type", default=st.BLOCKER_INPUT,
        choices=[st.BLOCKER_INPUT, st.BLOCKER_REPLAN],
    )

    args = parser.parse_args(argv)
    # Host-scoped commands skip the per-plan ProjectConfig load (which
    # requires --project). Bare `clu` is the fleet view; `clu list` is the
    # name-only listing kept for scripting.
    if args.cmd is None:
        return cmd_fleet(args)
    if args.cmd == "list":
        return cmd_list(args)

    try:
        st.validate_slug(args.plan, kind="plan slug")
        cfg = load_project_config(args.project)
        state_path = cfg.state_path(args.plan)
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))

    dispatchers = {
        "init": cmd_init,
        "tick": cmd_tick,
        "status": cmd_status,
        "answer": cmd_answer,
        "spawn": cmd_spawn,
        "complete": cmd_complete,
        "block": cmd_block,
        "task-done": cmd_task_done,
        "heartbeat": cmd_heartbeat,
        "register": cmd_register,
        "unregister": cmd_unregister,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "retry": cmd_retry,
    }
    return dispatchers[args.cmd](args, cfg, state_path)


def cmd_init(args, cfg: ProjectConfig, state_path: Path) -> int:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with st.locked(state_path):
        # Re-check existence INSIDE the lock to defeat concurrent inits.
        if state_path.exists():
            print(f"State already exists: {state_path}", file=sys.stderr)
            return 1
        st.save_atomic(state_path, st.empty_state(args.plan, cfg.plan_dir))
    # Auto-register so fleet view / inbound routing can find the plan
    # without a separate setup step.
    registry.register(cfg.project_root, args.plan)
    print(f"Initialized {state_path}")
    return 0


def cmd_register(args, cfg: ProjectConfig, state_path: Path) -> int:
    added = registry.register(cfg.project_root, args.plan)
    msg = "Registered" if added else "Already registered"
    print(f"{msg}: {cfg.project_root}  →  {args.plan}")
    return 0


def cmd_unregister(args, cfg: ProjectConfig, state_path: Path) -> int:
    removed = registry.unregister(cfg.project_root, args.plan)
    msg = "Unregistered" if removed else "Not in registry"
    print(f"{msg}: {cfg.project_root}  →  {args.plan}")
    return 0


def cmd_list(args) -> int:
    rows = registry.entries()
    if not rows:
        print("No plans registered. Run `clu init` or `clu register` to add one.")
        return 0
    for row in rows:
        print(f"  {row.plan_slug:<30}  {row.project_root}")
    return 0


def cmd_fleet(args) -> int:
    print(fleet.render(registry.entries()), end="")
    return 0


def cmd_tick(args, cfg: ProjectConfig, state_path: Path) -> int:
    result = tick(state_path, cfg)
    print(result)
    if args.dispatch and result.action == "dispatch":
        from .dispatch import dispatch_for_tick
        dispatch_for_tick(result, cfg, args.plan, state_path)
    if result.notify_body and (kind := ACTION_NOTIFY_KIND.get(result.action)):
        notify.notify(cfg.notify, kind, result.notify_body)
    return 0


def cmd_status(args, cfg: ProjectConfig, state_path: Path) -> int:
    if not state_path.exists():
        print(f"No state at {state_path}", file=sys.stderr)
        return 1
    data = st.load(state_path)
    if args.json:
        json.dump(data, sys.stdout, indent=2)
        print()
        return 0

    print(f"Plan:    {data['plan_slug']}")
    print(f"Status:  {data['status']}")
    if reason := st.status_reason(data):
        print(f"Reason:  {reason}")
    if claim := data.get("current_claim"):
        print(
            f"Active:  {claim['phase_id']} "
            f"(by {claim['claimed_by']}, lease {claim['lease_expires']}, "
            f"attempt {claim['attempts']})"
        )
        print(f"         {_format_heartbeat(data, claim)}")
    else:
        print("Active:  none")

    if completed := sorted(st.completed_phase_ids(data)):
        print(f"Done:    {', '.join(completed)}")

    open_blockers = st.open_blockers(data)
    if open_blockers:
        print(f"\nOpen blockers ({len(open_blockers)}):")
        for b in open_blockers:
            print(f"  {b['id']}  [{b['phase_id']}]  {b['question']}")
            for i, opt in enumerate(b["options"]):
                print(f"      [{i}] {opt}")
            if b["context"]:
                print(f"      ctx: {b['context']}")

    pending_tasks = [t for t in data["spawned_tasks"] if t["status"] == "pending"]
    if pending_tasks:
        print(f"\nPending spawned tasks ({len(pending_tasks)}):")
        for t in pending_tasks:
            print(f"  {t['id']}  [{t['source']} ← {t['spawned_by_phase']}]  {t['title']}")
    return 0


def _format_heartbeat(data: dict, claim: dict) -> str:
    age = st.heartbeat_age_seconds(claim)
    if age is None:
        return "Heartbeat: unknown"
    threshold = data["config"].get(
        "stalled_heartbeat_minutes", st.DEFAULT_STALLED_HEARTBEAT_MIN,
    )
    label = "STALLED" if st.is_claim_stalled(claim, threshold) else "Heartbeat:"
    return f"{label} {_humanize_age(age)} ago"


def _humanize_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.0f}m"
    return f"{minutes / 60:.1f}h"


def cmd_pause(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        if data["status"] == st.STATUS_DONE:
            return _die(ExitCode.STATUS_TRANSITION, "plan is done — nothing to pause")
        if data["status"] == st.STATUS_PAUSED:
            print("Already paused.")
            return ExitCode.OK
        data["status"] = st.STATUS_PAUSED
        st.append_event(data, st.EVENT_PAUSED, reason=args.reason)
    print(f"Paused {args.plan}.")
    return ExitCode.OK


def cmd_resume(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        status = data["status"]
        if status == st.STATUS_RUNNING:
            print("Already running.")
            return ExitCode.OK
        if status in (st.STATUS_HALTED, st.STATUS_HALTED_REPLAN):
            return _die(
                ExitCode.STATUS_TRANSITION,
                f"plan is {status} — use `clu retry` to clear attempts",
            )
        if status == st.STATUS_DONE:
            return _die(ExitCode.STATUS_TRANSITION, "plan is done — nothing to resume")
        data["status"] = st.STATUS_RUNNING
        st.append_event(data, st.EVENT_RESUMED)
    print(f"Resumed {args.plan}.")
    return ExitCode.OK


def cmd_retry(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        if data["status"] == st.STATUS_DONE:
            return _die(ExitCode.STATUS_TRANSITION, "plan is done — nothing to retry")
        phase = args.phase or st.most_recent_halted_phase(data)
        if phase is None:
            return _die(
                ExitCode.STATUS_TRANSITION,
                "no halted phase to retry — pass --phase or use `clu resume`",
            )
        try:
            st.validate_slug(phase, kind="phase id")
        except st.InvalidSlug as exc:
            return _die(ExitCode.INVALID_SLUG, str(exc))
        data["status"] = st.STATUS_RUNNING
        st.append_event(data, st.EVENT_RETRY_REQUESTED, phase=phase)
    print(f"Retrying {args.plan}/{phase}.")
    return ExitCode.OK


def cmd_answer(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        resolved = st.resolve_blocker_answer(data, args.blocker_id, args.answer)
        st.answer_blocker(data, args.blocker_id, resolved)
    print(f"Answered {args.blocker_id}: {resolved}")
    return 0


@_translate_claim_mismatch
def cmd_spawn(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        st.assert_claim_match(data, args.token, args.phase)
        cap = data["config"].get("max_spawns_per_phase", st.DEFAULT_MAX_SPAWNS_PER_PHASE)
        existing = sum(
            1 for t in data["spawned_tasks"]
            if t.get("spawned_by_phase") == args.phase
        )
        if existing >= cap:
            return _die(
                ExitCode.SPAWN_CAP,
                f"phase {args.phase} already spawned {existing} task(s); cap is {cap}",
            )
        task_id = f"task-{len(data['spawned_tasks']) + 1}"
        data["spawned_tasks"].append({
            "id": task_id,
            "source": args.source,
            "spawned_by_phase": args.phase,
            "title": args.title,
            "description": args.description,
            "depends_on_phases": [args.phase],
            "status": "pending",
            "spawned_at": st.utcnow(),
        })
        st.append_event(
            data, st.EVENT_TASK_SPAWNED,
            task=task_id, source=args.source, spawned_by_phase=args.phase,
        )
    print(f"Spawned {task_id}: {args.title}")
    return 0


@_translate_claim_mismatch
def cmd_task_done(args, cfg: ProjectConfig, state_path: Path) -> int:
    if args.force and args.token:
        return _die(ExitCode.CLAIM_MISMATCH, "--force and --token are mutually exclusive")
    with st.mutate(state_path) as data:
        match = next(
            (t for t in data["spawned_tasks"] if t["id"] == args.task_id),
            None,
        )
        if match is None:
            return _die(ExitCode.UNKNOWN_TASK, f"no task {args.task_id!r}")
        if match["status"] == "done":
            print(f"task {args.task_id} already done")
            return ExitCode.OK
        if not args.force:
            if not args.token:
                return _die(
                    ExitCode.CLAIM_MISMATCH,
                    "--token required (or pass --force for manual cleanup)",
                )
            st.assert_claim_match(data, args.token, match["spawned_by_phase"])
        match["status"] = "done"
        match["completed_at"] = st.utcnow()
        st.append_event(
            data, st.EVENT_TASK_COMPLETED,
            task=args.task_id, commits=list(args.commits),
            forced=bool(args.force),
        )
    print(f"task {args.task_id} done")
    return 0


def _verify_commit_shas(project_root: Path, shas: list[str]) -> str | None:
    """Run `git cat-file -e <sha>` for each. Returns error message on first miss."""
    for sha in shas:
        result = subprocess.run(
            ["git", "-C", str(project_root), "cat-file", "-e", sha],
            capture_output=True,
        )
        if result.returncode != 0:
            return f"unknown commit SHA {sha!r} in {project_root}"
    return None


@_translate_claim_mismatch
def cmd_complete(args, cfg: ProjectConfig, state_path: Path) -> int:
    if args.commits:
        if err := _verify_commit_shas(cfg.project_root, args.commits):
            return _die(ExitCode.BAD_SHA, err)
    with st.mutate(state_path) as data:
        st.release_claim(data, expected_token=args.token, expected_phase=args.phase)
        st.append_event(
            data, st.EVENT_PHASE_COMPLETED,
            phase=args.phase, commits=list(args.commits),
        )
    print(f"Completed phase {args.phase}")
    return ExitCode.OK


@_translate_claim_mismatch
def cmd_heartbeat(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        ts = st.record_heartbeat(data, args.token, args.phase)
    print(f"heartbeat {args.phase} @ {ts}")
    return ExitCode.OK


@_translate_claim_mismatch
def cmd_block(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        st.release_claim(data, expected_token=args.token, expected_phase=args.phase)
        blocker_id = st.add_blocker(
            data, args.phase, args.question, args.options,
            args.context, args.type,
        )
    notify.notify(
        cfg.notify, notify.KIND_BLOCKER,
        notify.render_blocker(
            args.plan, blocker_id, args.phase, args.question, args.options,
        ),
    )
    print(f"Blocked {blocker_id} on phase {args.phase}")
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
