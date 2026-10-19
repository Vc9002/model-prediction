"""Regression checks for the versioned soccer league normalization correction."""

from dataclasses import asdict, replace

import pytest

from model_prediction.coded_market_replay import build_snapshot, replay_inputs, validate_decision_snapshot
from model_prediction.features.base import GameRecord
from model_prediction.models.soccer import UpcomingMatch, soccer_model_v2
from model_prediction.models.soccer_v3 import soccer_model_for_version, soccer_model_v3
from model_prediction.soccer.registry import resolve


def history(league="ARGENTINA_2", n=3, score=(2, 0)):
    return [
        GameRecord(str(i), f"2026-09-{i + 1:02d}T12:00:00Z", league, "Away", "Home", score[1], score[0])
        for i in range(n)
    ]


def match(league="ARGENTINA_2"):
    return UpcomingMatch("next", "2026-10-01T12:00:00Z", "Away", "Home", league)


def moneyline(model, games, upcoming):
    return next(p for p in model.predict_games(games, [upcoming]) if p.market_type == "moneyline")


def test_unknown_league_uses_own_scoring_scale_and_configured_shrinkage():
    model = soccer_model_v3()
    games = history()
    strengths, historical = model._strengths(games)
    config = model._league_config("ARGENTINA_2", historical)
    assert historical == config.baseline == 1.0
    cfg = resolve("ARGENTINA_2")
    expected_attack = 1 + (3 / (3 + cfg.shrinkage_prior_games)) * (3 / (3 + cfg.ewma_prior_strength))
    assert strengths["Home"]["attack"] == pytest.approx(expected_attack)
    assert moneyline(model, games, match()).feature_basis["league_goals_per_team"] == 1.0


def test_named_league_strengths_and_rates_use_same_baseline():
    model = soccer_model_v3()
    cfg = resolve("LA_LIGA_2")
    strengths, _ = model._strengths(history("LA_LIGA_2", score=(1, 1)))
    # An average of one goal shrunk toward cfg.baseline is normalized by that
    # same baseline, rather than by the historical mean of one.
    expected = ((3 / 11) * 1 + (8 / 11) * cfg.baseline) / cfg.baseline
    assert strengths["Home"]["attack"] == pytest.approx(expected)


def test_other_competitions_and_future_results_cannot_change_prediction():
    model = soccer_model_v3()
    games = history()
    original = moneyline(model, games, match())
    added = history("EPL", score=(8, 0)) + [replace(games[0], event_start_utc="2026-10-02T12:00:00Z")]
    assert moneyline(model, games + added, match()).probabilities == original.probabilities
    assert model.predict_games(games, [match("NO_HISTORY")]) == []
    with pytest.raises(ValueError, match="explicit_competition"):
        model.predict_games(games, [match("SOCCER")])


def test_legacy_v2_keeps_global_fallback_and_v3_snapshot_replays():
    legacy = moneyline(soccer_model_v2(), history(), match())
    assert legacy.feature_basis["league_goals_per_team"] == resolve("ARGENTINA_2").baseline
    candidate = moneyline(soccer_model_v3(), history(), match())
    assert candidate.model_version == "soccer-poisson-dc-v3"
    replayed = replay_inputs(candidate.inference_inputs)
    assert {k: replayed[k] for k in candidate.probabilities} == candidate.probabilities
    assert "models/soccer_v3.py" in candidate.inference_inputs["code_sha256"]


def test_serving_config_registry_and_factory_agree():
    from pathlib import Path

    import yaml

    from model_prediction.production_registry import ProductionModelRegistry

    root = Path(__file__).resolve().parents[1]
    registry = ProductionModelRegistry.load(root)
    champion = registry.champion("SOCCER", "moneyline")
    config = yaml.safe_load((root / "config/model.yaml").read_text(encoding="utf-8"))
    version = config["models"]["SOCCER"]["active_production_version"]
    assert version == champion.model_id == "soccer-poisson-dc-v3"
    assert champion.rollback_model == "soccer-poisson-dc-v2"
    assert champion.evidence_status == "unverified"
    assert soccer_model_for_version(version).version == version
    assert registry.entries[version].available
    with pytest.raises(ValueError, match="unsupported_soccer_model_version"):
        soccer_model_for_version("soccer-poisson-dc-v30")


def test_sparse_low_scoring_league_does_not_abort_other_competitions():
    model = soccer_model_v3()
    sparse = history(n=2, score=(0, 0))
    sparse[1] = replace(sparse[1], home_score=1)
    valid = history("EPL", score=(0, 0))
    predictions = model.predict_games(sparse + valid, [match(), replace(match("EPL"), event_id="valid")])
    assert predictions and all(p.event_id == "valid" for p in predictions)
    assert model.skipped_predictions[0]["event_id"] == "next"
    assert "implausible baseline" in model.skipped_predictions[0]["reason"]
    assert (
        moneyline(model, valid, match("EPL")).feature_basis["league_goals_per_team"]
        == resolve("EPL").baseline
    )


def test_v3_persisted_snapshot_contract_and_code_identity():
    candidate = moneyline(soccer_model_v3(), history(), match())
    contract = asdict(candidate) | {"selection": "home", "model_probability": candidate.probabilities["home"]}
    artifact_hash = candidate.inference_inputs["code_sha256"]["models/soccer.py"]
    snapshot = build_snapshot(contract, artifact_hash, "2026-10-01T10:00:00Z")
    row = contract | {"league": "SOCCER", "model_artifact_hash": artifact_hash}
    assert validate_decision_snapshot(snapshot, row) == candidate.probabilities["home"]
    candidate.inference_inputs["code_sha256"]["models/soccer_v3.py"] = "corrupted"
    with pytest.raises(ValueError, match="code_identity_mismatch"):
        replay_inputs(candidate.inference_inputs)
