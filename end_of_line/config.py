"""Per-project `.orchestrator.json` loader."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import state as st

CONFIG_FILENAME = ".orchestrator.json"
ORCHESTRATOR_DIR = ".orchestrator"

_KNOWN_KINDS = {"imessage", "discord"}
_KIND_REQUIRED: dict[str, list[str]] = {
    "imessage": ["to"],
    "discord": ["bot_token", "user_id"],
}


class ConfigError(ValueError):
    """Raised when `.orchestrator.json` fails schema validation."""


@dataclass
class ChannelSpec:
    kind: str
    kinds: frozenset[str] | None = None  # None = all notification kinds
    enabled: bool = True
    params: dict = field(default_factory=dict)


@dataclass
class DispatchSpec:
    kind: str = "shell"
    command: str = ""
    path: str = ""
    # Optional. When set, a corrupt queue.json triggers a synchronous
    # repair worker via this template (substitutes {corrupt_path},
    # {backup_path}, {diagnosis}, {schema_json}, {log_path}). Unset →
    # auto-repair disabled; clu falls back to a plain corrupt notification.
    repair_command: str | None = None


@dataclass
class NotifySpec:
    channels: tuple[ChannelSpec, ...] = field(default_factory=tuple)
    quiet_hours: tuple[str, str] | None = None
    inbound_auto_tick: bool = True

    @classmethod
    def imessage_only(
        cls,
        to: str,
        *,
        quiet_hours: tuple[str, str] | None = None,
        inbound_auto_tick: bool = True,
    ) -> "NotifySpec":
        """Factory for tests: single iMessage channel."""
        return cls(
            channels=(ChannelSpec(kind="imessage", params={"to": to}),),
            quiet_hours=quiet_hours,
            inbound_auto_tick=inbound_auto_tick,
        )


@dataclass
class ProjectConfig:
    project_root: Path
    plan_dir: str = "plans"
    dispatch: DispatchSpec = field(default_factory=DispatchSpec)
    notify: NotifySpec = field(default_factory=NotifySpec)
    test_command: str | None = None

    def queue_path(self) -> Path:
        """Per-project queue file. Lives in the same `.orchestrator/` dir as
        state files. No slug → no path-traversal validation needed."""
        return self.project_root / self.plan_dir / ORCHESTRATOR_DIR / "queue.json"

    def master_plan_path(self, plan_slug: str) -> Path:
        """The plan's `<slug>.md` master file under `plan_dir/`.

        Absence is the canonical "archived" signal — `cmd_unregister
        --all-archived` and `clu worktree gc` both treat a missing
        master as the plan having been moved out of the active set.
        """
        return self.project_root / self.plan_dir / f"{plan_slug}.md"

    def state_path(self, plan_slug: str) -> Path:
        path = self.project_root / self.plan_dir / ORCHESTRATOR_DIR / f"{plan_slug}.state.json"
        # Defense in depth — even after slug validation, refuse paths that
        # would resolve outside the project. Project_root isn't checked for
        # symlink escape because the user owns it; the slug is the attacker.
        resolved = path.resolve()
        base = (self.project_root / self.plan_dir / ORCHESTRATOR_DIR).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise st.InvalidSlug(f"state_path escapes orchestrator dir: {resolved}") from exc
        return path


def _validate_channel(raw: dict) -> ChannelSpec:
    kind = raw.get("kind")
    if kind not in _KNOWN_KINDS:
        raise ConfigError(f"notify.channels: unknown kind {kind!r} (expected one of {sorted(_KNOWN_KINDS)})")
    for required in _KIND_REQUIRED.get(kind, []):
        if not raw.get(required):
            raise ConfigError(f"notify.channels[kind={kind!r}]: missing required field {required!r}")
    kinds_raw = raw.get("kinds")
    kinds = frozenset(kinds_raw) if kinds_raw is not None else None
    enabled = bool(raw.get("enabled", True))
    params = {k: v for k, v in raw.items() if k not in {"kind", "kinds", "enabled"}}
    return ChannelSpec(kind=kind, kinds=kinds, enabled=enabled, params=params)


def load_project_config(project_root: Path) -> ProjectConfig:
    project_root = project_root.resolve()
    cfg_path = project_root / CONFIG_FILENAME
    if not cfg_path.exists():
        return ProjectConfig(project_root=project_root)
    raw = json.loads(cfg_path.read_text())
    disp = raw.get("dispatch", {})
    notify_raw = raw.get("notify", {})
    quiet = notify_raw.get("quiet_hours")
    raw_path = disp.get("path", "") or ""
    if raw_path:
        raw_path = ":".join(
            os.path.expanduser(seg) for seg in raw_path.split(":")
        )
    channels_raw = notify_raw.get("channels")
    if channels_raw is None:
        legacy_to = (notify_raw.get("imessage") or {}).get("to")
        channels_raw = [{"kind": "imessage", "to": legacy_to, "enabled": True}] if legacy_to else []
    channels = tuple(_validate_channel(c) for c in channels_raw)
    return ProjectConfig(
        project_root=project_root,
        plan_dir=raw.get("plan_dir", "plans"),
        dispatch=DispatchSpec(
            kind=disp.get("kind", "shell"),
            command=disp.get("command", ""),
            path=raw_path,
            repair_command=disp.get("repair_command") or None,
        ),
        notify=NotifySpec(
            channels=channels,
            quiet_hours=tuple(quiet) if quiet and len(quiet) == 2 else None,
            inbound_auto_tick=bool(notify_raw.get("inbound_auto_tick", True)),
        ),
        test_command=raw.get("test_command") or None,
    )
