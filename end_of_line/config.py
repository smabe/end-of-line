"""Per-project `.orchestrator.json` loader."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = ".orchestrator.json"
ORCHESTRATOR_DIR = ".orchestrator"


@dataclass
class DispatchSpec:
    kind: str = "shell"
    command: str = ""


@dataclass
class ProjectConfig:
    project_root: Path
    plan_dir: str = "plans"
    dispatch: DispatchSpec = field(default_factory=DispatchSpec)
    notify: dict = field(default_factory=dict)

    def state_path(self, plan_slug: str) -> Path:
        return self.project_root / self.plan_dir / ORCHESTRATOR_DIR / f"{plan_slug}.state.json"


def load_project_config(project_root: Path) -> ProjectConfig:
    project_root = project_root.resolve()
    cfg_path = project_root / CONFIG_FILENAME
    if not cfg_path.exists():
        return ProjectConfig(project_root=project_root)
    raw = json.loads(cfg_path.read_text())
    disp = raw.get("dispatch", {})
    return ProjectConfig(
        project_root=project_root,
        plan_dir=raw.get("plan_dir", "plans"),
        dispatch=DispatchSpec(
            kind=disp.get("kind", "shell"),
            command=disp.get("command", ""),
        ),
        notify=raw.get("notify", {}),
    )
