"""clu CLI entry point.

Subcommands (orchestrator-side):
  tick      — one supervisor tick (cron target)
  tick-all  — tick every registered plan once (cron target for the host)
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
import time
from enum import IntEnum
from pathlib import Path

from . import fleet, notify, queue, registry, state as st
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

    p_tick = sub.add_parser(
        "tick",
        help="Run one supervisor tick (dispatches worker by default; "
             "use --dry-tick for state mutation only).",
    )
    add_common(p_tick)
    p_tick.add_argument(
        "--dry-tick", action="store_true",
        help="Skip worker spawn (state mutation only — debug use). "
             "Default is to dispatch.",
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

    p_install_skill = sub.add_parser(
        "install-skill",
        help="Copy bundled skills (/clu-phase worker, /plan authorship, "
             "/brainstorm pre-planning) into ~/.claude/skills/<name>/SKILL.md "
             "so Claude Code can find them. Default installs all three; use "
             "--only to install one.",
        description="Copy bundled skills into ~/.claude/skills/<name>/SKILL.md "
                    "so Claude Code can find them. Three skills ship: /clu-phase "
                    "(the worker clu's dispatch invokes — required for clu to "
                    "function), /plan (authorship skill for writing plans in "
                    "the shape clu's parser expects), and /brainstorm "
                    "(parallel-persona pre-planning for fuzzy problem spaces). "
                    "Default installs all three; pass --only <name> to install one.",
    )
    p_install_skill.add_argument(
        "--force", action="store_true", default=False,
        help="Overwrite an existing target, even a regular file the operator "
             "wrote. Symlinks are overwritten without --force.",
    )
    p_install_skill.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print the planned action without writing.",
    )
    # Runtime-validate `--only` rather than `choices=` so the failure path
    # exits via _die (ExitCode), not argparse's SystemExit(2).
    p_install_skill.add_argument(
        "--only", default=None,
        help="Install only the named skill (default: install all bundled).",
    )
    # Primes a fresh user's Claude on autonomous-loop pacing so multi-plan
    # chains drive themselves without a human re-poking the chain.
    _claude_md_grp = p_install_skill.add_mutually_exclusive_group()
    _claude_md_grp.add_argument(
        "--add-claude-md-note", dest="add_claude_md_note",
        action="store_true", default=False,
        help="Append/update a clu-managed section in ~/.claude/CLAUDE.md "
             "about ScheduleWakeup discipline for autonomous tasks.",
    )
    _claude_md_grp.add_argument(
        "--no-claude-md-note", dest="no_claude_md_note",
        action="store_true", default=False,
        help="Skip the CLAUDE.md note prompt (non-interactive runs).",
    )

    p_queue = sub.add_parser(
        "queue",
        help="Manage the project's plan queue (operator-only in v1).",
    )
    queue_subs = p_queue.add_subparsers(dest="queue_cmd")
    p_queue_add = queue_subs.add_parser(
        "add", help="Append a plan slug to the queue (--front to insert at head).",
    )
    p_queue_add.add_argument("slug")
    p_queue_add.add_argument(
        "--front", action="store_true",
        help="Insert at head instead of tail.",
    )
    p_queue_add.add_argument(
        "--project", type=Path, default=None,
        help="Project root (defaults to CWD).",
    )

    sub.add_parser(
        "tick-all",
        help="Tick every registered plan once (cron entry point). Per-plan "
             "errors are logged to stderr; the loop continues so one bad "
             "plan doesn't poison the cadence.",
    )

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

    p_release_claim = sub.add_parser(
        "release-claim",
        help="Clear a stuck current_claim (operator escape hatch). Refuses "
             "to clear a fresh-heartbeat claim on a running plan unless "
             "`--force` is passed; use `clu pause` first or `--force` "
             "explicitly. Emits EVENT_CLAIM_FORCE_RELEASED so the audit log "
             "distinguishes operator recovery from automatic lease expiry.",
    )
    add_common(p_release_claim)
    p_release_claim.add_argument(
        "--force", action="store_true", default=False,
        help="Override the live-worker safety check (running + fresh heartbeat).",
    )
    p_release_claim.add_argument(
        "--reason", default="",
        help="Optional explanation, recorded in the audit event.",
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

    p_logs = sub.add_parser(
        "logs",
        help="Tail the active worker's log (or newest log in the dir if idle)",
    )
    add_common(p_logs)
    p_logs.add_argument(
        "-f", "--follow", action="store_true",
        help="Stream new lines as they're appended (like `tail -f`)",
    )

    p_prior_blocker = sub.add_parser(
        "prior-blocker",
        help="Print the answer for the phase's most recent answered blocker (exit 0); "
             "non-zero if none. Used by worker resume-after-answer detection.",
    )
    add_common(p_prior_blocker)
    p_prior_blocker.add_argument("--phase", required=True)

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
    if args.cmd == "tick-all":
        return cmd_tick_all(args)
    if args.cmd == "install-skill":
        return cmd_install_skill(args)
    if args.cmd == "queue":
        return cmd_queue(args)

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
        "release-claim": cmd_release_claim,
        "prior-blocker": cmd_prior_blocker,
        "logs": cmd_logs,
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


BUNDLED_SKILLS = ("clu-phase", "plan", "brainstorm")

_CLU_NOTE_START = "<!-- clu:start autonomous-loop-pacing -->"
_CLU_NOTE_END = "<!-- clu:end autonomous-loop-pacing -->"
_CLU_NOTE_BODY = (
    "When the operator delegates a multi-step autonomous task to clu "
    "(\"leave the grid\", \"keep driving\", \"ping me when done\"), use "
    "ScheduleWakeup to self-pace check-ins — don't end the turn after "
    "dispatching a single phase. The `/loop` framing in the tool "
    "description is provenance, not scope."
)


def _decide_inject_claude_md_note(args) -> bool:
    """Apply flag/TTY logic. Prompts the user only on an interactive TTY
    when neither flag is set."""
    if args.no_claude_md_note:
        return False
    if args.add_claude_md_note:
        return True
    if not sys.stdin.isatty():
        return False
    response = input(
        "Add a one-liner about autonomous-clu-chain pacing to "
        "~/.claude/CLAUDE.md? [y/N] "
    ).strip().lower()
    return response in ("y", "yes")


def _inject_claude_md_note(claude_md: Path) -> None:
    """Idempotently write the clu-managed section into ~/.claude/CLAUDE.md.

    Both markers present → replace content between them. Neither → append
    section to end (create file if missing). One marker without its pair →
    raise ValueError; bail rather than guess where to splice.
    """
    section = f"{_CLU_NOTE_START}\n{_CLU_NOTE_BODY}\n{_CLU_NOTE_END}\n"
    if not claude_md.exists():
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        claude_md.write_text(section)
        return
    text = claude_md.read_text()
    has_start = _CLU_NOTE_START in text
    has_end = _CLU_NOTE_END in text
    if has_start != has_end:
        raise ValueError(
            f"{claude_md} has malformed clu markers (one without the "
            f"other). Fix manually before re-running install-skill."
        )
    if has_start and has_end:
        start = text.index(_CLU_NOTE_START)
        end = text.index(_CLU_NOTE_END) + len(_CLU_NOTE_END)
        new_section = f"{_CLU_NOTE_START}\n{_CLU_NOTE_BODY}\n{_CLU_NOTE_END}"
        claude_md.write_text(text[:start] + new_section + text[end:])
        return
    # No markers — append with a blank-line separator from prior content.
    prior = text.rstrip("\n")
    claude_md.write_text(prior + "\n\n" + section)


def cmd_install_skill(args) -> int:
    from importlib.resources import files

    if args.only is not None and args.only not in BUNDLED_SKILLS:
        return _die(
            ExitCode.GENERIC,
            f"unknown skill {args.only!r}; valid: {', '.join(BUNDLED_SKILLS)}",
        )

    skills_to_install = (args.only,) if args.only else BUNDLED_SKILLS

    # Pre-flight: every target is checked before any write. A non-symlink
    # collision aborts the whole run so install-skill is atomic — the
    # operator never sees a half-installed state.
    plans = []
    for name in skills_to_install:
        bundled = files("end_of_line").joinpath(f"skills/{name}/SKILL.md")
        target = Path.home() / ".claude" / "skills" / name / "SKILL.md"
        # `is_symlink` first — `exists()` follows symlinks, so a broken
        # symlink would otherwise look like a clean target.
        is_symlink = target.is_symlink()
        exists = is_symlink or target.exists()
        if exists and not is_symlink and not args.force:
            return _die(
                ExitCode.STATUS_TRANSITION,
                f"refusing to overwrite {target} (regular file, not a "
                f"symlink clu owns). Pass --force to overwrite, or "
                f"--only <other> to skip {name}. No skills were installed.",
            )
        plans.append((name, bundled, target, exists))

    if args.dry_run:
        for name, bundled, target, exists in plans:
            verb = "Would overwrite" if exists else "Would write"
            print(f"{verb} {target} from bundled {bundled}")
        return ExitCode.OK

    for name, bundled, target, exists in plans:
        target.parent.mkdir(parents=True, exist_ok=True)
        if exists:
            # Unlink before writing — handles symlinks (don't follow to
            # destination), hardlinks (don't modify the shared inode), and
            # regular files (replace cleanly with a fresh inode).
            target.unlink()
        target.write_bytes(bundled.read_bytes())
        print(f"Installed {name} skill to {target}")

    if _decide_inject_claude_md_note(args):
        claude_md = Path.home() / ".claude" / "CLAUDE.md"
        try:
            _inject_claude_md_note(claude_md)
        except ValueError as exc:
            return _die(ExitCode.GENERIC, str(exc))
        print(f"Updated {claude_md} with autonomous-loop-pacing section")
    return ExitCode.OK


def cmd_queue(args) -> int:
    if args.queue_cmd == "add":
        return cmd_queue_add(args)
    print("usage: clu queue add <slug> [--front] [--project PATH]", file=sys.stderr)
    return _die(ExitCode.GENERIC, "missing queue subcommand")


def cmd_queue_add(args) -> int:
    slug = args.slug
    try:
        st.validate_slug(slug, kind="plan slug")
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))

    project = args.project if args.project is not None else Path.cwd()
    cfg = load_project_config(project)

    registered_roots = {Path(e.project_root).resolve() for e in registry.entries()}
    if cfg.project_root not in registered_roots:
        return _die(
            ExitCode.GENERIC,
            f"project {cfg.project_root} has no registered plans; "
            f"run `clu init --project {cfg.project_root} --plan <slug>` first",
        )

    plan_file = cfg.project_root / cfg.plan_dir / f"{slug}.md"
    if not plan_file.exists():
        return _die(ExitCode.UNKNOWN_TASK, f"no plan file at {plan_file}")

    with queue.mutate(cfg.queue_path()) as data:
        for idx, entry in enumerate(data["queue"]):
            if entry["slug"] == slug:
                return _die(
                    ExitCode.STATUS_TRANSITION,
                    f"{slug!r} already queued at position {idx + 1}; "
                    f"`clu queue remove {slug}` first to re-order",
                )
        entry = {
            "slug": slug,
            "added_at": st.utcnow(),
            "added_by": "operator",
            "position_at_add": "front" if args.front else "tail",
        }
        if args.front:
            data["queue"].insert(0, entry)
            position = 1
        else:
            data["queue"].append(entry)
            position = len(data["queue"])

    print(f"queued at position {position}")
    return ExitCode.OK


def _tick_one_plan(
    plan_slug: str, cfg: ProjectConfig, state_path: Path, *, dispatch: bool,
):
    """Run one supervisor tick + optional dispatch + optional notify.

    Side-effect helper shared by `cmd_tick` (single-plan) and `cmd_tick_all`
    (host-scoped). Printing is the caller's prerogative — single-plan wants
    the result plain; tick-all wants it prefixed with plan id + project.
    """
    result = tick(state_path, cfg)
    if dispatch and result.action == "dispatch":
        from .dispatch import dispatch_for_tick
        dispatch_for_tick(result, cfg, plan_slug, state_path)
    if result.notify_body and (kind := ACTION_NOTIFY_KIND.get(result.action)):
        notify.notify(cfg.notify, kind, result.notify_body)
    return result


def cmd_tick(args, cfg: ProjectConfig, state_path: Path) -> int:
    result = _tick_one_plan(args.plan, cfg, state_path, dispatch=not args.dry_tick)
    print(result)
    return 0


def cmd_tick_all(args) -> int:
    for row in registry.entries():
        try:
            cfg = load_project_config(Path(row.project_root))
            state_path = cfg.state_path(row.plan_slug)
            result = _tick_one_plan(row.plan_slug, cfg, state_path, dispatch=True)
            print(f"tick {row.plan_slug} @ {row.project_root}: {result}")
        except Exception as exc:
            # Per-plan exceptions must not abort the loop — a single broken
            # plan can't be allowed to poison the 5-minute cron cadence.
            print(
                f"tick-all: {row.plan_slug} @ {row.project_root}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    return ExitCode.OK


def cmd_prior_blocker(args, cfg: ProjectConfig, state_path: Path) -> int:
    try:
        st.validate_slug(args.phase, kind="phase id")
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))
    if not state_path.exists():
        return _die(ExitCode.UNKNOWN_TASK, f"no state at {state_path}")
    data = st.load(state_path)
    answered = [
        b for b in data.get("blockers", [])
        if b["phase_id"] == args.phase and b.get("answer") is not None
    ]
    if not answered:
        return _die(
            ExitCode.UNKNOWN_TASK,
            f"no answered blocker for phase {args.phase!r}",
        )
    print(answered[-1]["answer"])
    return ExitCode.OK


def _resolve_log_path(state_path: Path, cfg: ProjectConfig) -> Path | None:
    """Active claim's log_path wins; otherwise newest file in the logs dir."""
    if state_path.exists():
        claim = st.load(state_path).get("current_claim") or {}
        if log_path := claim.get("log_path"):
            return Path(log_path)
    log_dir = state_path.parent / "logs"
    if not log_dir.exists():
        return None
    candidates = [p for p in log_dir.iterdir() if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _follow_log(
    log_path: Path, *, stop_after_seconds: float | None = None,
    poll_interval: float = 0.25,
) -> int:
    """Stream a log file in tail-f fashion. Rotation/truncation mid-follow is
    not handled — punted to a follow-up. `stop_after_seconds` exists so tests
    can exercise the loop without a subprocess timeout."""
    deadline = (
        time.monotonic() + stop_after_seconds
        if stop_after_seconds is not None else None
    )
    with open(log_path) as fh:
        sys.stdout.write(fh.read())
        sys.stdout.flush()
        while True:
            chunk = fh.read()
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
                continue
            if deadline is not None and time.monotonic() >= deadline:
                return ExitCode.OK
            try:
                time.sleep(poll_interval)
            except KeyboardInterrupt:
                return ExitCode.OK


def cmd_logs(args, cfg: ProjectConfig, state_path: Path) -> int:
    log_path = _resolve_log_path(state_path, cfg)
    if log_path is None or not log_path.exists():
        return _die(ExitCode.UNKNOWN_TASK, f"no logs found for plan {args.plan!r}")
    if args.follow:
        return _follow_log(log_path)
    sys.stdout.write(log_path.read_text())
    return ExitCode.OK


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


def cmd_release_claim(args, cfg: ProjectConfig, state_path: Path) -> int:
    with st.mutate(state_path) as data:
        claim = data.get("current_claim")
        if claim is None:
            print(f"no claim to release on {args.plan}", file=sys.stderr)
            return ExitCode.OK
        threshold = data["config"].get(
            "stalled_heartbeat_minutes", st.DEFAULT_STALLED_HEARTBEAT_MIN,
        )
        running = data["status"] == st.STATUS_RUNNING
        fresh = not st.is_claim_stalled(claim, threshold)
        if running and fresh and not args.force:
            return _die(
                ExitCode.STATUS_TRANSITION,
                f"plan is running with a fresh-heartbeat claim on phase "
                f"{claim['phase_id']!r} — `clu pause` first or pass `--force`",
            )
        phase = claim["phase_id"]
        token = claim.get("claimed_by")
        fields = {
            "phase": phase, "token": token, "forced": bool(args.force),
            "released_by_operator": True,
        }
        if args.reason:
            fields["reason"] = args.reason
        st.release_claim(data)
        st.append_event(data, st.EVENT_CLAIM_FORCE_RELEASED, **fields)
    print(f"Released claim on {args.plan}/{phase}.")
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
