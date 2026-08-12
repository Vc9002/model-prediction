from __future__ import annotations

import hashlib
import http.client
import json
import threading
from pathlib import Path

import yaml

import dashboard_server


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
    monkeypatch.setattr(
        dashboard_server, "FEATURE_REGISTRY_FILE", tmp_path / "config" / "tested_features.json"
    )
    monkeypatch.setattr(dashboard_server, "DATA", data)
    monkeypatch.setattr(dashboard_server, "OUTPUTS", outputs)
    return config, data, outputs


def test_production_evidence_enumerates_artifacts_and_uses_exact_metric_sources(
    monkeypatch, tmp_path: Path
) -> None:
    logistic = _hashed(
        {
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
        }
    )
    esports = _hashed(
        {
            "schema_version": "esports-neutral-elo-v1",
            "title": "lol",
            "model_version": "lol-v2",
            "initial_rating": 1500.0,
            "k": 48.0,
            "home_or_order_advantage": 0.0,
            "confidence_threshold": 0.05,
            "target": "series winner",
            "qualified_for_betting": False,
        }
    )
    _, _, outputs = _configure(
        monkeypatch,
        tmp_path,
        {
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
        },
    )
    _write_json(tmp_path / "config" / "models" / "mlb-v2.json", logistic)
    _write_json(tmp_path / "config" / "models" / "lol-v2.json", esports)
    _write_json(
        outputs / "esports-baseline-validation.json",
        {
            "titles": {
                "lol": {
                    "model_version": "lol-v2",
                    "artifact_hash": esports["artifact_hash"],
                    "locked_test": {"selected_matches": {"calls": 70, "accuracy": 0.66}},
                }
            }
        },
    )
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    result = dashboard_server.production_evidence()

    assert result["configured_production_models"] == 2
    assert result["generated_at"] == result["generated_at_utc"]
    by_sport = {model["sport"]: model for model in result["models"]}
    assert set(by_sport) == {"mlb", "lol"}
    assert by_sport["mlb"]["artifact"]["valid"] is True
    assert by_sport["mlb"]["artifact"]["health"] == "VERIFIED"
    assert by_sport["mlb"]["artifact"]["sha256"] == logistic["artifact_hash"]
    assert by_sport["mlb"]["artifact"]["hash_verified"] is True
    assert by_sport["mlb"]["artifact"]["lineage"] == "VERIFIED"
    assert by_sport["mlb"]["model_spec"]["features"] == [
        {"name": "elo_probability", "coefficient": 2.5},
        {"name": "trend_gap", "coefficient": -0.1},
    ]
    assert by_sport["mlb"]["locked_backfill"]["metrics"]["calls"] == 51
    assert (
        by_sport["mlb"]["backfill"]
        | {
            "observations": None,
            "calls": 51,
            "hit_rate": 0.61,
            "brier_score": None,
            "qualified": False,
        }
        == by_sport["mlb"]["backfill"]
    )
    assert by_sport["lol"]["model_spec"]["parameters"]["k"] == 48.0
    assert by_sport["lol"]["locked_backfill"]["metrics"]["selected_matches"]["calls"] == 70
    for model in by_sport.values():
        assert {
            "model_version",
            "status",
            "features",
            "backfill",
            "main_ledger",
            "flat_ledger",
            "artifact",
            "profitability",
            "warnings",
        } <= model.keys()
        assert model["main_ledger"]["settled"] is None
        assert model["main_ledger"]["wins"] is None
        assert model["main_ledger"]["brier"] is None
        assert model["main_ledger"]["pnl"]["shadow"]["pnl_units"] is None
    assert any(
        warning["code"] == "config_artifact_qualification_mismatch" for warning in by_sport["mlb"]["warnings"]
    )
    assert any(
        warning["code"] == "config_artifact_qualification_mismatch" for warning in by_sport["lol"]["warnings"]
    )


