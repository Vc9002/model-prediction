"""Typed, fail-closed configuration for the isolated rebuild."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..runtime_paths import RuntimePaths

SHADOW_ONLY = True
EXECUTION_ENABLED = False
PRODUCTION_PROMOTION = False


class RebuildConfigError(ValueError):
    """Raised when rebuild configuration weakens an isolation invariant."""


@dataclass(frozen=True)
class RebuildMode:
    enabled: bool
    shadow_only: bool
    execution_enabled: bool
    production_promotion: bool


@dataclass(frozen=True)
class RebuildPaths:
    data_root: Path
    output_root: Path
    challenger_root: Path


@dataclass(frozen=True)
class RebuildExecution:
    allow_live_orders: bool
    allow_production_ledger_write: bool


@dataclass(frozen=True)
class SportConfig:
    enabled: bool
    status: str


@dataclass(frozen=True)
class RebuildConfig:
    repo_root: Path
    rebuild: RebuildMode
    paths: RebuildPaths
    execution: RebuildExecution
    sports: dict[str, SportConfig]


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RebuildConfigError(f"{name} must be a mapping")
    return value


def _bool(section: dict[str, Any], key: str) -> bool:
    value = section.get(key)
    if not isinstance(value, bool):
        raise RebuildConfigError(f"{key} must be an explicit boolean")
    return value


def _resolve(repo_root: Path, value: Any, key: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RebuildConfigError(f"paths.{key} must be a non-empty path")
    path = Path(value)
    return (repo_root / path).resolve() if not path.is_absolute() else path.resolve()


def load_rebuild_config(path: str | Path | None = None) -> RebuildConfig:
    config_path = Path(path).resolve() if path is not None else default_repo_root() / "config" / "rebuild.yaml"
    repo_root = config_path.parent.parent.resolve()
    try:
        raw = yaml.safe_load(config_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RebuildConfigError(f"cannot load rebuild config: {exc}") from exc
    root = _mapping(raw, "config")
    mode_raw = _mapping(root.get("rebuild"), "rebuild")
    paths_raw = _mapping(root.get("paths"), "paths")
    execution_raw = _mapping(root.get("execution"), "execution")
    sports_raw = _mapping(root.get("sports"), "sports")

    mode = RebuildMode(
        enabled=_bool(mode_raw, "enabled"),
        shadow_only=_bool(mode_raw, "shadow_only"),
        execution_enabled=_bool(mode_raw, "execution_enabled"),
        production_promotion=_bool(mode_raw, "production_promotion"),
    )
    execution = RebuildExecution(
        allow_live_orders=_bool(execution_raw, "allow_live_orders"),
        allow_production_ledger_write=_bool(execution_raw, "allow_production_ledger_write"),
    )
    if not mode.enabled:
        raise RebuildConfigError("rebuild.enabled must remain true for this CLI")
    if mode.shadow_only is not SHADOW_ONLY:
        raise RebuildConfigError("rebuild.shadow_only must remain true")
    if mode.execution_enabled is not EXECUTION_ENABLED:
        raise RebuildConfigError("rebuild.execution_enabled must remain false")
    if mode.production_promotion is not PRODUCTION_PROMOTION:
        raise RebuildConfigError("rebuild.production_promotion must remain false")
    if execution.allow_live_orders or execution.allow_production_ledger_write:
        raise RebuildConfigError("live orders and production-ledger writes are disabled by design")

    sports: dict[str, SportConfig] = {}
    for sport, value in sports_raw.items():
        section = _mapping(value, f"sports.{sport}")
        status = section.get("status")
        if not isinstance(status, str) or not status:
            raise RebuildConfigError(f"sports.{sport}.status must be a non-empty string")
        sports[str(sport)] = SportConfig(enabled=_bool(section, "enabled"), status=status)

    # data_root is mutable runtime state (raw cache, normalized parquet,
    # shadow.db) -- it now resolves through RuntimePaths, the same
    # repo_root/runtime_root split the dashboard reader already uses
    # (MODEL_PREDICTION_RUNTIME_ROOT), rather than always living inside
    # this Git checkout. output_root/challenger_root stay repo-relative:
    # they hold versioned evidence (audit reports, challenger model
    # configs) meant to be committed, matching RuntimePaths' own
    # rebuild_output_root split. The paths.data_root key is kept only as a
    # fail-closed sentinel -- changing it no longer changes where data
    # actually goes, so a stale/edited value must not silently be ignored.
    if paths_raw.get("data_root") != "data/rebuild":
        raise RebuildConfigError(
            "paths.data_root must remain the literal 'data/rebuild' sentinel; "
            "the real runtime location now resolves via RuntimePaths "
            "(MODEL_PREDICTION_RUNTIME_ROOT), not this config file"
        )
    data_root = RuntimePaths.resolve(repo_root=repo_root).rebuild_root

    return RebuildConfig(
        repo_root=repo_root,
        rebuild=mode,
        paths=RebuildPaths(
            data_root=data_root,
            output_root=_resolve(repo_root, paths_raw.get("output_root"), "output_root"),
            challenger_root=_resolve(repo_root, paths_raw.get("challenger_root"), "challenger_root"),
        ),
        execution=execution,
        sports=sports,
    )
