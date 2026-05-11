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

Worker-side commands require `--token` matching the live claim. Tokens come
from `{token}` in the dispatch command template.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import state as st
from .config import ProjectConfig, load_project_config
from .supervisor import tick


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clu", description="End of Line — plan orchestrator (clu CLI)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

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

    p_status = sub.add_parser("status", help="Show current state")
    add_common(p_status)
    p_status.add_argument(
        "--json", action="store_true", help="Dump raw state JSON",
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
    try:
        st.validate_slug(args.plan, kind="plan slug")
    except st.InvalidSlug as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    cfg = load_project_config(args.project)
    state_path = cfg.state_path(args.plan)

    dispatchers = {
        "init": cmd_init,
        "tick": cmd_tick,
        "status": cmd_status,
        "answer": cmd_answer,
        "spawn": cmd_spawn,
        "complete": cmd_complete,
        "block": cmd_block,
        "task-done": cmd_task_done,
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
    print(f"Initialized {state_path}")
    return 0


def cmd_tick(args, cfg: ProjectConfig, state_path: Path) -> int:
    result = tick(state_path, cfg)
    print(result)
    if args.dispatch and result.action == "dispatch":
        from .dispatch import dispatch_for_tick
        dispatch_for_tick(result, cfg, args.plan, state_path)
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
    if claim := data.get("current_claim"):
        print(
            f"Active:  {claim['phase_id']} "
            f"(by {claim['claimed_by']}, lease {claim['lease_expires']}, "
            f"attempt {claim['attempts']})"
        )
    else:
        print("Active:  none")

    if completed := sorted(st.completed_phase_ids(data)):
        print(f"Done:    {', '.join(completed)}")

    open_blockers = [b for b in data["blockers"] if b["answer"] is None]
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


def cmd_answer(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        resolved = st.resolve_blocker_answer(data, args.blocker_id, args.answer)
        st.answer_blocker(data, args.blocker_id, resolved)
    print(f"Answered {args.blocker_id}: {resolved}")
    return 0


def cmd_spawn(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        try:
            st.assert_claim_match(data, args.token, args.phase)
        except st.ClaimMismatch as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 4
        cap = data["config"].get("max_spawns_per_phase", st.DEFAULT_MAX_SPAWNS_PER_PHASE)
        existing = sum(
            1 for t in data["spawned_tasks"]
            if t.get("spawned_by_phase") == args.phase
        )
        if existing >= cap:
            print(
                f"error: phase {args.phase} already spawned {existing} task(s); "
                f"cap is {cap}",
                file=sys.stderr,
            )
            return 5
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


def cmd_task_done(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        match = next(
            (t for t in data["spawned_tasks"] if t["id"] == args.task_id),
            None,
        )
        if match is None:
            print(f"error: no task {args.task_id!r}", file=sys.stderr)
            return 6
        if match["status"] == "done":
            print(f"task {args.task_id} already done")
            return 0
        if not args.force:
            if not args.token:
                print(
                    "error: --token required (or pass --force for manual cleanup)",
                    file=sys.stderr,
                )
                return 4
            try:
                st.assert_claim_match(data, args.token, match["spawned_by_phase"])
            except st.ClaimMismatch as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 4
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


def cmd_complete(args, cfg: ProjectConfig, state_path: Path) -> int:
    if args.commits:
        if err := _verify_commit_shas(cfg.project_root, args.commits):
            print(f"error: {err}", file=sys.stderr)
            return 3
    with st.mutate(state_path) as data:
        try:
            st.release_claim(data, expected_token=args.token, expected_phase=args.phase)
        except st.ClaimMismatch as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 4
        st.append_event(
            data, st.EVENT_PHASE_COMPLETED,
            phase=args.phase, commits=list(args.commits),
        )
    print(f"Completed phase {args.phase}")
    return 0


def cmd_block(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        try:
            st.assert_claim_match(data, args.token, args.phase)
        except st.ClaimMismatch as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 4
        blocker_id = st.add_blocker(
            data, args.phase, args.question, args.options,
            args.context, args.type,
        )
        st.release_claim(data, expected_token=args.token, expected_phase=args.phase)
    print(f"Blocked {blocker_id} on phase {args.phase}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
