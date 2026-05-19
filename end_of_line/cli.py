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
import datetime as _dt
import functools
import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
from enum import IntEnum
from pathlib import Path

from . import cross_plan_rules, dispatch, dry_merge, fleet, monitor, notify, queue, registry, state as st, state_blocker, state_locator, watch
from .config import CONFIG_FILENAME, ProjectConfig, load_project_config
from .plan_parser import parse_effort_minutes, parse_sessions_index
from .supervisor import ACTION_NOTIFY_KIND, tick


_MONITOR_TIP = (
    "\n  Tip: run /clu-monitor for background notifications on "
    "halts and blockers.\n"
)


def _maybe_print_monitor_tip() -> None:
    """Print the /clu-monitor tip if monitoring isn't scheduled and stdout
    is a TTY. Silent otherwise — worker subprocesses pipe stdout to a log
    file, so this naturally suppresses there."""
    if not sys.stdout.isatty():
        return
    if monitor.is_scheduled():
        return
    print(_MONITOR_TIP, end="")


def _print_worker_model(cfg: ProjectConfig) -> None:
    """Print which Claude model the worker will use on each phase.

    Operator-facing only — called from `cmd_init` and the operator path
    of `cmd_queue_add`. Worker-driven enqueue (`--token`) skips this:
    cron stdout is log noise, and the worker already knows its model.
    """
    model = dispatch.resolved_model(cfg.dispatch.command)
    if model:
        print(f"worker model: {model} (pinned via --model in dispatch.command)")
    else:
        print(
            "worker model: resolves via Claude Code settings "
            "(no --model in dispatch.command)"
        )


def _maybe_print_watch_tip(
    *, scope: str, slug: str | None = None, quiet: bool = False,
) -> None:
    if quiet:
        return
    if scope == "plan" and slug:
        print(
            f"\nTip: `clu watch --project . --plan {slug}` "
            f"streams state events (use with Claude's Monitor tool)."
        )
    elif scope == "all":
        print(
            "\nTip: `clu watch --project . --all` streams "
            "every queued plan (use with Claude's Monitor tool)."
        )


_CLU_SECTION_RE = re.compile(r"^##\s+clu\s*$", re.IGNORECASE | re.MULTILINE)

_CLU_SECTION_TEMPLATE = """

## clu

This project uses clu for autonomous plan execution.

- `clu queue add <slug>` to enqueue a plan; cron dispatches on each tick.
- `clu queue list` for pending; `clu list` for fleet status.
- Run `/clu-monitor` once per machine for background notifications on
  halts and blockers (status: `~/.config/clu/monitor.json`).
- The `/plan`, `/clu-plan`, and `/brainstorm` skills (bundled via
  `clu install-skill`) are the canonical authoring + pre-planning entry
  points. `/plan` is project-agnostic; `/clu-plan` produces the master +
  sub-plan files clu's supervisor expects for queue dispatch.
"""


def _decline_marker_path(cfg: ProjectConfig) -> Path:
    return cfg.project_root / cfg.plan_dir / ".orchestrator" / ".no-claude-md"


def _claude_md_has_clu_section(claude_md: Path) -> bool:
    try:
        text = claude_md.read_text()
    except OSError:
        return False
    return bool(_CLU_SECTION_RE.search(text))


def _write_decline_marker(cfg: ProjectConfig) -> None:
    marker = _decline_marker_path(cfg)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch(exist_ok=True)


def _append_clu_section(claude_md: Path) -> None:
    with claude_md.open("a") as f:
        f.write(_CLU_SECTION_TEMPLATE)
    print(f"Added clu section to {claude_md}")


def _maybe_handle_claude_md_injection(
    cfg: ProjectConfig, args: argparse.Namespace,
) -> None:
    """CLAUDE.md `## clu` injection flow.

    Flag overrides win over the interactive flow. The decline marker
    persists per-project so a re-run of `clu init` doesn't re-prompt
    after the operator already said no.
    """
    claude_md = cfg.project_root / "CLAUDE.md"

    if getattr(args, "inject_claude_md", False):
        if not claude_md.exists() or _claude_md_has_clu_section(claude_md):
            return
        _append_clu_section(claude_md)
        return

    if getattr(args, "no_claude_md", False):
        _write_decline_marker(cfg)
        return

    # Interactive flow: stdin must be a TTY (we need to read input), the
    # file must exist (don't auto-create), no existing section, no prior
    # decline. Failing any precondition is silent — this is a polite
    # suggestion, not a workflow gate.
    if not sys.stdin.isatty():
        return
    if not claude_md.exists():
        return
    if _claude_md_has_clu_section(claude_md):
        return
    if _decline_marker_path(cfg).exists():
        return

    print(
        "\nThis project doesn't have a clu section in CLAUDE.md yet. "
        "Adding one helps future Claude sessions orient on clu's "
        "workflow. May I append a short section? [y/N]: ",
        end="",
    )
    response = input().strip().lower()
    if response in {"y", "yes"}:
        _append_clu_section(claude_md)
    else:
        _write_decline_marker(cfg)
        print(
            "Skipped. Run `clu init --inject-claude-md` later if you "
            "change your mind.",
        )


