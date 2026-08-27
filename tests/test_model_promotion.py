"""Tests for atomic promotion/rollback (consolidation A-3, part 2)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from model_prediction.model_promotion import (
    _active_record,
    _db_path,
    history,
    promote,
    rollback,
)
from model_prediction.production_canary import _compute_artifact_hash
from model_prediction.production_registry import ProductionModelRegistry


@pytest.fixture(autouse=True)
def _isolated_runtime_root(tmp_path: Path, monkeypatch) -> None:
    """Promotion is operational: it must only record to an external
    runtime root, so every test gets its own isolated one."""
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))


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
        market_evidence_id="mkt-evidence-42",
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
    assert active["market_evidence_id"] == "mkt-evidence-42"
    assert active["market_evidence_unavailable_reason"] is None
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
        market_evidence_id="mkt-evidence-42",
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


def test_mlb_v8_champion_permanently_protected() -> None:
    """MLB v8 champion must remain frozen and protected in production config."""
    from model_prediction.config import PROJECT_ROOT, load_config

    config = load_config()
    mlb_cfg = config["models"]["MLB"]
    assert mlb_cfg["active_production_version"] == "mlb-elo-trend-lr-v8"
    assert "mlb-elo-trend-lr-v8" in mlb_cfg.get("protected_versions", [])

    artifact_path = PROJECT_ROOT / "config" / "models" / "mlb-elo-trend-lr-v8.json"
    assert artifact_path.is_file()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["model_version"] == "mlb-elo-trend-lr-v8"
    assert "market_models" in artifact and "moneyline" in artifact["market_models"]
    expected_features = [
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
    ]
    assert artifact["market_models"]["moneyline"]["feature_names"] == expected_features


def test_promote_fails_closed_without_market_evidence(tmp_path: Path) -> None:
    """Phase-23 gate (2026-08-27): promotion refuses without market
    evidence, and the explicit unavailable-reason escape hatch works."""
    repo = _make_repo(tmp_path)
    with pytest.raises(ValueError, match="market-relative evidence"):
        promote(
            sport="MLB",
            market="moneyline",
            new_model_id="mlb-elo-trend-lr-v9",
            approved_by="operator",
            repo_root=repo,
        )
    record = promote(
        sport="MLB",
        market="moneyline",
        new_model_id="mlb-elo-trend-lr-v9",
        approved_by="operator",
        market_evidence_unavailable_reason="no market data exists for this market",
        repo_root=repo,
    )
    assert record["status"] == "active"
    assert record["market_evidence_unavailable_reason"] == "no market data exists for this market"
    persisted = _active_record(repo, "MLB", "moneyline")
    assert persisted["market_evidence_unavailable_reason"] == "no market data exists for this market"
    assert persisted["market_evidence_id"] is None


_OLD_PROMOTIONS_SCHEMA = """
CREATE TABLE promotions (
    promotion_id       TEXT PRIMARY KEY,
    sport              TEXT NOT NULL,
    market             TEXT NOT NULL,
    old_model_id       TEXT,
    new_model_id       TEXT NOT NULL,
    old_artifact_hash  TEXT,
    new_artifact_hash  TEXT,
    approved_by        TEXT NOT NULL,
    evidence_id        TEXT,
    git_sha            TEXT,
    promoted_at_utc    TEXT NOT NULL,
    status             TEXT NOT NULL,
    rolled_back_at_utc TEXT,
    note               TEXT
);
"""


def test_promote_migrates_pre_phase23_schema_and_preserves_old_records(tmp_path: Path) -> None:
    """A promotions DB created before the market-evidence gate landed
    (2026-08-27) has no market_evidence_id/market_evidence_unavailable_reason
    columns. _conn's ALTER TABLE migration must add them without touching
    rows already recorded under the old schema."""
    repo = _make_repo(tmp_path)
    db_path = _db_path(repo)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(db_path)
    try:
        raw.executescript(_OLD_PROMOTIONS_SCHEMA)
        raw.execute(
            "INSERT INTO promotions (promotion_id, sport, market, old_model_id, "
            "new_model_id, old_artifact_hash, new_artifact_hash, approved_by, "
            "evidence_id, git_sha, promoted_at_utc, status) VALUES "
            "('promo-legacy-1', 'WNBA', 'moneyline', NULL, "
            "'wnba-elo-trend-lr-v4', NULL, 'hash-legacy', 'operator', "
            "'exp-legacy', 'sha-legacy', '2026-08-01T00:00:00+00:00', 'active')"
        )
        raw.commit()
    finally:
        raw.close()

    record = promote(
        sport="MLB",
        market="moneyline",
        new_model_id="mlb-elo-trend-lr-v9",
        approved_by="operator",
        market_evidence_id="mkt-evidence-99",
        repo_root=repo,
    )
    assert record["status"] == "active"

    rows = {row["promotion_id"]: row for row in history(repo_root=repo, limit=20)}
    legacy = rows["promo-legacy-1"]
    assert legacy["new_model_id"] == "wnba-elo-trend-lr-v4"
    assert legacy["evidence_id"] == "exp-legacy"
    assert legacy["market_evidence_id"] is None
    new = rows[record["promotion_id"]]
    assert new["market_evidence_id"] == "mkt-evidence-99"


def test_promote_leaves_champion_unchanged_when_yaml_write_fails(tmp_path: Path, monkeypatch) -> None:
    """If the atomic yaml write (or its re-validate) fails after the
    promotion row is inserted, the champion pointer must not move — the
    failure is recorded for audit, but production keeps serving the old
    model."""
    from model_prediction import model_promotion

    repo = _make_repo(tmp_path)

    def _boom(_root: Path, _config: dict[str, Any]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(model_promotion, "_atomic_write_yaml", _boom)

    with pytest.raises(OSError, match="disk full"):
        promote(
            sport="MLB",
            market="moneyline",
            new_model_id="mlb-elo-trend-lr-v9",
            approved_by="operator",
            market_evidence_id="mkt-evidence-42",
            repo_root=repo,
        )

    # Champion pointer on disk is untouched.
    registry = ProductionModelRegistry.load(repo)
    assert registry.champion("MLB", "moneyline").model_id == "mlb-elo-trend-lr-v8"

    # The attempt is recorded as failed, not active, and never became the
    # active record for the market.
    rows = history(repo_root=repo)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert "disk full" in (rows[0]["note"] or "")
    assert _active_record(repo, "MLB", "moneyline") is None
