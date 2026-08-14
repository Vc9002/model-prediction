"""Tests for the single production model registry (consolidation A-1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from model_prediction.production_canary import _compute_artifact_hash, health_check
from model_prediction.production_registry import (
    IMPLEMENTATION_CODE_BACKED,
    IMPLEMENTATION_JSON_ARTIFACT,
    ProductionModelRegistry,
)

REPO = Path(__file__).resolve().parents[1]


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _make_artifact(
    model_id: str, sport: str = "wnba", *, include_hash: bool = True
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model_version": model_id,
        "sport": sport,
        "schema_version": "1",
        "market_models": {"moneyline": {"confidence_threshold": 0.5}},
        "qualification": {"hit_rate": 0.6, "calls": 10, "hits": 6},
    }
    if include_hash:
        payload["artifact_hash"] = _compute_artifact_hash(payload)
    return payload


def _make_v3_config(models: list[dict[str, Any]], primary_id: str) -> dict[str, Any]:
    return {
        "schema_version": "3",
        "prediction_service": {
            "enabled": True,
            "mode": "production",
            "primary": {"sport": "WNBA", "market": "moneyline", "model_id": primary_id},
            "models": models,
            "fallback_action": "no_prediction",
        },
        "execution": {"automated_orders": False, "manual_orders_only": True},
        "health": {"max_data_age_minutes": 120},
    }


def _v3_entry(model_id: str, artifact: str | None = None, **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "model_id": model_id,
        "sport": "WNBA",
        "market": "moneyline",
        "implementation": IMPLEMENTATION_JSON_ARTIFACT,
        "enabled": True,
    }
    if artifact:
        entry["artifact"] = artifact
    entry.update(overrides)
    return entry


def _setup_v3(
    tmp_path: Path,
    models: list[dict[str, Any]],
    primary_id: str,
    *,
    write_artifacts: bool = True,
) -> Path:
    repo = tmp_path / "repo"
    _write_yaml(repo / "config" / "production.yaml", _make_v3_config(models, primary_id))
    if write_artifacts:
        for m in models:
            if m.get("artifact"):
                _write_json(repo / m["artifact"], _make_artifact(m["model_id"]))
    return repo


# ── the checked-in config is a testable contract ────────────────────────────


def test_real_production_yaml_resolves_every_model() -> None:
    """The actual checked-in config/production.yaml must resolve every
    enabled entry — the CI contract item from the consolidation plan. A
    model whose contract cannot resolve is failed closed, so a fully
    healthy registry has zero problem entries."""
    registry = ProductionModelRegistry.load(REPO)

    assert registry.schema_version == "3"
    assert len(registry.entries) == 13
    assert len(registry.available_entries()) == 13
    assert registry.problem_entries() == []
    assert registry.primary.model_id == "wnba-elo-trend-lr-v4"
    assert registry.primary.artifact_hash

    mlb = registry.entries["mlb-elo-trend-lr-v8"]
    assert mlb.rollback_model == "mlb-elo-trend-lr-v7"
    assert mlb.feature_schema_version == "1"

    soccer = registry.entries["soccer-poisson-dc-v1"]
    assert soccer.implementation == IMPLEMENTATION_CODE_BACKED
    assert soccer.entry == "model_prediction.models.soccer:soccer_model"
    tennis = registry.entries["tennis-surface-elo-v1"]
    assert tennis.implementation == IMPLEMENTATION_CODE_BACKED
    assert tennis.entry == "model_prediction.models.tennis:tennis_model"


# ── fail-closed per model ───────────────────────────────────────────────────


def test_broken_secondary_model_is_failed_closed_not_a_load_failure(tmp_path: Path) -> None:
    """A secondary model whose artifact is missing must be disabled with a
    recorded load_error while every other model keeps resolving."""
    models = [
        _v3_entry("wnba-elo-trend-lr-v4", "config/models/wnba-elo-trend-lr-v4.json"),
        _v3_entry("cs2-tiered-elo-v6", "config/models/cs2-missing.json"),
    ]
    repo = _setup_v3(tmp_path, models, "wnba-elo-trend-lr-v4", write_artifacts=False)
    _write_json(
        repo / "config/models/wnba-elo-trend-lr-v4.json",
        _make_artifact("wnba-elo-trend-lr-v4"),
    )

    registry = ProductionModelRegistry.load(repo)

    assert registry.primary.available
    cs2 = registry.entries["cs2-tiered-elo-v6"]
    assert not cs2.available
    assert "not found" in (cs2.load_error or "")
    assert [e.model_id for e in registry.problem_entries()] == ["cs2-tiered-elo-v6"]
    assert registry.resolve("WNBA", "moneyline") is registry.primary
    assert registry.resolve("CS2", "moneyline") is None  # resolution refuses it


def test_broken_primary_is_a_hard_config_error(tmp_path: Path) -> None:
    models = [
        _v3_entry("wnba-elo-trend-lr-v4", "config/models/wnba-missing.json"),
    ]
    repo = _setup_v3(tmp_path, models, "wnba-elo-trend-lr-v4", write_artifacts=False)

    with pytest.raises(ValueError, match="failed validation"):
        ProductionModelRegistry.load(repo)


def test_hash_mismatch_disables_the_entry(tmp_path: Path) -> None:
    models = [
        _v3_entry("wnba-elo-trend-lr-v4", "config/models/wnba-elo-trend-lr-v4.json"),
    ]
    repo = _setup_v3(tmp_path, models, "wnba-elo-trend-lr-v4")
    # Tamper with the payload without fixing the embedded hash.
    artifact_path = repo / "config/models/wnba-elo-trend-lr-v4.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["qualification"]["hit_rate"] = 0.99
    _write_json(artifact_path, payload)

    with pytest.raises(ValueError, match="failed validation"):
        ProductionModelRegistry.load(repo)


def test_model_version_mismatch_disables_the_entry(tmp_path: Path) -> None:
    models = [
        _v3_entry("wnba-elo-trend-lr-v4", "config/models/wnba-elo-trend-lr-v4.json"),
    ]
    repo = _setup_v3(tmp_path, models, "wnba-elo-trend-lr-v4")
    artifact_path = repo / "config/models/wnba-elo-trend-lr-v4.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["model_version"] = "wnba-elo-trend-lr-v3"
    _write_json(artifact_path, payload)

    with pytest.raises(ValueError, match="failed validation"):
        ProductionModelRegistry.load(repo)


def test_unknown_implementation_type_is_failed_closed(tmp_path: Path) -> None:
    models = [
        _v3_entry(
            "wnba-elo-trend-lr-v4",
            "config/models/wnba-elo-trend-lr-v4.json",
            implementation="neural_network",
        ),
    ]
    repo = _setup_v3(tmp_path, models, "wnba-elo-trend-lr-v4")

    with pytest.raises(ValueError, match="failed validation"):
        ProductionModelRegistry.load(repo)


def test_code_backed_entrypoint_must_resolve(tmp_path: Path) -> None:
    good = _v3_entry(
        "soccer-poisson-dc-v1",
        sport="SOCCER",
        implementation=IMPLEMENTATION_CODE_BACKED,
        entry="model_prediction.models.soccer:soccer_model",
    )
    bad = _v3_entry(
        "tennis-surface-elo-v1",
        sport="TENNIS",
        implementation=IMPLEMENTATION_CODE_BACKED,
        entry="model_prediction.models.tennis:no_such_factory",
    )
    repo = _setup_v3(tmp_path, [good, bad], "soccer-poisson-dc-v1")

    registry = ProductionModelRegistry.load(repo)

    assert registry.entries["soccer-poisson-dc-v1"].available
    tennis = registry.entries["tennis-surface-elo-v1"]
    assert not tennis.available
    assert "does not resolve" in (tennis.load_error or "")


# ── legacy config compatibility ─────────────────────────────────────────────


def test_legacy_v1_config_derives_entries_from_allowlist(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_json(
        repo / "config/models/wnba-elo-trend-lr-v4.json",
        _make_artifact("wnba-elo-trend-lr-v4", sport="wnba"),
    )
    _write_yaml(
        repo / "config/production.yaml",
        {
            "schema_version": "1",
            "prediction_service": {
                "enabled": True,
                "mode": "canary",
                "primary": {
                    "sport": "WNBA",
                    "market": "moneyline",
                    "model_id": "wnba-elo-trend-lr-v4",
                    "artifact": "config/models/wnba-elo-trend-lr-v4.json",
                },
                "allowed_models": ["wnba-elo-trend-lr-v4"],
                "fallback_action": "no_prediction",
            },
            "execution": {"automated_orders": False, "manual_orders_only": True},
        },
    )

    registry = ProductionModelRegistry.load(repo)

    assert list(registry.entries) == ["wnba-elo-trend-lr-v4"]
    assert registry.primary.available
    # Identity derived from the artifact's own sport field.
    assert registry.primary.sport == "wnba"


def test_legacy_empty_allowlist_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_yaml(
        repo / "config/production.yaml",
        {
            "schema_version": "1",
            "prediction_service": {
                "primary": {
                    "sport": "WNBA",
                    "market": "moneyline",
                    "model_id": "wnba-elo-trend-lr-v4",
                    "artifact": "config/models/wnba-elo-trend-lr-v4.json",
                },
                "allowed_models": [],
            },
        },
    )

    with pytest.raises(ValueError, match="non-empty"):
        ProductionModelRegistry.load(repo)


# ── health integration ──────────────────────────────────────────────────────


def test_health_check_reports_broken_secondary_as_degraded(tmp_path: Path) -> None:
    models = [
        _v3_entry("wnba-elo-trend-lr-v4", "config/models/wnba-elo-trend-lr-v4.json"),
        _v3_entry("cs2-tiered-elo-v6", "config/models/cs2-missing.json"),
    ]
    repo = _setup_v3(tmp_path, models, "wnba-elo-trend-lr-v4", write_artifacts=False)
    _write_json(
        repo / "config/models/wnba-elo-trend-lr-v4.json",
        _make_artifact("wnba-elo-trend-lr-v4"),
    )
    config = yaml.safe_load((repo / "config/production.yaml").read_text(encoding="utf-8"))

    result = health_check(config, repo_root=repo)

    assert result["status"] == "DEGRADED"
    assert result["details"]["failed_models"] == ["cs2-tiered-elo-v6"]
    assert result["details"]["models"]["wnba-elo-trend-lr-v4"] == "ok"


def test_health_check_healthy_registry_reports_all_models(tmp_path: Path) -> None:
    models = [
        _v3_entry("wnba-elo-trend-lr-v4", "config/models/wnba-elo-trend-lr-v4.json"),
    ]
    repo = _setup_v3(tmp_path, models, "wnba-elo-trend-lr-v4")
    config = yaml.safe_load((repo / "config/production.yaml").read_text(encoding="utf-8"))
    # Freshness now reads the canonical production.db (item 12) — seed it.
    from model_prediction.production_store import ProductionPredictionStore
    from model_prediction.runtime_paths import RuntimePaths

    rt = tmp_path / "rt"
    store = ProductionPredictionStore(RuntimePaths(repo_root=repo, runtime_root=rt))
    run_id = store.start_run()
    stamp = datetime.now(UTC).isoformat()
    store.append_prediction(
        run_id=run_id,
        prediction_id="p1",
        event_id="e1",
        sport="WNBA",
        market="moneyline",
        market_type="moneyline",
        model_id="wnba-elo-trend-lr-v4",
        probabilities={"home": 0.6, "away": 0.4},
        decision_time_utc=stamp,
        prediction_time_utc=stamp,
    )
    store.close()

    result = health_check(config, repo_root=repo, runtime_root=rt)

    assert result["status"] == "HEALTHY"
    assert result["details"]["models"]["wnba-elo-trend-lr-v4"] == "ok"
