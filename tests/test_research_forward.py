from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from model_prediction.research_forward import forecast, normalize_fixture, rebuild_forecast, replay_forecast
from model_prediction.research_generation import (
    FEATURE_NAMES,
    Fixture,
    Game,
    ResearchRow,
    build_rows,
    digest,
    file_hash,
    fixture_features,
    load_games,
    train_candidate,
)


def history():
    return [
        Game(
            str(i),
            (datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i)).date().isoformat(),
            "NBA:A",
            "NBA:B",
            float(8 + i % 4),
            9.5,
        )
        for i in range(120)
    ]


def test_unplayed_feature_parity_and_observation_cutoff():
    games = history()
    historical = build_rows(games, "NBA")
    fixtures = [Fixture(g.event_id, g.date, g.a, g.b) for g in games]
    built = fixture_features(games, fixtures)
    for row in historical:
        assert built[row.event_id] == (row.features, row.feature_max_event_date)
    upcoming = [Fixture("new", "2025-05-10", "NBA:A", "NBA:B")]
    first = fixture_features(games, upcoming, available_before="2025-04-01")
    altered = [replace(g, score_a=999) if g.date >= "2025-04-01" else g for g in games]
    assert first == fixture_features(altered, upcoming, available_before="2025-04-01")
    assert first["new"][1] == "2025-03-31"
    with pytest.raises(ValueError, match="duplicate_fixture"):
        fixture_features(games, upcoming * 2)
    assert fixture_features(games, [replace(upcoming[0], a="unknown")])["new"] is None


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    root = tmp_path_factory.mktemp("forward")
    rows = [
        ResearchRow(
            str(i),
            (datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i)).date().isoformat(),
            tuple(float((i + j) % 3) for j in range(len(FEATURE_NAMES))),
            i % 2,
            float(i % 7 - 3),
            float(10 + i % 8),
            "2024-12-01",
        )
        for i in range(240)
    ]
    for market in ("moneyline", "spread", "total"):
        train_candidate(rows, "NBA", market, "incumbent", root / market)
    return root


def fixture(market):
    result = {
        "event_id": "new",
        "event_start_utc": "2025-05-10T20:00:00Z",
        "home_team_id": "A",
        "away_team_id": "B",
    }
    if market in {"spread", "total"}:
        result.update(
            line=-3.5 if market == "spread" else 19.5, period="full_game", line_convention="signed_home"
        )
    return result


def evidence(games):
    return {
        "captured_at_utc": "2025-05-10T10:00:00Z",
        "source_sha256": "a" * 64,
        "normalized_games_sha256": digest([asdict(g) for g in games]),
    }


@pytest.mark.parametrize("market", ["moneyline", "spread", "total"])
def test_forecast_replays_named_inputs_and_rejects_wrong_contract(models, market):
    path = models / market / "model.joblib"
    games = history()
    p = forecast(
        path,
        fixture(market),
        "NBA",
        market,
        "2025-05-10T12:00:00Z",
        games=games,
        source_evidence=evidence(games),
    )
    assert np.isclose(sum(p["probabilities"].values()), 1)
    assert replay_forecast(p, path) == p["probabilities"]
    assert p["economic_evaluation"] is None and not p["promote"]
    bad = {**p, "line": 12.5}
    bad["snapshot_hash"] = digest({k: v for k, v in bad.items() if k != "snapshot_hash"})
    with pytest.raises(ValueError, match="line_mismatch"):
        replay_forecast(bad, path)
    bad = {**p, "features": {**p["features"], "rating_gap": 99}}
    with pytest.raises(ValueError, match="hash_mismatch"):
        replay_forecast(bad, path)
    with pytest.raises(ValueError, match="normalized_history_hash_mismatch"):
        forecast(
            path,
            fixture(market),
            "NBA",
            market,
            "2025-05-10T12:00:00Z",
            games=games[1:],
            source_evidence=evidence(games),
        )


def test_full_source_rebuild_and_source_tampering(models, tmp_path):
    source = tmp_path / "history.jsonl"
    rows = [
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
    source.write_text("\n".join(json.dumps(r) for r in rows))
    games, _ = load_games(source, "NBA", "2025-05-10")
    source_evidence = {**evidence(games), "source_sha256": file_hash(source)}
    path = models / "moneyline/model.joblib"
    p = forecast(
        path,
        fixture("moneyline"),
        "NBA",
        "moneyline",
        "2025-05-10T12:00:00Z",
        games=games,
        source_evidence=source_evidence,
    )
    assert rebuild_forecast(p, path, source) == p["probabilities"]
    source.write_text(source.read_text() + "\n")
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        rebuild_forecast(p, path, source)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"event_start_utc": "2025-05-10T08:00:00Z"}, "fixture_not_future"),
        ({"event_start_utc": "2025-05-10T20:00:00"}, "timezone_required"),
        ({"home_score": 0}, "fixture_contains_outcome"),
        ({"home_team_id": None}, "invalid_fixture_participants"),
    ],
)
def test_fixture_boundary(change, reason):
    with pytest.raises(ValueError, match=reason):
        normalize_fixture({**fixture("moneyline"), **change}, "NBA", "2025-05-10T12:00:00Z")


