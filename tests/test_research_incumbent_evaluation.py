from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from model_prediction.models.learned_market import LearnedMarketArtifact, artifact_hash
from model_prediction.research_incumbent_evaluation import (
    QuoteArchive,
    canonical_hash,
    deduplicate,
    economic_metrics,
    home_side,
    legacy_projection,
    probability,
    replay,
    simulate,
    taker_fee,
    timestamp,
    validate_quote,
)


@pytest.fixture
def quote():
    return {
        "provider": "polymarket_us",
        "timestamp_valid": True,
        "reconstructed": False,
        "usage": "prospective_executable_bbo",
        "market_state": "MARKET_STATE_OPEN",
        "market_type": "moneyline",
        "market_slug": "aec-mlb-nyy-bos-2026-09-08",
        "market_id": "123",
        "league": "MLB",
        "line": None,
        "observed_at_utc": "2026-09-08T20:00:00Z",
        "event_start_utc": "2026-09-08T22:00:00Z",
        "long": {"description": "New York Yankees", "ask": 0.51, "bid": 0.49, "ask_size": 100},
        "short": {"description": "Boston Red Sox", "ask": 0.51, "bid": 0.49, "ask_size": 100},
    }


@pytest.fixture
def record():
    return {
        "pick_id": "a",
        "model_id": "model",
        "model_artifact_hash": "hash",
        "event_id": "1",
        "market_type": "moneyline",
        "selection": "home",
        "line": None,
        "sport": "mlb",
        "created_at_utc": "2026-09-08T20:01:00Z",
        "event_start_utc": "2026-09-08T22:00:00Z",
        "model_probability": 0.6,
        "result": "win",
        "feature_payload_json": "{}",
        "decision_payload_json": "{}",
        "ledger_tier": "main",
        "market_snapshot_hash": None,
    }


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), -0.1, 1.01, ""])
def test_probability_rejects_missing_or_invalid_without_neutral_fallback(bad):
    with pytest.raises(ValueError):
        probability(bad)


def test_zero_is_a_valid_probability_and_naive_times_are_rejected():
    assert probability(0) == 0
    with pytest.raises(ValueError, match="timezone"):
        timestamp("2026-09-08T20:00:00")


def test_mirrors_collapse_but_conflicts_and_distinct_horizons_do_not(record):
    mirror = record | {"pick_id": "b", "ledger_tier": "flat"}
    rows, counts = deduplicate([record, mirror])
    assert len(rows) == 1
    assert counts["mirror_duplicates"] == 1
    assert rows[0]["source_pick_ids"] == ["a", "b"]
    rows, counts = deduplicate([record, mirror | {"model_probability": 0.7}])
    assert rows == []
    assert counts["conflicting_context_rows"] == 2
    rows, _ = deduplicate([record, mirror | {"created_at_utc": "2026-09-08T21:01:00Z"}])
    assert len(rows) == 2


def test_feature_metadata_variations_do_not_hide_actual_feature_conflicts(record):
    envelope = {"features": {"elo_probability": "0.6"}, "model_artifact_hash": "hash"}
    a = record | {"feature_payload_json": json.dumps(envelope | {"availability_status": "available"})}
    b = record | {"feature_payload_json": json.dumps(envelope | {"availability_status": "partial"})}
    rows, counts = deduplicate([a, b])
    assert len(rows) == 1
    assert counts["feature_metadata_only_difference_contexts"] == 1
    b["feature_payload_json"] = json.dumps(envelope | {"features": {"elo_probability": "0.7"}})
    assert deduplicate([a, b])[0] == []


def test_official_fee_examples_and_round_half_even():
    assert taker_fee(1000, Decimal("0.5")) == Decimal("15.00")
    assert taker_fee(1000, Decimal("0.1")) == Decimal("5.40")
    assert taker_fee(1, Decimal("0.5")) == Decimal("0.02")
    assert taker_fee(3, Decimal("0.5")) == Decimal("0.04")


def test_economics_is_budget_depth_and_fee_bounded(quote):
    quote["long"]["ask_size"] = 3.9
    trade = simulate(0.9, True, quote, "long")
    assert trade["quantity"] == 3
    assert trade["cost_usd"] <= 5
    assert trade["fee_usd"] > 0
    assert trade["pnl_usd"] == pytest.approx(3 - trade["cost_usd"])
    assert not simulate(0.5, True, quote, "long")["called"]
    # Home orientation must be independent of the exchange's long side.
    assert simulate(0.9, True, quote, "short")["side"] == "short"


def test_empty_economics_is_null_not_zero_profit():
    report = economic_metrics([])
    assert report["candidate"]["pnl_usd"] is None
    assert report["candidate"]["roi"] is None
    assert report["profitability_proven"] is False