def _prompt_yn(question: str, *, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    print(f"\n{question} {hint}: ", end="", flush=True)
    resp = input().strip().lower()
    if not resp:
        return default
    return resp in {"y", "yes"}


def _maybe_handle_notify_prompts(
    cfg: "ProjectConfig", args: argparse.Namespace,
) -> None:
    """Interactive iMessage/Discord channel setup during `clu init`.

    Skipped when --no-notify-prompt is passed, or when stdin is not a TTY
    (scripts, worker subprocesses, CI). Writes results to .orchestrator.json.
    """
    if not getattr(args, "notify_prompt", True):
        return
    if not sys.stdin.isatty():
        return
    channels: list[dict] = []
    try:
        if _prompt_yn("Wire iMessage?", default=platform.system() == "Darwin"):
            to = input("  iMessage handle (phone or email): ").strip()
            if to:
                channels.append({"kind": "imessage", "to": to})
        if _prompt_yn("Wire Discord?", default=False):
            token = input("  Discord bot token: ").strip()
            user_id = input("  Discord user ID: ").strip()
            if token and user_id:
                channels.append({"kind": "discord", "bot_token": token, "user_id": user_id})
    except EOFError:
        return  # stdin closed or non-interactive; skip prompt without writing
    cfg_path = cfg.project_root / CONFIG_FILENAME
    try:
        raw: dict = json.loads(cfg_path.read_text())
    except FileNotFoundError:
        raw = {}
    except json.JSONDecodeError:
        raw = {}
    raw.setdefault("notify", {})["channels"] = channels
    cfg_path.write_text(json.dumps(raw, indent=2) + "\n")


class ExitCode(IntEnum):
    OK = 0
    GENERIC = 1
    INVALID_SLUG = 2
    BAD_SHA = 3
    CLAIM_MISMATCH = 4
    SPAWN_CAP = 5
    UNKNOWN_TASK = 6
    STATUS_TRANSITION = 7
    INVALID_VALUE = 8
    # Repair worker's intent: "I won't touch this — would lose data."
    # clu's validation rejects the result anyway regardless of rc, so
    # this code is purely a legibility win when reading worker logs.
    REPAIR_DECLINED = 9
    # `clu init --worktree` rolled back: `git worktree add` succeeded but a
    # downstream step (e.g. state save) failed, and we tore the worktree +
    # branch back down. Distinct exit so callers can tell setup failure from
    # an invalid-slug or status-transition refusal.
    WORKTREE_SETUP_FAILED = 10
    # Worker called `clu queue add` but has already hit the per-phase cap
    # (default: DEFAULT_MAX_QUEUE_ADDS_PER_PHASE). Operator path is uncapped.
    QUEUE_CAP = 11


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
    parser.add_argument(
        "--no-notify", action="store_true", default=False, dest="no_notify",
        help="Suppress all outbound notify sends for this invocation "
             "(inbox writes are unaffected). Useful for debugging or dry-runs.",
    )
    # required=False so bare `clu` falls through to the fleet view — the
    # daily-driver entry point. `clu list` keeps the dumb name+root listing
    # for scripting that needs no projection.
    sub = parser.add_subparsers(dest="cmd", required=False)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--project", type=Path, default=None,
            help="Project root (contains .orchestrator.json). "
                 "Defaults to the current working directory.",
        )
        p.add_argument("--plan", required=True, help="Plan slug")

    p_tick = sub.add_parser(
        "tick",
        help="Run one supervisor tick (dispatches worker by default; "
             "use --dry-tick for state mutation only). Omit --plan to "
             "tick every plan in --project plus its cross-plan chain.",
    )
    p_tick.add_argument(
        "--project", type=Path, default=None,
        help="Project root (contains .orchestrator.json). "
             "Defaults to the current working directory.",
    )
    p_tick.add_argument(
        "--plan", default=None,
        help="Plan slug. Omit to tick every plan in the project "
             "and run the cross-plan rule chain (queue advance, "
             "auto-archive, worktree conflicts).",
    )
    p_tick.add_argument(
        "--dry-tick", action="store_true",
        help="Skip worker spawn (state mutation only — debug use). "
             "Default is to dispatch.",
    )

    p_init = sub.add_parser("init", help="Bootstrap orchestrator state for a plan")
    add_common(p_init)
    # CLAUDE.md injection flags — init-only (not in add_common, which is
    # shared with register/unregister). Mutually exclusive: either force-on
    # or force-off; neither → interactive prompt under TTY.
    _claude_md_group = p_init.add_mutually_exclusive_group()
    _claude_md_group.add_argument(
        "--inject-claude-md", action="store_true",
        help="Force-append a clu section to project CLAUDE.md (no prompt). "
             "Idempotent if section already exists.",
    )
    _claude_md_group.add_argument(
        "--no-claude-md", action="store_true",
        help="Skip the CLAUDE.md prompt and write a decline marker so "
             "future inits don't re-ask.",
    )
    # Worktree flags — opt-in per-plan isolation. `--worktree` alone uses
    # the default path; with a value, treats it as the path. `--branch` and
    # `--base-ref` only take effect with `--worktree`.
    p_init.add_argument(
        "--worktree", nargs="?", default=False, const=True,
        metavar="PATH",
        help="Create a git worktree at PATH (default: "
             "<project-parent>/<basename>-<slug>) on branch clu/<slug> "
             "(override with --branch) forked from current HEAD (override "
             "with --base-ref). Plan dispatch will run with cwd=PATH.",
    )
    p_init.add_argument(
        "--branch", default=None,
        help="With --worktree: branch name to create (default: clu/<slug>). "
             "Ignored without --worktree.",
    )
    p_init.add_argument(
        "--base-ref", default=None, dest="base_ref",
        help="With --worktree: ref to fork the new branch from "
             "(default: HEAD). Ignored without --worktree.",
    )
    p_init.add_argument(
        "--lease-ttl-minutes", type=int, default=None,
        dest="lease_ttl_minutes",
        help="Override default lease TTL (minutes). Default: 30.",
    )
    p_init.add_argument(
        "--stalled-heartbeat-minutes", type=int, default=None,
        dest="stalled_heartbeat_minutes",
        help="Override stall threshold (minutes). Default: 10.",
    )
    p_init.add_argument(
        "--max-attempts-per-phase", type=int, default=None,
        dest="max_attempts_per_phase",
        help="Override max phase attempts. Default: 3.",
    )
    p_init.add_argument(
        "--quiet", action="store_true",
        help="Suppress post-init tips (useful for scripts).",
    )
    p_init.add_argument(
        "--no-notify-prompt", action="store_false", dest="notify_prompt",
        help="Skip the interactive notify channel setup prompts.",
    )
    p_init.set_defaults(notify_prompt=True)

    p_register = sub.add_parser(
        "register",
        help="Add a (project, plan) pair to the host registry (auto-runs on init)",
    )
    add_common(p_register)

    p_archive = sub.add_parser(
        "archive",
        help="Wrap up a finished/paused plan: clean up its worktree + "
             "branch when commits are upstream-reachable, or retain + "
             "warn when ahead of origin. Idempotent.",
    )
    p_archive.add_argument(
        "--project", type=Path, required=True, help="Project root.",
    )
    p_archive.add_argument(
        "--plan", required=True, help="Plan slug.",
    )

    p_integrate = sub.add_parser(
        "integrate",
        help="Dry-merge a batch's branches in a scratch worktree and "
             "optionally run the project's test_command. Operator-on-demand "
             "replay; does NOT mutate plan state or file follow-ups (the "
             "cross-plan rule owns that).",
    )
    p_integrate.add_argument("--project", type=Path, required=True)
    p_integrate.add_argument(
        "--batch",
        default=None,
        help="Batch id; resolves DONE member branches from the registry.",
    )
    p_integrate.add_argument(
        "--branches",
        default=None,
        help="Comma-separated branch names; overrides --batch resolution.",
    )
    p_integrate.add_argument(
        "--no-suite", action="store_true",
        help="Textual-merge only — skip test_command even when configured.",
    )
    p_integrate.add_argument(
        "--base-ref", default="main",
        help="Base ref to merge off. Defaults to main.",
    )

    p_unregister = sub.add_parser(
        "unregister",
        help="Remove plan(s) from the host registry. Per-plan: "
             "--project P --plan S. Batch: --all-archived prunes every "
             "registry entry whose master plan file no longer exists.",
    )
    # --project / --plan are optional at parse-time so --all-archived can
    # forbid them; cmd_unregister validates the combination at runtime.
    p_unregister.add_argument(
        "--project", type=Path, default=None,
        help="Project root (required without --all-archived)",
    )
    p_unregister.add_argument(
        "--plan", default=None,
        help="Plan slug (required without --all-archived; "
             "mutually exclusive with --all-archived)",
    )
    p_unregister.add_argument(
        "--all-archived", action="store_true",
        help="Remove every registry entry whose master plan file no "
             "longer exists (post-archive ghost cleanup).",
    )
    p_unregister.add_argument(
        "--dry-run", action="store_true",
        help="With --all-archived: print what would be removed without "
             "mutating the registry.",
    )

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
        "--list", action="store_true", default=False,
        help="List bundled skills and their install targets, then exit. "
             "No writes; other flags are ignored.",
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

    sub.add_parser(
        "install-hook",
        help="Register the clu UserPromptSubmit hook in "
             "~/.claude/settings.json so Claude Code sessions see "
             "unprocessed inbox events at the start of every turn. "
             "Idempotent; preserves the operator's other hooks and "
             "their nested/flat array style.",
    )
    sub.add_parser(
        "uninstall-hook",
        help="Remove the clu UserPromptSubmit hook from "
             "~/.claude/settings.json (leaving the operator's other "
             "hooks intact) and clear the monitor marker.",
    )

    p_blockers = sub.add_parser(
        "blockers", help="List or show open blockers on a plan",
    )
    blockers_subs = p_blockers.add_subparsers(dest="blockers_cmd")
    p_blockers_list = blockers_subs.add_parser(
        "list", help="List open blockers on the plan",
    )
    add_common(p_blockers_list)
    p_blockers_show = blockers_subs.add_parser(
        "show", help="Show a blocker by id with full context and events",
    )
    add_common(p_blockers_show)
    p_blockers_show.add_argument("blocker_id")

    p_queue = sub.add_parser(
        "queue",
        help="Manage the project's plan queue (operator-only in v1).",
    )
    queue_subs = p_queue.add_subparsers(dest="queue_cmd")
    p_queue_add = queue_subs.add_parser(
        "add",
        help="Append one or more plan slugs to the queue "
             "(--front to insert at head).",
    )
    p_queue_add.add_argument("slugs", nargs="+", help="One or more plan slugs")
    p_queue_add.add_argument(
        "--front", action="store_true",
        help="Insert at head instead of tail.",
    )
    p_queue_add.add_argument(
        "--project", type=Path, default=None,
        help="Project root (defaults to CWD).",
    )
    p_queue_add.add_argument(
        "--token", default=None,
        help="Worker claim token (switches to worker mode).",
    )
    p_queue_add.add_argument(
        "--plan", dest="source_plan", default=None,
        help="Source plan slug (required in worker mode).",
    )
    p_queue_add.add_argument(
        "--phase", dest="source_phase", default=None,
        help="Source phase id (required in worker mode).",
    )
    p_queue_add.add_argument(
        "--reason", default=None,
        help="Optional reason text logged on the queue entry.",
    )
    p_queue_add.add_argument(
        "--batch", dest="batch", default=None,
        help="Tag this batch of plans with a shared batch_id (validated as a slug). "
             "Required for the multi-plan dry-merge gate to fire (#50).",
    )
    p_queue_add.add_argument(
        "--quiet", action="store_true",
        help="Suppress post-add tips (useful for scripts).",
    )
    p_queue_list = queue_subs.add_parser(
        "list", help="Show the pending queue and any recent failures.",
    )
    p_queue_list.add_argument(
        "--project", type=Path, default=None,
        help="Project root (defaults to CWD).",
    )
    p_queue_remove = queue_subs.add_parser(
        "remove", help="Drop a pending slug (moves it to history).",
    )
    p_queue_remove.add_argument("slug")
    p_queue_remove.add_argument(
        "--project", type=Path, default=None,
        help="Project root (defaults to CWD).",
    )

    p_worktree = sub.add_parser(
        "worktree",
        help="Manage per-plan git worktrees (subcommands: gc, reattach, attach).",
    )
    worktree_subs = p_worktree.add_subparsers(dest="worktree_cmd")
    p_worktree_gc = worktree_subs.add_parser(
        "gc",
        help="List or remove worktrees of done/halted plans "
             "(default: dry-run list).",
    )
    p_worktree_gc.add_argument(
        "--project", type=Path, default=None,
        help="Project root (defaults to CWD).",
    )
    p_worktree_gc.add_argument(
        "--confirm", action="store_true",
        help="Actually remove (default: dry run).",
    )
    p_worktree_gc.add_argument(
        "--delete-branch", action="store_true", dest="delete_branch",
        help="Also drop the clu/<slug> branch via `git branch -D`. "
             "Default keeps the branch so its commits stay reachable.",
    )
    p_worktree_gc.add_argument(
        "--include-archived", action="store_true", dest="include_archived",
        help="Widen to plans whose master plan file is gone (post-archive). "
             "Default scope: currently-tracked plans only.",
    )

    p_worktree_reattach = worktree_subs.add_parser(
        "reattach",
        help="Point a plan's state.worktree.path at a new directory "
             "(after operator moved or rebuilt the worktree).",
    )
    p_worktree_reattach.add_argument(
        "--project", type=Path, required=True,
        help="Project root.",
    )
    p_worktree_reattach.add_argument(
        "--plan", required=True, help="Plan slug.",
    )
    p_worktree_reattach.add_argument(
        "--path", type=Path, required=True,
        help="New worktree path. Must already exist and be a valid "
             "git working directory (this command does NOT create one).",
    )

    p_worktree_attach = worktree_subs.add_parser(
        "attach",
        help="Retrofit a worktree record onto an already-init'd plan "
             "(use when the operator built the worktree by hand).",
    )
    p_worktree_attach.add_argument(
        "--project", type=Path, required=True, help="Project root.",
    )
    p_worktree_attach.add_argument(
        "--plan", required=True, help="Plan slug.",
    )
    p_worktree_attach.add_argument(
        "--path", type=Path, required=True,
        help="Existing worktree path. Must be a valid git working "
             "directory on a branch (not detached HEAD).",
    )

    p_doctor = sub.add_parser(
        "doctor",
        help="Show what PATH and binary resolutions a worker subprocess "
             "would see (read-only; doesn't touch plan state).",
        description="Build the same env dict `dispatch_for_tick` would pass "
                    "to subprocess.Popen, then run a one-shot `sh -c` probe "
                    "to print PATH and resolve gh/pipx/clu. Closes #14: "
                    "operators can now see what their LaunchAgent worker "
                    "actually inherits instead of guessing dispatch.path.",
    )
    p_doctor.add_argument(
        "--project", type=Path, required=True,
        help="Project root (contains .orchestrator.json)",
    )
    p_doctor.add_argument(
        "--worktree", action="store_true",
        help="Also walk every registered plan in this project and report "
             "worktree-path liveness (stat + `git rev-parse --git-dir`).",
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

    p_extend_lease = sub.add_parser(
        "extend-lease",
        help="Extend a running claim's lease by N minutes (operator escape hatch).",
    )
    add_common(p_extend_lease)
    p_extend_lease.add_argument(
        "minutes", type=int, help="Minutes to add to the current lease expiry",
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
    p_release_claim.add_argument(
        "--reset-attempts", action="store_true", default=False,
        help="Zero the phase's attempts counter on release (for operator-driven "
             "aborts that shouldn't burn against max_attempts_per_phase).",
    )

    p_answer = sub.add_parser("answer", help="Answer a pending blocker")
    p_answer.add_argument(
        "--project", type=Path, default=None,
        help="Project root (ignored; kept for backward compat).",
    )
    p_answer.add_argument(
        "--plan", default=None,
        help="Plan slug to disambiguate a bare-digit reply. Optional.",
    )
    p_answer.add_argument(
        "answer", help='Option index ("0", "1", …)',
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
    p_complete.add_argument(
        "--skip-verify", action="store_true", default=False,
        help="Bypass the verify attestation gate (emits audit event)",
    )
    p_complete.add_argument(
        "--skip-simplify", action="store_true", default=False,
        help="Bypass the simplify attestation gate (emits audit event)",
    )

    p_force_complete = sub.add_parser(
        "force-complete",
        help="Operator marks a phase complete after worker died with work on "
             "disk. Releases any active claim without token validation; emits "
             "EVENT_OPERATOR_FORCE_COMPLETE + EVENT_PHASE_COMPLETED so the "
             "supervisor's plan_done detection fires normally next tick.",
    )
    add_common(p_force_complete)
    p_force_complete.add_argument("--phase", required=True)
    p_force_complete.add_argument(
        "--commit", action="append", default=[], dest="commits",
        help="Commit SHA the operator committed on the phase's behalf "
             "(repeatable, validated against git)",
    )
    p_force_complete.add_argument(
        "--reason", default="",
        help="Optional explanation, recorded in the audit event.",
    )
    p_force_complete.add_argument(
        "--really", action="store_true", default=False,
        help="Bypass the never-started safety check (use only when sure the "
             "phase has on-disk work despite no phase_started event).",
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

    p_watch = sub.add_parser(
        "watch",
        help="Stream state-machine events for one plan, one project, "
             "or every registered plan. One line per transition; "
             "designed for AI-agent consumption via Claude's Monitor.",
    )
    p_watch.add_argument("--project", type=Path, default=None)
    _watch_scope = p_watch.add_mutually_exclusive_group()
    _watch_scope.add_argument("--plan", default=None, dest="watch_plan")
    _watch_scope.add_argument(
        "--all", action="store_true", default=False, dest="watch_all",
    )
    p_watch.add_argument("--json", action="store_true", default=False)
    p_watch.add_argument("--verbose", action="store_true", default=False)
    p_watch.add_argument(
        "--task-list", action="store_true", default=False,
        dest="watch_task_list",
        help="Emit TASK_CREATE/TASK_UPDATE protocol lines for "
             "Claude's TaskCreate UI (mutex with --json and --all). "
             "See docs/operations.md § 'Task-list mode'.",
    )
    p_watch.add_argument(
        "--interval", type=float, default=None,
        help="Poll interval seconds (default: 1.0 single-project, 5.0 with --all)",
    )

    p_notify_test = sub.add_parser(
        "notify-test",
        help="Send a test notification through configured channels and report "
             "per-channel status. Useful for verifying credentials after setup.",
    )
    p_notify_test.add_argument(
        "--project", type=Path, default=None,
        help="Project root (contains .orchestrator.json). Defaults to cwd.",
    )
    p_notify_test.add_argument(
        "--channel", default=None, metavar="KIND",
        help="Test a specific channel kind only (e.g. imessage, discord).",
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

    p_verify = sub.add_parser(
        "verify",
        help="Run the project verify command and stamp attestations.verify on success",
    )
    add_common(p_verify)
    p_verify.add_argument(
        "--phase", default="", help="Phase id (required when passing --token)",
    )
    p_verify.add_argument(
        "--token", default="", help="Worker claim token (optional; operator omits)",
    )

    p_attest = sub.add_parser(
        "attest",
        help=(
            "Attest that a quality pass ran on the current claim. "
            "Stamps current HEAD as the attested commit. "
            "Use --simplify after running /simplify; additional flavors land here."
        ),
    )
    add_common(p_attest)
    p_attest.add_argument(
        "--phase", required=True, help="Phase id (must match the live claim)",
    )
    p_attest.add_argument(
        "--token", required=True, help="Worker claim token",
    )
    p_attest.add_argument(
        "--simplify", action="store_true",
        help="Attest that /simplify was run on this phase's diff",
    )

    args = parser.parse_args(argv)
    if getattr(args, "no_notify", False):
        notify.set_global_suppress(True)
        print("notify: suppressed via --no-notify", file=sys.stderr)
    # Host-scoped commands skip the per-plan ProjectConfig load (which
    # requires --project). Bare `clu` is the fleet view; `clu list` is the
    # name-only listing kept for scripting.
    if args.cmd is None:
        return cmd_fleet(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "tick-all":
        return cmd_tick_all(args)
    if args.cmd == "tick":
        return cmd_tick(args)
    if args.cmd == "install-skill":
        return cmd_install_skill(args)
    if args.cmd == "install-hook":
        return cmd_install_hook(args)
    if args.cmd == "uninstall-hook":
        return cmd_uninstall_hook(args)
    if args.cmd == "queue":
        return cmd_queue(args)
    if args.cmd == "worktree":
        return cmd_worktree(args)
    # `unregister` needs to handle --all-archived (no single project/plan)
    # alongside the per-plan path; the dispatcher branches inside.
    if args.cmd == "unregister":
        return cmd_unregister(args)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "archive":
        return cmd_archive(args)
    if args.cmd == "blockers":
        return cmd_blockers(args)
    if args.cmd == "watch":
        return cmd_watch(args)
    if args.cmd == "notify-test":
        return cmd_notify_test(args)
    if args.cmd == "answer":
        return cmd_answer(args)
    if args.cmd == "integrate":
        return cmd_integrate(args)

    try:
        st.validate_slug(args.plan, kind="plan slug")
        cfg = load_project_config(_resolve_project_arg(args))
        state_path = cfg.state_path(args.plan)
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))

    dispatchers = {
        "init": cmd_init,
        "status": cmd_status,
        "spawn": cmd_spawn,
        "complete": cmd_complete,
        "force-complete": cmd_force_complete,
        "block": cmd_block,
        "task-done": cmd_task_done,
        "heartbeat": cmd_heartbeat,
        "register": cmd_register,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "retry": cmd_retry,
        "extend-lease": cmd_extend_lease,
        "release-claim": cmd_release_claim,
        "prior-blocker": cmd_prior_blocker,
        "logs": cmd_logs,
        "verify": cmd_verify,
        "attest": cmd_attest,
    }
    return dispatchers[args.cmd](args, cfg, state_path)


def _resolve_ref(project_root: Path, ref: str) -> str | None:
    """Resolve `ref` to a commit SHA via `git rev-parse`, or None if unknown.

    `_verify_commit_shas` (used by `cmd_complete`) only handles raw SHAs via
    `cat-file -e`; symbolic refs like `HEAD` or `main` need rev-parse with
    the `^{commit}` peel to fail cleanly on tags-without-commits.
    """
    result = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--verify",
         f"{ref}^{{commit}}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _remove_worktree_and_branch(
    project_root: Path,
    path: str,
    branch: str,
    *,
    delete_branch: bool = True,
    timeout: int | None = None,
) -> tuple[tuple[bool, str], tuple[bool, str]]:
    """Run `git worktree remove --force <path>` and optionally drop the
    branch with `git branch -D <branch>`.

    Best-effort: never raises on git failure. Returns
    `((worktree_ok, worktree_stderr), (branch_ok, branch_stderr))` so
    callers decide whether to log, event, or ignore the outcome. When
    `delete_branch=False`, the branch tuple is `(True, "")` so consumers
    don't need to special-case the skip.
    """
    wt = subprocess.run(
        ["git", "-C", str(project_root), "worktree", "remove",
         "--force", path],
        capture_output=True, text=True, timeout=timeout,
    )
    wt_pair = (wt.returncode == 0, wt.stderr.strip())
    if not delete_branch:
        return wt_pair, (True, "")
    br = subprocess.run(
        ["git", "-C", str(project_root), "branch", "-D", branch],
        capture_output=True, text=True, timeout=timeout,
    )
    return wt_pair, (br.returncode == 0, br.stderr.strip())


def _resolve_default_branch(project_root: Path) -> str | None:
    """Read `origin`'s default branch name (e.g. 'main', 'master').

    Uses `git symbolic-ref refs/remotes/origin/HEAD`; returns None when
    the remote isn't configured or HEAD isn't set, so callers can fall
    back to retain-and-warn rather than guess.
    """
    result = subprocess.run(
        ["git", "-C", str(project_root), "symbolic-ref",
         "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    ref = result.stdout.strip()
    return ref.rsplit("/", 1)[-1] if "/" in ref else None


def _is_branch_reachable_from_origin(
    project_root: Path, branch: str,
) -> tuple[bool, str]:
    """True iff every commit on `branch` is reachable from
    `origin/<default-branch>`, OR no origin is configured.

    Returns `(reachable, reason)`. When there's no origin remote there's
    no upstream to be "ahead of" — the operator's local state IS the
    only state, so cleanup is safe; the reason field documents the
    skip ("no origin remote configured") and the caller can surface it
    in the audit event. Only returns False when an origin exists AND
    the branch has commits not on origin/<default>.
    """
    default = _resolve_default_branch(project_root)
    if default is None:
        return True, "no origin remote configured"
    result = subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor",
         branch, f"origin/{default}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, ""
    if result.returncode == 1:
        return False, f"branch ahead of origin/{default}"
    return False, f"git merge-base failed: {result.stderr.strip() or 'unknown'}"


def _commits_ahead_of_origin(
    project_root: Path, branch: str,
) -> list[str]:
    """Short SHAs of commits on `branch` not on origin/<default>.

    Empty list when there are no unreachable commits or when the lookup
    fails — callers use this only for diagnostic context on the retain
    event, so a best-effort empty list is fine.
    """
    default = _resolve_default_branch(project_root)
    if default is None:
        return []
    result = subprocess.run(
        ["git", "-C", str(project_root), "log", "--format=%h",
         f"origin/{default}..{branch}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _maybe_cleanup_worktree(
    cfg: ProjectConfig,
    data: dict,
    *,
    trigger: str,
    require_all_phases_done: bool,
) -> None:
    """Best-effort worktree cleanup at plan end. Mutates `data` in place.

    Used by `cmd_complete` (with `require_all_phases_done=True` so
    interim completions don't yank the worktree out from under pending
    phases) and `cmd_archive` (with `False` — the operator explicitly
    asked for cleanup). On success: removes worktree + branch and
    clears `state.worktree`, appending `EVENT_WORKTREE_CLEANED`. On
    "branch ahead of origin": leaves both in place and appends
    `EVENT_WORKTREE_RETAINED_AHEAD` with the unreachable SHAs.
    """
    wt = st.get_worktree(data)
    if wt is None:
        return
    if require_all_phases_done:
        plan_path = cfg.project_root / cfg.plan_dir / f"{data['plan_slug']}.md"
        try:
            phases = parse_sessions_index(plan_path)
        except (OSError, ValueError):
            return
        completed = st.completed_phase_ids(data)
        if not phases or not all(p.id in completed for p in phases):
            return
    reachable, reason = _is_branch_reachable_from_origin(
        cfg.project_root, wt["branch"],
    )
    if not reachable:
        ahead = _commits_ahead_of_origin(cfg.project_root, wt["branch"])
        st.append_event(
            data, st.EVENT_WORKTREE_RETAINED_AHEAD,
            path=wt["path"], branch=wt["branch"],
            reason=reason, ahead_commits=ahead, trigger=trigger,
        )
        return
    (wt_ok, wt_err), (br_ok, br_err) = _remove_worktree_and_branch(
        cfg.project_root, wt["path"], wt["branch"], timeout=30,
    )
    st.append_event(
        data, st.EVENT_WORKTREE_CLEANED,
        path=wt["path"], branch=wt["branch"],
        worktree_removed=wt_ok, branch_removed=br_ok,
        worktree_error=wt_err, branch_error=br_err,
        trigger=trigger,
    )
    data["worktree"] = None


def _rollback_worktree(project_root: Path, record: dict) -> None:
    """Tear down a worktree + branch created by `_setup_worktree`.

    Best-effort: swallows git errors so the caller's primary error path
    (state save failure) surfaces cleanly. Operator can mop up via
    `clu worktree gc` or `git worktree prune` if a step fails.
    """
    _remove_worktree_and_branch(project_root, record["path"], record["branch"])


def _setup_worktree(args, cfg: ProjectConfig) -> dict | int:
    """Materialize the worktree + branch and return the record dict.

    Returns an int rc (via `_die`) on any precondition or git failure;
    callers check `isinstance(result, int)`. On success the worktree is
    on disk and the caller is responsible for rolling it back if a
    downstream step (state save) fails.

    The persisted `base_ref` is the resolved commit SHA, not the symbolic
    ref the operator passed — freezes the fork point unambiguously.
    """
    # Project must be a git repo.
    git_check = subprocess.run(
        ["git", "-C", str(cfg.project_root), "rev-parse", "--git-dir"],
        capture_output=True, text=True,
    )
    if git_check.returncode != 0:
        return _die(
            ExitCode.WORKTREE_SETUP_FAILED,
            f"--worktree requires a git repository at {cfg.project_root}",
        )

    if args.worktree is True:
        worktree_path = (
            cfg.project_root.parent / f"{cfg.project_root.name}-{args.plan}"
        )
    else:
        worktree_path = Path(args.worktree).expanduser()

    branch = args.branch if args.branch else f"clu/{args.plan}"
    base_ref_input = args.base_ref if args.base_ref else "HEAD"

    base_sha = _resolve_ref(cfg.project_root, base_ref_input)
    if base_sha is None:
        return _die(
            ExitCode.WORKTREE_SETUP_FAILED,
            f"base ref {base_ref_input!r} could not be resolved in "
            f"{cfg.project_root}",
        )

    branch_check = subprocess.run(
        ["git", "-C", str(cfg.project_root), "rev-parse", "--verify",
         f"refs/heads/{branch}"],
        capture_output=True, text=True,
    )
    if branch_check.returncode == 0:
        return _die(
            ExitCode.WORKTREE_SETUP_FAILED,
            f"branch {branch!r} already exists; pass --branch <new> or "
            f"delete the old branch first",
        )

    if worktree_path.exists():
        return _die(
            ExitCode.WORKTREE_SETUP_FAILED,
            f"worktree path already exists: {worktree_path}",
        )

    add_result = subprocess.run(
        ["git", "-C", str(cfg.project_root), "worktree", "add",
         "-b", branch, str(worktree_path), base_sha],
        capture_output=True, text=True,
    )
    if add_result.returncode != 0:
        return _die(
            ExitCode.WORKTREE_SETUP_FAILED,
            f"git worktree add failed: {add_result.stderr.strip()}",
        )

    # Echo provenance to stderr — `--base-ref` errors silently if it
    # resolves to the wrong commit (e.g. stale local branch), so the
    # operator sees both the symbolic ref and the SHA they actually got.
    print(
        f"Worktree at {worktree_path}\n"
        f"  Branch: {branch}\n"
        f"  Base:   {base_ref_input} → {base_sha}",
        file=sys.stderr,
    )

    return {
        "path": str(worktree_path),
        "branch": branch,
        "base_ref": base_sha,
    }


def _is_plan_active(state: dict) -> bool:
    """True iff a plan is currently advancing — claim in flight or running.

    Done / halted / paused plans aren't writing, so they don't conflict
    regardless of worktree status. Inverse of `state.TERMINAL_STATUSES`
    plus the current-claim short-circuit (a claim on a non-terminal status
    is the standard advancing case).
    """
    if state.get("current_claim"):
        return True
    return state.get("status") == st.STATUS_RUNNING


def _active_no_worktree_siblings(
    project_root: Path, exclude_slug: str | None = None,
) -> list[str]:
    """Slugs of plans in `project_root` that are active AND lack a worktree."""
    cfg = load_project_config(project_root)
    return [
        p.slug for p in cross_plan_rules.load_plans_for_project(project_root, cfg)
        if p.slug != exclude_slug
        and not st.get_worktree(p.state)
        and _is_plan_active(p.state)
    ]


def _maybe_print_worktree_conflict_hint(
    project_root: Path, plan_slug: str, has_worktree: bool,
) -> None:
    """One-shot init-time stderr hint when sibling active plans lack a worktree.

    Only fires when the new plan ALSO lacks one — operator who explicitly
    passed `--worktree` already opted into isolation and doesn't need the
    nudge. Silent when no active siblings exist.
    """
    if has_worktree:
        return
    siblings = _active_no_worktree_siblings(project_root, exclude_slug=plan_slug)
    if not siblings:
        return
    sibling_str = ", ".join(siblings)
    print(
        f"hint: {plan_slug} and active sibling(s) [{sibling_str}] both run "
        f"against {project_root}. Concurrent ticks may clobber each "
        f"other's working tree — rerun init with `--worktree` if you want "
        f"isolation.",
        file=sys.stderr,
    )


def cmd_init(args, cfg: ProjectConfig, state_path: Path) -> int:
    for attr, label in [
        ("lease_ttl_minutes", "--lease-ttl-minutes"),
        ("stalled_heartbeat_minutes", "--stalled-heartbeat-minutes"),
        ("max_attempts_per_phase", "--max-attempts-per-phase"),
    ]:
        val = getattr(args, attr, None)
        if val is not None and val <= 0:
            return _die(
                ExitCode.INVALID_VALUE,
                f"{label} must be a positive integer, got {val}",
            )

    worktree_record: dict | None = None
    if args.worktree is not False:
        result = _setup_worktree(args, cfg)
        if isinstance(result, int):
            return result
        worktree_record = result

    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with st.locked(state_path):
            # Re-check existence INSIDE the lock to defeat concurrent inits.
            if state_path.exists():
                print(f"State already exists: {state_path}", file=sys.stderr)
                if worktree_record is not None:
                    _rollback_worktree(cfg.project_root, worktree_record)
                return 1
            data = st.empty_state(args.plan, cfg.plan_dir)
            for key in (
                "lease_ttl_minutes",
                "stalled_heartbeat_minutes",
                "max_attempts_per_phase",
            ):
                val = getattr(args, key, None)
                if val is not None:
                    data["config"][key] = val
            plan_path = cfg.project_root / cfg.plan_dir / f"{args.plan}.md"
            try:
                phases = parse_sessions_index(plan_path)
            except FileNotFoundError:
                phases = []
            if phases:
                global_default = data["config"]["lease_ttl_minutes"]
                scale = cfg.lease_ttl_scale
                phase_records = []
                for phase in phases:
                    record: dict = {"id": phase.id}
                    effort_minutes = parse_effort_minutes(phase.effort)
                    if effort_minutes is not None:
                        record["lease_ttl_minutes"] = max(
                            global_default, round(effort_minutes * scale)
                        )
                    phase_records.append(record)
                data["phases"] = phase_records
            if worktree_record is not None:
                data["worktree"] = worktree_record
            st.save_atomic(state_path, data)
    except Exception:
        # save_atomic / lock failure → tear down the worktree we just made.
        # WORKTREE_SETUP_FAILED is the operator-facing receipt; the raised
        # exception's traceback still surfaces for debugging.
        if worktree_record is not None:
            _rollback_worktree(cfg.project_root, worktree_record)
        raise
    # Auto-register so fleet view / inbound routing can find the plan
    # without a separate setup step.
    registry.register(cfg.project_root, args.plan)
    print(f"Initialized {state_path}")
    _print_worker_model(cfg)
    _maybe_print_worktree_conflict_hint(
        cfg.project_root, args.plan, worktree_record is not None,
    )
    _maybe_handle_claude_md_injection(cfg, args)
    _maybe_handle_notify_prompts(cfg, args)
    _maybe_print_monitor_tip()
    _maybe_print_watch_tip(
        scope="plan", slug=args.plan, quiet=getattr(args, "quiet", False),
    )
    return 0


def cmd_register(args, cfg: ProjectConfig, state_path: Path) -> int:
    added = registry.register(cfg.project_root, args.plan)
    msg = "Registered" if added else "Already registered"
    print(f"{msg}: {cfg.project_root}  →  {args.plan}")
    return 0


def cmd_unregister(args) -> int:
    if args.all_archived:
        return cmd_unregister_all_archived(args)
    return cmd_unregister_one(args)


def cmd_unregister_one(args) -> int:
    # --project / --plan are parse-time optional so --all-archived can
    # forbid them; the per-plan path validates them here.
    if args.project is None or args.plan is None:
        return _die(
            ExitCode.GENERIC,
            "unregister requires --project and --plan "
            "(or --all-archived for batch ghost cleanup)",
        )
    try:
        st.validate_slug(args.plan, kind="plan slug")
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))
    cfg = load_project_config(args.project)
    removed = registry.unregister(cfg.project_root, args.plan)
    msg = "Unregistered" if removed else "Not in registry"
    print(f"{msg}: {cfg.project_root}  →  {args.plan}")
    return ExitCode.OK


def cmd_unregister_all_archived(args) -> int:
    if args.plan is not None:
        return _die(
            ExitCode.GENERIC,
            "--all-archived is mutually exclusive with --plan",
        )

    to_remove: list[tuple[str, str]] = []
    skipped: list[tuple[str, str, str]] = []
    # (proj, slug, wt_path, branch) — branch may be custom (`--branch` at init).
    orphan_worktrees: list[tuple[str, str, str, str]] = []
    for entry in registry.entries():
        proj = Path(entry.project_root)
        try:
            cfg = load_project_config(proj)
        except (OSError, ValueError) as exc:
            # Project dir gone → archived. Dir present but config
            # unreadable → surface for operator review, don't auto-remove.
            if not proj.exists():
                to_remove.append((entry.project_root, entry.plan_slug))
            else:
                skipped.append(
                    (entry.project_root, entry.plan_slug, str(exc))
                )
            continue
        if not cfg.master_plan_path(entry.plan_slug).exists():
            to_remove.append((entry.project_root, entry.plan_slug))
            # Best-effort: if the state file lingered with a worktree
            # record, surface the path so the operator can `clu worktree
            # gc` (or `git worktree remove`) before the dir orphans on disk.
            state = registry.load_entry_state(entry)
            if state and (wt := st.get_worktree(state)):
                orphan_worktrees.append(
                    (entry.project_root, entry.plan_slug,
                     wt["path"], wt["branch"])
                )

    if not to_remove:
        print("(nothing to unregister)")
    else:
        if args.dry_run:
            print("Would unregister:")
        else:
            # Atomic batch removal under one _mutate window — operators see
            # one all-or-nothing transition, not a half-pruned registry.
            targets = {(p, s) for p, s in to_remove}
            with registry._mutate(registry.registry_path()) as data:
                data["plans"] = [
                    row for row in data["plans"]
                    if (row["project_root"], row["plan_slug"]) not in targets
                ]
            print(f"Unregistered {len(to_remove)} plans:")
        for proj_root, slug in to_remove:
            print(f"  {proj_root}  →  {slug}")

    for proj_root, slug, wt_path, branch in orphan_worktrees:
        # After this command's registry mutate, the entry is gone — so
        # `clu worktree gc` can't see the plan anymore (it walks the
        # registry). Direct git removal is the only path that works
        # both for dry-run AND post-unregister.
        print(
            f"warning: {proj_root} / {slug} had worktree at {wt_path}. "
            f"Clean up with `git -C {proj_root} worktree remove --force "
            f"{wt_path}` (and `git branch -D {branch}` if you also "
            f"want the branch dropped).",
            file=sys.stderr,
        )

    for proj_root, slug, reason in skipped:
        print(f"  skipped: {proj_root}  →  {slug}  ({reason})")
    return ExitCode.OK


def cmd_list(args) -> int:
    rows = registry.entries()
    if not rows:
        print("No plans registered. Run `clu init` or `clu register` to add one.")
        return 0
    for row in rows:
        # Worktree annotation is best-effort — a stale registry entry whose
        # state can't be loaded falls back to the plain (slug, root) line.
        state = registry.load_entry_state(row)
        wt_marker = ""
        if state is not None and st.get_worktree(state):
            wt_marker = "  (worktree)"
        print(f"  {row.plan_slug:<30}  {row.project_root}{wt_marker}")
    return 0


def cmd_fleet(args) -> int:
    entries = registry.entries()
    print(fleet.render(entries), end="")
    footer = _queue_footer(entries)
    if footer:
        print(footer)
    return 0


_QUEUE_LOAD_ERRORS = (
    json.JSONDecodeError, st.SchemaVersionMismatch, KeyError, OSError,
)


def _queue_footer(entries) -> str | None:
    """One-line summary of pending queue work across all distinct projects.

    Iterates distinct project_roots (registry.entries() may yield multiple
    rows per project), loads each queue.json best-effort, and renders a
    single line. Returns None when no project has pending work and no queue
    is unreadable — keeps the fleet view quiet in the steady state.
    """
    counts: list[tuple[Path, int]] = []
    unreadable: list[Path] = []
    seen: set[Path] = set()
    for entry in entries:
        try:
            root = Path(entry.project_root).resolve()
        except OSError:
            continue
        if root in seen:
            continue
        seen.add(root)
        try:
            cfg = load_project_config(root)
        except (OSError, ValueError):
            continue
        qp = cfg.queue_path()
        if not qp.exists():
            continue
        try:
            data = queue.load(qp)
        except _QUEUE_LOAD_ERRORS:
            unreadable.append(root)
            continue
        if data.get("queue"):
            counts.append((root, len(data["queue"])))

    if not counts and not unreadable:
        return None

    parts: list[str] = []
    if len(counts) == 1:
        root, n = counts[0]
        parts.append(f"queue: {n} pending in {root} — see `clu queue list`")
    elif counts:
        total = sum(n for _, n in counts)
        parts.append(
            f"queue: {total} pending across {len(counts)} projects "
            "— see `clu queue list --project <P>`"
        )
    if unreadable:
        suffix = "s" if len(unreadable) > 1 else ""
        parts.append(f"{len(unreadable)} queue file{suffix} unreadable")
    return "(" + "; ".join(parts) + ")"


def _refuse_on_corrupt_queue(queue_path: Path, exc: Exception) -> int:
    """Operator-at-keyboard refusal path. Surfaces backup paths and a
    paste-into-Claude instruction; the auto-repair pipeline only runs
    from cmd_tick_all (phase `repair`), never from the CLI."""
    backups = sorted(
        queue_path.parent.glob(f"{queue_path.name}.corrupt-*"), reverse=True,
    )
    lines = [
        f"queue.json corrupt at {queue_path}:",
        f"  {type(exc).__name__}: {exc}",
    ]
    if backups:
        head = f"Backup at {backups[0]}"
        if len(backups) > 1:
            head += f" (and {len(backups) - 1} older)"
        lines.append(head + ".")
    else:
        lines.append("No backup files found.")
    lines.append("Open Claude in this project to repair.")
    return _die(ExitCode.GENERIC, "\n".join(lines))


BUNDLED_SKILLS = ("brainstorm", "clu-monitor", "clu-phase", "clu-plan", "clu-reply", "plan")

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

    if args.list:
        targets = [
            (name, Path.home() / ".claude" / "skills" / name / "SKILL.md")
            for name in BUNDLED_SKILLS
        ]
        width = max(len(name) for name, _ in targets)
        print("Bundled skills available via clu install-skill:")
        for name, target in targets:
            print(f"  {name.ljust(width)}  {target}")
        return ExitCode.OK

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
        if target.parent.is_symlink():
            print(
                f"warning: {target.parent} is a symlink → "
                f"{target.parent.resolve()}; install-skill will write through",
                file=sys.stderr,
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


def _hook_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _resolve_hook_script_path() -> str:
    """Absolute on-disk path to the bundled UserPromptSubmit hook script.

    Resolved at install time and baked into `settings.json` so the hook
    keeps working even if `sys.executable` / the cwd change between
    install and trigger. `importlib.resources.files(...)` returns a
    Traversable; on a real install it's a concrete `Path`.
    """
    from importlib.resources import files
    return str(files("end_of_line").joinpath("hooks/clu_inbox_surface.py"))


def _hook_command(hook_path: str) -> str:
    """Shell command Claude Code runs each UserPromptSubmit.

    Use `sys.executable -u` so the python the operator's clu was
    installed under is the same python that imports `end_of_line`.
    `-u` keeps stdout unbuffered — Claude reads our JSON synchronously.
    """
    return f"{sys.executable} -u {hook_path}"


def _entry_command(entry: dict) -> str | None:
    """Pull the `command` string from a settings.json hook entry.

    Both shapes are valid:
      flat:   {"type": "command", "command": "..."}
      nested: {"matcher"?: ..., "hooks": [{"type": "command", "command": "..."}]}
    """
    if "command" in entry:
        return entry.get("command")
    inner = entry.get("hooks")
    if isinstance(inner, list) and inner:
        first = inner[0]
        if isinstance(first, dict):
            return first.get("command")
    return None


def _entry_mentions_hook_path(entry: dict, hook_path: str) -> bool:
    cmd = _entry_command(entry) or ""
    return hook_path in cmd


def _detect_nested_style(hooks: dict) -> bool:
    """True if any existing hook entry uses the nested-array shape.

    The operator's machine may carry a SessionStart entry in nested
    shape (the style the Claude Code docs lead with). Preserve it on
    fresh installs so settings.json reads as if the operator wrote
    every entry by hand. With no entries to learn from, default to the
    nested shape — it's the richer form and accepts a `timeout` field.
    """
    for event_entries in hooks.values():
        if not isinstance(event_entries, list):
            continue
        for entry in event_entries:
            if isinstance(entry, dict) and "hooks" in entry:
                return True
            if isinstance(entry, dict) and "command" in entry:
                return False
    return True


def _build_hook_entry(command: str, *, nested: bool) -> dict:
    if nested:
        return {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
    return {"type": "command", "command": command}


def cmd_install_hook(args) -> int:
    """Register the clu inbox surface hook in `~/.claude/settings.json`.

    Adds (or refreshes) a single UserPromptSubmit entry pointing at the
    bundled hook script. Idempotent on absolute hook_path. Refuses on
    malformed settings.json (don't try to repair). Writes the marker
    on success.
    """
    hook_path = _resolve_hook_script_path()
    command = _hook_command(hook_path)
    settings_path = _hook_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except json.JSONDecodeError as exc:
            return _die(
                ExitCode.GENERIC,
                f"settings.json malformed ({exc}); refusing to edit "
                f"{settings_path}. Fix the JSON manually and re-run.",
            )
    else:
        data = {}

    hooks = data.setdefault("hooks", {})
    ups = hooks.setdefault("UserPromptSubmit", [])
    nested = _detect_nested_style(hooks)

    already = any(_entry_mentions_hook_path(e, hook_path) for e in ups)
    if not already:
        ups.append(_build_hook_entry(command, nested=nested))
        tmp = settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, settings_path)
        print(f"Installed UserPromptSubmit hook → {hook_path}")
    else:
        print(f"Hook already installed at {hook_path}")
    print(f"Settings: {settings_path}")

    monitor.record_hook_installed(hook_path, str(settings_path))
    return ExitCode.OK


def cmd_uninstall_hook(args) -> int:
    """Remove the clu hook entry, leaving the operator's other hooks alone.

    Matches by absolute hook_path so unrelated UserPromptSubmit entries
    (the operator's own work) survive. Clears the marker on success.
    Idempotent — running on a host without the hook installed is OK.
    """
    settings_path = _hook_settings_path()
    if not settings_path.exists():
        monitor.clear_marker()
        return ExitCode.OK
    try:
        data = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        return _die(
            ExitCode.GENERIC,
            f"settings.json malformed; refusing to edit {settings_path}.",
        )

    hook_path = _resolve_hook_script_path()
    ups = data.get("hooks", {}).get("UserPromptSubmit", [])
    filtered = [
        e for e in ups if not _entry_mentions_hook_path(e, hook_path)
    ]
    if len(filtered) == len(ups):
        print("clu inbox hook not present in settings.json")
    else:
        data.setdefault("hooks", {})["UserPromptSubmit"] = filtered
        tmp = settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, settings_path)
        print(f"Uninstalled UserPromptSubmit hook ({hook_path})")
    monitor.clear_marker()
    return ExitCode.OK


_DOCTOR_PROBE_SCRIPT = (
    'echo "PATH=$PATH"; '
    'for b in gh pipx clu; do '
    'printf "%s = " "$b"; '
    'command -v "$b" || echo "NOT FOUND: $b"; '
    'done'
)


def cmd_doctor(args) -> int:
    """Smoke-test the worker subprocess env: print PATH + resolved binaries.

    Read-only: no state.json read or write, no registry mutation. Reuses
    `dispatch.build_worker_env` so what the operator sees here is byte-for-
    byte what a real worker would inherit. Refuses on a project without an
    `.orchestrator.json` — without one there's no override config to report,
    and the operator is asking about a project that isn't initialized.
    """
    cfg_path = args.project / CONFIG_FILENAME
    if not cfg_path.exists():
        return _die(
            ExitCode.UNKNOWN_TASK,
            f"no {CONFIG_FILENAME} at {cfg_path} — "
            f"run `clu init --project {args.project} --plan <slug>` first",
        )
    cfg = load_project_config(args.project)
    env = dispatch.build_worker_env(cfg)
    source = "dispatch.path" if env is not None else "inherited"
    probe_env = env if env is not None else dict(os.environ)
    # Hardcode /bin/sh so an empty-ish dispatch.path can't make this probe
    # itself fail to spawn — matches how `shell=True` in dispatch_for_tick
    # resolves the shell independently of the env we pass.
    result = subprocess.run(
        ["/bin/sh", "-c", _DOCTOR_PROBE_SCRIPT],
        env=probe_env, capture_output=True, text=True,
    )
    print("Worker subprocess will see:")
    for line in result.stdout.splitlines():
        print(f"  {line}")
    print(f"  (source: {source})")

    _print_notify_health(cfg)
    _print_effort_health(cfg)
    if getattr(args, "worktree", False):
        _print_worktree_health(cfg)
    return ExitCode.OK


def _print_notify_health(cfg: ProjectConfig) -> None:
    """For each iMessage channel, report self-chat resolution status.

    Auto-resolve looks up the operator's unique self-chat in chat.db;
    a `self_chat_id` param on the channel overrides the lookup. Reports
    the resolved chat_identifier or the `SelfChatLookupError` message
    pointing at the override path.
    """
    imessage_channels = [
        c for c in cfg.notify.channels if c.kind == "imessage" and c.enabled
    ]
    if not imessage_channels:
        return
    print("\nNotify channels:")
    from .notify_imessage_inbound import (
        DEFAULT_CHAT_DB,
        SelfChatLookupError,
        _resolve_self_chat_id,
        open_chat_db,
    )
    needs_chatdb = any(
        c.params.get("self_chat_id") is None for c in imessage_channels
    )
    conn = None
    if needs_chatdb:
        try:
            conn = open_chat_db(DEFAULT_CHAT_DB)
        except Exception as exc:
            print(f"  iMessage: chat.db inaccessible ({DEFAULT_CHAT_DB}) — {exc}")
            return
    for ch in imessage_channels:
        to = ch.params.get("to", "<no handle>")
        override = ch.params.get("self_chat_id")
        try:
            resolved = _resolve_self_chat_id(
                conn, operator_handle=to, override=override,
            )
        except SelfChatLookupError as exc:
            print(f"  iMessage[to={to}]: {exc}")
            continue
        source = "override" if override else "auto-resolved"
        print(f"  iMessage[to={to}]: self_chat={resolved} ({source})")


def _print_effort_health(cfg: ProjectConfig) -> None:
    """Warn about phases with non-empty but unparseable Effort cells.

    Empty Effort is fine (plan pre-dates the convention); only non-empty
    cells that fail parse_effort_minutes are surfaced — those will fall
    back to the global default and silently get a shorter lease than intended.
    Plan-read failures are silently skipped — this is advisory, not hard.
    """
    project_root = cfg.project_root.resolve()
    malformed: list[tuple[str, str, str]] = []
    for p in cross_plan_rules.load_plans_for_project(project_root, cfg):
        plan_file = cfg.master_plan_path(p.slug)
        try:
            phases = parse_sessions_index(plan_file)
        except Exception:
            continue
        for phase in phases:
            if phase.effort.strip() and parse_effort_minutes(phase.effort) is None:
                malformed.append((p.slug, phase.id, phase.effort))
    if malformed:
        print("\n[warn] Malformed Effort cells (lease will fall back to default):")
        for plan_slug, phase_id, raw in malformed:
            print(f"  {plan_slug}:{phase_id}  Effort={raw}")


def _print_worktree_health(cfg: ProjectConfig) -> None:
    """Walk every plan in the project, report worktree-path liveness.

    `ok` = stat'd + `git rev-parse --git-dir` succeeded — what the
    dispatcher's pre-Popen gate checks. `MISSING` covers both
    deleted-dir and `git worktree prune` cases. Plans without a
    worktree record print as `(none)`.
    """
    project_root = cfg.project_root.resolve()
    print("\nWorktrees:")
    rows: list[tuple[str, str, str]] = []
    for p in cross_plan_rules.load_plans_for_project(project_root, cfg):
        wt = st.get_worktree(p.state)
        if wt is None:
            rows.append((p.slug, "(none)", "-"))
            continue
        ok = dispatch.worktree_alive(Path(wt["path"]))
        rows.append((p.slug, wt["path"], "ok" if ok else "MISSING"))
    if not rows:
        print("  (no registered plans in this project)")
        return
    width = max(len(slug) for slug, _, _ in rows)
    for slug, path, status in rows:
        print(f"  {slug:<{width}}  {status:<8}  {path}")


def cmd_queue(args) -> int:
    # Bare `clu queue` defaults to `list` — mirrors bare `clu` → fleet view.
    # Argparse routes a missing subcommand through here with queue_cmd=None.
    if args.queue_cmd is None or args.queue_cmd == "list":
        return cmd_queue_list(args)
    if args.queue_cmd == "add":
        return cmd_queue_add(args)
    if args.queue_cmd == "remove":
        return cmd_queue_remove(args)
    print(
        "usage: clu queue {add|list|remove} [--project PATH]",
        file=sys.stderr,
    )
    return _die(ExitCode.GENERIC, f"unknown queue subcommand {args.queue_cmd!r}")


def _slug_is_running(slug: str, cfg: ProjectConfig) -> bool:
    """True when slug has a live current_claim in the project registry."""
    project_str = str(cfg.project_root)
    for entry in registry.entries():
        if entry.plan_slug == slug and entry.project_root == project_str:
            state_data = registry.load_entry_state(entry)
            if state_data and state_data.get("current_claim"):
                return True
    return False


@_translate_claim_mismatch
def _cmd_queue_add_worker(args) -> int:
    slug = args.slugs[0]
    try:
        st.validate_slug(slug, kind="plan slug")
        st.validate_slug(args.source_plan, kind="plan slug")
        st.validate_slug(args.source_phase, kind="phase id")
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))

    cfg = load_project_config(_resolve_project_arg(args))
    source_state_path = cfg.state_path(args.source_plan)
    if not source_state_path.exists():
        return _die(ExitCode.UNKNOWN_TASK, f"no state for plan {args.source_plan!r}")

    plan_file = cfg.project_root / cfg.plan_dir / f"{slug}.md"
    token_fp = hashlib.sha256(args.token.encode()).hexdigest()[:8]
    queue_path = cfg.queue_path()

    # Lock order: state lock outer, queue lock inner.
    with st.mutate(source_state_path) as state_data:
        st.assert_claim_match(state_data, args.token, args.source_phase)

        # Plan-file existence check inside state lock so rejection event is atomic.
        if not plan_file.exists():
            st.append_event(
                state_data, st.EVENT_QUEUE_REJECTED,
                slug=slug, source_phase=args.source_phase, reason="missing_plan_file",
            )
            return _die(ExitCode.UNKNOWN_TASK, f"no plan file at {plan_file}")

        cap = state_data["config"].get(
            "max_queue_adds_per_phase", st.DEFAULT_MAX_QUEUE_ADDS_PER_PHASE
        )

        with queue.mutate(queue_path) as qdata:
            # Cap: count source-tagged entries across pending + history.
            existing_count = sum(
                1 for e in qdata["queue"] + qdata["history"]
                if e.get("source_plan") == args.source_plan
                and e.get("source_phase") == args.source_phase
            )
            if existing_count >= cap:
                st.append_event(
                    state_data, st.EVENT_QUEUE_REJECTED,
                    slug=slug, source_phase=args.source_phase, reason="cap",
                )
                return _die(
                    ExitCode.QUEUE_CAP,
                    f"phase {args.source_phase!r} hit queue cap of {cap}",
                )

            # Idempotency: pending slug (check first — active intent wins over history).
            pending = {e["slug"]: i + 1 for i, e in enumerate(qdata["queue"])}
            if slug in pending:
                print(f"already queued: {slug} (position {pending[slug]})")
                return ExitCode.OK

            # Idempotency: running slug (popped, in-flight).
            if _slug_is_running(slug, cfg):
                print(f"already queued: {slug} (running)")
                return ExitCode.OK

            # Idempotency: done slug (in history → error).
            if slug in {e["slug"] for e in qdata["history"]}:
                return _die(
                    ExitCode.STATUS_TRANSITION,
                    f"{slug!r} already ran in this queue; "
                    "remove from history or pick a different slug",
                )

            qdata["queue"].append({
                "slug": slug,
                "added_at": st.utcnow(),
                "added_by": "worker",
                "position_at_add": "tail",
                "source_plan": args.source_plan,
                "source_phase": args.source_phase,
                "source_token_fp": token_fp,
                "reason": args.reason,
            })
            pos = len(qdata["queue"])

        event_fields: dict = {
            "slug": slug,
            "source_phase": args.source_phase,
            "token_fp": token_fp,
        }
        if args.reason is not None:
            event_fields["reason"] = args.reason
        st.append_event(state_data, st.EVENT_QUEUE_APPENDED, **event_fields)
    print(f"queued at position {pos}")
    return ExitCode.OK


def cmd_queue_add(args) -> int:
    if args.token is not None:
        if args.source_plan is None or args.source_phase is None:
            return _die(ExitCode.GENERIC, "--token requires --plan and --phase")
        if args.front:
            return _die(ExitCode.GENERIC,
                        "--front is operator-only (forbidden with --token)")
        if args.batch is not None:
            return _die(ExitCode.GENERIC, "--batch is operator-only")
        if len(args.slugs) != 1:
            return _die(ExitCode.GENERIC, "--token requires a single slug")
        return _cmd_queue_add_worker(args)
    if args.source_plan is not None or args.source_phase is not None:
        return _die(ExitCode.GENERIC,
                    "--plan/--phase require --token (worker mode only)")

    slugs = list(args.slugs)

    # Slug regex first — cheapest validation, do it for all.
    for slug in slugs:
        try:
            st.validate_slug(slug, kind="plan slug")
        except st.InvalidSlug as exc:
            return _die(ExitCode.INVALID_SLUG, str(exc))

    batch_id = args.batch
    if batch_id is not None:
        try:
            st.validate_slug(batch_id, kind="batch id")
        except st.InvalidSlug as exc:
            return _die(ExitCode.INVALID_SLUG, str(exc))

    # Within-batch duplicates — reject before touching disk.
    seen: set[str] = set()
    for slug in slugs:
        if slug in seen:
            return _die(
                ExitCode.STATUS_TRANSITION,
                f"duplicate slug {slug!r} in batch",
            )
        seen.add(slug)

    cfg = load_project_config(_resolve_project_arg(args))

    registered_roots = {Path(e.project_root).resolve() for e in registry.entries()}
    if cfg.project_root not in registered_roots:
        return _die(
            ExitCode.GENERIC,
            f"project {cfg.project_root} has no registered plans; "
            f"run `clu init --project {cfg.project_root} --plan <slug>` first",
        )

    # Plan-file existence — all must exist before any mutation.
    for slug in slugs:
        plan_file = cfg.project_root / cfg.plan_dir / f"{slug}.md"
        if not plan_file.exists():
            return _die(ExitCode.UNKNOWN_TASK, f"no plan file at {plan_file}")

    queue_path = cfg.queue_path()
    if queue_path.exists():
        try:
            queue.load(queue_path)
        except _QUEUE_LOAD_ERRORS as exc:
            return _refuse_on_corrupt_queue(queue_path, exc)

    # Single mutation window — atomic from cron's POV. Early-return on a
    # pre-existing duplicate is safe: data is untouched, so locked_json's
    # post-yield save_atomic just rewrites the same bytes.
    positions: list[int] = []
    with queue.mutate(queue_path) as data:
        existing_by_slug = {
            entry["slug"]: i + 1 for i, entry in enumerate(data["queue"])
        }
        for slug in slugs:
            if slug in existing_by_slug:
                return _die(
                    ExitCode.STATUS_TRANSITION,
                    f"{slug!r} already queued at position "
                    f"{existing_by_slug[slug]}; "
                    f"`clu queue remove {slug}` first to re-order",
                )
        entries = [
            {
                "slug": slug,
                "added_at": st.utcnow(),
                "added_by": "operator",
                "position_at_add": "front" if args.front else "tail",
                "source_plan": None,
                "source_phase": None,
                "source_token_fp": None,
                "reason": args.reason,
                "batch_id": batch_id,
            }
            for slug in slugs
        ]
        if args.front:
            data["queue"][0:0] = entries
            positions = list(range(1, len(entries) + 1))
        else:
            start = len(data["queue"]) + 1
            data["queue"].extend(entries)
            positions = list(range(start, start + len(entries)))

    _spawn_post_action_tick(cfg)
    for pos in positions:
        print(f"queued at position {pos}")
    if len(slugs) > 1:
        print(f"queued {len(slugs)} plans")
    _print_worker_model(cfg)
    _maybe_print_monitor_tip()
    _maybe_print_watch_tip(scope="all", quiet=getattr(args, "quiet", False))
    return ExitCode.OK


_FREEZE_STATUSES = frozenset({
    st.STATUS_HALTED, st.STATUS_HALTED_REPLAN, st.STATUS_PAUSED,
})


def _project_state_status(state: dict) -> str:
    """Project a loaded state.json into the one-word STATUS column label."""
    claim = state.get("current_claim")
    threshold = state.get("config", {}).get(
        "stalled_heartbeat_minutes", st.DEFAULT_STALLED_HEARTBEAT_MIN,
    )
    if claim and st.is_claim_stalled(claim, threshold):
        return st.STATUS_STALLED
    return state["status"]


def _queue_row(
    slug: str, cfg: ProjectConfig, reg_states: dict,
    *, is_head: bool, head_frozen: bool,
) -> tuple[str, str]:
    """Compute (STATUS, NOTE) for one pending queue entry.

    NOTE precedence: head-freeze marker beats everything (it's the most
    actionable signal). Otherwise: missing plan file > path hint for an
    unregistered slug > empty (the row's already speaking through STATUS).
    """
    plan_file = cfg.project_root / cfg.plan_dir / f"{slug}.md"
    state = reg_states.get(slug)
    status = _project_state_status(state) if state else "queued"

    if is_head and head_frozen:
        note = "chain frozen at head"
    elif not plan_file.exists():
        note = "plan file missing"
    elif state is None:
        note = str(plan_file.relative_to(cfg.project_root))
    else:
        note = ""
    return status, note


def _format_table(headers: list[str], rows: list[tuple[str, ...]]) -> str:
    """Two-space-separated columns, ljust-padded. Matches fleet.render."""
    widths = [
        max(len(headers[i]), max((len(r[i]) for r in rows), default=0))
        for i in range(len(headers))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers).rstrip()]
    for row in rows:
        lines.append(fmt.format(*row).rstrip())
    return "\n".join(lines)


def _format_age_iso(ts_iso: str | None) -> str:
    """ISO timestamp → fleet.humanize_age string. Unknown / unparseable → '?'."""
    if not ts_iso:
        return "?"
    try:
        dt = st.parse_iso(ts_iso)
    except (TypeError, ValueError):
        return "?"
    seconds = (st._now_utc() - dt).total_seconds()
    return fleet.humanize_age(seconds)


def _format_iso_clock(ts_iso: str | None) -> str:
    """ISO timestamp → 'HH:MM:SS UTC'. Unknown / unparseable → '?'."""
    if not ts_iso:
        return "?"
    try:
        dt = st.parse_iso(ts_iso)
    except (TypeError, ValueError):
        return "?"
    return dt.strftime("%H:%M:%S UTC")


def cmd_queue_list(args) -> int:
    cfg = load_project_config(_resolve_project_arg(args))
    queue_path = cfg.queue_path()

    if not queue_path.exists():
        pending: list[dict] = []
        history: list[dict] = []
    else:
        try:
            data = queue.load(queue_path)
        except _QUEUE_LOAD_ERRORS as exc:
            return _refuse_on_corrupt_queue(queue_path, exc)
        pending = data["queue"]
        history = data["history"]

    # Always build reg_states — empty-pending + in-flight is a real case
    # (the only queued plan was just popped, dispatch in flight).
    reg_states = {
        e.plan_slug: registry.load_entry_state(e)
        for e in registry.entries()
        if Path(e.project_root).resolve() == cfg.project_root.resolve()
    }

    if not pending:
        print("(queue is empty)")
    else:
        head_state = reg_states.get(pending[0]["slug"])
        head_frozen = bool(
            head_state and head_state.get("status") in _FREEZE_STATUSES
        )
        rows = [
            (str(i), entry["slug"], *_queue_row(
                entry["slug"], cfg, reg_states,
                is_head=(i == 1), head_frozen=head_frozen,
            ))
            for i, entry in enumerate(pending, start=1)
        ]
        print(_format_table(["POS", "SLUG", "STATUS", "NOTE"], rows))
        for i, entry in enumerate(pending, start=1):
            if entry.get("added_by") == "worker":
                src = f"{entry['source_plan']}/{entry['source_phase']}"
                print(f"  {i}: (from {src})")
                if entry.get("reason"):
                    print(f"     reason: {entry['reason']}")

    if history:
        print()
        print("Recent failures:")
        # Cap at 10 — operator wants the most recent context, not the full log.
        for entry in history[-10:]:
            age = _format_age_iso(entry.get("ended_at"))
            outcome = entry.get("outcome", "?")
            print(f"  {entry['slug']}  {outcome}  ({age} ago)")

    pending_slugs = {e["slug"] for e in pending}
    in_flight = []
    for slug, state in reg_states.items():
        if not state:
            continue
        claim = state.get("current_claim")
        if not claim:
            continue
        if slug in pending_slugs:
            # Defensive dedup: a claimed slug shouldn't also be pending,
            # but if it is, the table row already speaks for it.
            continue
        in_flight.append((slug, claim))
    in_flight.sort(key=lambda sc: sc[1].get("started_at", ""))

    if in_flight:
        print()
        for slug, claim in in_flight:
            started = _format_iso_clock(claim.get("started_at"))
            lease = _format_iso_clock(claim.get("lease_expires"))
            print(
                f"In flight: {slug} (dispatched {started}, "
                f"lease until {lease})"
            )
    return ExitCode.OK


def cmd_queue_remove(args) -> int:
    slug = args.slug
    try:
        st.validate_slug(slug, kind="plan slug")
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))

    cfg = load_project_config(_resolve_project_arg(args))
    queue_path = cfg.queue_path()

    if not queue_path.exists():
        return _die(ExitCode.UNKNOWN_TASK, f"{slug!r} is not in the queue")

    try:
        queue.load(queue_path)
    except _QUEUE_LOAD_ERRORS as exc:
        return _refuse_on_corrupt_queue(queue_path, exc)

    with queue.mutate(queue_path) as data:
        positions = [
            i for i, e in enumerate(data["queue"]) if e["slug"] == slug
        ]
        if not positions:
            return _die(ExitCode.UNKNOWN_TASK, f"{slug!r} is not in the queue")
        entry = data["queue"].pop(positions[0])
        data["history"].append({
            **entry,
            "ended_at": st.utcnow(),
            "outcome": "removed",
        })

    print(f"removed {slug} from queue")
    return ExitCode.OK


