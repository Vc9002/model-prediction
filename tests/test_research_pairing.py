import json
from decimal import Decimal

import pytest

from model_prediction.research_forward import forecast
from model_prediction.research_generation import file_hash, load_games
from model_prediction.research_incumbent_evaluation import simulate, taker_fee
from model_prediction.research_pairing import (
    coherent_distribution,
    compare_pair,
    incumbent_distribution,
    simulate_contracts,
)
from tests.test_research_forward import evidence, fixture, history, models  # noqa: F401


def test_binary_economics_exactly_reproduces_existing_policy():
    for p_home in (0.1, 0.49, 0.5, 0.8):
        for won_home in (False, True):
            snapshot = {"long": {"ask": 0.43, "ask_size": 12}, "short": {"ask": 0.6, "ask_size": 12}}
            old = simulate(p_home, won_home, snapshot, "long")
            contracts = [
                dict(
                    market_id="m",
                    side=side,
                    **snapshot[side],
                    settlement_values={"home": int(side == "long"), "away": int(side == "short")},
                )
                for side in ("long", "short")
            ]
            new = simulate_contracts(
                {"home": p_home, "away": 1 - p_home}, "home" if won_home else "away", contracts
            )
            assert all(new[key] == value for key, value in old.items())


@pytest.mark.parametrize("draw_payout", [0.5, "entry_price"])
def test_half_value_and_principal_refund_are_distinct(draw_payout):
    contract = {
        "market_id": "m",
        "side": "long",
        "ask": 0.3,
        "ask_size": 20,
        "settlement_values": {"home": 1, "away": 0, "draw": draw_payout},
    }
    trade = simulate_contracts({"home": 0.65, "away": 0.25, "draw": 0.1}, "draw", [contract])
    assert trade["quantity"] * trade["ask"] + trade["fee_usd"] <= 5
    cash = Decimal("0.3" if draw_payout == "entry_price" else "0.5")
    fee = taker_fee(trade["quantity"], Decimal("0.3"))
    assert trade["pnl_usd"] == float(trade["quantity"] * (cash - Decimal("0.3")) - fee)
    if draw_payout == "entry_price":
        assert trade["pnl_usd"] < 0  # Entry fee is retained, not erased.


def test_quote_capacity_and_incomplete_terms_fail_closed():
    contract = {
        "market_id": "m",
        "side": "long",
        "ask": 0.1,
        "ask_size": 2.9,
        "settlement_values": {"home": 1, "away": 0},
    }
    assert simulate_contracts({"home": 0.9, "away": 0.1}, "home", [contract])["quantity"] == 2
    with pytest.raises(ValueError, match="settlement"):
        simulate_contracts({"home": 0.8, "away": 0.1, "draw": 0.1}, "draw", [contract])
    with pytest.raises(ValueError, match="duplicate"):
        simulate_contracts({"home": 0.9, "away": 0.1}, "home", [contract, contract])
    assert not simulate_contracts({"home": 0.01, "away": 0.99}, "away", [contract])["called"]


def test_independent_calibrated_sides_cannot_be_normalized_into_joint_probabilities():
    with pytest.raises(ValueError, match="incoherent"):
        incumbent_distribution(
            {
                "schema": "mlb-margin-replay-v1",
                "served_probabilities": {"home": 0.6, "away": 0.4},
                "raw_probabilities": {"push": 0.1},
            }
        )
    p, error = coherent_distribution({"home": 0.600001, "away": 0.3, "draw": 0.1})
    assert error == pytest.approx(1e-6)
    assert sum(p.values()) == pytest.approx(1)
    values, _ = incumbent_distribution(
        {
            "schema": "international-elo-replay-v1",
            "outcome_probabilities": {"home": 0.6, "away": 0.3, "draw": 0.1},
            "settlement_values": {"home": 0.65, "away": 0.35},
        }
    )
    assert values["home"] == 0.6


