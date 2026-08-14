"""Production canary infrastructure for the model-prediction system.

A *production canary* is a single model that runs alongside the main rebuild
shadow pipeline, exposing its predictions through a separate, independently
health-checked path. It is the first real model whose outputs may eventually
drive live decisions; every other model remains research/shadow only.

The canary is deliberately narrow:
  - an explicit allowlist of models (primary must be allowlisted; the
    list is non-empty — currently 13 models, primary wnba-elo-trend-lr-v4)
  - manual orders only (automated_orders is locked false)
  - fail-closed: any validation or health failure returns DOWN, never a
    silent fallback

Configuration lives in ``config/production.yaml``. The production artifact
lives at ``config/models/wnba-elo-trend-lr-v4.json``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .production_registry import (
    ProductionModelRegistry,
)
from .production_registry import (
    # Re-exported so historical importers (tests, cli_production) keep a
    # single hash convention without each defining their own copy.
    compute_artifact_hash as _compute_artifact_hash,  # noqa: F401
)

# ── helpers ────────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Resolve the project root consistently with the rest of the package."""
    from .config import PROJECT_ROOT

    return PROJECT_ROOT


# ── config loading ─────────────────────────────────────────────────────────


def load_production_config(
    *, repo_root: Path | str | None = None
) -> dict[str, Any]:
    """Load and return the production canary configuration.

    Reads ``config/production.yaml`` relative to *repo_root* (defaults to
    the project root detected by ``model_prediction.config``).
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()
    config_path = root / "config" / "production.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"production config not found at {config_path}")
    with open(config_path, "r", encoding="utf-8") as fh:
        config: dict[str, Any] = yaml.safe_load(fh) or {}
    return config


# ── validation ──────────────────────────────────────────────────────────────


def validate_production_config(
    config: dict[str, Any], *, repo_root: Path | str | None = None
) -> None:
    """Validate the production config via the single production registry.

    Rules (fail-closed — any failure raises ``ValueError``):
      1. ``prediction_service.allowed_models`` / ``models`` must be
         non-empty; the primary model must be one of the entries.
      2. The artifact file referenced by the primary must exist.
      3. The embedded ``artifact_hash`` in the JSON must match a
         re-computed hash of the rest of the artifact.
      4. ``model_id`` in ``prediction_service.primary`` must match
         ``model_version`` inside the artifact.

    Additionally (the registry's startup contract), **every** enabled entry
    is validated: a broken *secondary* model is failed closed inside the
    registry (recorded ``load_error``, resolution refused) rather than
    raising here — only the primary's failure propagates.
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()
    ProductionModelRegistry.from_config(config, repo_root=root)


# ── model access ────────────────────────────────────────────────────────────


def get_production_model(config: dict[str, Any]) -> dict[str, Any]:
    """Return the single production canary model as a dict.

    The caller is expected to have already called ``validate_production_config``.
    """
    svc = config["prediction_service"]
    return {
        "sport": svc["primary"]["sport"],
        "market": svc["primary"]["market"],
        "model_id": svc["primary"]["model_id"],
        "artifact": svc["primary"]["artifact"],
    }


# ── health check ────────────────────────────────────────────────────────────


def health_check(
    config: dict[str, Any] | None = None,
    runtime_root: Path | str | None = None,
    *,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    """Run a fail-closed health check against the production canary.

    Returns a dict with at least ``status`` (one of ``"HEALTHY"``,
    ``"DEGRADED"``, or ``"DOWN"``), ``model_id``, ``artifact_hash``, and
    ``checked_at_utc``. Additional detail keys are added when the status is
    not ``HEALTHY``.

    Checks performed:
      1. Load and validate ``config/production.yaml``.
      2. Verify the artifact file exists.
      3. Parse the artifact JSON and re-validate its embedded hash.
      4. Verify every probability value in the artifact is finite.
      5. Data-freshness check: the latest prediction in the canonical
         ``production/production.db`` (under the resolved runtime root)
         must be younger than ``health.max_data_age_minutes``; a missing
         database or stale record degrades the check. The legacy
         ``production_state.json`` is NOT consulted (compatibility
         consumers only — consolidation item 12).

    *runtime_root* is accepted to pin the mutable-state root explicitly;
    otherwise the resolution follows ``RuntimePaths``
    (``MODEL_PREDICTION_RUNTIME_ROOT`` when set).
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()
    now_utc = datetime.now(UTC).isoformat()

    # Resolve runtime root for data-freshness check. Fail closed: an
    # env-less operational invocation must not fall back to a repo-local
    # second runtime (split-brain).
    if runtime_root is not None:
        rt = Path(runtime_root)
    elif os.environ.get("MODEL_PREDICTION_RUNTIME_ROOT"):
        rt = Path(os.environ["MODEL_PREDICTION_RUNTIME_ROOT"])
    else:
        raise RuntimeError(
            "MODEL_PREDICTION_RUNTIME_ROOT is required for the canary "
            "health check; refusing the repo-local data/ fallback."
        )

    details: dict[str, Any] = {}

    # 1. Load config
    if config is None:
        try:
            config = load_production_config(repo_root=root)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "DOWN",
                "reason": f"config load failed: {exc}",
                "checked_at_utc": now_utc,
            }

    # 2. Build + validate the production registry (this also verifies
    #    artifact existence + hash for the primary AND resolves every other
    #    enabled entry, failing them closed per model).
    try:
        registry = ProductionModelRegistry.from_config(config, repo_root=root)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "DOWN",
            "model_id": (
                config.get("prediction_service", {}).get("primary", {}).get("model_id")
            ),
            "reason": f"config validation failed: {exc}",
            "checked_at_utc": now_utc,
        }

    primary = registry.primary
    model_id = primary.model_id
    artifact_rel = primary.artifact or ""
    artifact_path = root / artifact_rel

    # Per-model registry contract status — every entry, not just the
    # primary. A broken secondary model degrades the overall status.
    details["models"] = {
        entry.model_id: (
            "ok" if entry.available else f"failed: {entry.load_error}"
        )
        for entry in registry.entries.values()
    }
    if registry.problem_entries():
        details["failed_models"] = [
            entry.model_id for entry in registry.problem_entries()
        ]
        return {
            "status": "DEGRADED",
            "model_id": model_id,
            "artifact_hash": primary.artifact_hash,
            "reason": (
                f"{len(registry.problem_entries())} production model(s) "
                "failed contract validation"
            ),
            "checked_at_utc": now_utc,
            "details": details,
        }

    # 3. Parse the artifact
    try:
        with open(artifact_path, "r", encoding="utf-8") as fh:
            artifact: dict[str, Any] = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "DOWN",
            "model_id": model_id,
            "reason": f"artifact parse failed: {exc}",
            "checked_at_utc": now_utc,
        }

    artifact_hash = artifact.get("artifact_hash", "")

    # 4. Finite-probability check
    if config.get("health", {}).get("require_finite_probabilities", True):
        try:
            _check_finite_probabilities(artifact)
        except ValueError as exc:
            return {
                "status": "DOWN",
                "model_id": model_id,
                "artifact_hash": artifact_hash,
                "reason": str(exc),
                "checked_at_utc": now_utc,
                "details": details,
            }

    # 4b. Probability normalization — config says normalization is
    #     required, so health must ENFORCE it: binary outputs must satisfy
    #     |sum(p) - 1| <= 1e-6. Artifact-embedded pairs are checked where
    #     they exist; the always-checked source is the stored predictions
    #     in production.db (the canonical operational record, item 12).
    if config.get("health", {}).get("require_probability_normalization", True):
        try:
            _check_probability_normalization(artifact, root, rt)
        except ValueError as exc:
            return {
                "status": "DOWN",
                "model_id": model_id,
                "artifact_hash": artifact_hash,
                "reason": str(exc),
                "checked_at_utc": now_utc,
                "details": details,
            }

    # 5. Data freshness — the canary's last recorded prediction run under
    #    runtime_root must be younger than max_data_age_minutes.
    max_age_minutes = config.get("health", {}).get("max_data_age_minutes", 120)
    stale = _check_data_freshness(root, rt, max_age_minutes)
    if stale:
        details["data_freshness"] = stale
        return {
            "status": "DEGRADED",
            "model_id": model_id,
            "artifact_hash": artifact_hash,
            "reason": stale,
            "checked_at_utc": now_utc,
            "details": details,
        }

    return {
        "status": "HEALTHY",
        "model_id": model_id,
        "artifact_hash": artifact_hash,
        "checked_at_utc": now_utc,
        "details": details,
    }


def _check_finite_probabilities(artifact: dict[str, Any]) -> None:
    """Scan known probability fields in the artifact for NaN / ±Inf.

    Only inspects the top-level ``qualification`` block (``hit_rate``,
    ``called_rate``, ``minimum_hit_rate``, ``selectivity``, and
    ``reliability_buckets[*].{lower,upper,mean_p,hit_rate}``) plus any
    ``market_models.*.confidence_threshold`` values. This is deliberately
    not a full deep scan — it targets the fields that feed downstream
    decision logic.
    """

    def _check(val: Any, label: str) -> None:
        if isinstance(val, (int, float)) and not _isfinite(val):
            raise ValueError(f"non-finite probability in artifact: {label}={val}")

    qual = artifact.get("qualification", {})
    if isinstance(qual, dict):
        for key in ("hit_rate", "called_rate", "minimum_hit_rate", "selectivity"):
            _check(qual.get(key), f"qualification.{key}")
        for bucket in qual.get("reliability_buckets", []) or []:
            if isinstance(bucket, dict):
                for key in ("lower", "upper", "mean_p", "hit_rate"):
                    _check(bucket.get(key), f"qualification.reliability_bucket.{key}")

    for mm_key, mm_val in (artifact.get("market_models") or {}).items():
        if isinstance(mm_val, dict):
            _check(mm_val.get("confidence_threshold"), f"market_models.{mm_key}.confidence_threshold")


def _check_data_freshness(
    repo_root: Path, runtime_root: Path, max_age_minutes: int
) -> str | None:
    """Return a human-readable staleness description, or *None* if fresh.

    Freshness is judged on the canonical production database
    (``production/production.db`` under the resolved runtime root), NOT
    the legacy ``production_state.json`` — one operational truth, one
    storage (consolidation item 12). The state file remains for
    compatibility consumers only.
    """
    from .production_store import read_latest_prediction_utc
    from .runtime_paths import RuntimePaths

    paths = RuntimePaths(repo_root=repo_root, runtime_root=runtime_root)
    last_prediction = read_latest_prediction_utc(paths)
    if last_prediction is None:
        return (
            f"no production predictions recorded in {paths.production_db} "
            "(never recorded a prediction run)"
        )
    try:
        last_dt = datetime.fromisoformat(last_prediction)
    except ValueError:
        return f"unparseable latest prediction timestamp {last_prediction!r}"
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=UTC)
    age_minutes = (datetime.now(UTC) - last_dt).total_seconds() / 60
    if age_minutes > max_age_minutes:
        return (
            f"last prediction {age_minutes:.0f} minutes ago "
            f"(max_data_age_minutes={max_age_minutes})"
        )
    return None


_NORMALIZATION_TOLERANCE = 1e-6
_BINARY_PROBABILITY_PAIRS = (
    ("home_probability", "away_probability"),
    ("prob_home", "prob_away"),
    ("home_win_probability", "away_win_probability"),
)


def _check_probability_normalization(
    artifact: dict[str, Any], repo_root: Path, runtime_root: Path
) -> None:
    """Enforce the normalization contract on every binary probability
    pair the system stores or emits.

    Two sources are checked:
    1. Artifact-embedded pairs (where an artifact stores explicit
       probabilities) — each pair must satisfy |sum - 1| <= 1e-6 and each
       value must lie in [0, 1].
    2. The stored predictions in production.db (the canonical record) —
       the most recent rows' probability pairs must satisfy the same
       bound. A stored non-normalized pair is a DOWN, not a warning:
       config says normalization is required, and health enforces it.
    """
    for market, model in (artifact.get("market_models") or {}).items():
        if not isinstance(model, dict):
            continue
        for home_key, away_key in _BINARY_PROBABILITY_PAIRS:
            home = model.get(home_key)
            away = model.get(away_key)
            if home is None and away is None:
                continue
            if home is None or away is None:
                raise ValueError(
                    f"market_models.{market} has {home_key} without {away_key}"
                )
            for label, value in ((home_key, home), (away_key, away)):
                if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                    raise ValueError(
                        f"market_models.{market}.{label} out of [0,1]: {value!r}"
                    )
            if abs(float(home) + float(away) - 1.0) > _NORMALIZATION_TOLERANCE:
                raise ValueError(
                    f"market_models.{market} probabilities not normalized: "
                    f"{home} + {away} = {float(home) + float(away)} "
                    f"(tolerance {_NORMALIZATION_TOLERANCE})"
                )

    from .production_store import read_recent_probabilities
    from .runtime_paths import RuntimePaths

    paths = RuntimePaths(repo_root=repo_root, runtime_root=runtime_root)
    for pair in read_recent_probabilities(paths, limit=20):
        values = [v for v in pair.values() if isinstance(v, (int, float))]
        if not values:
            continue
        total = sum(values)
        if any(not 0.0 <= v <= 1.0 for v in values):
            raise ValueError(
                f"stored prediction probabilities out of [0,1]: {pair}"
            )
        if abs(total - 1.0) > _NORMALIZATION_TOLERANCE:
            raise ValueError(
                f"stored prediction probabilities not normalized: {pair} "
                f"sums to {total} (tolerance {_NORMALIZATION_TOLERANCE})"
            )


def _isfinite(value: float) -> bool:
    """True when *value* is a finite float (not NaN, +Inf, or -Inf)."""
    import math

    return not (math.isnan(value) or math.isinf(value))
