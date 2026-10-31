from model_prediction.features.base import GameRecord
from scripts.nba_v5_pit_walkforward import (
    paired_date_bootstrap,
    reproduction_gate,
    split_complete_dates,
)


def _game(day: int, suffix: str) -> GameRecord:
    return GameRecord(
        event_id=f"{day}-{suffix}",
        event_start_utc=f"2026-01-{day:02d}T20:00:00Z",
        league="NBA",
        away_team=f"Away {suffix}",
        home_team=f"Home {suffix}",
        away_score=100,
        home_score=105,
        season_type="regular-season",
    )


def test_split_complete_dates_never_splits_one_date_across_cohorts():
    games = [_game(day, suffix) for day in range(1, 11) for suffix in ("a", "b")]
    train, validation, holdout, metadata = split_complete_dates(games)
    cohorts = [train, validation, holdout]
    date_sets = [{game.event_start_utc[:10] for game in cohort} for cohort in cohorts]
    assert date_sets == [
        {f"2026-01-{day:02d}" for day in range(1, 7)},
        {"2026-01-07", "2026-01-08"},
        {"2026-01-09", "2026-01-10"},
    ]
    assert metadata["train"]["games"] == 12
    assert not (date_sets[0] & date_sets[1] or date_sets[1] & date_sets[2])


def test_reproduction_gate_fails_when_control_cohort_or_metrics_do_not_match():
    verdict = reproduction_gate(
        observed={"n_games": 100, "mae": 10.0, "rmse": 12.0},
        expected={"sample_size": 1338, "mae": 11.22, "rmse": 13.85},
    )
    assert verdict["status"] == "FAIL_REPRODUCTION"
    assert verdict["checks"] == {
        "sample_size": False,
        "mae": False,
        "rmse": False,
    }


def test_paired_date_bootstrap_uses_date_clusters_and_detects_clear_gain():
    rows = [
        {"date": "2026-01-01", "control_abs_error": 4.0, "candidate_abs_error": 2.0},
        {"date": "2026-01-01", "control_abs_error": 3.0, "candidate_abs_error": 1.0},
        {"date": "2026-01-02", "control_abs_error": 5.0, "candidate_abs_error": 3.0},
    ]
    result = paired_date_bootstrap(rows, iterations=200, seed=7)
    assert result["mean_gain"] == 2.0
    assert result["ci_95"] == [2.0, 2.0]
    assert result["p_gain_positive"] == 1.0