def test_production_evidence_deduplicates_exact_versions_and_separates_predecessors(
    monkeypatch, tmp_path: Path
) -> None:
    active = _hashed(
        {
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
        }
    )
    predecessor = _hashed(
        {
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
        }
    )
    _, _, _ = _configure(
        monkeypatch,
        tmp_path,
        {
            "MLB": {
                "status": "shadow_qualified",
                "active_production_version": "mlb-v2",
                "production_artifact": "config/models/mlb-v2.json",
            }
        },
    )
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
        {
            **common,
            "pick_id": "main-id",
            "event_id": "event-1",
            "model_version": "mlb-v2",
            "result": "win",
            "model_probability": 0.7,
            "model_artifact_hash": active["artifact_hash"],
            "probability_clv": 0.01,
        },
    ]
    flat = [
        dict(main[0]),
        {**dict(main[0]), "pick_id": "different-pick-id-same-evidence", "line": "1.5000"},
        {
            **common,
            "pick_id": "active-loss",
            "event_id": "event-2",
            "model_version": "mlb-v2",
            "result": "loss",
            "model_probability": 0.6,
            "pnl_units": -1.0,
        },
        {
            **common,
            "pick_id": "old",
            "event_id": "event-old",
            "model_version": "mlb-v1",
            "result": "win",
            "model_artifact_hash": predecessor["artifact_hash"],
            "probability_clv": 0.02,
        },
        {
            **common,
            "pick_id": "push",
            "event_id": "event-push",
            "model_version": "mlb-v2",
            "result": "push",
            "model_artifact_hash": active["artifact_hash"],
        },
        {
            **common,
            "pick_id": "open",
            "event_id": "event-open",
            "model_version": "mlb-v2",
            "status": "open",
            "result": "win",
        },
    ]
    monkeypatch.setattr(
        dashboard_server,
        "_read_evidence_ledger",
        lambda path: main if path.parent.name == "main" else flat,
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

    assert flat_result["settled"] == 3
    assert flat_result["wins"] == 1
    assert flat_result["losses"] == 1
    assert flat_result["pushes"] == 1
    assert flat_result["duplicates_removed"] == 1
    assert flat_result["brier"] == 0.225
    assert flat_result["clv"]["coverage"] == 0.5
    assert flat_result["predecessor_rows_excluded"] == 1
    assert flat_result["predecessor_version_counts"] == {"mlb-v1": 1}
    assert flat_result["pnl"]["shadow"]["label"] == "shadow_not_executed"
    assert flat_result["pnl_units"] == 0.0
    assert flat_result["pnl_basis"] == "shadow"
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
    wrong_version = _hashed(
        {
            "schema_version": "esports-neutral-elo-v1",
            "title": "lol",
            "model_version": "lol-v1",
            "initial_rating": 1500.0,
            "k": 32.0,
        }
    )
    _, _, outputs = _configure(
        monkeypatch,
        tmp_path,
        {
            "LOL": {
                "status": "shadow_qualified",
                "active_production_version": "lol-v2",
                "production_artifact": "config/models/lol-v2.json",
            }
        },
    )
    _write_json(tmp_path / "config" / "models" / "lol-v2.json", wrong_version)
    _write_json(
        outputs / "esports-baseline-validation.json",
        {
            "titles": {
                "lol": {
                    "model_version": "lol-v2",
                    "artifact_hash": wrong_version["artifact_hash"],
                    "locked_test": {"selected_matches": {"calls": 99}},
                }
            }
        },
    )
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    model = dashboard_server.production_evidence()["models"][0]

    assert model["artifact"]["hash_valid"] is True
    assert model["artifact"]["version_matches_config"] is False
    assert model["artifact"]["valid"] is False
    assert model["locked_backfill"]["metrics"] is None
    assert model["evidence_valid"] is False


def test_external_esports_metrics_require_exact_report_version(monkeypatch, tmp_path: Path) -> None:
    artifact = _hashed(
        {
            "schema_version": "esports-neutral-elo-v1",
            "title": "lol",
            "model_version": "lol-v2",
            "initial_rating": 1500.0,
            "k": 48.0,
        }
    )
    _, _, outputs = _configure(
        monkeypatch,
        tmp_path,
        {
            "LOL": {
                "status": "shadow_qualified",
                "active_production_version": "lol-v2",
                "production_artifact": "config/models/lol-v2.json",
            }
        },
    )
    _write_json(tmp_path / "config" / "models" / "lol-v2.json", artifact)
    _write_json(
        outputs / "esports-baseline-validation.json",
        {
            "titles": {
                "lol": {
                    "model_version": "lol-v1",
                    "artifact_hash": artifact["artifact_hash"],
                    "locked_test": {"selected_matches": {"calls": 999}},
                }
            }
        },
    )
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    model = dashboard_server.production_evidence()["models"][0]

    assert model["artifact"]["valid"] is True
    assert model["locked_backfill"]["status"] == "rejected_external_validation_mismatch"
    assert model["locked_backfill"]["metrics"] is None
    assert "validation_model_version_mismatch" in model["locked_backfill"]["mismatches"]


def test_corrupt_artifact_hash_suppresses_embedded_metrics(monkeypatch, tmp_path: Path) -> None:
    artifact = _hashed(
        {
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
        }
    )
    artifact["market_models"]["moneyline"]["coefficients"] = [999.0]
    _configure(
        monkeypatch,
        tmp_path,
        {
            "MLB": {
                "status": "shadow_qualified",
                "active_production_version": "mlb-v2",
                "production_artifact": "config/models/mlb-v2.json",
            }
        },
    )
    _write_json(tmp_path / "config" / "models" / "mlb-v2.json", artifact)
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    model = dashboard_server.production_evidence()["models"][0]

    assert model["artifact"]["hash_valid"] is False
    assert model["artifact"]["health"] == "FAILED"
    assert model["artifact"]["hash_verified"] is False
    assert model["artifact"]["valid"] is False
    assert model["locked_backfill"]["metrics"] is None
    assert model["model_definition_and_backfill_valid"] is False


def test_current_configured_artifacts_are_valid_after_rename_and_config_fix(monkeypatch, tmp_path: Path) -> None:
    """Shipped state (2026-08-13, post-fix): every configured production_
    artifact resolves to its renamed v2 file, so the evidence API must
    report valid — not fabricate a mismatch. The mismatch-detection path
    itself stays covered by the synthetic-stale-ref test below."""
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    result = dashboard_server.production_evidence()
    configured = {
        sport.lower(): model["active_production_version"]
        for sport, model in (dashboard_server._config_payload().get("models") or {}).items()
        if isinstance(model, dict) and model.get("production_artifact")
    }

    assert {model["sport"]: model["active_model_version"] for model in result["models"]} == configured
    assert result["all_model_definitions_and_backfills_valid"] is True
    assert result["feature_registry"]["valid"] is True
    assert len(result["feature_registry"]["features"]) == 25
    assert len(result["feature_registry"]["production_ablation_summary"]) == 15
    rejected = {
        model["sport"]
        for model in result["models"]
        if not model["model_definition_and_backfill_valid"]
    }
    # Post-fix (2026-08-13): config refs now match the renamed v2 files, so
    # no model is rejected for artifact integrity. The detection path stays
    # covered by test_stale_artifact_ref_is_still_surfaced_as_rejected.
    assert rejected == set()
    for model in result["models"]:
        assert model["artifact"]["valid"] is True
        assert model["artifact"]["version_matches_config"] is True
        assert model["artifact"]["lineage_matches_config"] is True
        assert model["artifact"]["hash_valid"] is True

    # Fail-closed on missing external validation: without the esports and
    # international-baseball validation reports, every report-backed model
    # must be rejected, not silently passed.
    monkeypatch.setattr(dashboard_server, "OUTPUTS", tmp_path / "empty_outputs")
    result = dashboard_server.production_evidence()
    rejected = {
        model["sport"]
        for model in result["models"]
        if not model["model_definition_and_backfill_valid"]
    }
    assert rejected == {"lol", "cs2", "dota2", "valorant", "kbo", "npb"}


def test_stale_artifact_ref_is_still_surfaced_as_rejected_integrity(
    monkeypatch, tmp_path: Path
) -> None:
    """The fail-closed detection that caught the kbo/npb v1->v2 mismatch must
    stay covered now that the real config is fixed: inject a synthetic stale
    production_artifact ref and confirm the API rejects that sport."""
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    real = dashboard_server._config_payload()
    stale = json.loads(json.dumps(real))
    # This path was renamed away 2026-08-13; it no longer exists on disk.
    stale["models"]["KBO"]["production_artifact"] = "config/models/kbo-tie-aware-elo-v1.json"
    monkeypatch.setattr(dashboard_server, "_config_payload", lambda: stale)

    result = dashboard_server.production_evidence()
    kbo = next(model for model in result["models"] if model["sport"] == "kbo")
    assert kbo["artifact"]["valid"] is False
    assert "artifact_missing_or_invalid_json" in kbo["artifact"]["mismatches"]
    assert kbo["model_definition_and_backfill_valid"] is False
    assert result["all_model_definitions_and_backfills_valid"] is False


def test_feature_registry_is_validated_and_joined_to_exact_active_features(
    monkeypatch, tmp_path: Path
) -> None:
    artifact = _hashed(
        {
            "schema_version": "1",
            "sport": "nba",
            "method": "logistic_regression",
            "model_version": "nba-v2",
            "market_models": {
                "moneyline": {
                    "feature_names": ["elo_probability", "trend_gap"],
                    "coefficients": [3.0, 0.01],
                    "intercept": -1.5,
                }
            },
            "qualification": {"locked_holdout": True, "qualified": True},
        }
    )
    _configure(
        monkeypatch,
        tmp_path,
        {
            "NBA": {
                "status": "shadow_qualified",
                "active_production_version": "nba-v2",
                "production_artifact": "config/models/nba-v2.json",
            }
        },
    )
    _write_json(tmp_path / "config" / "models" / "nba-v2.json", artifact)
    _write_json(
        tmp_path / "config" / "tested_features.json",
        {
            "schema_version": "1",
            "last_updated": "2026-07-22",
            "retention_policy": {"name": "keep_any_positive", "keep_when": "any positive"},
            "features": [
                {
                    "name": "elo_probability",
                    "verdict": "keep",
                    "status": "production",
                    "evidence_grade": "A",
                    "sports": ["NBA"],
                },
                {
                    "name": "trend_gap",
                    "verdict": "keep",
                    "status": "production",
                    "evidence_grade": "A",
                    "sports": ["NBA"],
                },
            ],
            "production_ablation_summary": [
                {
                    "sport": "nba",
                    "model_version": "nba-v2",
                    "feature": "trend_gap",
                    "strict_decision": "INCONCLUSIVE",
                    "retention_decision": "KEEP",
                    "production_safe": True,
                }
            ],
        },
    )
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])

    result = dashboard_server.production_evidence()

    assert result["feature_registry"]["status"] == "verified"
    assert result["feature_registry"]["counts_by_verdict"] == {"keep": 2}
    joined = {item["name"]: item for item in result["models"][0]["feature_registry"]}
    assert joined["elo_probability"]["registered"] is True
    assert joined["elo_probability"]["sport_evidence"] is None
    assert joined["trend_gap"]["sport_evidence"]["retention_decision"] == "KEEP"


