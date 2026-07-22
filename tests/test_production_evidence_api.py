from __future__ import annotations

import hashlib
import json
from pathlib import Path

import dashboard_server
import yaml


def _hashed(payload: dict) -> dict:
    payload = dict(payload)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["artifact_hash"] = hashlib.sha256(encoded).hexdigest()
    return payload


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _configure(monkeypatch, tmp_path: Path, models: dict) -> tuple[Path, Path, Path]:
    config = tmp_path / "config" / "model.yaml"
    data = tmp_path / "data"
    outputs = tmp_path / "outputs" / "latest"
    config.parent.mkdir(parents=True)
    data.mkdir()
    outputs.mkdir(parents=True)
    config.write_text(yaml.safe_dump({"models": models}), encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard_server, "CONFIG_FILE", config)
    monkeypatch.setattr(dashboard_server, "DATA", data)
    monkeypatch.setattr(dashboard_server, "OUTPUTS", outputs)
    return config, data, outputs


def test_production_evidence_enumerates_artifacts_and_uses_exact_metric_sources(
    monkeypatch, tmp_path: Path
) -> None:
    logistic = _hashed({
        "schema_version": "1",
        "sport": "mlb",
        "method": "logistic_regression",
        "model_version": "mlb-v2",
        "market_models": {
            "moneyline": {
                "feature_names": ["elo_probability", "trend_gap"],
                "coefficients": [2.5, -0.1],
                "intercept": -1.2,
                "confidence_threshold": 0.6,
                "positive_class": "home",
            }
        },
        "qualification": {"locked_holdout": True, "calls": 51, "hit_rate": 0.61},
    })
    esports = _hashed({
        "schema_version": "esports-neutral-elo-v1",
        "title": "lol",
        "model_version": "lol-v2",
        "initial_rating": 1500.0,
        "k": 48.0,
        "home_or_order_advantage": 0.0,
        "confidence_threshold": 0.05,
        "target": "series winner",
    })
    _, _, outputs = _configure(monkeypatch, tmp_path, {
        "MLB": {
            "status": "shadow_qualified",
            "active_production_version": "mlb-v2",
            "production_artifact": "config/models/mlb-v2.json",
        },
        "LOL": {
            "status": "shadow_qualified",
            "active_production_version": "lol-v2",
            "production_artifact": "config/models/lol-v2.json",
        },
        "TENNIS": {"status": "deferred"},
    })
    _write_json(tmp_path / "config" / "models" / "mlb-v2.json", logistic)
    _write_json(tmp_path / "config" / "models" / "lol-v2.json", esports)
    _write_json(outputs / "esports-baseline-validation.json", {
        "titles": {
            "lol": {
                "model_version": "lol-v2",
                "artifact_hash": esports["artifact_hash"],
                "locked_test": {"selected_matches": {"calls": 70, "accuracy": 0.66}},
            }
        }
    })
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    result = dashboard_server.production_evidence()

    assert result["configured_production_models"] == 2
    by_sport = {model["sport"]: model for model in result["models"]}
    assert set(by_sport) == {"mlb", "lol"}
    assert by_sport["mlb"]["artifact"]["valid"] is True
    assert by_sport["mlb"]["model_spec"]["features"] == [
        {"name": "elo_probability", "coefficient": 2.5},
        {"name": "trend_gap", "coefficient": -0.1},
    ]
    assert by_sport["mlb"]["locked_backfill"]["metrics"]["calls"] == 51
    assert by_sport["lol"]["model_spec"]["parameters"]["k"] == 48.0
    assert by_sport["lol"]["locked_backfill"]["metrics"]["selected_matches"]["calls"] == 70


