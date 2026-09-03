from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .units import UnitPolicy

# ── Shared thresholds (DD-8) — single source of truth ──────────────────────
# Moved from scattered hardcoded values. Identical numeric values; only the
# location changed. Do NOT change these values here without running the full
# ablation pipeline on locked holdout data.

UNIT_MIN_EDGE: float = 0.02
UNIT_INCREMENT: float = 0.25
TENNIS_MODEL_UNCERTAINTY: float = 0.05
ESPORTS_MIN_OBSERVATIONS: int = 50
ESPORTS_MIN_ACCURACY: float = 0.60
SIGNIFICANCE_THRESHOLD: float = 0.05

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


_VALID_MODEL_STATES = frozenset(
    {"research", "shadow_candidate", "shadow_qualified", "degraded", "suspended", "retired"}
)
_VALID_MODEL_ORIGINS = frozenset(
    {"statistical_model", "analyst_estimate", "market_baseline", "synthetic_test"}
)


def validate_config(config: dict[str, Any]) -> None:
    """Fail loudly at load time on a structurally broken config.

    Real bug class this guards against: a typo like ``status: reserach``
    previously surfaced as a cryptic ``ValueError: 'reserach' is not a valid
    ModelState`` deep inside a forecast call (or, worse, inside
    ``ModelState(configured_state)`` on a code path wrapped in a broad
    ``except (ValueError, KeyError): continue`` -- see the four cli.py
    forecast loops fixed 2026-08-02 -- which would have silently swallowed
    it and logged nothing for that entire sport). This is intentionally not
    an exhaustive schema: it checks the specific fields whose bad values
    cause a real runtime failure or silent misbehavior elsewhere in this
    codebase, not every key in the file.
    """
    errors: list[str] = []

    def require_section(name: str) -> dict[str, Any]:
        section = config.get(name)
        if not isinstance(section, dict):
            errors.append(f"'{name}' section is missing or not a mapping")
            return {}
        return section

    project = require_section("project")
    for key in ("ledger_path", "audit_path"):
        value = project.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"project.{key} must be a non-empty string")
    max_age = project.get("maximum_data_age_hours")
    if max_age is not None and (not isinstance(max_age, (int, float)) or max_age <= 0):
        errors.append("project.maximum_data_age_hours must be a positive number")

    bankroll = require_section("bankroll")
    unit_value = bankroll.get("unit_value_usd")
    if not isinstance(unit_value, (int, float)) or unit_value <= 0:
        errors.append("bankroll.unit_value_usd must be a positive number")
    kelly = bankroll.get("kelly_fraction")
    if kelly is not None and (not isinstance(kelly, (int, float)) or not 0 < kelly <= 1):
        errors.append("bankroll.kelly_fraction must be in (0, 1] when set")
    min_units, max_units = bankroll.get("min_pick_units"), bankroll.get("max_pick_units")
    for key, value in (("min_pick_units", min_units), ("max_pick_units", max_units)):
        if value is not None and (not isinstance(value, (int, float)) or value < 0):
            errors.append(f"bankroll.{key} must be a non-negative number when set")
    if isinstance(min_units, (int, float)) and isinstance(max_units, (int, float)) and min_units > max_units:
        errors.append("bankroll.min_pick_units must not exceed bankroll.max_pick_units")

    require_section("execution")

    polymarket_edge = config.get("polymarket_edge")
    if polymarket_edge is not None:
        if not isinstance(polymarket_edge, dict):
            errors.append("'polymarket_edge' section must be a mapping when present")
        else:
            for key in ("scanner_enabled", "ledger_enabled"):
                if not isinstance(polymarket_edge.get(key), bool):
                    errors.append(f"polymarket_edge.{key} must be a boolean")

    models = require_section("models")
    for name, spec in models.items():
        if not isinstance(spec, dict) or "status" not in spec:
            continue  # not a per-sport model entry (e.g. shared_features, market_residual, promotion)
        status = spec.get("status")
        if status not in _VALID_MODEL_STATES:
            errors.append(
                f"models.{name}.status {status!r} is not a valid ModelState "
                f"(expected one of {sorted(_VALID_MODEL_STATES)})"
            )
        origin = spec.get("origin")
        if origin is not None and origin not in _VALID_MODEL_ORIGINS:
            errors.append(
                f"models.{name}.origin {origin!r} is not a valid ModelOrigin "
                f"(expected one of {sorted(_VALID_MODEL_ORIGINS)})"
            )
        min_edge = spec.get("min_edge")
        if min_edge is not None and (not isinstance(min_edge, (int, float)) or not 0 <= min_edge < 1):
            errors.append(f"models.{name}.min_edge must be in [0, 1) when set")

    if errors:
        raise ValueError(
            "config/model.yaml failed validation:\n" + "\n".join(f"  - {error}" for error in errors)
        )


def load_config() -> dict[str, Any]:
    path = config_path()
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    # Individual artifacts remain immutable for reproducibility; research moves
    # forward by creating and evaluating a new version.
    config["model_iteration_policy"] = CONTINUOUS_MODEL_ITERATION_POLICY.copy()
    validate_config(config)
    return config


def polymarket_edge_enabled(config: dict[str, Any], component: str) -> bool:
    """Return the explicit operator gate for an edge scanner/ledger component."""
    if component not in {"scanner", "ledger"}:
        raise ValueError(f"unknown Polymarket edge component: {component}")
    section = config.get("polymarket_edge")
    if not isinstance(section, dict):
        return False
    return section.get(f"{component}_enabled") is True


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
        **{field: values.get(field, getattr(defaults, field)) for field in UnitPolicy.__dataclass_fields__}
    )


def economic_gate_thresholds(config: dict[str, Any]):
    from .economic_gate import EconomicGateThresholds

    values = config.get("economic_gate") or {}
    defaults = EconomicGateThresholds()
    return EconomicGateThresholds(
        **{
            field: values.get(field, getattr(defaults, field))
            for field in EconomicGateThresholds.__dataclass_fields__
        }
    )
