"""Daemonized heartbeat loop for clu phase workers (`clu heartbeat-daemon`).

Replaces the bash `( while kill -0 $WORKER_PID; do clu heartbeat; sleep 120;
done ) &` compound from clu-phase/SKILL.md. Under scoped-permission dispatch
(`dontAsk` + `--allowedTools`) that compound is denied even with every inner
command allowlisted — subshell/trap/while constructs don't survive permission
decomposition (#90 spike Test B, 2026-06-10, claude 2.1.170). One flat
`clu heartbeat-daemon ...` command is allowlistable; it double-forks + setsid
to detach, then pings the live claim every 120s while the worker PID is alive.

Loop contract per tick: worker PID dead → clean exit; heartbeat ping rejected
(`ClaimMismatch`: claim released by `clu complete`/`block`, or superseded) →
clean exit, NOT a strike; any other failure → strike, and the 3rd consecutive
strike fires the `notify-heartbeat-failure` path once. Each tick's stderr goes
to the sidecar log `<plans>/.orchestrator/logs/<phase>.<token>.hb.log`.

Reaper interaction (accepted by design): `os.setsid()` puts the daemon in its
own process group, so `reap_orphan_pgroup`'s killpg never reaches it. That is
fine — the daemon's exits are independent backstops: a reaped worker is a dead
PID (exit on the next liveness probe) and a released claim is a token
rejection (clean exit on the next ping). Do NOT add the daemon to any reaper,
and never record its PID in the claim — claim.pid stays the worker's
(supervisor-lifecycle PTY constraint: wrappers must not change claim.pid).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from end_of_line import state as st

DEFAULT_INTERVAL_SECONDS = 120
STRIKE_LIMIT = 3

ACTION_OK = "ok"
ACTION_STRIKE = "strike"
ACTION_EXIT_WORKER_DEAD = "exit_worker_dead"
ACTION_EXIT_CLAIM_GONE = "exit_claim_gone"


def _make_pid_alive(plan: str | None):
    """Cmdline-anchored liveness probe closing over the plan slug.

    Delegates to `st.claim_worker_alive` so the probe requires the plan slug
    as a whole token in the process cmdline (the #76 marker check) — a bare
    `os.kill(pid, 0)` false-positives on PID reuse, and once death drives an
    operator notification that false positive would page the operator about a
    stranger's process. `plan=None` (bare kill(0) semantics) is preserved for
    callers that inject their own probe.
    """

    def probe(pid: int) -> bool:
        return st.claim_worker_alive({"pid": pid}, cmdline_match=plan)

    return probe


def _ping(state_path, token: str, phase: str) -> None:
    """The same code path `cmd_heartbeat` uses — in-process, no PATH games."""
    with st.mutate(Path(state_path)) as data:
        st.record_heartbeat(data, token, phase)


def _notify_failure(project_root, plan: str, phase: str, token: str, log_path) -> None:
    """Fire the worker self-report after 3 consecutive failed pings.

    In-process `cli.main` call (lazy import — cli.py imports this module).
    `cmd_notify_heartbeat_failure` is idempotent per claim, so repeated
    strike runs can't double-notify the operator.
    """
    from end_of_line.cli import main as cli_main

    rc = cli_main(
        [
            "notify-heartbeat-failure",
            "--project", str(project_root),
            "--plan", plan,
            "--phase", phase,
            "--token", token,
            "--log", str(log_path),
        ]
    )
    if rc != 0:
        print(f"notify-heartbeat-failure exited {rc}", file=sys.stderr)


def _report_death(project_root, plan: str, phase: str, token: str, state_path) -> None:
    """Report worker death through the token-validated `notify-worker-dead`.

    In-process `cli.main` call (lazy import — cli.py imports this module),
    mirroring `_notify_failure`. The command is idempotent per claim and
    bounds its own lock, so a contended state file can't hang this exit path;
    `run_loop` wraps the call anyway so nothing — LockTimeout included — can
    stop the loop from returning 0.
    """
    from end_of_line.cli import main as cli_main

    rc = cli_main(
        [
            "notify-worker-dead",
            "--project", str(project_root),
            "--plan", plan,
            "--phase", phase,
            "--token", token,
        ]
    )
    if rc != 0:
        print(f"notify-worker-dead exited {rc}", file=sys.stderr)


def tick_once(
    state_path,
    phase: str,
    token: str,
    worker_pid: int,
    plan: str | None = None,
    *,
    pid_alive=None,
    ping=None,
) -> str:
    """One heartbeat tick → action. Pure decision core; the loop interprets it.

    `plan` is the cmdline marker for the default liveness probe (see
    `_make_pid_alive`); injected `pid_alive` overrides it for tests.
    """
    pid_alive = pid_alive or _make_pid_alive(plan)
    ping = ping or _ping
    if not pid_alive(worker_pid):
        return ACTION_EXIT_WORKER_DEAD
    try:
        ping(state_path, token, phase)
    except st.ClaimMismatch:
        return ACTION_EXIT_CLAIM_GONE
    except Exception as exc:  # noqa: BLE001 — any other failure is a strike
        print(f"heartbeat tick failed: {exc!r}", file=sys.stderr)
        return ACTION_STRIKE
    return ACTION_OK


def run_loop(
    *,
    project_root,
    plan: str,
    phase: str,
    token: str,
    worker_pid: int,
    state_path,
    log_path,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    sleep=time.sleep,
    tick=tick_once,
    notify_failure=None,
    report_death=None,
    max_ticks: int | None = None,
) -> int:
    """Tick until an exit action (or `max_ticks`, the test seam).

    Consecutive failures count up, the 3rd fires the notify path once,
    success resets the counter to zero. The notify call is best-effort —
    a broken transport must not kill the loop.

    On worker-dead exit the loop fires the `report_death` callback so the
    detection turns into an operator-visible event/inbox/notify (#104); the
    call is wrapped so no exception — LockTimeout included — can stop the
    loop returning 0. Claim-gone (a `clu complete`/`block` release) is a
    normal finish, NOT a death, so it never reports.
    """
    notify_failure = notify_failure or _notify_failure
    report_death = report_death or _report_death
    strikes = 0
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        action = tick(state_path, phase, token, worker_pid, plan)
        ticks += 1
        if action == ACTION_EXIT_WORKER_DEAD:
            try:
                report_death(project_root, plan, phase, token, state_path)
            except Exception as exc:  # noqa: BLE001 — report must never hang/kill the loop
                print(f"notify-worker-dead failed: {exc!r}", file=sys.stderr)
            print(f"heartbeat-daemon exiting: {action}", file=sys.stderr)
            return 0
        if action == ACTION_EXIT_CLAIM_GONE:
            print(f"heartbeat-daemon exiting: {action}", file=sys.stderr)
            return 0
        if action == ACTION_STRIKE:
            strikes += 1
            if strikes == STRIKE_LIMIT:
                try:
                    notify_failure(project_root, plan, phase, token, log_path)
                except Exception as exc:  # noqa: BLE001
                    print(f"notify-heartbeat-failure failed: {exc!r}", file=sys.stderr)
        else:
            strikes = 0
        sleep(interval)
    return 0


def _daemonize(log_path: Path) -> bool:
    """Double-fork + setsid. Returns True in the daemon, False in the parent.

    The grandchild's stdio is redirected to the sidecar log so every tick's
    stderr survives for post-mortem inspection.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.fork()
    if pid > 0:
        os.waitpid(pid, 0)  # reap the intermediate child; grandchild detaches
        return False
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(devnull)
    os.close(log_fd)
    return True


def run(
    *,
    project_root,
    plan: str,
    phase: str,
    token: str,
    worker_pid: int,
    state_path,
    log_path,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    detach: bool = True,
) -> int:
    """Detach (unless `detach=False`, the test seam) and run the loop.

    The parent returns 0 immediately; the daemon never returns — it
    `os._exit`s after the loop so it can't fall back into CLI plumbing.
    """
    if detach and not _daemonize(Path(log_path)):
        return 0
    rc = run_loop(
        project_root=project_root,
        plan=plan,
        phase=phase,
        token=token,
        worker_pid=worker_pid,
        state_path=state_path,
        log_path=log_path,
        interval=interval,
    )
    if detach:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(rc)
    return rc