def test_production_evidence_deduplicates_exact_versions_and_separates_predecessors(
    monkeypatch, tmp_path: Path
) -> None:
    active = _hashed({
        "schema_version": "1",
        "sport": "mlb",
        "method": "logistic_regression",
        "model_version": "mlb-v2",
        "market_models": {
            "moneyline": {
                "feature_names": ["elo_probability"],
                "coefficients": [2.0],
                "intercept": -1.0,
                "confidence_threshold": 0.55,
            }
        },
        "qualification": {"locked_holdout": True, "calls": 60},
    })
    predecessor = _hashed({
        "schema_version": "1",
        "sport": "mlb",
        "method": "logistic_regression",
        "model_version": "mlb-v1",
        "market_models": {
            "moneyline": {
                "feature_names": ["elo_probability"],
                "coefficients": [1.5],
                "intercept": -0.8,
                "confidence_threshold": 0.54,
            }
        },
        "qualification": {"locked_holdout": True, "calls": 55},
    })
    _, _, _ = _configure(monkeypatch, tmp_path, {
        "MLB": {
            "status": "shadow_qualified",
            "active_production_version": "mlb-v2",
            "production_artifact": "config/models/mlb-v2.json",
        }
    })
    _write_json(tmp_path / "config" / "models" / "mlb-v2.json", active)
    _write_json(tmp_path / "config" / "models" / "mlb-v1.json", predecessor)
    common = {
        "league": "MLB",
        "status": "settled",
        "record_type": "QUALIFIED_SHADOW_CALL",
        "units": 1.0,
        "pnl_units": 1.0,
    }
    main = [
        {**common, "pick_id": "same", "model_version": "mlb-v2", "result": "win",
         "model_artifact_hash": active["artifact_hash"], "probability_clv": 0.01},
    ]
    flat = [
        dict(main[0]),
        {**common, "pick_id": "active-loss", "model_version": "mlb-v2", "result": "loss",
         "pnl_units": -1.0, "model_artifact_hash": active["artifact_hash"]},
        {**common, "pick_id": "old", "model_version": "mlb-v1", "result": "win",
         "model_artifact_hash": predecessor["artifact_hash"], "probability_clv": 0.02},
        {**common, "pick_id": "open", "model_version": "mlb-v2", "status": "open",
         "result": "win"},
    ]
    monkeypatch.setattr(
        dashboard_server,
        "_read_evidence_ledger",
        lambda path: main if path.name == "picks.xlsx" else flat,
    )

    model = dashboard_server.production_evidence()["models"][0]
    current = model["ledger_evidence"]["active_version"]
    prior = model["ledger_evidence"]["predecessor_versions"]

    assert current["model_version"] == "mlb-v2"
    assert current["settled_decisive_rows"] == 2
    assert current["duplicates_removed"] == 1
    assert current["wins"] == 1
    assert current["pnl"]["shadow"]["label"] == "shadow_not_executed"
    assert current["pnl"]["executed"]["roi"] is None
    assert current["profitability_claim"]["allowed"] is False
    assert current["feature_value_attribution"]["status"] == "missing"
    assert [item["model_version"] for item in prior] == ["mlb-v1"]
    assert prior[0]["settled_decisive_rows"] == 1


def test_artifact_or_external_version_mismatch_fails_closed(monkeypatch, tmp_path: Path) -> None:
    wrong_version = _hashed({
        "schema_version": "esports-neutral-elo-v1",
        "title": "lol",
        "model_version": "lol-v1",
        "initial_rating": 1500.0,
        "k": 32.0,
    })
    _, _, outputs = _configure(monkeypatch, tmp_path, {
        "LOL": {
            "status": "shadow_qualified",
            "active_production_version": "lol-v2",
            "production_artifact": "config/models/lol-v2.json",
        }
    })
    _write_json(tmp_path / "config" / "models" / "lol-v2.json", wrong_version)
    _write_json(outputs / "esports-baseline-validation.json", {
        "titles": {
            "lol": {
                "model_version": "lol-v2",
                "artifact_hash": wrong_version["artifact_hash"],
                "locked_test": {"selected_matches": {"calls": 99}},
            }
        }
    })
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    model = dashboard_server.production_evidence()["models"][0]

    assert model["artifact"]["hash_valid"] is True
    assert model["artifact"]["version_matches_config"] is False
    assert model["artifact"]["valid"] is False
    assert model["locked_backfill"]["metrics"] is None
    assert model["evidence_valid"] is False


def test_production_evidence_route_is_get_only() -> None:
    get_source = dashboard_server.Handler.do_GET.__code__.co_consts
    post_source = dashboard_server.Handler.do_POST.__code__.co_consts

    assert "/api/production-evidence" in get_source
    assert "/api/production-evidence" not in post_source
