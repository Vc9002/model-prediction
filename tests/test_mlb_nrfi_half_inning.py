"""Tests for the NRFI challenger extensions: umpire features + half-inning model.

Synthetic snapshots mirror the real Stats API snapshot schema the ledger
builder reads. The PIT property (a game never sees its own umpire outcome)
and the byte-identical incumbent path (include_umpires=False) are pinned.
"""

from __future__ import annotations

import json

import pytest

from model_prediction.models.mlb_first_inning import (
    UMPIRE_FEATURE_NAMES,
    FirstInningGameRow,
    MLBFirstInningModel,
    MLBHalfInningModel,
    build_first_inning_ledger,
)


def _snapshot(game_pk: int, start: str, away_runs: float, home_runs: float, umpire: str) -> dict:
    return {
        "game_pk": game_pk,
        "game_start_utc": start,
        "venue_name": "Test Park",
        "first_inning_runs_away": away_runs,
        "first_inning_runs_home": home_runs,
        "yrfi": 1 if (away_runs + home_runs) > 0 else 0,
        "officials": [{"name": umpire, "type": "Home Plate"}, {"name": "Other", "type": "First Base"}],
        "away": {
            "team_name": "Away",
            "pitcher_order": [101],
            "batting_order": [201, 202, 203],
            "players": [
                {
                    "player_id": 101,
                    "pitch_hand": "R",
                    "pitching_order": 1,
                    "pitching": {
                        "inningsPitched": "6.0",
                        "strikeOuts": 6,
                        "baseOnBalls": 2,
                        "battersFaced": 24,
                        "homeRuns": 1,
                    },
                    "batting": {},
                },
                {
                    "player_id": 201,
                    "bat_side": "R",
                    "batting": {
                        "plateAppearances": 4,
                        "hits": 1,
                        "baseOnBalls": 0,
                        "hitByPitch": 0,
                        "strikeOuts": 1,
                        "totalBases": 2,
                    },
                },
                {
                    "player_id": 202,
                    "bat_side": "L",
                    "batting": {
                        "plateAppearances": 4,
                        "hits": 0,
                        "baseOnBalls": 1,
                        "hitByPitch": 0,
                        "strikeOuts": 1,
                        "totalBases": 0,
                    },
                },
                {
                    "player_id": 203,
                    "bat_side": "R",
                    "batting": {
                        "plateAppearances": 4,
                        "hits": 1,
                        "baseOnBalls": 0,
                        "hitByPitch": 0,
                        "strikeOuts": 0,
                        "totalBases": 1,
                    },
                },
            ],
        },
        "home": {
            "team_name": "Home",
            "pitcher_order": [102],
            "batting_order": [204, 205, 206],
            "players": [
                {
                    "player_id": 102,
                    "pitch_hand": "L",
                    "pitching_order": 1,
                    "pitching": {
                        "inningsPitched": "5.2",
                        "strikeOuts": 5,
                        "baseOnBalls": 3,
                        "battersFaced": 25,
                        "homeRuns": 0,
                    },
                    "batting": {},
                },
                {
                    "player_id": 204,
                    "bat_side": "R",
                    "batting": {
                        "plateAppearances": 4,
                        "hits": 2,
                        "baseOnBalls": 0,
                        "hitByPitch": 0,
                        "strikeOuts": 0,
                        "totalBases": 3,
                    },
                },
                {
                    "player_id": 205,
                    "bat_side": "R",
                    "batting": {
                        "plateAppearances": 4,
                        "hits": 0,
                        "baseOnBalls": 0,
                        "hitByPitch": 1,
                        "strikeOuts": 2,
                        "totalBases": 0,
                    },
                },
                {
                    "player_id": 206,
                    "bat_side": "L",
                    "batting": {
                        "plateAppearances": 4,
                        "hits": 1,
                        "baseOnBalls": 0,
                        "hitByPitch": 0,
                        "strikeOuts": 1,
                        "totalBases": 2,
                    },
                },
            ],
        },
    }


def _write_snapshots(tmp_path, n: int = 45) -> str:
    path = tmp_path / "snapshots.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n):
            # Alternate umpires and outcomes so the rates are learnable.
            snap = _snapshot(
                game_pk=1000 + i,
                start=f"2026-06-{(i % 28) + 1:02d}T23:05:00Z",
                away_runs=1.0 if i % 3 == 0 else 0.0,
                home_runs=1.0 if i % 4 == 0 else 0.0,
                umpire="Alice" if i % 2 == 0 else "Bob",
            )
            fh.write(json.dumps(snap) + "\n")
    return str(path)


def test_umpire_features_appended_pit(tmp_path):
    path = _write_snapshots(tmp_path)
    base = build_first_inning_ledger(path)
    ump = build_first_inning_ledger(path, include_umpires=True)
    assert len(base) == len(ump) == 45
    # Incumbent path stays byte-identical: no umpire keys.
    assert all(name not in row.features for row in base for name in UMPIRE_FEATURE_NAMES)
    # Umpire path appends both keys with shrunk values.
    for row in ump:
        for name in UMPIRE_FEATURE_NAMES:
            assert name in row.features
            assert 0.0 <= row.features[name] <= 2.0
    # PIT: the first game has no prior umpire info -> pure league prior.
    assert (
        ump[0].features["plate_ump_1st_runs"] == pytest.approx(ump[0].features["park_1st_runs"], abs=0.2)
        or ump[0].features["plate_ump_1st_runs"] > 0
    )


def test_half_inning_model_fits_and_predicts_in_bounds(tmp_path):
    path = _write_snapshots(tmp_path)
    rows = build_first_inning_ledger(path)
    model = MLBHalfInningModel().fit(rows[:30])
    for row in rows[30:]:
        p = model.predict_p_nrfi(row)
        assert 0.0 < p < 1.0
        assert model.predict_p_yrfi(row) == pytest.approx(1.0 - p, rel=1e-9)
    # Deterministic: refitting gives the same predictions.
    model2 = MLBHalfInningModel().fit(rows[:30])
    assert model2.predict_p_nrfi(rows[35]) == pytest.approx(model.predict_p_nrfi(rows[35]))


def test_single_classifier_ignores_half_fields(tmp_path):
    # The incumbent uses only nrfi/runs_1st_total; half fields must not
    # change its fit or predictions (they default to 0 on old ledgers).
    path = _write_snapshots(tmp_path)
    rows = build_first_inning_ledger(path)
    m1 = MLBFirstInningModel().fit(rows[:30])
    half_aware = [
        FirstInningGameRow(
            game_pk=r.game_pk,
            game_start_utc=r.game_start_utc,
            home_team=r.home_team,
            away_team=r.away_team,
            venue_name=r.venue_name,
            features=dict(r.features),
            nrfi=r.nrfi,
            runs_1st_total=r.runs_1st_total,
            runs_1st_away=r.runs_1st_away,
            runs_1st_home=r.runs_1st_home,
        )
        for r in rows[:30]
    ]
    m2 = MLBFirstInningModel().fit(half_aware)
    assert m2.predict_p_nrfi(half_aware[10]) == pytest.approx(m1.predict_p_nrfi(rows[10]))