def _resolve_project_arg(args) -> Path:
    """Resolve `args.project` (or CWD fallback) to an absolute symlink-free path.

    Centralizes the four-site `args.project or Path.cwd()` pattern with a
    uniform `.resolve()` so a symlinked project dir compares equal to its
    canonical form during registry walks. `getattr` tolerates the bare
    `clu queue` dispatch shape where the queue parser has no `--project`
    attribute on the Namespace.
    """
    project = getattr(args, "project", None)
    return (project if project else Path.cwd()).resolve()


def cmd_worktree(args) -> int:
    if args.worktree_cmd == "gc":
        return cmd_worktree_gc(args)
    if args.worktree_cmd == "reattach":
        return cmd_worktree_reattach(args)
    if args.worktree_cmd == "attach":
        return cmd_worktree_attach(args)
    print(
        "usage: clu worktree {gc|reattach|attach} [...]",
        file=sys.stderr,
    )
    return _die(
        ExitCode.GENERIC,
        f"unknown worktree subcommand {args.worktree_cmd!r}",
    )


def cmd_worktree_reattach(args) -> int:
    """Rewrite a plan's `state.worktree.path` to point at a new directory.

    Recovery path when the operator has moved or rebuilt the worktree dir
    by hand (or `git worktree move` made the original path stale). The
    new path must already exist AND pass the same alive-check the
    dispatcher uses, so we don't silently re-attach to a non-git dir.
    Leaves status alone — operator runs `clu resume` separately if the
    plan was paused by `EVENT_WORKTREE_MISSING`.
    """
    try:
        st.validate_slug(args.plan, kind="plan slug")
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))
    cfg = load_project_config(args.project.resolve())
    state_path = cfg.state_path(args.plan)
    if not state_path.exists():
        return _die(
            ExitCode.UNKNOWN_TASK,
            f"no state at {state_path}",
        )

    new_path = args.path.expanduser().resolve()
    if not dispatch.worktree_alive(new_path):
        return _die(
            ExitCode.GENERIC,
            f"{new_path} is not a valid git working directory "
            f"(refusing to reattach to a non-git path)",
        )

    with st.mutate(state_path) as data:
        existing = st.get_worktree(data)
        if existing is None:
            return _die(
                ExitCode.STATUS_TRANSITION,
                f"plan {args.plan!r} has no worktree record — use "
                f"`clu init --worktree` instead of reattach",
            )
        old_path = existing["path"]
        existing["path"] = str(new_path)
        data["worktree"] = existing
    print(
        f"Reattached {args.plan}: {old_path} → {new_path} "
        f"(branch: {existing['branch']})",
    )
    return ExitCode.OK


