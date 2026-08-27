"""Tests for the Statcast point-in-time game metrics aggregator.

2026-08-26 wiring: the aggregation was a manual-only script (3+ days
behind); it now lives in the package and runs in the daily pipeline after
the MLB snapshot capture steps.
"""

import json
from pathlib import Path

from model_prediction.statcast_aggregates import (
    BATTER_METRICS_FILE,
    PITCHER_METRICS_FILE,
    build_statcast_game_aggregates,
)


def _write_snapshot(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "game_pk": "123",
        "game_start_utc": "2026-08-25T23:10:00+00:00",
        "home": {
            "team_name": "Home Team",
            "team_id": "H",
            "players": [
                {
                    "player_id": "p1",
                    "name": "Pitcher, Ace",
                    "pitching_order": 1,
                    "pitching": {
                        "inningsPitched": "6.0",
                        "battersFaced": 22,
                        "numberOfPitches": 88,
                        "strikes": 58,
                        "strikeOuts": 7,
                        "baseOnBalls": 1,
                        "earnedRuns": 2,
                    },
                },
                {
                    "player_id": "b1",
                    "name": "Batter, Bo",
                    "batting_order": "3",
                    "batting": {
                        "plateAppearances": 4,
                        "atBats": 4,
                        "hits": 1,
                        "doubles": 1,
                        "triples": 0,
                        "homeRuns": 0,
                        "baseOnBalls": 0,
                        "strikeOuts": 1,
                    },
                },
            ],
        },
        "away": {
            "team_name": "Away Team",
            "team_id": "A",
            "players": [
                {
                    "player_id": "p2",
                    "name": "Pitcher, Joe",
                    "pitching_order": 0,  # relief arm
                    "pitching": {"battersFaced": 3, "numberOfPitches": 12},
                }
            ],
        },
    }
    path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")


def test_builds_pitcher_and_batter_parquets_from_snapshots(tmp_path) -> None:
    _write_snapshot(tmp_path / "mlb_statsapi" / "game_snapshots.jsonl")

    pitchers, batters = build_statcast_game_aggregates(tmp_path)

    assert len(pitchers) == 2
    assert len(batters) == 1
    assert (tmp_path / "statcast" / PITCHER_METRICS_FILE).exists()
    assert (tmp_path / "statcast" / BATTER_METRICS_FILE).exists()

    starter = pitchers.filter(pitchers["pitcher_id"] == "p1").to_dicts()[0]
    assert starter["is_starter"] is True
    assert starter["game_date"] == "2026-08-25"
    assert starter["innings_pitched"] == 6.0
    assert starter["k_rate"] == round(7 / 22, 4)

    batter = batters.to_dicts()[0]
    assert batter["bip_count"] == 3  # ab - strikeouts
    assert batter["team"] == "Home Team"


def test_missing_snapshots_file_yields_empty_frames(tmp_path) -> None:
    pitchers, batters = build_statcast_game_aggregates(tmp_path)

    assert pitchers.is_empty()
    assert batters.is_empty()
    # Nothing to write: the parquets must not appear for empty input.
    assert not (tmp_path / "statcast" / PITCHER_METRICS_FILE).exists()
    assert not (tmp_path / "statcast" / BATTER_METRICS_FILE).exists()


def test_corrupt_lines_are_skipped(tmp_path) -> None:
    _write_snapshot(tmp_path / "mlb_statsapi" / "game_snapshots.jsonl")
    path = tmp_path / "mlb_statsapi" / "game_snapshots.jsonl"
    path.write_text("not json\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    pitchers, batters = build_statcast_game_aggregates(tmp_path)

    assert len(pitchers) == 2
    assert len(batters) == 1


def test_explicit_snapshots_path_override(tmp_path) -> None:
    _write_snapshot(tmp_path / "custom_snapshots.jsonl")
    # data_root has no mlb_statsapi tree at all; the override must win.
    pitchers, _batters = build_statcast_game_aggregates(
        tmp_path / "data_root",
        snapshots_path=tmp_path / "custom_snapshots.jsonl",
    )
    assert len(pitchers) == 2
    assert (tmp_path / "data_root" / "statcast" / PITCHER_METRICS_FILE).exists()
