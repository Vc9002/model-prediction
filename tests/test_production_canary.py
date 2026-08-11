"""Tests for the production canary infrastructure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from model_prediction.production_canary import (
    _compute_artifact_hash,
    get_production_model,
    health_check,
    load_production_config,
    validate_production_config,
)

# ── helpers ────────────────────────────────────────────────────────────────


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _make_artifact_data(
    model_id: str = "wnba-elo-trend-lr-v4",
    include_hash: bool = True,
) -> dict:
    """Return a minimal valid artifact payload and the computed hash."""
    payload = {
        "model_version": model_id,
        "sport": "wnba",
        "schema_version": "1",
        "market_models": {
            "moneyline": {
                "confidence_threshold": 0.5,
            }
        },
        "qualification": {
            "hit_rate": 0.67,
            "called_rate": 1.0,
            "minimum_hit_rate": 0.6,
            "selectivity": 1.0,
            "calls": 163,
            "hits": 110,
            "reliability_buckets": [
                {
                    "lower": 0.5,
                    "upper": 0.6,
                    "mean_p": 0.55,
                    "hit_rate": 0.57,
                    "count": 56,
                },
            ],
        },
    }
    if include_hash:
        h = _compute_artifact_hash(payload)
        payload["artifact_hash"] = h
    return payload


def _make_production_config(overrides: dict | None = None) -> dict:
    """Return a valid production config, optionally with overrides merged."""
    config: dict = {
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
        "execution": {
            "automated_orders": False,
            "manual_orders_only": True,
        },
        "dashboard": {
            "production_card_enabled": True,
        },
        "health": {
            "max_data_age_minutes": 120,
            "require_finite_probabilities": True,
            "require_probability_normalization": True,
        },
    }
    if overrides:
        _deep_merge(config, overrides)
    return config


def _deep_merge(base: dict, overrides: dict) -> None:
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _setup_production_env(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal repo tree that passes validation.

    Returns (repo_root, artifact_path).
    """
    repo = tmp_path / "repo"
    # config/production.yaml
    config_dir = repo / "config"
    config_dir.mkdir(parents=True)
    _write_yaml(config_dir / "production.yaml", _make_production_config())

    # config/model.yaml  (stub — needed for PROJECT_ROOT detection)
    _write_yaml(config_dir / "model.yaml", {"schema_version": "2"})

    # config/models/wnba-elo-trend-lr-v4.json
    models_dir = config_dir / "models"
    models_dir.mkdir(parents=True)
    artifact_data = _make_artifact_data()
    artifact_path = models_dir / "wnba-elo-trend-lr-v4.json"
    _write_json(artifact_path, artifact_data)

    # src/model_prediction/__init__.py (needed for import)
    src_pkg = repo / "src" / "model_prediction"
    src_pkg.mkdir(parents=True, exist_ok=True)
    (src_pkg / "__init__.py").touch()

    return repo, artifact_path


# ── tests ───────────────────────────────────────────────────────────────────


class TestConfigLoadsSuccessfully:
    def test_config_loads_successfully(self, tmp_path: Path) -> None:
        """config/production.yaml is readable and returns a dict."""
        repo, _ = _setup_production_env(tmp_path)
        config = load_production_config(repo_root=repo)
        assert isinstance(config, dict)
        assert config.get("schema_version") == "1"


class TestAllowedModelConstraints:
    def test_config_has_exactly_one_allowed_model(self, tmp_path: Path) -> None:
        """allowed_models must be a list with exactly one entry."""
        repo, _ = _setup_production_env(tmp_path)
        config = load_production_config(repo_root=repo)
        allowed = config["prediction_service"]["allowed_models"]
        assert isinstance(allowed, list)
        assert len(allowed) == 1
        assert allowed[0] == "wnba-elo-trend-lr-v4"

    def test_exactly_one_model_allowlisted(self, tmp_path: Path) -> None:
        """validate rejects zero allowed models."""
        repo, _ = _setup_production_env(tmp_path)
        bad = _make_production_config(
            {"prediction_service": {"allowed_models": []}}
        )
        _write_yaml(repo / "config" / "production.yaml", bad)
        config = load_production_config(repo_root=repo)
        with pytest.raises(ValueError, match="exactly one"):
            validate_production_config(config, repo_root=repo)

    def test_multiple_models_in_allowlist_rejected(self, tmp_path: Path) -> None:
        """validate rejects more than one allowed model."""
        repo, _ = _setup_production_env(tmp_path)
        bad = _make_production_config(
            {
                "prediction_service": {
                    "allowed_models": ["wnba-elo-trend-lr-v4", "nba-elo-trend-lr-v4"],
                }
            }
        )
        _write_yaml(repo / "config" / "production.yaml", bad)
        config = load_production_config(repo_root=repo)
        with pytest.raises(ValueError, match="exactly one"):
            validate_production_config(config, repo_root=repo)