def _render_model_card(model: dict) -> dict[str, str]:
    backfill = model["backfill"]
    if backfill["observations"] is None or backfill["calls"] is None:
        backfill_text = "—"
    else:
        qualification = "QUALIFIED" if backfill["qualified"] else "NOT QUALIFIED"
        backfill_text = (
            f"{backfill['observations']} obs · {backfill['calls']} calls · "
            f"{backfill['hit_rate']:.1%} · Brier {backfill['brier_score']:.3f} · "
            f"{qualification}"
        )

    def ledger_text(ledger: dict) -> str:
        if ledger["settled"] is None:
            return "—"
        return f"{ledger['wins']}-{ledger['losses']}-{ledger['pushes']}"

    return {
        "backfill": backfill_text,
        "artifact": model["artifact"]["health"],
        "main_ledger": ledger_text(model["main_ledger"]),
        "flat_ledger": ledger_text(model["flat_ledger"]),
    }


def test_http_payload_renders_nba_verified_with_missing_ledger_dashes(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "_read_evidence_ledger", lambda _path: [])
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
        nba = next(model for model in payload["models"] if model["sport"] == "nba")

        rendered = _render_model_card(nba)

        assert response.status == 200
        assert rendered == {
            "backfill": "654 obs · 577 calls · 73.7% · Brier 0.185 · QUALIFIED",
            "artifact": "VERIFIED",
            "main_ledger": "—",
            "flat_ledger": "—",
        }
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
            headers={
                "Content-Type": "application/json",
                "X-Dashboard-Token": dashboard_server._DASHBOARD_TOKEN,
            },
        )
        response = connection.getresponse()
        assert response.status == 404
        assert json.loads(response.read()) == {"error": "unknown route"}
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_without_dashboard_token_is_refused(monkeypatch) -> None:
    """Real gap fixed 2026-08-02: this dashboard has real order-execution
    capability (POST /api/order/submit shells out to `execute --execute`)
    but had no authentication -- only an Origin/Host CSRF check and a
    client-supplied confirm:true flag, neither of which stops a different
    local process/user on the same machine from curling the API directly."""
    server = dashboard_server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/dedupe",
            body=json.dumps({"confirm": True}),
            headers={"Content-Type": "application/json"},  # no X-Dashboard-Token
        )
        response = connection.getresponse()
        assert response.status == 401
        assert json.loads(response.read())["status"] == "refused"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_with_wrong_dashboard_token_is_refused() -> None:
    server = dashboard_server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/dedupe",
            body=json.dumps({"confirm": True}),
            headers={"Content-Type": "application/json", "X-Dashboard-Token": "not-the-real-token"},
        )
        response = connection.getresponse()
        assert response.status == 401
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_with_correct_dashboard_token_passes_auth(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "dedupe_ledger", lambda: {"status": "ok", "removed": 0})
    server = dashboard_server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/dedupe",
            body=json.dumps({"confirm": True}),
            headers={
                "Content-Type": "application/json",
                "X-Dashboard-Token": dashboard_server._DASHBOARD_TOKEN,
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"status": "ok", "removed": 0}
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_get_requests_do_not_require_the_dashboard_token() -> None:
    """Only state-changing POST routes are gated -- read-only GET endpoints
    stay open, matching this project's existing read-only dashboard API."""
    server = dashboard_server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        response.read()
        assert response.status == 200
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_model_ledger(path: Path, rows: list[dict]) -> None:
    from model_prediction.model_ledger import FIELDNAMES  # only used to build a real fixture file

    path.parent.mkdir(parents=True, exist_ok=True)
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Predictions"
    sheet.append(FIELDNAMES)
    for row in rows:
        sheet.append([row.get(field, "") for field in FIELDNAMES])
    workbook.save(path)


