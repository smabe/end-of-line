"""`clu demo` — scaffold + run + tear down a synthetic demo fleet.

`clu demo` is a verify-the-install tool: it stands up a handful of throwaway
`demo-*` plans in the *real* registry, dispatches one synthetic worker per
scenario through clu's real init -> tick -> claim -> transcript pipeline, and
lights up `clu top` / `clu serve` with busy / idle / blocked / dead rows — then
guarantees teardown (Ctrl-C trap, `clu demo down`, and a `clu doctor` sweep).

Decision A (operator-approved): the demo lives in the real
`~/.config/clu/registry.json`, namespaced `demo-`, rather than an isolated
registry — so the operator's own `clu top`/`clu serve` see it. The `demo-`
prefix makes the plans queryable + visually distinct and teardown-by-marker
exact; three teardown paths bound orphan risk.

Each scenario needs its own `dispatch.command` (the `--scenario` flag differs),
and `dispatch.command` is project-level config, so each scenario gets its own
project dir under `demo_root()`. The scaffolded `.orchestrator.json` masks every
inherited global notify channel — the demo must never reach the operator's
phone, and the cron supervisor (which we can't suppress in-process) would
otherwise notify on the dead worker.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from end_of_line import demo_worker, notify, registry
from end_of_line import state as st
from end_of_line import top
from end_of_line._xdg_guard import clu_config_dir
from end_of_line.config import CONFIG_FILENAME

DEMO_SLUG_PREFIX = "demo-"


def demo_root() -> Path:
    """Root of the throwaway demo project tree (under the real clu config dir)."""
    return clu_config_dir() / "demo"


@dataclass(frozen=True)
class DemoPlan:
    scenario: str
    slug: str
    project_root: Path


def _slug(scenario: str) -> str:
    return f"{DEMO_SLUG_PREFIX}{scenario}"


def _master_plan(slug: str) -> str:
    """A one-phase master with a parseable `## Sessions index` (cmd_init needs
    only the master; the synthetic worker never reads a sub-plan file)."""
    return (
        f"# Demo plan: {slug}\n\n"
        "## Sessions index\n\n"
        "| Session | Plan file | Scope | Effort |\n"
        "|---|---|---|---|\n"
        f"| a | `{slug}-a.md` | synthetic demo work | 1h |\n"
    )


def _orchestrator_config(scenario: str) -> dict:
    """`.orchestrator.json` for one demo plan: the scenario's dispatch command
    plus a mask that disables every inherited global notify channel."""
    masks = [{"kind": kind, "enabled": False} for kind in notify._NOTIFIER_REGISTRY]
    return {
        "dispatch": {"command": demo_worker.command_template(scenario)},
        "notify": {"channels": masks},
    }


def scaffold(scenarios=demo_worker.SCENARIOS, *, root: Path | None = None) -> list[DemoPlan]:
    """Write each scenario's throwaway project (`.orchestrator.json` + master
    plan) under `root`. Pure filesystem — no registry/dispatch side effects."""
    root = root or demo_root()
    plans: list[DemoPlan] = []
    for scenario in scenarios:
        slug = _slug(scenario)
        proj = root / slug
        (proj / "plans").mkdir(parents=True, exist_ok=True)
        (proj / CONFIG_FILENAME).write_text(json.dumps(_orchestrator_config(scenario), indent=2))
        (proj / "plans" / f"{slug}.md").write_text(_master_plan(slug))
        # Resolve so the registered root matches dispatch's {project} (both
        # resolve()), keeping the locator's cwd comparison exact.
        plans.append(DemoPlan(scenario=scenario, slug=slug, project_root=proj.resolve()))
    return plans


def _cli(argv: list[str]) -> int:
    """Run a `clu` subcommand in-process (lazy import dodges a cli<->demo cycle)."""
    from end_of_line.cli import main

    return main(argv)


def _dispatch(plan: DemoPlan) -> None:
    """Tick the plan once so its synthetic worker is claimed + spawned. Split
    out so tests can stub the real subprocess spawn."""
    _cli(["tick", "--project", str(plan.project_root), "--plan", plan.slug])


def up(scenarios=demo_worker.SCENARIOS, *, root: Path | None = None) -> list[DemoPlan]:
    """Scaffold, init (auto-registers), and dispatch each demo plan."""
    plans = scaffold(scenarios, root=root)
    for plan in plans:
        # --no-notify-prompt: `clu demo` runs in the operator's terminal (a
        # TTY), so without this `cmd_init`'s interactive "Wire iMessage? / Wire
        # Discord?" wizard fires for every demo plan AND overwrites the masked
        # notify config we just scaffolded. The demo configures notify itself.
        _cli(["init", "--no-notify-prompt", "--project", str(plan.project_root), "--plan", plan.slug])
        _dispatch(plan)
    return plans


def sweep() -> list[str]:
    """Every `demo-*` plan slug currently in the registry (for the doctor sweep)."""
    return [e.plan_slug for e in registry.entries() if e.plan_slug.startswith(DEMO_SLUG_PREFIX)]


def down(*, root: Path | None = None, projects_root: Path = top.PROJECTS_ROOT) -> list[str]:
    """Tear the demo down: kill each live worker pgroup, drop every synthetic
    transcript dir, unregister every `demo-*` plan, and remove the project tree.

    Idempotent — safe to call from a signal handler and again from the `finally`
    block, and safe when nothing is left to clean. Non-`demo-*` registry entries
    are never touched. Returns the slugs removed.
    """
    root = root or demo_root()
    removed: list[str] = []
    for entry in list(registry.entries()):
        if not entry.plan_slug.startswith(DEMO_SLUG_PREFIX):
            continue
        data = registry.load_entry_state(entry)
        claim = (data or {}).get("current_claim")
        if claim and claim.get("pgid"):
            # cmdline_match guards PID reuse: only signal a group still carrying
            # the demo slug marker.
            st.reap_orphan_pgroup(claim["pgid"], cmdline_match=entry.plan_slug)
        # Remove the project's whole transcript dir — re-dispatch mints a fresh
        # session_id per attempt, so one claim's session_id misses prior files.
        enc = Path(projects_root) / top.encode_project_dir(entry.project_root)
        shutil.rmtree(enc, ignore_errors=True)
        registry.unregister(Path(entry.project_root), entry.plan_slug)
        removed.append(entry.plan_slug)
    shutil.rmtree(root, ignore_errors=True)
    return removed