class TestArtifactValidation:
    def test_allowed_model_artifact_exists(self, tmp_path: Path) -> None:
        """The referenced artifact file exists on disk."""
        repo, artifact_path = _setup_production_env(tmp_path)
        assert artifact_path.is_file()
        config = load_production_config(repo_root=repo)
        validate_production_config(config, repo_root=repo)

    def test_wrong_model_id_rejected(self, tmp_path: Path) -> None:
        """model_id in config must match model_version in artifact."""
        repo, _ = _setup_production_env(tmp_path)

        # Write a config with a wrong model_id but fix the allowed_models too
        bad = _make_production_config(
            {
                "prediction_service": {
                    "primary": {"model_id": "nba-elo-trend-lr-v4"},
                    "allowed_models": ["nba-elo-trend-lr-v4"],
                }
            }
        )
        _write_yaml(repo / "config" / "production.yaml", bad)
        config = load_production_config(repo_root=repo)
        with pytest.raises(ValueError, match="model_id mismatch"):
            validate_production_config(config, repo_root=repo)


class TestHealthCheck:
    def test_health_check_returns_healthy(self, tmp_path: Path) -> None:
        """A valid setup returns HEALTHY."""
        repo, _ = _setup_production_env(tmp_path)
        config = load_production_config(repo_root=repo)
        result = health_check(config, repo_root=repo, runtime_root=tmp_path / "rt")
        assert result["status"] == "HEALTHY"
        assert result["model_id"] == "wnba-elo-trend-lr-v4"
        assert "artifact_hash" in result
        assert "checked_at_utc" in result

    def test_missing_artifact_returns_degraded_or_down(self, tmp_path: Path) -> None:
        """health_check returns DOWN when artifact is missing."""
        repo, artifact_path = _setup_production_env(tmp_path)
        artifact_path.unlink()
        config = load_production_config(repo_root=repo)
        result = health_check(config, repo_root=repo)
        assert result["status"] == "DOWN"

    def test_health_check_config_load_failure(self, tmp_path: Path) -> None:
        """health_check returns DOWN when config/production.yaml is missing."""
        repo, _ = _setup_production_env(tmp_path)
        (repo / "config" / "production.yaml").unlink()
        result = health_check(repo_root=repo)
        assert result["status"] == "DOWN"

    def test_health_check_with_nonfinite_probability(self, tmp_path: Path) -> None:
        """health_check returns DOWN when a probability field is NaN."""
        repo, _ = _setup_production_env(tmp_path)

        # Inject NaN into the artifact
        artifact_path = repo / "config" / "models" / "wnba-elo-trend-lr-v4.json"
        data = json.loads(artifact_path.read_text())
        data["qualification"]["hit_rate"] = float("nan")
        h = _compute_artifact_hash(data)
        data["artifact_hash"] = h
        _write_json(artifact_path, data)

        config = load_production_config(repo_root=repo)
        result = health_check(config, repo_root=repo)
        assert result["status"] == "DOWN"


class TestExecutionPolicy:
    def test_automated_orders_is_false(self, tmp_path: Path) -> None:
        """execution.automated_orders must be false."""
        repo, _ = _setup_production_env(tmp_path)
        config = load_production_config(repo_root=repo)
        assert config["execution"]["automated_orders"] is False

    def test_fallback_action_is_no_prediction(self, tmp_path: Path) -> None:
        """prediction_service.fallback_action must be 'no_prediction'."""
        repo, _ = _setup_production_env(tmp_path)
        config = load_production_config(repo_root=repo)
        assert config["prediction_service"]["fallback_action"] == "no_prediction"


class TestGetProductionModel:
    def test_get_production_model(self, tmp_path: Path) -> None:
        """get_production_model returns the expected dict."""
        repo, _ = _setup_production_env(tmp_path)
        config = load_production_config(repo_root=repo)
        model = get_production_model(config)
        assert model == {
            "sport": "WNBA",
            "market": "moneyline",
            "model_id": "wnba-elo-trend-lr-v4",
            "artifact": "config/models/wnba-elo-trend-lr-v4.json",
        }