def test_pair_rebuild_gate_and_strict_decision_context(models, tmp_path, monkeypatch):  # noqa: F811
    from model_prediction.learned_replay import build_snapshot as learned_snapshot
    from model_prediction.models.learned_market import LearnedMarketArtifact

    source = tmp_path / "source.jsonl"
    raw_history = [
        {
            "event_id": g.event_id,
            "event_start_utc": g.date,
            "home_team_id": "A",
            "away_team_id": "B",
            "home_score": g.score_a,
            "away_score": g.score_b,
        }
        for g in history()
    ]
    source.write_text("\n".join(json.dumps(r) for r in raw_history))
    games, _ = load_games(source, "NBA", "2025-05-10")
    raw_fixture = fixture("moneyline") | {"home_team": "Home", "away_team": "Away"}
    candidate = forecast(
        models / "moneyline/model.joblib",
        raw_fixture,
        "NBA",
        "moneyline",
        "2025-05-10T12:00:00Z",
        games=games,
        source_evidence=evidence(games) | {"source_sha256": file_hash(source)},
    )
    # Use the production learned artifact type with an explicit NBA artifact.
    from pathlib import Path

    import yaml

    root = Path(__file__).parents[1]
    registered = next(
        m
        for m in yaml.safe_load((root / "config/production.yaml").read_text())["prediction_service"]["models"]
        if m["sport"] == "NBA" and m["market"] == "moneyline"
    )
    artifact = LearnedMarketArtifact.load(root / registered["artifact"])
    features = {name: 0.5 for name in artifact.raw["market_models"]["moneyline"]["feature_names"]}
    p = artifact.probability("moneyline", features)
    snapshot = learned_snapshot(
        artifact,
        features,
        event_id="new",
        observed_at_utc=candidate["observed_at_utc"],
        event_start_utc=raw_fixture["event_start_utc"],
        base_home_probability=p,
        served_home_probability=p,
        adjustment={"method": "identity", "applied": False},
    )
    row = {
        "pick_id": "pick",
        "sport": "NBA",
        "model_id": artifact.version,
        "model_artifact_hash": artifact.hash,
        "event_id": "new",
        "event_start_utc": raw_fixture["event_start_utc"],
        "created_at_utc": "2025-05-10T12:00:01Z",
        "settled_at_utc": "2025-05-10T23:00:00Z",
        "status": "settled",
        "result": "win",
        "market_type": "moneyline",
        "selection": "home",
        "line": None,
        "model_probability": p,
        "decision_payload_json": json.dumps({"home_team": "Home", "away_team": "Away"}),
        "feature_payload_json": json.dumps({"model_input_snapshot": snapshot}),
    }
    outcome = raw_fixture | {"status": "completed", "home_score": 102, "away_score": 99}
    args = (candidate, models / "moneyline/model.joblib", source, outcome, "2025-05-11T00:00:00Z")
    result = compare_pair(row, *args)
    assert result["incumbent_reproduction"] == "PASS"
    assert result["outcome"] == "a_win" and result["economics"] is None
    assert result["incumbent_score"]["brier_vector_sum"] == pytest.approx(2 * (1 - p) ** 2)
    with pytest.raises(ValueError, match="settlement"):
        compare_pair(row | {"result": "loss"}, *args)
    with pytest.raises(ValueError, match="context"):
        compare_pair(row | {"created_at_utc": "2025-05-10T11:00:00Z"}, *args)
    with pytest.raises(ValueError):
        compare_pair(row | {"model_probability": p + 0.01}, *args)
    with pytest.raises(ValueError, match="orientation"):
        compare_pair(
            row | {"decision_payload_json": json.dumps({"home_team": "Away", "away_team": "Home"})}, *args
        )

    # Drive the real report runner from retained candidate/source/outcome files;
    # only the canonical database read is replaced with this deterministic row.
    import shutil

    import scripts.evaluate_captured_pairs as runner
    from model_prediction.research_generation import digest

    scored = tmp_path / "scores"
    (scored / "models/nba-moneyline").mkdir(parents=True)
    (scored / "inputs").mkdir()
    (scored / "outcomes").mkdir()
    for filename in ("model.joblib", "recipe.json"):
        shutil.copy2((models / "moneyline" / filename), scored / "models/nba-moneyline" / filename)
    shutil.copy2(source, scored / "inputs/nba-games.jsonl")
    outcome_path = scored / "outcomes/nba-games.jsonl"
    outcome_path.write_text(json.dumps(outcome) + "\n")
    (scored / "generation_manifest.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "sport": "NBA",
                        "market": "moneyline",
                        "model_id": candidate["model_id"],
                        "incumbent": artifact.version,
                        "artifact_sha256": candidate["model_file_sha256"],
                    }
                ]
            }
        )
    )
    (scored / "forecast_records.jsonl").write_text(
        json.dumps({"status": "CAPTURED_REPLAY_PASS", "prediction": candidate}) + "\n"
    )
    (scored / "scored_rows.jsonl").write_text(
        json.dumps(
            {
                "status": "SCORED",
                "sport": "NBA",
                "market": "moneyline",
                "event_id": "new",
                "snapshot_hash": candidate["snapshot_hash"],
                "outcome_record_hash": digest(outcome),
                "outcome_source": {
                    "source_sha256": file_hash(outcome_path),
                    "captured_at_utc": "2025-05-11T00:00:00Z",
                },
            }
        )
        + "\n"
    )
    monkeypatch.setattr(runner, "read_records", lambda *a: [row | {"ledger_tier": "flat"}])
    report = runner.run(root, scored, tmp_path / "not_opened.db", tmp_path / "paired")
    assert report["paired"] == 1
    assert report["models"][0]["verdict"] == "INSUFFICIENT_PAIRED_EVIDENCE"
    assert report["models"][0]["economics"]["events"] == 0
    with pytest.raises(FileExistsError):
        runner.run(root, scored, tmp_path / "not_opened.db", tmp_path / "paired")
    monkeypatch.setattr(
        runner, "read_records", lambda *a: [row | {"ledger_tier": "flat", "feature_payload_json": "{}"}]
    )
    report = runner.run(root, scored, tmp_path / "not_opened.db", tmp_path / "blocked")
    assert report["paired"] == 0
    assert report["models"][0]["metrics"] is None
    assert report["models"][0]["excluded"] == {"missing_incumbent_serving_snapshot": 1}
