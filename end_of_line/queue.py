"""Per-project plan queue.

clu's cron tick advances *phases within* a plan, but inter-plan
transitions need someone to invoke `clu init` for the next plan. The
queue holds that list so an operator can scribble plans, queue them,
walk away, and wake up to a drained chain.

Storage lives next to per-plan state files at
`<plan_dir>/.orchestrator/queue.json`. See `docs/contract.md` for the
schema and `.claude/plans/plan-queue-master.md` for the design pass.
"""
from __future__ import annotations

from pathlib import Path

from . import state as st

SCHEMA_VERSION = 1


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "queue": [], "history": []}


def load(path: Path) -> dict:
    return st.load(path, expected_version=SCHEMA_VERSION)


def save_atomic(path: Path, data: dict) -> None:
    st.save_atomic(path, data)


def mutate(path: Path):
    """lock + load + yield-for-mutation + atomic write. Tolerates a missing
    file (first `queue add` creates it) via `state.locked_json`."""
    return st.locked_json(path, expected_version=SCHEMA_VERSION, empty=_empty)