def test_model_ledger_comparison_groups_open_predictions_by_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DATA", tmp_path)
    _write_model_ledger(
        tmp_path / "model_ledgers" / "mlb-moneyline-elo-trend-lr.xlsx",
        [
            {
                "prediction_id": "p1",
                "model_id": "mlb-moneyline-elo-trend-lr",
                "event_id": "event-1",
                "market_type": "moneyline",
                "selection": "home",
                "model_probability": "0.62",
                "decision_price": "0.58",
                "model_market_difference": "0.04",
                "event_start_utc": "2026-08-03T00:00:00Z",
                "status": "open",
            },
            {
                "prediction_id": "p2",
                "model_id": "mlb-moneyline-elo-trend-lr",
                "event_id": "event-2",
                "market_type": "moneyline",
                "status": "settled",
                "result": "win",
                "pnl_units": "0.75",
            },
        ],
    )
    _write_model_ledger(
        tmp_path / "model_ledgers" / "mlb-spread-measured-edge.xlsx",
        [
            {
                "prediction_id": "p3",
                "model_id": "mlb-spread-measured-edge",
                "event_id": "event-1",
                "market_type": "spread",
                "selection": "home",
                "line": "-1.5",
                "status": "open",
                "event_start_utc": "2026-08-03T00:00:00Z",
            }
        ],
    )

    result = dashboard_server.model_ledger_comparison()

    assert set(result["models"]) == {"mlb-moneyline-elo-trend-lr", "mlb-spread-measured-edge"}
    assert result["models"]["mlb-moneyline-elo-trend-lr"]["settled"] == 1
    assert result["models"]["mlb-moneyline-elo-trend-lr"]["pnl_units"] == 0.75
    events = {e["event_id"]: e for e in result["events"]}
    assert set(events) == {"event-1"}  # event-2's only row is settled -- not in the open comparison view
    model_ids_for_event_1 = {p["model_id"] for p in events["event-1"]["predictions"]}
    assert model_ids_for_event_1 == {"mlb-moneyline-elo-trend-lr", "mlb-spread-measured-edge"}