def test_unique_raw_and_legacy_projection_linkage(tmp_path, quote, record):
    path = tmp_path / "quotes.jsonl"
    path.write_text(json.dumps(quote) + "\n" + json.dumps(quote) + "\n")
    archive = QuoteArchive(tmp_path)
    ref = record | {"market_snapshot_archive_path": str(path), "market_snapshot_hash": canonical_hash(quote)}
    assert archive.lookup(ref)[1] == "FULL_RECORD_HASH"
    ref["market_snapshot_hash"] = canonical_hash(legacy_projection(quote, "long"))
    assert archive.lookup(ref)[1] == "LEGACY_PROJECTION_HASH"
    # Projection omits depth, so two distinct records can have the same old
    # hash. This must be rejected instead of choosing a convenient size.
    changed = copy.deepcopy(quote)
    changed["long"]["ask_size"] = 200
    path.write_text(json.dumps(quote) + "\n" + json.dumps(changed) + "\n")
    with pytest.raises(ValueError, match="ambiguous"):
        QuoteArchive(tmp_path).lookup(ref)


def test_valid_quote_maps_home_and_rejects_same_city_ambiguity(quote, record):
    assert validate_quote(record, quote, "New York Yankees", "Boston Red Sox") == "long"
    quote["short"]["description"] = "New York"
    with pytest.raises(ValueError, match="ambiguous"):
        home_side(quote, "New York Yankees", "New York Mets")


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("market_slug", "aec-mlb-nyy-bos-2026-09-08-f5", "horizon"),
        ("market_type", "spread", "horizon"),
        ("line", 0.5, "horizon"),
        ("league", "WNBA", "horizon"),
        ("timestamp_valid", "true", "provenance"),
        ("reconstructed", True, "provenance"),
        ("event_start_utc", "2026-09-08T23:00:00Z", "start_mismatch"),
        ("observed_at_utc", "2026-09-08T20:02:00Z", "not_pregame"),
        ("observed_at_utc", "2026-09-08T19:00:00Z", "stale"),
    ],
)
def test_quote_fail_closed(quote, record, field, value, error):
    quote[field] = value
    with pytest.raises(ValueError, match=error):
        validate_quote(record, quote, "New York Yankees", "Boston Red Sox")


def test_older_fee_schedule_is_not_inferred_from_current_schedule(quote, record):
    quote["observed_at_utc"] = "2026-06-08T20:00:00Z"
    quote["event_start_utc"] = record["event_start_utc"] = "2026-06-08T22:00:00Z"
    record["created_at_utc"] = "2026-06-08T20:01:00Z"
    with pytest.raises(ValueError, match="fee_schedule"):
        validate_quote(record, quote, "New York Yankees", "Boston Red Sox")


def test_replay_requires_exact_features_hash_and_probability(record):
    payload = {
        "method": "logistic_regression",
        "model_version": "model",
        "sport": "MLB",
        "market_models": {"moneyline": {"feature_names": ["x"], "coefficients": [1.0], "intercept": 0.0}},
    }
    payload["artifact_hash"] = artifact_hash(payload)
    artifact = LearnedMarketArtifact(payload)
    record["model_artifact_hash"] = artifact.hash
    record["model_probability"] = 0.5
    record["feature_payload_json"] = json.dumps(
        {"model_artifact_hash": artifact.hash, "features": {"x": 0.0}}
    )
    assert replay(record, artifact)["status"] == "PASS"
    record["model_probability"] = 0.7
    assert replay(record, artifact)["status"] == "PROBABILITY_MISMATCH"
    record["model_probability"] = None
    assert replay(record, artifact)["status"] == "MISSING_OR_INVALID_INPUT"
    record["model_artifact_hash"] = "older"
    assert replay(record, artifact)["status"] == "DIFFERENT_OR_MISSING_ARTIFACT"


def test_failed_replay_never_reaches_comparison(tmp_path, monkeypatch, record):
    from scripts import evaluate_research_incumbents as runner

    config = tmp_path / "config"
    (config / "models").mkdir(parents=True)
    model = {"sport": "WNBA", "market": "moneyline", "incumbent": "model", "model_id": "candidate"}
    registry_model = {
        "sport": "WNBA",
        "market": "moneyline",
        "model_id": "model",
        "enabled": True,
        "serving_status": "production",
        "artifact": "config/models/model.json",
    }
    (config / "production.yaml").write_text(json.dumps({"prediction_service": {"models": [registry_model]}}))
    (config / "models/model.json").write_text("{}")
    generation = tmp_path / "generation"
    generation.mkdir()
    (generation / "manifest.json").write_text(json.dumps({"models": [model]}))
    for name in (
        "src/model_prediction/models/soccer.py",
        "src/model_prediction/models/tennis.py",
        "src/model_prediction/research_incumbent_evaluation.py",
        "scripts/evaluate_research_incumbents.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    class Artifact:
        hash = "hash"

    monkeypatch.setattr(runner.LearnedMarketArtifact, "load", lambda _: Artifact())
    record["sport"] = "wnba"
    monkeypatch.setattr(runner, "read_records", lambda *_: [record])
    monkeypatch.setattr(runner, "replay", lambda *_: {"status": "PROBABILITY_MISMATCH"})

    def forbidden(*args):
        raise AssertionError("comparison must not run after replay failure")

    monkeypatch.setattr(runner, "paired_rows", forbidden)
    result = runner.run(tmp_path, generation, tmp_path / "unused.db", tmp_path / "output")
    report = result["models"][0]
    assert report["incumbent_reproduction"] == "FAIL"
    assert report["predictive"] is None
    assert report["economics"]["status"] == "NOT_EVALUATED_REPLAY_BLOCKED"
