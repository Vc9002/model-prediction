from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .units import UnitPolicy


CONTINUOUS_MODEL_ITERATION_POLICY: dict[str, bool | str] = {
    "status": "continuous",
    "parameter_freezes_allowed": True,
    "require_versioned_change": True,
    "require_walk_forward_ablation": True,
    "require_locked_holdout_before_promotion": True,
}


def _project_root() -> Path:
    configured = os.getenv("MODEL_PREDICTION_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "config/model.yaml").exists():
        return source_root
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "config/model.yaml").exists() and (candidate / "src/model_prediction").exists():
            return candidate
    return source_root


PROJECT_ROOT = _project_root()


def load_config() -> dict[str, Any]:
    path = config_path()
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    # Individual artifacts remain immutable for reproducibility; research moves
    # forward by creating and evaluating a new version.
    config["model_iteration_policy"] = CONTINUOUS_MODEL_ITERATION_POLICY.copy()
    return config


def config_path() -> Path:
    return Path(os.getenv("MODEL_PREDICTION_CONFIG", PROJECT_ROOT / "config/model.yaml"))


def ledger_path(config: dict[str, Any]) -> Path:
    path = Path(os.getenv("MODEL_PREDICTION_LEDGER", config["project"]["ledger_path"]))
    return path if path.is_absolute() else PROJECT_ROOT / path


def audit_path(config: dict[str, Any]) -> Path:
    path = Path(os.getenv("MODEL_PREDICTION_AUDIT", config["project"].get("audit_path", "data/events.jsonl")))
    return path if path.is_absolute() else PROJECT_ROOT / path


def entity_registry_path(config: dict[str, Any]) -> Path:
    path = Path(config["project"].get("entity_registry_path", "data/entities/teams.json"))
    return path if path.is_absolute() else PROJECT_ROOT / path


def polymarket_snapshot_path(config: dict[str, Any]) -> Path:
    path = Path(
        os.getenv(
            "MODEL_PREDICTION_POLYMARKET_SNAPSHOTS",
            config["project"].get("polymarket_snapshot_path", "data/polymarket_us_snapshots.jsonl"),
        )
    )
    return path if path.is_absolute() else PROJECT_ROOT / path


def market_odds_snapshot_path(config: dict[str, Any]) -> Path:
    path = Path(
        os.getenv(
            "MODEL_PREDICTION_MARKET_ODDS_SNAPSHOTS",
            config["project"].get(
                "market_odds_snapshot_path",
                "data/market_odds_snapshots.jsonl",
            ),
        )
    )
    return path if path.is_absolute() else PROJECT_ROOT / path


def unit_policy(config: dict[str, Any]) -> UnitPolicy:
    values = config["bankroll"]
    defaults = UnitPolicy()
    return UnitPolicy(
        **{
            field: values.get(field, getattr(defaults, field))
            for field in UnitPolicy.__dataclass_fields__
        }
    )
