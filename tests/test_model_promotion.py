"""Tests for atomic promotion/rollback (consolidation A-3, part 2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from model_prediction.model_promotion import (
    _active_record,
    history,
    promote,
    rollback,
)
from model_prediction.production_canary import _compute_artifact_hash
from model_prediction.production_registry import ProductionModelRegistry


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _make_artifact(model_id: str, sport: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model_version": model_id,
        "sport": sport,
        "schema_version": "1",
        "market_models": {"moneyline": {"confidence_threshold": 0.5}},
        "qualification": {"hit_rate": 0.6, "calls": 10, "hits": 6},
    }
    payload["artifact_hash"] = _compute_artifact_hash(payload)
    return payload


def _make_repo(tmp_path: Path) -> Path:
    """WNBA primary canary + MLB moneyline with champion v8 and a
    registered challenger v9 (the promotion's candidate)."""
    repo = tmp_path / "repo"
    _write_yaml(
        repo / "config" / "production.yaml",
        {
            "schema_version": "3",
            "prediction_service": {
                "enabled": True,
                "mode": "production",
                "primary": {
                    "sport": "WNBA",
                    "market": "moneyline",
                    "model_id": "wnba-elo-trend-lr-v4",
                },
                "champions": {
                    "WNBA": {"moneyline": "wnba-elo-trend-lr-v4"},
                    "MLB": {"moneyline": "mlb-elo-trend-lr-v8"},
                },
                "models": [
                    {
                        "model_id": "wnba-elo-trend-lr-v4",
                        "sport": "WNBA",
                        "market": "moneyline",
                        "implementation": "json_artifact",
                        "artifact": "config/models/wnba-elo-trend-lr-v4.json",
                        "enabled": True,
                    },
                    {
                        "model_id": "mlb-elo-trend-lr-v8",
                        "sport": "MLB",
                        "market": "moneyline",
                        "implementation": "json_artifact",
                        "artifact": "config/models/mlb-elo-trend-lr-v8.json",
                        "enabled": True,
                    },
                    {
                        "model_id": "mlb-elo-trend-lr-v9",
                        "sport": "MLB",
                        "market": "moneyline",
                        "implementation": "json_artifact",
                        "artifact": "config/models/mlb-elo-trend-lr-v9.json",
                        "enabled": True,
                    },
                ],
                "fallback_action": "no_prediction",
            },
            "execution": {"automated_orders": False, "manual_orders_only": True},
            "health": {"max_data_age_minutes": 120},
        },
    )
    _write_json(
        repo / "config/models/wnba-elo-trend-lr-v4.json",
        _make_artifact("wnba-elo-trend-lr-v4", "wnba"),
    )
    _write_json(
        repo / "config/models/mlb-elo-trend-lr-v8.json",
        _make_artifact("mlb-elo-trend-lr-v8", "mlb"),
    )
    _write_json(
        repo / "config/models/mlb-elo-trend-lr-v9.json",
        _make_artifact("mlb-elo-trend-lr-v9", "mlb"),
    )
    return repo


def test_promote_switches_champion_preserves_rollback_and_records(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    record = promote(
        sport="MLB",
        market="moneyline",
        new_model_id="mlb-elo-trend-lr-v9",
        approved_by="operator",
        evidence_id="exp-42",
        repo_root=repo,
    )

    assert record["status"] == "active"
    assert record["old_model_id"] == "mlb-elo-trend-lr-v8"
    assert record["old_artifact_hash"] and record["new_artifact_hash"]

    # The config file itself changed, and the new registry view agrees.
    # The canary primary (WNBA) is untouched; the MLB champion pointer moved.
    registry = ProductionModelRegistry.load(repo)
    assert registry.primary.model_id == "wnba-elo-trend-lr-v4"
    assert registry.champion("MLB", "moneyline").model_id == "mlb-elo-trend-lr-v9"
    assert registry.entries["mlb-elo-trend-lr-v9"].rollback_model == "mlb-elo-trend-lr-v8"

    active = _active_record(repo, "MLB", "moneyline")
    assert active["new_model_id"] == "mlb-elo-trend-lr-v9"
    assert active["old_model_id"] == "mlb-elo-trend-lr-v8"
    assert active["evidence_id"] == "exp-42"
    assert active["approved_by"] == "operator"


def test_promote_unknown_or_broken_model_rejected(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    with pytest.raises(ValueError, match="unknown model"):
        promote(
            sport="MLB",
            market="moneyline",
            new_model_id="mlb-elo-trend-lr-v99",
            approved_by="operator",
            repo_root=repo,
        )

    (repo / "config/models/mlb-elo-trend-lr-v9.json").unlink()
    with pytest.raises(ValueError, match="failed contract validation"):
        promote(
            sport="MLB",
            market="moneyline",
            new_model_id="mlb-elo-trend-lr-v9",
            approved_by="operator",
            repo_root=repo,
        )
    # Nothing was recorded for the rejected promotions.
    assert history(repo_root=repo) == []


def test_promote_already_serving_model_rejected(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    with pytest.raises(ValueError, match="already serves"):
        promote(
            sport="WNBA",
            market="moneyline",
            new_model_id="wnba-elo-trend-lr-v4",
            approved_by="operator",
            repo_root=repo,
        )


def test_rollback_restores_previous_champion_in_one_command(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    promote(
        sport="MLB",
        market="moneyline",
        new_model_id="mlb-elo-trend-lr-v9",
        approved_by="operator",
        repo_root=repo,
    )

    result = rollback(sport="MLB", market="moneyline", repo_root=repo)

    assert result["rolled_back_from"] == "mlb-elo-trend-lr-v9"
    assert result["rolled_back_to"] == "mlb-elo-trend-lr-v8"
    registry = ProductionModelRegistry.load(repo)
    assert registry.champion("MLB", "moneyline").model_id == "mlb-elo-trend-lr-v8"
    # The old promotion record is closed and the rollback is active.
    assert _active_record(repo, "MLB", "moneyline")["new_model_id"] == "mlb-elo-trend-lr-v8"
    rows = history(repo_root=repo)
    assert rows[0]["status"] == "active" and "rollback" in (rows[0]["note"] or "")
    assert rows[1]["status"] == "rolled_back"


def test_rollback_without_rollback_pointer_rejected(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    with pytest.raises(ValueError, match="no rollback model"):
        rollback(sport="WNBA", market="moneyline", repo_root=repo)
