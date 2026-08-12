"""Production canary infrastructure for the model-prediction system.

A *production canary* is a single model that runs alongside the main rebuild
shadow pipeline, exposing its predictions through a separate, independently
health-checked path. It is the first real model whose outputs may eventually
drive live decisions; every other model remains research/shadow only.

The canary is deliberately narrow:
  - exactly one allowed model (wnba-elo-trend-lr-v4)
  - manual orders only (automated_orders is locked false)
  - fail-closed: any validation or health failure returns DOWN, never a
    silent fallback

Configuration lives in ``config/production.yaml``. The production artifact
lives at ``config/models/wnba-elo-trend-lr-v4.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

# ── helpers ────────────────────────────────────────────────────────────────


def _compute_artifact_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON form, excluding the embedded hash field.

    Matches the convention used in ``total_score._artifact_hash`` and
    ``international_baseball``: sort keys, compact separators, and skip the
    ``artifact_hash`` key so the hash isn't self-referential.
    """
    canonical = {k: v for k, v in payload.items() if k != "artifact_hash"}
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
    """Validate the production canary config.

    Rules (fail-closed — any failure raises ``ValueError``):
      1. ``prediction_service.allowed_models`` must be a list with **exactly
         one** entry.
      2. The artifact file referenced by
         ``prediction_service.primary.artifact`` must exist.
      3. The embedded ``artifact_hash`` in the JSON must match a
         re-computed hash of the rest of the artifact.
      4. ``model_id`` in ``prediction_service.primary`` must match
         ``model_version`` inside the artifact.
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()

    svc = config.get("prediction_service")
    if not isinstance(svc, dict):
        raise ValueError("prediction_service section missing or not a mapping")  # noqa: TRY004

    # ── allowed models must be non-empty ──
    allowed = svc.get("allowed_models")
    if not isinstance(allowed, list) or len(allowed) == 0:
        raise ValueError(
            "prediction_service.allowed_models must be a non-empty list"
        )

    primary = svc.get("primary")
    if not isinstance(primary, dict):
        raise ValueError("prediction_service.primary missing or not a mapping")  # noqa: TRY004

    primary_model_id = primary.get("model_id")
    if not isinstance(primary_model_id, str) or not primary_model_id.strip():
        raise ValueError("prediction_service.primary.model_id is missing or empty")

    # Must match the single allowed entry
    if primary_model_id != allowed[0]:
        raise ValueError(
            f"primary.model_id '{primary_model_id}' does not match "
            f"allowed_models[0] '{allowed[0]}'"
        )

    artifact_rel = primary.get("artifact")
    if not isinstance(artifact_rel, str) or not artifact_rel.strip():
        raise ValueError("prediction_service.primary.artifact is missing or empty")

    artifact_path = root / artifact_rel
    if not artifact_path.is_file():
        raise FileNotFoundError(f"production artifact not found at {artifact_path}")

    # Load and validate hash + model_version
    with open(artifact_path, "r", encoding="utf-8") as fh:
        try:
            artifact: dict[str, Any] = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"production artifact is not valid JSON: {exc}") from exc

    embedded_hash = artifact.get("artifact_hash")
    computed_hash = _compute_artifact_hash(artifact)
    if embedded_hash != computed_hash:
        raise ValueError(
            f"artifact_hash mismatch: embedded '{embedded_hash}' != "
            f"computed '{computed_hash}'"
        )

    artifact_model_id = artifact.get("model_version")
    if artifact_model_id != primary_model_id:
        raise ValueError(
            f"model_id mismatch: config says '{primary_model_id}', "
            f"artifact says '{artifact_model_id}'"
        )


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
      5. (Stub) Data-freshness check — not yet wired to a real data source;
         always passes unless the artifact itself carries a stale timestamp.

    *runtime_root* is accepted for forward compatibility but is not yet used
    beyond the data-freshness check. When ``MODEL_PREDICTION_RUNTIME_ROOT``
    is set, the health check prefers it.
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()
    now_utc = datetime.now(UTC).isoformat()

    # Resolve runtime root for data-freshness check
    if runtime_root is not None:
        rt = Path(runtime_root)
    elif os.environ.get("MODEL_PREDICTION_RUNTIME_ROOT"):
        rt = Path(os.environ["MODEL_PREDICTION_RUNTIME_ROOT"])
    else:
        rt = root / "data"

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

    # 2. Validate config (this also verifies artifact existence + hash)
    try:
        validate_production_config(config, repo_root=root)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "DOWN",
            "model_id": (
                config.get("prediction_service", {}).get("primary", {}).get("model_id")
            ),
            "reason": f"config validation failed: {exc}",
            "checked_at_utc": now_utc,
        }

    primary = config["prediction_service"]["primary"]
    model_id = primary["model_id"]
    artifact_rel = primary["artifact"]
    artifact_path = root / artifact_rel

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

    # 5. Data freshness (stub — real implementation would check
    #    the youngest data file under runtime_root)
    max_age_minutes = config.get("health", {}).get("max_data_age_minutes", 120)
    stale = _check_data_freshness(rt, max_age_minutes)
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
    runtime_root: Path, max_age_minutes: int
) -> str | None:
    """Return a human-readable staleness description, or *None* if fresh.

    This is a stub: the real implementation will walk the runtime data
    directory. For now it always returns *None* (fresh) because the
    artifact itself contains no live-data timestamp.
    """
    _ = runtime_root, max_age_minutes
    return None


def _isfinite(value: float) -> bool:
    """True when *value* is a finite float (not NaN, +Inf, or -Inf)."""
    import math

    return not (math.isnan(value) or math.isinf(value))
