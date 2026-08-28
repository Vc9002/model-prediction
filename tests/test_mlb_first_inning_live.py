"""Train/serve parity: ``live_first_inning_features`` must exactly reproduce
``build_first_inning_ledger``'s row for the same game, computed from the same
history but keyed by starter *name* (live) instead of player_id (batch) --
the two entity representations of the same real player must agree.
"""

from __future__ import annotations

import json

import pytest

from model_prediction.domain import parse_utc
from model_prediction.models.mlb_first_inning import (
    FEATURE_NAMES,
    build_first_inning_ledger,
    compute_first_inning_priors,
)
from model_prediction.models.mlb_first_inning_live import live_first_inning_features


def _batter(player_id: int, pa: float, hits: float = 1.0) -> dict:
    return {
        "player_id": player_id,
        "name": f"batter-{player_id}",
        "bat_side": "R",
        "batting": {
            "plateAppearances": pa,
            "hits": hits,
            "baseOnBalls": 1.0,
            "hitByPitch": 0.0,
            "strikeOuts": 2.0,
            "totalBases": hits,
        },
    }


def _snapshot(
    game_pk: int,
    start_utc: str,
    home_team: str,
    away_team: str,
    away_starter_id: int,
    away_starter_name: str,
    home_starter_id: int,
    home_starter_name: str,
    runs_away: int,
    runs_home: int,
    *,
    venue: str = "Test Park",
) -> dict:
    return {
        "game_pk": game_pk,
        "game_start_utc": start_utc,
        "venue_name": venue,
        "first_inning_runs_away": runs_away,
        "first_inning_runs_home": runs_home,
        "away": {
            "team_name": away_team,
            "pitcher_order": [away_starter_id],
            "batting_order": [],
            "players": [
                {
                    "player_id": away_starter_id,
                    "name": away_starter_name,
                    "pitch_hand": "R",
                    "pitching": {
                        "inningsPitched": "5.1",
                        "strikeOuts": 6,
                        "baseOnBalls": 1,
                        "battersFaced": 20,
                        "homeRuns": 1,
                    },
                },
                _batter(9001, 4.0),
                _batter(9002, 4.0),
            ],
        },
        "home": {
            "team_name": home_team,
            "pitcher_order": [home_starter_id],
            "batting_order": [],
            "players": [
                {
                    "player_id": home_starter_id,
                    "name": home_starter_name,
                    "pitch_hand": "R",
                    "pitching": {
                        "inningsPitched": "6.0",
                        "strikeOuts": 5,
                        "baseOnBalls": 2,
                        "battersFaced": 21,
                        "homeRuns": 0,
                    },
                },
                _batter(9003, 4.0),
                _batter(9004, 4.0),
            ],
        },
    }


def _write_snapshots(tmp_path, snaps: list[dict]):
    path = tmp_path / "snapshots.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for snap in snaps:
            handle.write(json.dumps(snap) + "\n")
    return path


def test_live_features_match_batch_ledger_for_same_game(tmp_path) -> None:
    snaps = [
        _snapshot(
            1,
            "2026-04-01T17:05:00Z",
            "Home A",
            "Away A",
            away_starter_id=101,
            away_starter_name="Away Starter One",
            home_starter_id=201,
            home_starter_name="Home Starter One",
            runs_away=2,
            runs_home=1,
        ),
        _snapshot(
            2,
            "2026-04-08T17:05:00Z",
            "Home A",
            "Away B",
            away_starter_id=102,
            away_starter_name="Away Starter Two",
            home_starter_id=201,
            home_starter_name="Home Starter One",
            runs_away=0,
            runs_home=0,
        ),
        _snapshot(
            3,
            "2026-04-15T17:05:00Z",
            "Home A",
            "Away A",
            away_starter_id=101,
            away_starter_name="Away Starter One",
            home_starter_id=201,
            home_starter_name="Home Starter One",
            runs_away=1,
            runs_home=1,
        ),
    ]
    path = _write_snapshots(tmp_path, snaps)

    decision = parse_utc(snaps[2]["game_start_utc"])
    priors = compute_first_inning_priors(path, end_utc=decision)
    rows = build_first_inning_ledger(path, priors=priors)
    assert len(rows) == 3
    target = rows[2]  # game 3 — has real prior history from games 1 and 2

    live_features = live_first_inning_features(
        home_team="Home A",
        away_team="Away A",
        venue_name="Test Park",
        home_starter_name="Home Starter One",
        away_starter_name="Away Starter One",
        decision=decision,
        snapshot_path=path,
        priors=priors,
    )

    assert set(live_features) == set(FEATURE_NAMES)
    for name in FEATURE_NAMES:
        # 1e-4 tolerance, not exact equality: composite/weighted-average
        # features can land a hair either side of the round(..., 5) boundary
        # depending on floating-point summation order -- verified against a
        # real historical game to be a <=1e-5 artifact, several orders of
        # magnitude below any frozen coefficient's sensitivity (e.g.
        # away_top3_composite's coefficient in mlb-nrfi-v1.json is 2e-5).
        assert live_features[name] == pytest.approx(target.features[name], abs=1e-4), (
            f"{name}: live={live_features[name]!r} batch={target.features[name]!r}"
        )


def test_live_features_cold_start_falls_back_to_priors(tmp_path) -> None:
    path = _write_snapshots(tmp_path, [])
    decision = parse_utc("2026-04-01T17:05:00Z")
    priors = compute_first_inning_priors(path, end_utc=decision)

    live_features = live_first_inning_features(
        home_team="Home A",
        away_team="Away A",
        venue_name="Test Park",
        home_starter_name="Nobody Yet",
        away_starter_name="Nobody Else",
        decision=decision,
        snapshot_path=path,
        priors=priors,
    )

    assert live_features["away_starter_opp_1st_runs"] == priors["half_away"]
    assert live_features["home_starter_opp_1st_runs"] == priors["half_home"]
    assert live_features["park_1st_runs"] == priors["total"]
    assert live_features["away_starter_fip"] == priors["fip"]
