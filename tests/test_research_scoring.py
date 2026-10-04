import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from model_prediction.research_forward import forecast, normalize_fixture
from model_prediction.research_generation import digest, file_hash, load_games
from model_prediction.research_scoring import outcome_label, proper_score
from scripts.score_research_forward import run
from tests.test_research_forward import evidence, fixture, history, models  # noqa: F401

ROOT = Path(__file__).parents[1]
MARKETS = [
    (m["sport"], m["market"])
    for m in yaml.safe_load((ROOT / "config/production.yaml").read_text())["prediction_service"]["models"]
    if m["enabled"] and m["serving_status"] == "production"
]


def test_ncaaf_legacy_name_namespace_requires_exact_provider_id_and_name():
    raw_fixture = {
        "event_id": "e",
        "event_start_utc": "2026-09-08T12:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "home_team_id": "Home",
        "away_team_id": "Away",
        "provider_home_team_id": "10",
        "provider_away_team_id": "20",
    }
    prediction = {
        "sport": "NCAAF",
        "market": "moneyline",
        "raw_fixture": raw_fixture,
        "fixture": asdict(normalize_fixture(raw_fixture, "NCAAF", "2026-09-08T11:00:00Z")),
        "observed_at_utc": "2026-09-08T11:00:00Z",
    }
    result = raw_fixture | {
        "home_team_id": "10",
        "away_team_id": "20",
        "home_score": 21,
        "away_score": 14,
        "status": "completed",
    }
    assert outcome_label(prediction, result, "2026-09-09T00:00:00Z") == "a_win"
    legacy = {k: v for k, v in result.items() if k not in {"home_team_id", "away_team_id"}}
    assert outcome_label(prediction, legacy, "2026-09-09T00:00:00Z") == "a_win"
    for changed in ({"home_team_id": "99"}, {"home_team": "Other"}):
        with pytest.raises(ValueError, match="namespace"):
            outcome_label(prediction, result | changed, "2026-09-09T00:00:00Z")


@pytest.mark.parametrize("sport,market", MARKETS)
def test_all_registered_markets_score_exact_oriented_outcomes(sport, market):
    fixture_raw = {
        "event_id": "e",
        "event_start_utc": "2026-09-08T12:00:00Z",
        "home_team_id": "A",
        "away_team_id": "B",
        "home_team": "Home",
        "away_team": "Away",
        "player1_id": "B",
        "player2_id": "A",
        "team1_id": "A",
        "team2_id": "B",
        "league": "ATP" if sport == "TENNIS" else sport,
        "surface": "hard",
    }
    normalized = normalize_fixture(fixture_raw, sport, "2026-09-08T11:00:00Z")
    p = {
        "sport": sport,
        "market": market,
        "fixture": asdict(normalized),
        "raw_fixture": fixture_raw,
        "observed_at_utc": "2026-09-08T11:00:00Z",
        "line": -1.0 if market == "spread" else 3.0 if market == "total" else None,
    }
    raw = {
        **fixture_raw,
        "status": "completed",
        "home_score": 2,
        "away_score": 1,
        "winner_id": "A",
        "loser_id": "B",
        "team1_score": 2,
        "team2_score": 1,
        "end_utc": "2026-09-08T14:00:00Z",
    }
    expected = "a_win"
    if market in {"spread", "total"}:
        expected = "push"
    if market == "nrfi":
        raw = {
            "status": "Final",
            "game_pk": 123,
            "game_start_utc": fixture_raw["event_start_utc"],
            "observed_at_utc": "2026-09-08T15:00:00Z",
            "home": {"team_name": "Home"},
            "away": {"team_name": "Away"},
            "first_inning_runs_home": 0,
            "first_inning_runs_away": 0,
        }
        expected = "nrfi"
    assert outcome_label(p, raw, "2026-09-09T00:00:00Z") == expected
    with pytest.raises(ValueError):
        outcome_label(p, raw, "2026-09-08T11:59:00Z")
    if market == "nrfi":
        with pytest.raises(ValueError, match="first_inning"):
            outcome_label(p, raw | {"first_inning_runs_home": None}, "2026-09-09T00:00:00Z")
    else:
        with pytest.raises(ValueError, match="mismatch"):
            outcome_label(p, raw | {"event_id": "other"}, "2026-09-09T00:00:00Z")


def test_vector_brier_draws_pushes_and_zero_probabilities():
    for outcome in ("draw", "push"):
        scores = proper_score({"a": 0.6, "b": 0.3, outcome: 0.1}, outcome)
        assert scores["brier_vector_sum"] == pytest.approx(1.26)
        assert scores["accuracy"] == 0
    assert proper_score({"a": 0.0, "b": 1.0}, "a")["brier_vector_sum"] == 2
    with pytest.raises(ValueError, match="distribution"):
        proper_score({"a": 0.6, "b": 0.6}, "a")


def test_scoring_runner_rebuilds_inputs_and_rejects_conflicting_scores(models, tmp_path):  # noqa: F811
    capture_dir = tmp_path / "capture"
    source = capture_dir / "sources/nba-games.jsonl"
    source.parent.mkdir(parents=True)
    historical = [
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
    source.write_text("\n".join(json.dumps(r) for r in historical))
    games, _ = load_games(source, "NBA", "2025-05-10")
    original = models / "moneyline/model.joblib"
    model = capture_dir / "models/nba-moneyline/model.joblib"
    model.parent.mkdir(parents=True)
    for filename in ("model.joblib", "recipe.json"):
        shutil.copy2(original.with_name(filename), model.with_name(filename))
    snapshot = forecast(
        model,
        fixture("moneyline"),
        "NBA",
        "moneyline",
        "2025-05-10T12:00:00Z",
        games=games,
        source_evidence={**evidence(games), "source_sha256": file_hash(source)},
    )
    (capture_dir / "generation_manifest.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "sport": "NBA",
                        "market": "moneyline",
                        "model_id": snapshot["model_id"],
                        "artifact_sha256": snapshot["model_file_sha256"],
                    }
                ]
            }
        )
    )
    (capture_dir / "predictions.jsonl").write_text(
        json.dumps({"status": "CAPTURED_REPLAY_PASS", "prediction": snapshot}) + "\n"
    )
    outcomes = tmp_path / "data/historical/nba_games_all.jsonl"
    outcomes.parent.mkdir(parents=True)
    final = {**fixture("moneyline"), "status": "completed", "home_score": 102, "away_score": 99}
    outcomes.write_text(json.dumps(final) + "\n")
    report = run(tmp_path, capture_dir, tmp_path / "scored")
    assert report["counts"] == {"SCORED": 1}
    assert report["economics"] is None and report["incumbent_superiority"] is None
    with pytest.raises(FileExistsError):
        run(tmp_path, capture_dir, tmp_path / "scored")
    outcomes.write_text(json.dumps(final) + "\n" + json.dumps(final | {"home_score": 104}) + "\n")
    report = run(tmp_path, capture_dir, tmp_path / "conflicting")
    assert report["counts"] == {"INTEGRITY_FAILURE": 1}
    assert report["coverage"][0]["metrics"] is None
    snapshot["features"]["rating_gap"] += 5
    snapshot["snapshot_hash"] = digest({k: v for k, v in snapshot.items() if k != "snapshot_hash"})
    (capture_dir / "predictions.jsonl").write_text(
        json.dumps({"status": "CAPTURED_REPLAY_PASS", "prediction": snapshot}) + "\n"
    )
    report = run(tmp_path, capture_dir, tmp_path / "tampered")
    assert report["counts"] == {"INTEGRITY_FAILURE": 1}
