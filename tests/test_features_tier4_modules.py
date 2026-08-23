from __future__ import annotations

import json
from pathlib import Path

from model_prediction.features.base import FeatureContext, GameRecord
from model_prediction.features.head_to_head import head_to_head, head_to_head_snapshot
from model_prediction.features.lineup_strength import lineup_strength_snapshot
from model_prediction.features.tennis_surface import load_matches, surface_profile, tennis_surface_snapshot


def test_head_to_head_calculation() -> None:
    games = [
        GameRecord("1", "2026-07-01T00:00:00Z", "MLB", "BOS", "NYY", 4, 6),
        GameRecord("2", "2026-07-02T00:00:00Z", "MLB", "NYY", "BOS", 5, 2),
        GameRecord("3", "2026-07-03T00:00:00Z", "MLB", "BOS", "NYY", 3, 3),
    ]
    res = head_to_head(games, "NYY", "BOS")
    assert res["games"] == 3
    assert res["team_a_wins"] == 2
    assert res["team_b_wins"] == 0
    assert res["draws"] == 1
    assert res["team_a_win_rate"] == round(2 / 3, 6)

    ctx = FeatureContext("MLB", "2026-08-01", tuple(games), Path("/tmp"))
    snap = head_to_head_snapshot(ctx)
    assert "BOS vs NYY" in snap["pairs"]


def test_tennis_surface_profiles(tmp_path: Path) -> None:
    hist_dir = tmp_path / "historical"
    hist_dir.mkdir(parents=True)
    matches_file = hist_dir / "tennis_matches_all.jsonl"

    data = [
        {"match_date": "2026-06-01", "winner": "Alcaraz", "loser": "Sinner", "surface": "Clay"},
        {"match_date": "2026-06-02", "winner": "Alcaraz", "loser": "Djokovic", "surface": "Clay"},
        {"match_date": "2026-07-01", "winner": "Sinner", "loser": "Alcaraz", "surface": "Grass"},
        {"match_date": "2026-07-02", "winner": "Alcaraz", "loser": "Medvedev", "surface": "Grass"},
    ]
    with matches_file.open("w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row) + chr(10))

    loaded = load_matches(tmp_path, "2026-08-01")
    assert len(loaded) == 4

    profile = surface_profile(loaded, "Alcaraz")
    assert profile["clay"]["matches"] == 2
    assert profile["clay"]["win_rate"] == 1.0
    assert profile["grass"]["matches"] == 2
    assert profile["grass"]["win_rate"] == 0.5
    assert profile["recent_win_rate"] == 0.75

    ctx = FeatureContext("TENNIS", "2026-08-01", (), tmp_path)
    snap = tennis_surface_snapshot(ctx)
    assert "players" in snap
    assert "Alcaraz" in snap["players"]
    assert snap["players"]["Alcaraz"]["clay"]["matches"] == 2


def test_lineup_strength_snapshot() -> None:
    games = [
        GameRecord("1", "2026-07-01T00:00:00Z", "MLB", "BOS", "NYY", 4, 6),
        GameRecord("2", "2026-07-02T00:00:00Z", "MLB", "BOS", "NYY", 2, 8),
        GameRecord("3", "2026-07-03T00:00:00Z", "MLB", "NYY", "BOS", 7, 3),
    ]
    ctx = FeatureContext("MLB", "2026-08-01", tuple(games), Path("/tmp"))
    snap = lineup_strength_snapshot(ctx)
    assert snap["league_baseline"] > 0
    assert "NYY" in snap["teams"]
    assert "BOS" in snap["teams"]
    assert snap["teams"]["NYY"]["offensive_rating"] > snap["teams"]["BOS"]["offensive_rating"]