def autodetect_branch_and_base_ref(
    worktree_path: Path,
) -> tuple[str, str]:
    """Read current branch + HEAD SHA from an existing git worktree.

    Returns `(branch, sha)` with both fields stripped. Empty `branch`
    means detached HEAD — caller decides whether to refuse. Best-effort:
    git failures surface as empty strings so the caller can produce a
    single message rather than juggling two subprocess error paths.
    """
    branch = subprocess.run(
        ["git", "-C", str(worktree_path), "branch", "--show-current"],
        capture_output=True, text=True,
    ).stdout.strip()
    sha = subprocess.run(
        ["git", "-C", str(worktree_path), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    return branch, sha


def cmd_worktree_attach(args) -> int:
    """Write a `state.worktree` record onto a plan that has none.

    Retrofit path for plans where the operator built the worktree by
    hand after `clu init` (without `--worktree`) had already created the
    state file. Mirrors `cmd_worktree_reattach` minus the "must already
    have a record" precondition, plus autodetection of branch + base_ref
    from the worktree's HEAD.
    """
    try:
        st.validate_slug(args.plan, kind="plan slug")
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))
    cfg = load_project_config(args.project.resolve())
    state_path = cfg.state_path(args.plan)
    if not state_path.exists():
        return _die(
            ExitCode.UNKNOWN_TASK,
            f"no state at {state_path}",
        )

    new_path = args.path.expanduser().resolve()
    if not dispatch.worktree_alive(new_path):
        return _die(
            ExitCode.GENERIC,
            f"{new_path} is not a valid git working directory "
            f"(refusing to attach to a non-git path)",
        )

    branch, sha = autodetect_branch_and_base_ref(new_path)
    if not branch:
        return _die(
            ExitCode.GENERIC,
            f"{new_path} is on a detached HEAD — attach requires a "
            f"named branch. Run `git -C {new_path} checkout -b <name>` "
            f"first.",
        )
    if not sha:
        return _die(
            ExitCode.GENERIC,
            f"{new_path}: could not resolve HEAD commit",
        )

    with st.mutate(state_path) as data:
        if st.get_worktree(data) is not None:
            return _die(
                ExitCode.STATUS_TRANSITION,
                f"plan {args.plan!r} already has a worktree record — "
                f"use `clu worktree reattach` to repoint it",
            )
        data["worktree"] = {
            "path": str(new_path),
            "branch": branch,
            "base_ref": sha,
        }
        st.append_event(
            data, st.EVENT_WORKTREE_ATTACHED,
            path=str(new_path), branch=branch, base_ref=sha,
        )
    print(
        f"Attached {args.plan}: {new_path}\n"
        f"  Branch: {branch}\n"
        f"  Base:   {sha}",
    )
    return ExitCode.OK