def test_orientation_uses_ids_and_tour_without_winner():
    raw = {
        "event_id": "x",
        "event_start_utc": "2025-05-10T20:00:00Z",
        "league": "ATP",
        "player1_id": "z",
        "player2_id": "a",
        "surface": "Clay",
    }
    result = normalize_fixture(raw, "TENNIS", "2025-05-10T12:00:00Z")
    assert (result.a, result.b, result.surface) == ("ATP:a", "ATP:z", "clay")


@pytest.mark.parametrize(
    "market,change,reason",
    [
        ("spread", {"line_convention": "signed_away"}, "signed_home_line_required"),
        ("total", {"period": "first_half"}, "exact_full_game_period_required"),
        ("total", {"line": None}, "exact_contract_line_required"),
        ("moneyline", {"line": 1}, "unexpected_line"),
    ],
)
def test_exact_market_semantics(models, market, change, reason):
    games = history()
    with pytest.raises(ValueError, match=reason):
        forecast(
            models / market / "model.joblib",
            {**fixture(market), **change},
            "NBA",
            market,
            "2025-05-10T12:00:00Z",
            games=games,
            source_evidence=evidence(games),
        )


def test_sidecar_cannot_change_artifact_semantics(models, tmp_path):
    import shutil

    from model_prediction.research_generation import predict_bundle

    for name in ("recipe.json", "model.joblib"):
        shutil.copyfile(models / "moneyline" / name, tmp_path / name)
    recipe = json.loads((tmp_path / "recipe.json").read_text())
    recipe["temperature"] += 1
    (tmp_path / "recipe.json").write_text(json.dumps(recipe))
    with pytest.raises(ValueError, match="artifact_recipe_mismatch"):
        predict_bundle(tmp_path / "model.joblib", np.zeros((1, len(FEATURE_NAMES))))


def test_runner_preserves_existing_output_and_reports_partial(models, tmp_path):
    import shutil

    from scripts.forecast_research_generation import run

    generation = tmp_path / "generation"
    model_dir = generation / "models/nba-moneyline"
    model_dir.mkdir(parents=True)
    for name in ("model.joblib", "recipe.json"):
        shutil.copyfile(models / "moneyline" / name, model_dir / name)
    (generation / "manifest.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "sport": "NBA",
                        "market": "moneyline",
                        "status": "TRAINED_RESEARCH_ONLY",
                        "artifact_sha256": file_hash(model_dir / "model.joblib"),
                    }
                ]
            }
        )
    )
    source = tmp_path / "data/historical/nba_games_all.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            json.dumps(
                {
                    "event_id": g.event_id,
                    "event_start_utc": g.date,
                    "home_team_id": "A",
                    "away_team_id": "B",
                    "home_score": g.score_a,
                    "away_score": g.score_b,
                }
            )
            for g in history()
        )
    )
    requests = tmp_path / "fixtures.jsonl"
    raw = {
        **fixture("moneyline"),
        "sport": "NBA",
        "market": "moneyline",
        "event_start_utc": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
    }
    requests.write_text(json.dumps(raw) + "\n" + json.dumps(raw) + "\n")
    output = tmp_path / "capture"
    result = run(tmp_path, generation, requests, output)
    assert result["status"] == "PARTIAL"
    assert result["counts"] == {"CAPTURED_REPLAY_PASS": 1, "NO_PREDICTION": 1}
    before = (output / "RUN_STATUS.json").read_bytes()
    with pytest.raises(FileExistsError):
        run(tmp_path, generation, requests, output)
    assert (output / "RUN_STATUS.json").read_bytes() == before
    records = [json.loads(line) for line in (output / "predictions.jsonl").read_text().splitlines()]
    assert records[1]["reason"] == "duplicate_fixture_contract"
    requests.write_text("")
    failed = tmp_path / "empty"
    with pytest.raises(ValueError, match="empty_fixture_batch"):
        run(tmp_path, generation, requests, failed)
    assert json.loads((failed / "RUN_STATUS.json").read_text())["status"] == "FAILED"
