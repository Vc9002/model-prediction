"""Unit tests for the WNBA ESPN boxscore loader feeding four-factors logs."""

from __future__ import annotations

import json
from pathlib import Path

from model_prediction.features.wnba_boxscores import (
    build_wnba_four_factors_logs,
    load_wnba_boxscore_files,
    parse_wnba_boxscore_team_stats,
)

LIBERTY = "New York Liberty"
SPARKS = "Los Angeles Sparks"


def _team_entry(
    name: str, home_away: str, fg: str, fg3: str, ft: str, oreb: str, dreb: str, tov: str
) -> dict:
    return {
        "homeAway": home_away,
        "team": {"displayName": name},
        "statistics": [
            {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": fg},
            {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": fg3},
            {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": ft},
            {"name": "offensiveRebounds", "displayValue": oreb},
            {"name": "defensiveRebounds", "displayValue": dreb},
            {"name": "totalTurnovers", "displayValue": tov},
        ],
    }


def _raw_boxscore(payload: dict) -> dict:
    return {"event_id": "g1", "payload": {"boxscore": payload}}


def test_parse_wnba_boxscore_team_stats() -> None:
    raw = _raw_boxscore(
        {
            "teams": [
                _team_entry(LIBERTY, "home", "33-69", "8-21", "27-29", "4", "28", "12"),
                _team_entry(SPARKS, "away", "38-75", "6-22", "13-17", "6", "27", "13"),
            ]
        }
    )
    stats = parse_wnba_boxscore_team_stats(raw)
    assert set(stats) == {LIBERTY, SPARKS}
    liberty = stats[LIBERTY]
    assert liberty["fgm"] == 33.0
    assert liberty["fga"] == 69.0
    assert liberty["fg3m"] == 8.0
    assert liberty["fta"] == 29.0
    assert liberty["tov"] == 12.0
    assert liberty["oreb"] == 4.0
    assert liberty["dreb"] == 28.0


def test_build_wnba_four_factors_logs_merges_scores_and_opponent_rebounds() -> None:
    raw = _raw_boxscore(
        {
            "teams": [
                _team_entry(LIBERTY, "home", "33-69", "8-21", "27-29", "4", "28", "12"),
                _team_entry(SPARKS, "away", "38-75", "6-22", "13-17", "6", "27", "13"),
            ]
        }
    )
    stats = parse_wnba_boxscore_team_stats(raw)
    logs = build_wnba_four_factors_logs(LIBERTY, SPARKS, 89, 84, stats)
    assert logs is not None
    assert logs[LIBERTY]["points"] == 89.0
    assert logs[LIBERTY]["opp_points"] == 84.0
    # Opponent's defensive rebounds become this team's opp_dreb (OREB% denominator)
    assert logs[LIBERTY]["opp_dreb"] == 27.0
    assert logs[SPARKS]["points"] == 84.0
    assert logs[SPARKS]["opp_points"] == 89.0
    assert logs[SPARKS]["opp_dreb"] == 28.0


def test_build_wnba_four_factors_logs_fails_closed_on_partial_boxscore() -> None:
    raw = _raw_boxscore({"teams": [_team_entry(LIBERTY, "home", "33-69", "8-21", "27-29", "4", "28", "12")]})
    stats = parse_wnba_boxscore_team_stats(raw)
    # A capture covering only one team is not usable for either side's pace.
    assert build_wnba_four_factors_logs(LIBERTY, SPARKS, 89, 84, stats) is None


def test_load_wnba_boxscore_files_skips_malformed(tmp_path: Path) -> None:
    good = {
        "event_id": "g1",
        "payload": {
            "boxscore": {
                "teams": [
                    _team_entry(LIBERTY, "home", "33-69", "8-21", "27-29", "4", "28", "12"),
                    _team_entry(SPARKS, "away", "38-75", "6-22", "13-17", "6", "27", "13"),
                ]
            }
        },
    }
    (tmp_path / "g1.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "g2.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "g3.json").write_text(json.dumps({"event_id": "g3", "payload": {}}), encoding="utf-8")

    loaded = load_wnba_boxscore_files(tmp_path)
    assert set(loaded) == {"g1"}
    assert loaded["g1"][LIBERTY]["fgm"] == 33.0


def test_load_wnba_boxscore_files_missing_dir(tmp_path: Path) -> None:
    assert load_wnba_boxscore_files(tmp_path / "nope") == {}
