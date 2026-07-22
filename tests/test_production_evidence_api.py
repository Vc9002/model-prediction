from __future__ import annotations

import hashlib
import http.client
import json
import threading
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
        "qualification": {
            "locked_holdout": True,
            "qualified": False,
            "calls": 51,
            "hit_rate": 0.61,
        },
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
        "qualified_for_betting": False,
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
    for model in by_sport.values():
        assert {
            "model_version", "status", "features", "backfill", "main_ledger",
            "flat_ledger", "artifact", "profitability", "warnings",
        } <= model.keys()
        assert model["main_ledger"]["settled"] is None
        assert model["main_ledger"]["wins"] is None
        assert model["main_ledger"]["brier"] is None
        assert model["main_ledger"]["pnl"]["shadow"]["pnl_units"] is None
    assert any(
        warning["code"] == "config_artifact_qualification_mismatch"
        for warning in by_sport["mlb"]["warnings"]
    )
    assert any(
        warning["code"] == "config_artifact_qualification_mismatch"
        for warning in by_sport["lol"]["warnings"]
    )


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
        "market_type": "moneyline",
        "selection": "home",
        "line": 1.5,
        "status": "settled",
        "record_type": "QUALIFIED_SHADOW_CALL",
        "units": 1.0,
        "pnl_units": 1.0,
    }
    main = [
        {**common, "pick_id": "main-id", "event_id": "event-1", "model_version": "mlb-v2",
         "result": "win", "model_probability": 0.7,
         "model_artifact_hash": active["artifact_hash"], "probability_clv": 0.01},
    ]
    flat = [
        dict(main[0]),
        {**dict(main[0]), "pick_id": "different-pick-id-same-evidence", "line": "1.5000"},
        {**common, "pick_id": "active-loss", "event_id": "event-2",
         "model_version": "mlb-v2", "result": "loss", "model_probability": 0.6,
         "pnl_units": -1.0},
        {**common, "pick_id": "old", "event_id": "event-old", "model_version": "mlb-v1",
         "result": "win", "model_artifact_hash": predecessor["artifact_hash"],
         "probability_clv": 0.02},
        {**common, "pick_id": "open", "event_id": "event-open", "model_version": "mlb-v2",
         "status": "open", "result": "win"},
    ]
    monkeypatch.setattr(
        dashboard_server,
        "_read_evidence_ledger",
        lambda path: main if path.name == "picks.xlsx" else flat,
    )

    model = dashboard_server.production_evidence()["models"][0]
    main_result = model["main_ledger"]
    flat_result = model["flat_ledger"]

    assert main_result["model_version"] == "mlb-v2"
    assert main_result["settled"] == 1
    assert main_result["wins"] == 1
    assert main_result["duplicates_removed"] == 0
    assert main_result["brier"] == 0.09
    assert main_result["clv"]["coverage"] == 1.0
    assert main_result["predecessor_rows_excluded"] == 0

    assert flat_result["settled"] == 2
    assert flat_result["wins"] == 1
    assert flat_result["losses"] == 1
    assert flat_result["duplicates_removed"] == 1
    assert flat_result["brier"] == 0.225
    assert flat_result["clv"]["coverage"] == 0.5
    assert flat_result["predecessor_rows_excluded"] == 1
    assert flat_result["predecessor_version_counts"] == {"mlb-v1": 1}
    assert flat_result["pnl"]["shadow"]["label"] == "shadow_not_executed"
    assert flat_result["pnl"]["executed"]["roi"] is None
    assert flat_result["profitability_claim"]["allowed"] is False
    assert flat_result["feature_value_attribution"]["status"] == "missing"
    assert flat_result["artifact_lineage"]["matching_hash_rows"] == 1
    assert flat_result["artifact_lineage"]["missing_hash_rows"] == 1
    assert flat_result["artifact_lineage"]["status"] == "missing"
    assert model["ledger_evidence"] == {
        "main_ledger": main_result,
        "flat_ledger": flat_result,
    }


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


def test_external_esports_metrics_require_exact_report_version(monkeypatch, tmp_path: Path) -> None:
    artifact = _hashed({
        "schema_version": "esports-neutral-elo-v1",
        "title": "lol",
        "model_version": "lol-v2",
        "initial_rating": 1500.0,
        "k": 48.0,
    })
    _, _, outputs = _configure(monkeypatch, tmp_path, {
        "LOL": {
            "status": "shadow_qualified",
            "active_production_version": "lol-v2",
            "production_artifact": "config/models/lol-v2.json",
        }
    })
    _write_json(tmp_path / "config" / "models" / "lol-v2.json", artifact)
    _write_json(outputs / "esports-baseline-validation.json", {
        "titles": {
            "lol": {
                "model_version": "lol-v1",
                "artifact_hash": artifact["artifact_hash"],
                "locked_test": {"selected_matches": {"calls": 999}},
            }
        }
    })
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    model = dashboard_server.production_evidence()["models"][0]

    assert model["artifact"]["valid"] is True
    assert model["locked_backfill"]["status"] == "rejected_external_validation_mismatch"
    assert model["locked_backfill"]["metrics"] is None
    assert "validation_model_version_mismatch" in model["locked_backfill"]["mismatches"]


def test_corrupt_artifact_hash_suppresses_embedded_metrics(monkeypatch, tmp_path: Path) -> None:
    artifact = _hashed({
        "schema_version": "1",
        "sport": "mlb",
        "method": "logistic_regression",
        "model_version": "mlb-v2",
        "market_models": {
            "moneyline": {
                "feature_names": ["elo_probability"],
                "coefficients": [2.0],
                "intercept": -1.0,
            }
        },
        "qualification": {"locked_holdout": True, "calls": 999},
    })
    artifact["market_models"]["moneyline"]["coefficients"] = [999.0]
    _configure(monkeypatch, tmp_path, {
        "MLB": {
            "status": "shadow_qualified",
            "active_production_version": "mlb-v2",
            "production_artifact": "config/models/mlb-v2.json",
        }
    })
    _write_json(tmp_path / "config" / "models" / "mlb-v2.json", artifact)
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    model = dashboard_server.production_evidence()["models"][0]

    assert model["artifact"]["hash_valid"] is False
    assert model["artifact"]["valid"] is False
    assert model["locked_backfill"]["metrics"] is None
    assert model["model_definition_and_backfill_valid"] is False


def test_current_configured_production_artifacts_are_verified(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    result = dashboard_server.production_evidence()
    configured = {
        sport.lower(): model["active_production_version"]
        for sport, model in (dashboard_server._config_payload().get("models") or {}).items()
        if isinstance(model, dict) and model.get("production_artifact")
    }

    assert {model["sport"]: model["active_model_version"] for model in result["models"]} == configured
    assert result["all_model_definitions_and_backfills_valid"] is True
    for model in result["models"]:
        assert model["artifact"]["hash_valid"] is True
        assert model["artifact"]["version_matches_config"] is True
        assert model["artifact"]["lineage_matches_config"] is True
        assert model["locked_backfill"]["status"] == "verified"


def test_production_evidence_route_is_get_only(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server,
        "production_evidence",
        lambda: {"read_only": True, "models": []},
    )
    with dashboard_server._CACHE_LOCK:
        dashboard_server._CACHE.clear()
    server = dashboard_server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/api/production-evidence")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload == {"read_only": True, "models": []}

        connection.request(
            "POST",
            "/api/production-evidence",
            body=json.dumps({"confirm": True}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 404
        assert json.loads(response.read()) == {"error": "unknown route"}
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
