from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from model_prediction.features.base import GameRecord
from model_prediction.features.wnba_boxscores import build_wnba_four_factors_logs
from model_prediction.features.wnba_pace_four_factors import compute_team_four_factors
from model_prediction.total_score import (
    WNBA_FEATURE_NAMES,
    TotalScoreArtifact,
    build_total_score_rows,
)


def _games(count: int, final_total_delta: int = 0) -> list[GameRecord]:
    teams = ("A", "B", "C", "D")
    start = datetime(2024, 1, 1, tzinfo=UTC)
    games = []
    for index in range(count):
        away = teams[index % 4]
        home = teams[(index + 1 + index // 4) % 4]
        if home == away:
            home = teams[(teams.index(home) + 1) % 4]
        away_score = 80 + teams.index(away) * 5 + index % 7
        home_score = 82 + teams.index(home) * 4 + (index * 3) % 9
        if index == count - 1:
            home_score += final_total_delta
        games.append(
            GameRecord(
                event_id=str(index),
                event_start_utc=(start + timedelta(days=index)).isoformat(),
                league="TEST",
                away_team=away,
                home_team=home,
                away_score=away_score,
                home_score=home_score,
            )
        )
    return games


def test_target_game_score_cannot_leak_into_its_features() -> None:
    ordinary = build_total_score_rows(_games(100))
    extreme_final = build_total_score_rows(_games(100, final_total_delta=100))

    assert ordinary[-1].event_id == extreme_final[-1].event_id
    assert ordinary[-1].features == extreme_final[-1].features
    assert ordinary[-1].actual_total + 100 == extreme_final[-1].actual_total


# ── WNBA signal wiring (2026-08-26) ─────────────────────────────────────────

NY = "New York Liberty"
LA = "Los Angeles Sparks"
CHI = "Chicago Sky"


def _wnba_game(event_id: str, day: int, away: str, home: str, away_score: int, home_score: int) -> GameRecord:
    return GameRecord(
        event_id=event_id,
        event_start_utc=f"2026-01-{day:02d}T23:00:00Z",
        league="WNBA",
        away_team=away,
        home_team=home,
        away_score=away_score,
        home_score=home_score,
    )


def _boxscore(fga: int) -> dict:
    """Synthetic parsed boxscore stats (the shape parse_wnba_boxscore_team_stats returns)."""
    return {
        "New York Liberty": {
            "fgm": 35.0,
            "fga": float(fga),
            "fg3m": 8.0,
            "fta": 15.0,
            "tov": 10.0,
            "oreb": 8.0,
            "dreb": 28.0,
        },
        "Los Angeles Sparks": {
            "fgm": 32.0,
            "fga": float(fga),
            "fg3m": 6.0,
            "fta": 14.0,
            "tov": 12.0,
            "oreb": 6.0,
            "dreb": 27.0,
        },
        "Chicago Sky": {
            "fgm": 33.0,
            "fga": float(fga),
            "fg3m": 7.0,
            "fta": 13.0,
            "tov": 11.0,
            "oreb": 7.0,
            "dreb": 26.0,
        },
    }


def _wnba_games() -> list[GameRecord]:
    return [
        _wnba_game("g1", 1, CHI, NY, 80, 85),  # NY + CHI
        _wnba_game("g2", 3, LA, CHI, 85, 80),  # LA + CHI
        _wnba_game("g3", 5, NY, LA, 90, 95),  # NY + LA
        _wnba_game("g4", 7, CHI, NY, 85, 90),  # NY + CHI
    ]


def _wnba_boxscore_map(fga: int) -> dict[str, dict[str, dict[str, float]]]:
    return _wnba_boxscore_map_by_event({"g1": fga, "g2": fga, "g3": fga, "g4": fga})


def _wnba_boxscore_map_by_event(fga_by_event: dict[str, int]) -> dict[str, dict[str, dict[str, float]]]:
    result = {}
    for event_id, fga in fga_by_event.items():
        stats = _boxscore(fga)
        result[event_id] = {NY: stats[NY], LA: stats[LA], CHI: stats[CHI]}
    return result


def test_wnba_signals_are_real_and_point_in_time() -> None:
    games = _wnba_games()
    ordinary = build_total_score_rows(
        games, minimum_team_games=1, minimum_league_games=2, wnba_boxscores=_wnba_boxscore_map(fga=70)
    )
    # Extreme pace for the target game g3 must not leak into its own row:
    # only g3's own boxscore changes, every prior game keeps fga=70.
    extreme = build_total_score_rows(
        games,
        minimum_team_games=1,
        minimum_league_games=2,
        wnba_boxscores=_wnba_boxscore_map_by_event({"g1": 70, "g2": 70, "g3": 250, "g4": 70}),
    )
    assert len(ordinary) == 2  # g3 and g4 qualify
    row_g3 = ordinary[0]
    row_g4 = ordinary[1]
    assert len(row_g3.features) == len(WNBA_FEATURE_NAMES) == 12
    assert row_g3.features == extreme[0].features  # strictly-prior: no leak
    # g4's row legitimately sees g3's boxscore (strictly prior to g4): the
    # extreme g3 pace must flow through and move g4's pace signal.
    assert (
        extreme[1].features[WNBA_FEATURE_NAMES.index("wnba_pace_40m")]
        != row_g4.features[WNBA_FEATURE_NAMES.index("wnba_pace_40m")]
    )

    # Rest days strictly from the schedule of prior games: g3 = (4 + 2) / 2.
    assert row_g3.features[WNBA_FEATURE_NAMES.index("wnba_rest_days_avg")] == 3.0
    # NY (-5) vs LA (-8): 3 hours timezone displacement.
    assert row_g3.features[WNBA_FEATURE_NAMES.index("wnba_travel_tz_hours")] == 3.0
    # g4 is CHI (-6) @ NY (-5): one hour displacement.
    assert row_g4.features[WNBA_FEATURE_NAMES.index("wnba_travel_tz_hours")] == 1.0
    assert row_g4.features[WNBA_FEATURE_NAMES.index("wnba_rest_days_avg")] == 3.0

    # Pace equals the four-factors module over strictly-prior boxscore logs.
    stats = _boxscore(70)
    ny_log_g1 = build_wnba_four_factors_logs(NY, CHI, 85.0, 80.0, {NY: stats[NY], CHI: stats[CHI]})
    la_log_g2 = build_wnba_four_factors_logs(LA, CHI, 80.0, 85.0, {LA: stats[LA], CHI: stats[CHI]})
    assert ny_log_g1 is not None and la_log_g2 is not None
    expected_pace_g3 = (
        compute_team_four_factors(NY, [ny_log_g1[NY]]).pace_40m
        + compute_team_four_factors(LA, [la_log_g2[LA]]).pace_40m
    ) / 2.0
    assert row_g3.features[WNBA_FEATURE_NAMES.index("wnba_pace_40m")] == pytest.approx(
        round(expected_pace_g3, 4), abs=1e-3
    )


def test_wnba_legacy_signals_reproduce_old_constants() -> None:
    rows = build_total_score_rows(
        _wnba_games(),
        minimum_team_games=1,
        minimum_league_games=2,
        wnba_legacy_signals=True,
    )
    assert len(rows) == 2
    assert len(rows[0].features) == 11
    # park_factor, weather_factor, bullpen_rest_days, travel_distance constants
    assert rows[0].features[5:9] == (1.0, 1.0, 3.0, 0.0)


def test_non_wnba_rows_keep_11_columns_and_constants() -> None:
    rows = build_total_score_rows(_games(100))
    assert all(len(r.features) == 11 for r in rows)
    assert rows[-1].features[5:9] == (1.0, 1.0, 3.0, 0.0)
    # The WNBA-only flag must not touch other leagues.
    flagged = build_total_score_rows(_games(100), wnba_legacy_signals=True, wnba_boxscores={})
    assert [r.features for r in flagged] == [r.features for r in rows]


def test_total_score_artifact_hash_and_prediction() -> None:
    payload = {
        "artifact_hash": "",
        "feature_names": ["x"],
        "coefficients": [2.0],
        "intercept": 1.0,
    }
    from model_prediction.total_score import _artifact_hash

    payload["artifact_hash"] = _artifact_hash(payload)
    artifact = TotalScoreArtifact(payload)

    assert artifact.predict({"x": 3.0}) == 7.0
    with pytest.raises(ValueError, match="missing total-score features"):
        artifact.predict({})
    payload["coefficients"] = [3.0]
    with pytest.raises(ValueError, match="hash mismatch"):
        TotalScoreArtifact(payload)


def test_mlb_pitching_runs_allowed():
    from model_prediction.total_score import mlb_pitching_runs_allowed

    # Normal starter on full rest (5.5 IP, 3.60 ERA) + Bullpen (3.5 IP, 4.50 ERA)
    # Expected: (3.60 / 9 * 5.5) + (4.50 / 9 * 3.5) = 2.20 + 1.75 = 3.95
    res = mlb_pitching_runs_allowed(
        starter_era=3.60, starter_expected_ip=5.5, bullpen_era=4.50, rest_days=5.0
    )
    assert res["rest_penalty_applied"] is False
    assert pytest.approx(res["expected_runs_allowed"], abs=0.01) == 3.95
    assert pytest.approx(res["starter_runs"], abs=0.01) == 2.20
    assert pytest.approx(res["bullpen_runs"], abs=0.01) == 1.75

    # Short rest (< 4 days) adds +0.50 ERA penalty
    res_short = mlb_pitching_runs_allowed(
        starter_era=3.60, starter_expected_ip=5.5, bullpen_era=4.50, rest_days=3.0
    )
    assert res_short["rest_penalty_applied"] is True
    assert res_short["effective_starter_era"] == 4.10
    assert res_short["expected_runs_allowed"] > res["expected_runs_allowed"]


def test_stadium_wind_orientation_multiplier():
    from model_prediction.total_score import stadium_wind_orientation_multiplier

    # Dome is completely neutral (1.0)
    assert stadium_wind_orientation_multiplier(wind_speed_mph=20, wind_direction_deg=0, is_dome=True) == 1.0

    # Wind blowing directly out to center (park=0 deg, wind=0 deg -> cos(0)=1)
    mult_out = stadium_wind_orientation_multiplier(
        wind_speed_mph=15.0, wind_direction_deg=0.0, park_orientation_deg=0.0, temp_f=72.0
    )
    assert mult_out > 1.05  # positive boost

    # Wind blowing directly in from center (park=0 deg, wind=180 deg -> cos(pi)=-1)
    mult_in = stadium_wind_orientation_multiplier(
        wind_speed_mph=15.0, wind_direction_deg=180.0, park_orientation_deg=0.0, temp_f=72.0
    )
    assert mult_in < 0.95  # negative suppression

    # High temperature boosts run expectancy
    mult_hot = stadium_wind_orientation_multiplier(wind_speed_mph=0.0, wind_direction_deg=0.0, temp_f=95.0)
    assert mult_hot > 1.0


def test_mlb_totals_v2_projected_runs():
    from model_prediction.total_score import mlb_pitching_runs_allowed, mlb_totals_v2_projected_runs

    home_pit = mlb_pitching_runs_allowed(starter_era=3.50, starter_expected_ip=6.0, bullpen_era=4.00)
    away_pit = mlb_pitching_runs_allowed(starter_era=4.50, starter_expected_ip=5.0, bullpen_era=4.50)

    proj = mlb_totals_v2_projected_runs(
        home_pitching=home_pit,
        away_pitching=away_pit,
        home_lineup_ops_ratio=1.05,
        away_lineup_ops_ratio=0.95,
        park_factor=1.04,
        wind_weather_multiplier=1.02,
    )
    assert proj["total_projected_runs"] > 6.0
    assert proj["home_projected_runs"] > proj["away_projected_runs"]
    assert pytest.approx(proj["total_projected_runs"], abs=0.01) == (
        proj["home_projected_runs"] + proj["away_projected_runs"]
    )
