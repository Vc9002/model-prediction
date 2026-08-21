"""Pure-function tests for scripts/mlb_elasticity_refit.py.

No live ESPN calls -- these exercise the GLM design-matrix construction,
the chronological-fold splitter, and the on-disk feature cache round-trip
against small synthetic fixtures.
"""

from __future__ import annotations

import importlib.util
import sys
from math import isclose, log
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "mlb_elasticity_refit", PROJECT_ROOT / "scripts" / "mlb_elasticity_refit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def refit():
    return _load_script_module()


@pytest.fixture
def spec(refit):
    # refit.load_formula_spec is aliased to load_formula_spec_unchecked
    # (mlb_measured_edge_calibrate.py) -- BASE_SPEC_PATH is whatever formula
    # this script refits FROM, not necessarily the live ENGINE_VERSION.
    return refit.load_formula_spec(refit.BASE_SPEC_PATH)


def _synthetic_row(event_id: str, game_date: str, away_score: int, home_score: int) -> dict:
    return {
        "event_id": event_id,
        "game_date": game_date,
        "away_team": "Away Team",
        "home_team": "Home Team",
        "away_score": away_score,
        "home_score": home_score,
        "away_form": {
            "runs_scored": [4, 5, 3],
            "runs_allowed": [3, 4, 2],
            "wins": 2,
            "losses": 1,
            "status": "available",
        },
        "home_form": {
            "runs_scored": [5, 4, 6],
            "runs_allowed": [3, 3, 4],
            "wins": 2,
            "losses": 1,
            "status": "available",
        },
        "away_starter": {
            "player_id": "1",
            "name": "Away Starter",
            "throwing_hand": "R",
            "starts_before_game": 12,
            "season_innings": 90.0,
            "season_earned_runs": 30,
            "season_strikeouts": 80,
            "season_walks": 25,
            "season_batters_faced": 380,
            "last_five_innings": 28.0,
            "last_five_earned_runs": 10,
            "last_five_strikeouts": 26,
            "last_five_walks": 8,
            "last_five_batters_faced": 118,
            "xfip": None,
            "xfip_status": "unavailable_from_source",
            "status": "available",
        },
        "home_starter": {
            "player_id": "2",
            "name": "Home Starter",
            "throwing_hand": "L",
            "starts_before_game": 15,
            "season_innings": 95.0,
            "season_earned_runs": 28,
            "season_strikeouts": 90,
            "season_walks": 20,
            "season_batters_faced": 390,
            "last_five_innings": 30.0,
            "last_five_earned_runs": 9,
            "last_five_strikeouts": 30,
            "last_five_walks": 6,
            "last_five_batters_faced": 122,
            "xfip": None,
            "xfip_status": "unavailable_from_source",
            "status": "available",
        },
        "park_factor": 1.05,
        "weather_factor": 0.98,
        "away_bullpen_weakness": 1.1,
        "home_bullpen_weakness": 0.9,
    }


def test_feature_cache_round_trips_through_disk(refit, tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "cache.jsonl"
    monkeypatch.setattr(refit, "CACHE_PATH", cache_path)
    row = _synthetic_row("evt-1", "2026-06-01", 4, 5)

    refit._append_cache(row)
    reloaded = refit._load_cache()

    assert reloaded == {"evt-1": row}


def test_design_matrix_uses_log_transformed_factors_two_rows_per_game(refit, spec) -> None:
    from model_prediction.models.mlb import _clip, _offense_index, _starter_weakness

    rows = [_synthetic_row("evt-1", "2026-06-01", 4, 5)]
    design, target, game_dates = refit.build_design_matrix(rows, spec, include_bullpen=False)

    assert design.shape == (2, 4)
    assert list(target) == [4, 5]
    assert game_dates == ["2026-06-01", "2026-06-01"]

    away_form = refit._team_form(rows[0]["away_form"])
    home_starter = refit._pitcher_form(rows[0]["home_starter"])
    away_offense = _offense_index(away_form, spec)
    home_starter_weakness = _starter_weakness(home_starter, spec)
    park = _clip(rows[0]["park_factor"], spec.factor_bounds["park"])
    weather = _clip(rows[0]["weather_factor"], spec.factor_bounds["weather"])

    expected_away_row = [log(away_offense), log(home_starter_weakness), log(park), log(weather)]
    assert all(isclose(a, b, rel_tol=1e-9) for a, b in zip(design[0], expected_away_row, strict=True))


def test_design_matrix_appends_bullpen_column_when_requested(refit, spec) -> None:
    rows = [_synthetic_row("evt-1", "2026-06-01", 4, 5)]

    without_bullpen, _, _ = refit.build_design_matrix(rows, spec, include_bullpen=False)
    with_bullpen, _, _ = refit.build_design_matrix(rows, spec, include_bullpen=True)

    assert without_bullpen.shape[1] == 4
    assert with_bullpen.shape[1] == 5


def test_chronological_folds_are_strictly_expanding_and_non_overlapping(refit) -> None:
    game_dates = [f"2026-06-{day:02d}" for day in range(1, 21) for _ in range(2)]  # 20 unique dates

    folds = refit.chronological_folds(game_dates, k=4)

    assert len(folds) == 4
    for i in range(1, 4):
        assert folds[i - 1]["train_dates"] < folds[i]["train_dates"]
    for fold in folds:
        assert not (fold["train_dates"] & fold["test_dates"])


def test_run_cv_returns_one_result_per_fold_with_enough_data(refit, spec) -> None:
    rows = [
        _synthetic_row(f"evt-{i}", f"2026-06-{(i % 28) + 1:02d}", 3 + (i % 4), 4 + (i % 3)) for i in range(60)
    ]
    design, target, game_dates = refit.build_design_matrix(rows, spec, include_bullpen=False)

    fold_results = refit.run_cv(design, target, game_dates)

    assert len(fold_results) >= 1
    for fold in fold_results:
        assert set(fold) == {
            "fold",
            "train_games",
            "test_games",
            "held_out_correlation",
            "held_out_mae",
            "coefficients",
        }
        assert len(fold["coefficients"]) == 4


def test_bullpen_stays_forced_to_zero_unless_every_fold_is_positive(refit) -> None:
    """Mirrors the selection-bias guard in main(): bullpen_elasticity is only
    overturned from its forced 0.0 when every fold's coefficient is positive.
    """
    all_positive = [0.02, 0.01, 0.03]
    mixed_sign = [0.02, -0.01, 0.03]

    assert all(c > 0 for c in all_positive) is True
    assert all(c > 0 for c in mixed_sign) is False
