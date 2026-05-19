"""Per-project `.orchestrator.json` loader."""
from __future__ import annotations

import json
import os
import sys
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
class CoolantSpec:
    """Coolant integration settings (closes #54).

    Coolant is a Claude Code plugin that throttles parallel-agent thermal
    load. When enabled (default), clu emits lifecycle events on worker
    dispatch + every claim release so coolant's counter + gate.sh's
    parallel-mode formula see clu workers. `script_dir` overrides
    auto-discovery of `~/.claude/plugins/cache/.../scripts/`.
    """
    enabled: bool = True
    script_dir: str | None = None

    def release_kwargs(self) -> dict:
        """Map to the kwargs that `state.release_claim_and_emit` accepts.

        Centralized so the 4 cli.py callback handlers don't each have to
        spell out `coolant_enabled=cfg.coolant.enabled,
        coolant_script_override=cfg.coolant.script_dir`.
        """
        return {
            "coolant_enabled": self.enabled,
            "coolant_script_override": self.script_dir,
        }


@dataclass
class QualitySpec:
    verify_command: str | None = None
    simplify_threshold: dict | None = None  # {"files": int, "lines": int}
    # Opt-out for projects whose authoritative test runner is an MCP tool
    # (or anything else `clu verify` can't reasonably re-run from a shell).
    # When False, cmd_complete skips the verify-attestation refusal AND
    # emits EVENT_VERIFY_POLICY_SKIPPED so the audit trail records the
    # bypass. The simplify gate is unaffected. (#61)
    verify_required: bool = True


@dataclass
class ProjectConfig:
    project_root: Path
    plan_dir: str = "plans"
    dispatch: DispatchSpec = field(default_factory=DispatchSpec)
    notify: NotifySpec = field(default_factory=NotifySpec)
    test_command: str | None = None
    auto_archive: bool = True
    tick_on_action: bool = True
    quality: QualitySpec = field(default_factory=QualitySpec)
    coolant: CoolantSpec = field(default_factory=CoolantSpec)
    lease_ttl_scale: float = 0.5

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

    def resolved_verify_command(self) -> str | None:
        return self.quality.verify_command or self.test_command

    def simplify_threshold_or_default(self) -> tuple[int, int]:
        t = self.quality.simplify_threshold
        if t is None:
            return (1, 30)
        return (int(t.get("files", 1)), int(t.get("lines", 30)))


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


def _validate_quality(raw: dict) -> QualitySpec:
    q = raw.get("quality") or {}
    vc = q.get("verify_command")
    if vc is not None and not isinstance(vc, str):
        raise ConfigError(
            f"quality.verify_command: must be string, got {type(vc).__name__!r}"
        )
    st_raw = q.get("simplify_threshold")
    if st_raw is not None:
        if not isinstance(st_raw, dict):
            raise ConfigError(
                "quality.simplify_threshold: must be object with files+lines"
            )
        for key in ("files", "lines"):
            v = st_raw.get(key)
            if not isinstance(v, int) or v < 0:
                raise ConfigError(
                    f"quality.simplify_threshold.{key}: must be non-negative int"
                )
    vr_raw = q.get("verify_required", True)
    if not isinstance(vr_raw, bool):
        raise ConfigError(
            f"quality.verify_required: must be bool, got {type(vr_raw).__name__!r}"
        )
    return QualitySpec(
        verify_command=vc,
        simplify_threshold=st_raw,
        verify_required=vr_raw,
    )


def _validate_coolant(raw: dict) -> CoolantSpec:
    block = raw.get("coolant") or {}
    enabled = block.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(
            f"coolant.enabled: must be bool, got {type(enabled).__name__!r}"
        )
    script_dir = block.get("script_dir")
    if script_dir is not None and not isinstance(script_dir, str):
        raise ConfigError(
            f"coolant.script_dir: must be string or null, "
            f"got {type(script_dir).__name__!r}"
        )
    return CoolantSpec(enabled=enabled, script_dir=script_dir)


def _validate_lease_ttl_scale(raw: dict) -> float:
    val = raw.get("lease_ttl_scale", 0.5)
    try:
        val = float(val)
    except (TypeError, ValueError):
        return 0.5
    if val < 0:
        print(
            f"warning: lease_ttl_scale={val!r} is negative; using default 0.5",
            file=sys.stderr,
        )
        return 0.5
    return val


def _validate_auto_archive(raw: dict) -> bool:
    value = raw.get("auto_archive", True)
    if not isinstance(value, bool):
        raise ConfigError(
            f"auto_archive: must be a boolean, got {type(value).__name__!r}"
        )
    return value


def _validate_tick_on_action(raw: dict) -> bool:
    value = raw.get("tick_on_action", True)
    if not isinstance(value, bool):
        raise ConfigError(
            f"tick_on_action: must be a boolean, got {type(value).__name__!r}"
        )
    return value


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
        auto_archive=_validate_auto_archive(raw),
        tick_on_action=_validate_tick_on_action(raw),
        quality=_validate_quality(raw),
        coolant=_validate_coolant(raw),
        lease_ttl_scale=_validate_lease_ttl_scale(raw),
    )