def cmd_worktree_gc(args) -> int:
    """List or remove worktrees for done/halted plans.

    Two-pass: scan picks candidates by status-at-list-time, action re-loads
    each state under `st.load` to verify status hasn't changed (a plan
    might have been retried in the gap). Stale-list races on the file
    itself — operator running gc twice in parallel — are accepted in v1.
    """
    project_root = _resolve_project_arg(args)
    try:
        cfg = load_project_config(project_root)
    except (OSError, ValueError) as exc:
        return _die(
            ExitCode.GENERIC,
            f"failed to load project at {project_root}: {exc}",
        )

    candidates: list[tuple[str, dict, bool]] = []  # (slug, worktree, archived)
    for p in cross_plan_rules.load_plans_for_project(project_root, cfg):
        wt = st.get_worktree(p.state)
        if not wt:
            continue
        if p.state.get("status") not in st.GC_ELIGIBLE_STATUSES:
            continue
        master_present = cfg.master_plan_path(p.slug).exists()
        if not master_present and not args.include_archived:
            continue
        candidates.append((p.slug, wt, not master_present))

    if not candidates:
        print("(no worktree-bearing done/halted plans)")
        return ExitCode.OK

    print(f"Candidates ({len(candidates)}):")
    for slug, wt, archived in candidates:
        tag = " (archived)" if archived else ""
        print(f"  {slug}{tag}  →  {wt['path']}  [{wt['branch']}]")

    if not args.confirm:
        print("(dry run — pass --confirm to remove)")
        return ExitCode.OK

    removed = 0
    for slug, wt, _archived in candidates:
        state_path = cfg.state_path(slug)
        # Re-check status at action time so a `clu retry` that landed
        # between list and confirm doesn't lose its worktree.
        if state_path.exists():
            try:
                fresh = st.load(state_path)
            except (OSError, ValueError, st.SchemaVersionMismatch):
                print(f"  skipped: {slug}: state unreadable")
                continue
            if fresh.get("status") not in st.GC_ELIGIBLE_STATUSES:
                print(f"  skipped: {slug}: status changed since list")
                continue

        reachable, reason = _is_branch_reachable_from_origin(
            cfg.project_root, wt["branch"],
        )
        if not reachable:
            print(
                f"  retained: {slug}: {reason} — push or "
                f"`git -C {cfg.project_root} worktree remove --force "
                f"{wt['path']}` to force.",
                file=sys.stderr,
            )
            continue

        (wt_ok, wt_err), (br_ok, br_err) = _remove_worktree_and_branch(
            cfg.project_root, wt["path"], wt["branch"],
            delete_branch=args.delete_branch,
            timeout=30,
        )
        if not wt_ok:
            print(f"  failed: {slug}: {wt_err}", file=sys.stderr)
            continue
        print(f"  removed: {slug}  →  {wt['path']}")
        removed += 1

        if args.delete_branch:
            if not br_ok:
                print(
                    f"  branch removal failed for {slug}: {br_err}",
                    file=sys.stderr,
                )
            else:
                print(f"  branch dropped: {wt['branch']}")

    print(f"Removed {removed}/{len(candidates)} worktree(s).")
    return ExitCode.OK