def test_model_ledger_comparison_empty_when_no_ledgers_exist(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DATA", tmp_path)

    result = dashboard_server.model_ledger_comparison()

    assert result["events"] == []
    assert result["models"] == {}


def test_model_ledgers_route_is_reachable_over_http(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server, "model_ledger_comparison", lambda: {"events": [], "models": {}}
    )
    with dashboard_server._CACHE_LOCK:
        dashboard_server._CACHE.clear()
    server = dashboard_server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/api/model-ledgers")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload == {"events": [], "models": {}}
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_record_model_ledger_decision_writes_operator_fields_only(tmp_path, monkeypatch) -> None:
    """"Not model promotion. It is an event-level decision... must not
    change the model's ledger, classification, historical statistics, or
    dashboard evidence." -- the model's own fields must be byte-for-byte
    unchanged after recording an operator decision."""
    from model_prediction.model_ledger import ModelLedger

    monkeypatch.setattr(dashboard_server, "DATA", tmp_path)
    ledger = ModelLedger(tmp_path / "model_ledgers" / "mlb-moneyline-elo-trend-lr.xlsx")
    original = ledger.append_prediction(
        {"model_id": "mlb-moneyline-elo-trend-lr", "model_version": "v7", "event_id": "e1", "model_probability": "0.62"}
    )

    result = dashboard_server.record_model_ledger_decision(
        {
            "model_id": "mlb-moneyline-elo-trend-lr",
            "prediction_id": original["prediction_id"],
            "decision": "executed",
            "units": 1.5,
            "note": "clean edge",
        }
    )

    assert result["status"] == "ok"
    row = result["row"]
    assert row["operator_decision"] == "executed"
    assert row["operator_units"] == "1.5"
    assert row["operator_timestamp"]
    from model_prediction.model_ledger import MODEL_FIELDS

    for field in MODEL_FIELDS:
        assert row[field] == original[field]


def test_record_model_ledger_decision_refuses_unknown_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DATA", tmp_path)

    result = dashboard_server.record_model_ledger_decision(
        {"model_id": "does-not-exist", "prediction_id": "p1", "decision": "executed"}
    )

    assert result["status"] == "refused"
    assert "unknown model_id" in result["error"]


def test_record_model_ledger_decision_refuses_unknown_prediction_id(tmp_path, monkeypatch) -> None:
    from model_prediction.model_ledger import ModelLedger

    monkeypatch.setattr(dashboard_server, "DATA", tmp_path)
    ModelLedger(tmp_path / "model_ledgers" / "mlb-moneyline-elo-trend-lr.xlsx").append_prediction(
        {"model_id": "mlb-moneyline-elo-trend-lr", "model_version": "v7", "event_id": "e1"}
    )

    result = dashboard_server.record_model_ledger_decision(
        {"model_id": "mlb-moneyline-elo-trend-lr", "prediction_id": "does-not-exist", "decision": "executed"}
    )

    assert result["status"] == "refused"
    assert "unknown prediction_id" in result["error"]


def test_record_model_ledger_decision_requires_the_core_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_server, "DATA", tmp_path)

    result = dashboard_server.record_model_ledger_decision({"model_id": "x"})

    assert result["status"] == "refused"
    assert "required" in result["error"]


def test_model_ledger_decision_route_requires_the_dashboard_token(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server, "record_model_ledger_decision", lambda payload: {"status": "ok", "row": {}}
    )
    server = dashboard_server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/model-ledgers/decision",
            body=json.dumps({"confirm": True, "model_id": "x", "prediction_id": "p1", "decision": "executed"}),
            headers={"Content-Type": "application/json"},  # no X-Dashboard-Token
        )
        response = connection.getresponse()
        assert response.status == 401
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_served_dashboard_html_embeds_the_real_session_token(tmp_path, monkeypatch) -> None:
    page = tmp_path / "dashboard.html"
    page.write_text('<head></head><body><script>\n"use strict";\nconsole.log(1);</script></body>', encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "ROOT", tmp_path)
    server = dashboard_server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read().decode()
        assert response.status == 200
        assert dashboard_server._DASHBOARD_TOKEN in body
        assert "X-Dashboard-Token" in body
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
