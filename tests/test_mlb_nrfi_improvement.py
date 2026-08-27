"""Pin the first-inning NRFI model's correctness invariants.

Covers the PIT ledger discipline (no self-leak), the league-constant 2x
correction, market-proxy math, model serialization, and prediction
non-constancy — all on synthetic snapshots, never live data.
"""

from __future__ import annotations

import json

import pytest

from model_prediction.features import yrfi_nrfi
from model_prediction.models.mlb_first_inning import (
    FirstInningGameRow,
    MLBFirstInningModel,
    build_first_inning_ledger,
    compute_first_inning_priors,
    market_proxy_probabilities,
)


def _snapshot(
    game_pk: int,
    start_utc: str,
    home_team: str,
    away_team: str,
    away_starter_id: int,
    home_starter_id: int,
    runs_away: int,
    runs_home: int,
    *,
    venue: str = "Test Park",
) -> dict:
    """Minimal snapshot in the real schema shape (verified against
    data/mlb_statsapi/game_snapshots.jsonl field layout)."""
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
                    "name": "away-sp",
                    "pitching": {
                        "inningsPitched": "5.0",
                        "strikeOuts": 6,
                        "baseOnBalls": 1,
                        "battersFaced": 20,
                        "homeRuns": 1,
                    },
                }
            ],
        },
        "home": {
            "team_name": home_team,
            "pitcher_order": [home_starter_id],
            "batting_order": [],
            "players": [
                {
                    "player_id": home_starter_id,
                    "name": "home-sp",
                    "pitching": {
                        "inningsPitched": "5.0",
                        "strikeOuts": 5,
                        "baseOnBalls": 2,
                        "battersFaced": 21,
                        "homeRuns": 0,
                    },
                }
            ],
        },
    }


def _write_snapshots(tmp_path, snaps: list[dict]):
    path = tmp_path / "snapshots.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for snap in snaps:
            handle.write(json.dumps(snap) + "\n")
    return path


def test_league_first_inning_run_rate_is_per_game_total() -> None:
    # The pre-fix value 0.52 was the per-TEAM half-inning mean used as the
    # per-GAME total — a 2x error that deflated every p_nrfi. Pin the
    # empirical correction so it can't silently regress.
    assert yrfi_nrfi.LEAGUE_FIRST_INNING_RUN_RATE == 1.036


def test_ledger_has_no_self_leak_first_game_is_pure_priors(tmp_path) -> None:
    # The very first game has zero history: every feature must be exactly
    # the league prior, and its own outcome must not appear in its features.
    path = _write_snapshots(
        tmp_path,
        [
            _snapshot(
                1, "2026-04-01T17:05:00Z", "Home A", "Away A",
                away_starter_id=101, home_starter_id=201, runs_away=2, runs_home=1,
            )
        ],
    )
    rows = build_first_inning_ledger(path)
    assert len(rows) == 1
    priors = compute_first_inning_priors(path)
    features = rows[0].features
    assert features["away_starter_opp_1st_runs"] == priors["half_away"]
    assert features["home_starter_opp_1st_runs"] == priors["half_home"]
    assert features["park_1st_runs"] == priors["total"]
    assert features["away_starter_fip"] == priors["fip"]
    assert rows[0].nrfi == 0  # 3 runs scored — realized outcome, not a feature


def test_ledger_is_chronological_and_prior_games_only(tmp_path) -> None:
    # Game 2's starter features must reflect game 1's history; game 1's
    # features must not change when game 2 is appended (strictly-prior).
    path = _write_snapshots(
        tmp_path,
        [
            _snapshot(
                1, "2026-04-01T17:05:00Z", "Home A", "Away A",
                away_starter_id=101, home_starter_id=201, runs_away=0, runs_home=0,
            ),
            _snapshot(
                2, "2026-04-02T17:05:00Z", "Home B", "Away A",
                away_starter_id=101, home_starter_id=202, runs_away=3, runs_home=0,
            ),
        ],
    )
    rows = build_first_inning_ledger(path)
    assert [r.game_pk for r in rows] == [1, 2]
    priors = compute_first_inning_priors(path)
    # Starter 101 allowed 0 first-inning runs in game 1 → game 2's away
    # starter feature shrinks toward zero (below the league prior).
    assert rows[1].features["away_starter_opp_1st_runs"] < priors["half_away"]
    # Game 1's features were emitted before game 1's boxscore entered the
    # accumulators, so its starter feature is exactly the prior.
    assert rows[0].features["away_starter_opp_1st_runs"] == priors["half_away"]
    # Away team A scored 0 in its first game's away half.
    assert rows[1].features["away_team_1st_scored_away"] < priors["half_away"]


def test_market_proxy_is_fair_two_way_with_vig() -> None:
    p_nrfi, p_yrfi = market_proxy_probabilities(0.51, vig=0.04)
    assert round(p_nrfi, 6) == round((0.51 + 0.02) / 1.04, 6)
    assert round(p_yrfi, 6) == round((0.49 + 0.02) / 1.04, 6)
    # The vig/2 shift renormalizes to a fair two-way (sums to exactly 1)
    # and compresses the spread toward 0.5.
    assert p_nrfi + p_yrfi == pytest.approx(1.0)
    assert p_nrfi < 0.51


def test_model_serialization_round_trip() -> None:
    model = MLBFirstInningModel()
    model.coef = [0.1, -0.2]
    model.intercept = 0.04
    model.scaler_mean = [0.5, 0.6]
    model.scaler_scale = [1.0, 1.0]
    restored = MLBFirstInningModel.from_dict(model.to_dict())
    assert restored.coef == model.coef
    assert restored.intercept == model.intercept
    assert restored.scaler_mean == model.scaler_mean
    assert restored.to_dict()["model_version"] == "mlb-first-inning-v1"


def test_predictions_vary_across_games() -> None:
    # Anti-market-tracking sanity: a fitted model over synthetic rows with
    # distinct features must not emit one constant probability.
    rows = [
        FirstInningGameRow(
            game_pk=i,
            game_start_utc=f"2026-04-{i:02d}T17:05:00Z",
            home_team="Home",
            away_team="Away",
            venue_name="Park",
            features={
                "away_starter_opp_1st_runs": 0.3 + i * 0.1,
                "home_starter_opp_1st_runs": 0.5 - i * 0.05,
                "away_team_1st_scored_away": 0.4,
                "home_team_1st_scored_home": 0.6,
                "away_team_1st_allowed_away": 0.5,
                "home_team_1st_allowed_home": 0.5,
                "park_1st_runs": 1.0,
                "away_starter_fip": 4.0,
                "home_starter_fip": 4.5,
                "away_starter_k_pct": 0.2,
                "home_starter_k_pct": 0.25,
                "away_starter_bb_pct": 0.08,
                "home_starter_bb_pct": 0.07,
                "away_top3_composite": 0.13,
                "home_top3_composite": 0.12,
                "away_starter_starts": 1.0,
                "home_starter_starts": 2.0,
            },
            nrfi=1 if i % 2 == 0 else 0,
            runs_1st_total=float(i % 2),
        )
        for i in range(1, 21)
    ]
    model = MLBFirstInningModel()
    model.fit(rows)
    probs = [model.predict_p_nrfi(r) for r in rows]
    assert len({round(p, 6) for p in probs}) > 1
    assert all(0.0 < p < 1.0 for p in probs)