def _spawn_post_action_tick(cfg: ProjectConfig) -> None:
    """Fire a detached project-scoped tick after a state-changing action,
    so the next phase dispatches without waiting for the cron interval.

    Must be called AFTER the `st.mutate` block has closed so the spawned
    tick reads the post-write state. Failure is swallowed — state is
    already on disk and the cron path will pick it up.
    """
    if not cfg.tick_on_action:
        return
    try:
        subprocess.Popen(
            [sys.argv[0], "tick", "--project", str(cfg.project_root.resolve())],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        print(
            f"tick-on-action: spawn failed ({type(exc).__name__}: {exc}); "
            f"cron will catch up on next tick",
            file=sys.stderr,
        )


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
    project_root = str(cfg.project_root.resolve())
    if result.notify_body and (kind := ACTION_NOTIFY_KIND.get(result.action)):
        # plan_slug + project_root piggyback so notify() drops an inbox
        # event alongside the iMessage — keeps in-session signaling aligned
        # with the operator's phone.
        notify.notify(
            cfg.notify, kind, result.notify_body,
            plan_slug=plan_slug, project_root=project_root,
        )
    # side_notifies (stuck-blocker re-pings, stalled-claim transitions)
    # already wrote rich-detail inbox events from inside tick(); fire
    # iMessage only — no plan_slug → no duplicate inbox event.
    for kind, body in result.side_notifies:
        notify.notify(cfg.notify, kind, body)
    return result


def cmd_tick(args) -> int:
    """Plan-scoped (`--plan X`) or project-scoped (no `--plan`) tick.

    Project-scoped is the spawn target for `_spawn_post_action_tick`:
    it ticks every plan registered to the project AND runs the
    cross-plan rule chain (queue advance, auto-archive, worktree
    conflicts) — same post-loop logic as `cmd_tick_all`, scoped to
    one project so a callback in plan A doesn't drag every plan on
    the host through a tick.
    """
    cfg = load_project_config(_resolve_project_arg(args))
    if args.plan is not None:
        try:
            st.validate_slug(args.plan, kind="plan slug")
        except st.InvalidSlug as exc:
            return _die(ExitCode.INVALID_SLUG, str(exc))
        state_path = cfg.state_path(args.plan)
        result = _tick_one_plan(
            args.plan, cfg, state_path, dispatch=not args.dry_tick,
        )
        print(result)
        return ExitCode.OK

    project_root = cfg.project_root.resolve()
    for row in registry.entries():
        if Path(row.project_root).resolve() != project_root:
            continue
        try:
            row_cfg = load_project_config(Path(row.project_root))
            row_state_path = row_cfg.state_path(row.plan_slug)
            result = _tick_one_plan(
                row.plan_slug, row_cfg, row_state_path,
                dispatch=not args.dry_tick,
            )
            print(f"tick {row.plan_slug}: {result}")
        except Exception as exc:
            print(
                f"tick: {row.plan_slug}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    try:
        plans = cross_plan_rules.load_plans_for_project(project_root, cfg)
        rule_result = cross_plan_rules.run_rules(project_root, plans)
        if rule_result is not None:
            for kind, body in rule_result.notifies:
                notify.notify(cfg.notify, kind, body)
    except Exception as exc:
        print(
            f"tick post-loop @ {project_root}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    return ExitCode.OK


def cmd_tick_all(args) -> int:
    entries = registry.entries()
    for row in entries:
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

    # Post-loop: per-project cross-plan rule chain (queue advancement →
    # worktree conflict scan), first-match-wins per ADR-0002.
    # Re-read registry.entries() — claim state mutated above is what the
    # busy gate needs to see.
    seen: dict[Path, None] = {}
    for row in registry.entries():
        try:
            seen.setdefault(Path(row.project_root).resolve(), None)
        except OSError:
            continue
    for project_root in sorted(seen):
        try:
            project_cfg = load_project_config(project_root)
            plans = cross_plan_rules.load_plans_for_project(project_root, project_cfg)
            result = cross_plan_rules.run_rules(project_root, plans)
            if result is not None:
                for kind, body in result.notifies:
                    notify.notify(project_cfg.notify, kind, body)
        except Exception as exc:
            print(
                f"tick-all post-loop @ {project_root}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    return ExitCode.OK


_REPAIR_MAX_ATTEMPTS = 3


def _handle_corrupt_queue(
    cfg: ProjectConfig, exc: Exception, queue_path: Path,
) -> None:
    """Backup-first auto-repair pipeline for an unparseable queue.json.

    Steps (any failure short-circuits to a notification + early return):
      1. Backup the current bytes to a `corrupt-<utc>` sibling. Always.
      2. Check the per-diagnosis-hash throttle. ≥ 3 attempts → notify only.
      3. `repair_command` unset → notify only, increment throttle.
      4. Spawn the repair worker synchronously, then run
         `queue.validate_repair` against the worker's output.
      5. Validation failed → revert bytes from backup, REPAIR_FAILED.
      6. Validation passed → REPAIRED, reset throttle.
    """
    from . import dispatch  # local: dispatch imports supervisor too.

    diagnosis = f"{type(exc).__name__}: {exc}"
    diagnosis_hash = hashlib.sha256(diagnosis.encode()).hexdigest()[:8]
    throttle_path = queue_path.with_name(queue_path.name + ".repair-attempts")

    try:
        original_bytes = queue_path.read_bytes()
    except OSError as read_exc:
        print(
            f"corrupt queue: cannot read {queue_path}: {read_exc}",
            file=sys.stderr,
        )
        return
    backup_path = queue_path.with_name(
        f"{queue_path.name}.corrupt-{st.utcnow_compact()}"
    )
    try:
        backup_path.write_bytes(original_bytes)
    except OSError as write_exc:
        print(
            f"corrupt queue: cannot write backup {backup_path}: {write_exc}",
            file=sys.stderr,
        )
        return

    attempts = queue.read_throttle(throttle_path, diagnosis_hash)
    if attempts >= _REPAIR_MAX_ATTEMPTS:
        notify.notify(
            cfg.notify, notify.KIND_QUEUE_CORRUPT,
            notify.render_queue_corrupt(diagnosis, backup_path)
            + f" (auto-repair gave up after {_REPAIR_MAX_ATTEMPTS} attempts)",
        )
        return

    if not cfg.dispatch.repair_command:
        notify.notify(
            cfg.notify, notify.KIND_QUEUE_CORRUPT,
            notify.render_queue_corrupt(diagnosis, backup_path),
        )
        queue.increment_throttle(throttle_path, diagnosis_hash)
        return

    log_path = (
        queue_path.parent / "logs"
        / f"repair-queue-{st.utcnow_compact()}.log"
    )

    try:
        dispatch.dispatch_repair_worker(
            cfg, queue_path, backup_path, diagnosis, log_path,
        )
    except (OSError, ValueError) as spawn_exc:
        notify.notify(
            cfg.notify, notify.KIND_QUEUE_REPAIR_FAILED,
            notify.render_queue_repair_failed(
                f"dispatch failed: {spawn_exc}", backup_path,
            ),
        )
        queue.increment_throttle(throttle_path, diagnosis_hash)
        return

    result = queue.validate_repair(original_bytes, queue_path)
    if not result.ok:
        try:
            queue_path.write_bytes(original_bytes)
        except OSError as revert_exc:
            print(
                f"corrupt queue: revert failed for {queue_path}: {revert_exc}",
                file=sys.stderr,
            )
        notify.notify(
            cfg.notify, notify.KIND_QUEUE_REPAIR_FAILED,
            notify.render_queue_repair_failed(result.reason or "unknown", backup_path),
        )
        queue.increment_throttle(throttle_path, diagnosis_hash)
        return

    repaired = queue.load(queue_path)
    notify.notify(
        cfg.notify, notify.KIND_QUEUE_REPAIRED,
        notify.render_queue_repaired(len(repaired["queue"]), backup_path),
    )
    queue.reset_throttle(throttle_path)


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


def cmd_watch(args) -> int:
    """Stream state-machine events — one line per transition.

    Resolves state paths from the registry, then delegates to
    watch.stream_loop. Exit on SIGINT → ExitCode.OK.
    """
    state_paths: list[Path] = []
    plan_slug: str | None = getattr(args, "watch_plan", None)
    all_mode: bool = getattr(args, "watch_all", False)
    task_list_mode: bool = getattr(args, "watch_task_list", False)

    if task_list_mode and args.json:
        return _die(ExitCode.GENERIC, "--task-list and --json are mutually exclusive")
    if task_list_mode and all_mode:
        return _die(ExitCode.GENERIC,
                    "--task-list requires --plan or single-project "
                    "(mutually exclusive with --all)")

    if all_mode:
        for e in registry.entries():
            if args.project is None or (
                Path(e.project_root).resolve() == args.project.resolve()
            ):
                cfg = load_project_config(Path(e.project_root))
                state_paths.append(cfg.state_path(e.plan_slug))
    elif plan_slug:
        project_root = _resolve_project_arg(args)
        cfg = load_project_config(project_root)
        registered = any(
            Path(e.project_root).resolve() == project_root
            and e.plan_slug == plan_slug
            for e in registry.entries()
        )
        if not registered:
            return _die(ExitCode.UNKNOWN_TASK,
                        f"plan {plan_slug!r} is not registered in {project_root}")
        state_paths.append(cfg.state_path(plan_slug))
    else:
        project_root = _resolve_project_arg(args)
        cfg = load_project_config(project_root)
        for e in registry.entries():
            if Path(e.project_root).resolve() == project_root:
                state_paths.append(cfg.state_path(e.plan_slug))

    interval = args.interval if args.interval is not None else (
        5.0 if all_mode else 1.0
    )
    try:
        return watch.stream_loop(
            state_paths,
            json_mode=args.json,
            task_list_mode=task_list_mode,
            verbose=args.verbose,
            poll_interval=interval,
        )
    except FileNotFoundError as exc:
        return _die(ExitCode.UNKNOWN_TASK, str(exc))


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


def cmd_extend_lease(args, cfg: ProjectConfig, state_path: Path) -> int:
    if args.minutes <= 0:
        return _die(
            ExitCode.INVALID_VALUE,
            f"minutes must be positive, got {args.minutes}",
        )
    with st.mutate(state_path) as data:
        claim = data.get("current_claim")
        if claim is None:
            return _die(
                ExitCode.STATUS_TRANSITION,
                f"no claim to extend on {args.plan}",
            )
        current = _dt.datetime.strptime(
            claim["lease_expires"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=_dt.timezone.utc)
        now = _dt.datetime.now(_dt.timezone.utc)
        baseline = max(current, now)
        new_expires = (baseline + _dt.timedelta(minutes=args.minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        claim["lease_expires"] = new_expires
        st.append_event(
            data, st.EVENT_LEASE_EXTENDED,
            phase=claim["phase_id"],
            extended_by_minutes=args.minutes,
            new_expires=new_expires,
            operator=True,
        )
    print(
        f"Extended {args.plan}/{claim['phase_id']} lease by "
        f"{args.minutes} min → {new_expires}"
    )
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
        if args.reset_attempts:
            st.append_event(data, st.EVENT_ATTEMPTS_RESET, phase=phase, operator=True)
    suffix = " Attempts reset." if args.reset_attempts else ""
    print(f"Released claim on {args.plan}/{phase}.{suffix}")
    return ExitCode.OK


def cmd_answer(args) -> int:
    reply_text = args.answer if args.plan is None else f"{args.plan} {args.answer}"
    result = state_locator.find_blocker_for_reply(registry.entries(), reply_text)
    if result.variant == "AMBIGUOUS":
        for cand in result.candidates:
            print(f"  {cand.plan_slug}: {cand.blocker_id}", file=sys.stderr)
        return _die(ExitCode.GENERIC, "ambiguous reply — pass --plan to disambiguate")
    if result.variant != "FOUND":
        return _die(ExitCode.UNKNOWN_TASK, result.variant.lower())
    with st.mutate(result.state_path) as data:
        resolved = st.resolve_blocker_answer(data, result.blocker_id, str(result.answer_index))
        st.answer_blocker(data, result.blocker_id, resolved)
    print(f"Answered {result.blocker_id}: {resolved}")
    return ExitCode.OK


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
    _spawn_post_action_tick(cfg)
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


def _compute_phase_diff(git_root: Path, base_sha: str) -> tuple[int, int]:
    """Return (files_changed, lines_changed) for diff base_sha..HEAD.

    Binary files emit '-' for line counts — treated as 0. Returns (0, 0) on
    any git error (no commits / empty diff / bad base ref).
    """
    result = subprocess.run(
        ["git", "-C", str(git_root), "diff", "--numstat", f"{base_sha}..HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    out = result.stdout.strip()
    if result.returncode != 0 or not out:
        return (0, 0)
    files = 0
    lines = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        try:
            added = int(parts[0]) if parts[0] != "-" else 0
            deleted = int(parts[1]) if parts[1] != "-" else 0
            lines += added + deleted
        except ValueError:
            continue
    return (files, lines)


def _claim_base_sha(claim: dict, data: dict) -> str | None:
    """Return the base SHA to diff against for this claim's phase.

    Worktree-mode: base_ref recorded on plan init (start of the whole plan branch).
    Non-worktree: head_sha_at_claim captured when claim was created.
    Returns None if neither is available (legacy claims; simplify gate skipped).
    """
    wt = st.get_worktree(data)
    if wt:
        return wt.get("base_ref")
    return claim.get("head_sha_at_claim")


@_translate_claim_mismatch
def cmd_complete(args, cfg: ProjectConfig, state_path: Path) -> int:
    if args.commits:
        if err := _verify_commit_shas(cfg.project_root, args.commits):
            return _die(ExitCode.BAD_SHA, err)

    # Token pre-check (read-only): fail fast before git diff or quality gate
    # evaluation so a bad token always gets CLAIM_MISMATCH, not STATUS_TRANSITION.
    data_snap = st.load(state_path)
    st.assert_claim_match(data_snap, args.token, args.phase)

    # Quality gates — evaluated before mutating state so a refusal leaves the
    # claim live and the worker can stamp + retry without a re-claim.
    claim = data_snap.get("current_claim") or {}

    if not args.skip_verify or not args.skip_simplify:
        git_root = st.claim_git_root(data_snap, cfg)
        head_sha = _resolve_ref(git_root, "HEAD") or ""

        if not args.skip_verify:
            stamped_at = st.attestation_commit_sha(data_snap, st.ATTESTATION_VERIFY)
            if stamped_at is None or stamped_at != head_sha:
                return _die(
                    ExitCode.STATUS_TRANSITION,
                    f"verify gate: stamp missing or stale "
                    f"(stamped at {stamped_at or 'never'}, HEAD is {head_sha}). "
                    f"Run `clu verify` before complete, or pass --skip-verify.",
                )

        if not args.skip_simplify:
            base_sha = _claim_base_sha(claim, data_snap)
            if base_sha:
                files_changed, lines_changed = _compute_phase_diff(git_root, base_sha)
                t_files, t_lines = cfg.simplify_threshold_or_default()
                if files_changed > t_files or lines_changed > t_lines:
                    stamped_at = st.attestation_commit_sha(data_snap, st.ATTESTATION_SIMPLIFY)
                    if stamped_at is None or stamped_at != head_sha:
                        return _die(
                            ExitCode.STATUS_TRANSITION,
                            f"simplify gate: diff is {files_changed} files / "
                            f"{lines_changed} lines (threshold: {t_files}/{t_lines}). "
                            f"Stamp missing or stale "
                            f"(stamped at {stamped_at or 'never'}, HEAD is {head_sha}). "
                            f"Run `clu attest --simplify` before complete, or pass --skip-simplify.",
                        )

    with st.mutate(state_path) as data:
        st.release_claim(data, expected_token=args.token, expected_phase=args.phase)
        st.append_event(
            data, st.EVENT_PHASE_COMPLETED,
            phase=args.phase, commits=list(args.commits),
        )
        if args.skip_verify:
            st.append_event(data, st.EVENT_OPERATOR_SKIP_VERIFY,
                            phase=args.phase, operator=True)
        if args.skip_simplify:
            st.append_event(data, st.EVENT_OPERATOR_SKIP_SIMPLIFY,
                            phase=args.phase, operator=True)
        _maybe_cleanup_worktree(
            cfg, data, trigger="complete", require_all_phases_done=True,
        )
    _spawn_post_action_tick(cfg)
    print(f"Completed phase {args.phase}")
    return ExitCode.OK


def cmd_force_complete(args, cfg: ProjectConfig, state_path: Path) -> int:
    """Operator-side recovery: mark a phase complete when the worker died
    after writing code but before calling `clu complete` (#48).

    Refuses on already-completed, unknown-phase, and never-started phases
    (the last is overridable with `--really`). Releases any active claim
    without token validation, then emits `EVENT_OPERATOR_FORCE_COMPLETE`
    + `EVENT_PHASE_COMPLETED` so the supervisor's plan_done detection
    fires normally on the next tick.
    """
    try:
        st.validate_slug(args.phase, kind="phase id")
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))
    plan_path = cfg.project_root / cfg.plan_dir / f"{args.plan}.md"
    try:
        phases = parse_sessions_index(plan_path)
    except (OSError, ValueError) as exc:
        return _die(ExitCode.UNKNOWN_TASK, f"cannot read plan {plan_path}: {exc}")
    known_ids = {p.id for p in phases}
    if args.phase not in known_ids:
        return _die(
            ExitCode.UNKNOWN_TASK,
            f"phase {args.phase!r} not in plan {args.plan!r} "
            f"(known: {sorted(known_ids)})",
        )
    if args.commits:
        if err := _verify_commit_shas(cfg.project_root, args.commits):
            return _die(ExitCode.BAD_SHA, err)
    with st.mutate(state_path) as data:
        if args.phase in st.completed_phase_ids(data):
            return _die(
                ExitCode.STATUS_TRANSITION,
                f"phase {args.phase!r} already completed — see `clu status`",
            )
        ever_started = st.latest_event(
            data, st.EVENT_PHASE_STARTED, phase=args.phase,
        ) is not None
        claim = data.get("current_claim")
        claim_on_phase = claim is not None and claim.get("phase_id") == args.phase
        if not ever_started and not claim_on_phase and not args.really:
            return _die(
                ExitCode.STATUS_TRANSITION,
                f"phase {args.phase!r} never started — pass `--really` to "
                f"force-complete anyway",
            )
        if claim_on_phase:
            st.release_claim(data)
        st.append_event(
            data, st.EVENT_OPERATOR_FORCE_COMPLETE,
            phase=args.phase, commits=list(args.commits),
            reason=args.reason, operator=True,
        )
        st.append_event(
            data, st.EVENT_PHASE_COMPLETED,
            phase=args.phase, commits=list(args.commits),
        )
        _maybe_cleanup_worktree(
            cfg, data, trigger="force-complete", require_all_phases_done=True,
        )
    _spawn_post_action_tick(cfg)
    print(f"Force-completed phase {args.phase}")
    return ExitCode.OK


def cmd_integrate(args) -> int:
    """Operator-on-demand dry merge of a batch's branches.

    Wraps dry_merge.attempt_merge directly; does NOT mutate plan state
    or write follow-up plan files (the cross-plan rule owns that).
    Useful for replay-after-fix, stuck batches, or CI-side validation.
    """
    project_root = args.project.resolve()
    if not project_root.is_dir():
        return _die(ExitCode.GENERIC, f"project not found: {project_root}")
    cfg = load_project_config(project_root)

    if args.branches:
        branches = [b.strip() for b in args.branches.split(",") if b.strip()]
        if len(branches) < 2:
            return _die(
                ExitCode.GENERIC,
                f"--branches requires at least 2 entries; got {len(branches)} after parsing",
            )
    elif args.batch:
        try:
            st.validate_slug(args.batch, kind="batch id")
        except st.InvalidSlug as exc:
            return _die(ExitCode.INVALID_SLUG, str(exc))
        plans = cross_plan_rules.load_plans_for_project(project_root, cfg)
        eligible = [
            p for p in plans
            if p.state.get("status") == st.STATUS_DONE
            and p.state.get("batch_id") == args.batch
            and st.get_worktree(p.state) is not None
        ]
        branches: list[str] = []
        for p in eligible:
            branch = st.get_worktree(p.state)["branch"]
            r = subprocess.run(
                ["git", "-C", str(project_root), "rev-parse", "--verify", branch],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                branches.append(branch)
            else:
                print(
                    f"integrate: branch {branch!r} not found for {p.slug!r}, skipping",
                    file=sys.stderr,
                )
        if len(branches) < 2:
            return _die(
                ExitCode.GENERIC,
                f"batch {args.batch!r} has fewer than 2 DONE plans with live worktree "
                f"branches (found {len(branches)}); nothing to integrate",
            )
    else:
        return _die(
            ExitCode.GENERIC,
            "one of --batch or --branches is required",
        )

    test_cmd = None if args.no_suite else cfg.test_command
    result = dry_merge.attempt_merge(
        project_root,
        args.base_ref,
        branches,
        test_cmd,
    )

    print(f"outcome: {result.outcome}")
    if result.conflict_files:
        print(f"conflict files: {', '.join(result.conflict_files)}")
    if result.test_exit_code is not None:
        print(f"test exit code: {result.test_exit_code}")
    if result.stderr_tail:
        print(f"stderr:\n{result.stderr_tail}", file=sys.stderr)

    return ExitCode.OK if result.outcome == "clean" else ExitCode.GENERIC


def _perform_archive(
    cfg: ProjectConfig,
    plan: str,
    *,
    unregister: bool = False,
) -> tuple[dict | None, dict | None, bool]:
    """Shared archive engine. Returns (before, after, plan_moved).

    Cleans up the worktree, moves the plan file to plans/shipped/, and
    optionally prunes the registry entry. Raises CalledProcessError or
    TimeoutExpired on git mv failure (state is still saved; caller decides
    how to surface the error). Does not check STATUS_RUNNING.
    """
    state_path = cfg.state_path(plan)
    plan_moved = False
    _git_mv_exc: Exception | None = None
    with st.mutate(state_path) as data:
        before = st.get_worktree(data)
        _maybe_cleanup_worktree(
            cfg, data, trigger="archive", require_all_phases_done=False,
        )
        after = st.get_worktree(data)
        plan_dir = cfg.project_root / cfg.plan_dir
        plan_md = plan_dir / f"{plan}.md"
        sources: list[Path] = []
        if plan_md.exists():
            sources.append(plan_md)
        sources.extend(sorted(plan_dir.glob(f"{plan}-*.md")))
        if sources:
            shipped_dir = plan_dir / "shipped"
            shipped_dir.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(
                    ["git", "mv", *[str(s) for s in sources], str(shipped_dir)],
                    check=True,
                    capture_output=True,
                    text=True,
                    cwd=str(cfg.project_root),
                    timeout=30,
                )
                plan_moved = True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                _git_mv_exc = exc
    if _git_mv_exc is not None:
        raise _git_mv_exc
    if unregister:
        # Auto-archive mode. Commit BEFORE unregister: if commit fails
        # we'd otherwise leave a pruned registry alongside a dirty
        # worktree, and the rule chain wouldn't retry. cmd_archive
        # (unregister=False) leaves commits to the operator.
        if plan_moved:
            subprocess.run(
                ["git", "-C", str(cfg.project_root), "commit",
                 "-m", f"chore: auto-archive {plan} (post-merge cleanup)"],
                check=True, capture_output=True, text=True, timeout=30,
            )
        registry.unregister(cfg.project_root, plan)
    return before, after, plan_moved


def cmd_archive(args) -> int:
    """Plan-level wrap: clean up the worktree + branch when commits are
    upstream-reachable; retain-and-warn when ahead.

    Refuses when the plan is still RUNNING — `clu pause` or `clu halt`
    first if the operator means to abandon mid-flight. Idempotent
    against an already-clean state (no worktree record → no-op success).
    """
    try:
        st.validate_slug(args.plan, kind="plan slug")
    except st.InvalidSlug as exc:
        return _die(ExitCode.INVALID_SLUG, str(exc))
    cfg = load_project_config(args.project.resolve())
    state_path = cfg.state_path(args.plan)
    if not state_path.exists():
        return _die(ExitCode.UNKNOWN_TASK, f"no state at {state_path}")
    data = st.load(state_path)
    if data["status"] == st.STATUS_RUNNING:
        return _die(
            ExitCode.STATUS_TRANSITION,
            f"plan {args.plan!r} is still RUNNING — pause or halt "
            f"first, or wait for plan_done before archiving",
        )
    try:
        before, after, plan_moved = _perform_archive(cfg, args.plan, unregister=False)
    except subprocess.CalledProcessError as exc:
        plan_md = cfg.project_root / cfg.plan_dir / f"{args.plan}.md"
        return _die(
            ExitCode.GENERIC,
            f"git mv failed for {plan_md.name}: {exc.stderr.strip() or str(exc)}",
        )
    except subprocess.TimeoutExpired:
        plan_md = cfg.project_root / cfg.plan_dir / f"{args.plan}.md"
        return _die(ExitCode.GENERIC, f"git mv timed out for {plan_md}")
    move_note = " Plan file moved to shipped/." if plan_moved else ""
    if before is None:
        print(f"Archive {args.plan}: no worktree to clean.{move_note}")
    elif after is None:
        print(
            f"Archive {args.plan}: removed {before['path']} "
            f"(branch {before['branch']}).{move_note}",
        )
    else:
        print(
            f"Archive {args.plan}: retained {before['path']} "
            f"(branch {before['branch']} ahead of origin).{move_note}",
        )
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
        state_blocker.render_blocker(
            args.plan, blocker_id, args.phase, args.question, args.options,
        ),
        plan_slug=args.plan,
        project_root=str(cfg.project_root.resolve()),
    )
    _spawn_post_action_tick(cfg)
    print(f"Blocked {blocker_id} on phase {args.phase}")
    return ExitCode.OK


@_translate_claim_mismatch
def cmd_verify(args, cfg: ProjectConfig, state_path: Path) -> int:
    """HEAD is captured before the command to prevent a mid-test commit from
    slipping past the gate. Token validation runs before the subprocess so a
    forged or stale token fails immediately rather than after a 600s test run.
    """
    cmd = cfg.resolved_verify_command()
    if not cmd:
        return _die(
            ExitCode.GENERIC,
            "no verify command configured "
            "(set quality.verify_command or test_command in .orchestrator.json)",
        )
    data_snap = st.load(state_path)
    if args.token:
        st.assert_claim_match(data_snap, args.token, args.phase)
    git_root = st.claim_git_root(data_snap, cfg)
    head = _resolve_ref(git_root, "HEAD")
    if not head:
        return _die(ExitCode.GENERIC, "could not resolve HEAD SHA")
    try:
        result = subprocess.run(
            shlex.split(cmd),
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return _die(ExitCode.GENERIC, f"verify timed out after 600s: {cmd}")
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-20:]
        return _die(
            ExitCode.GENERIC,
            f"verify failed (rc={result.returncode}):\n" + "\n".join(tail),
        )
    with st.mutate(state_path) as data:
        st.stamp_attestation(data, st.ATTESTATION_VERIFY, head)
        st.append_event(
            data, st.EVENT_VERIFY_STAMPED,
            phase=args.phase, commit_sha=head,
        )
    print(f"verified at {head}")
    return ExitCode.OK


@_translate_claim_mismatch
def cmd_attest(args, cfg: ProjectConfig, state_path: Path) -> int:
    """Pure self-attestation — stamps current HEAD into attestations[kind].

    clu cannot invoke /simplify (a Claude-Code-side skill), so the worker's
    word is the only signal. Token is required; no operator-side variant.
    """
    if not args.simplify:
        return _die(
            ExitCode.GENERIC,
            "clu attest: at least one attestation flag required (currently: --simplify)",
        )
    data_snap = st.load(state_path)
    git_root = st.claim_git_root(data_snap, cfg)
    head = _resolve_ref(git_root, "HEAD")
    if not head:
        return _die(ExitCode.GENERIC, "could not resolve HEAD SHA")
    with st.mutate(state_path) as data:
        st.assert_claim_match(data, args.token, args.phase)
        st.stamp_attestation(data, st.ATTESTATION_SIMPLIFY, head)
        st.append_event(
            data, st.EVENT_SIMPLIFY_STAMPED,
            phase=args.phase, commit_sha=head,
        )
    print(f"attested simplify at {head}")
    return ExitCode.OK


def cmd_blockers(args) -> int:
    if args.blockers_cmd == "list":
        return cmd_blockers_list(args)
    if args.blockers_cmd == "show":
        return cmd_blockers_show(args)
    print(
        "usage: clu blockers {list|show} --project PATH --plan SLUG",
        file=sys.stderr,
    )
    return _die(ExitCode.GENERIC, f"unknown blockers subcommand {args.blockers_cmd!r}")


def cmd_blockers_list(args) -> int:
    st.validate_slug(args.plan, kind="plan slug")
    cfg = load_project_config(args.project.resolve())
    state_path = cfg.state_path(args.plan)
    if not state_path.exists():
        return _die(ExitCode.UNKNOWN_TASK, f"no state at {state_path}")
    data = st.load(state_path, expected_version=st.SCHEMA_VERSION)
    open_blockers = [b for b in data.get("blockers", []) if b.get("answer") is None]
    if not open_blockers:
        print(f"no open blockers on {args.plan}")
        return ExitCode.OK
    for b in open_blockers:
        print(f"{b['id']} [{b['phase_id']}] (asked {b['asked_at']})")
        print(f"  {b['question']}")
        if b.get("options"):
            print("  Options:")
            for i, opt in enumerate(b["options"]):
                print(f"    {i}. {opt}")
        print()
    return ExitCode.OK


def cmd_blockers_show(args) -> int:
    st.validate_slug(args.plan, kind="plan slug")
    cfg = load_project_config(args.project.resolve())
    state_path = cfg.state_path(args.plan)
    if not state_path.exists():
        return _die(ExitCode.UNKNOWN_TASK, f"no state at {state_path}")
    data = st.load(state_path, expected_version=st.SCHEMA_VERSION)
    blocker = next(
        (b for b in data.get("blockers", []) if b["id"] == args.blocker_id),
        None,
    )
    if blocker is None:
        return _die(
            ExitCode.UNKNOWN_TASK,
            f"no blocker {args.blocker_id} on {args.plan}",
        )
    print(f"{blocker['id']} [{blocker['phase_id']}]")
    print(f"  asked: {blocker['asked_at']}")
    if blocker.get("answer") is not None:
        print(f"  answer: {blocker['answer']} (at {blocker['answered_at']})")
    print(f"  question: {blocker['question']}")
    if blocker.get("context"):
        print(f"  context: {blocker['context']}")
    if blocker.get("options"):
        print("  Options:")
        for i, opt in enumerate(blocker["options"]):
            print(f"    {i}. {opt}")
    related = [
        e for e in data.get("events", [])
        if e.get("blocker_id") == args.blocker_id
    ]
    if related:
        print("  Events:")
        for e in related:
            print(f"    {e.get('ts', '?')} {e.get('type', '?')}")
    return ExitCode.OK


def cmd_notify_test(args) -> int:
    """Fire a test notification through configured channels, reporting per-channel status.

    Skips disabled channels. Never touches inbox — outbound transport only.
    """
    cfg = load_project_config(_resolve_project_arg(args))
    channels = (
        [c for c in cfg.notify.channels if c.kind == args.channel]
        if args.channel
        else cfg.notify.channels
    )
    if not channels:
        print(
            "No channels configured. Run `clu init` to add one, "
            "or edit `.orchestrator.json` directly.",
            file=sys.stderr,
        )
        return ExitCode.GENERIC
    for ch in channels:
        if not ch.enabled:
            print(f"{ch.kind}: SKIPPED (disabled)")
            continue
        notifier_cls = notify._NOTIFIER_REGISTRY.get(ch.kind)
        if notifier_cls is None:
            print(f"{ch.kind}: SKIPPED (unknown kind)")
            continue
        notifier = notifier_cls.from_spec(ch)
        try:
            msg_id = notifier.send(
                notify.KIND_COMPLETED, "clu notify smoke test",
                plan_slug="_test", blocker_id=None,
            )
            suffix = f" (msg {msg_id})" if msg_id else ""
            print(f"{ch.kind}: OK{suffix}")
        except Exception as exc:
            print(f"{ch.kind}: FAILED ({exc!r})")
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
