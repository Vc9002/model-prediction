import json
from datetime import datetime, timezone

import pytest

from model_prediction.data_sources.wnba_injuries import (
    _Token,
    _parse_coordinate_table,
    available_report_links,
)
from model_prediction.features.player_availability import (
    matchup_player_availability,
    merge_availability_sources,
)
from model_prediction.features.base import FeatureStore
from model_prediction.learned_forward import build_learned_moneyline_slate
from model_prediction.models.learned_market import build_artifact


def _write_inputs(tmp_path, *, home_status="Out", submitted=True, future=False) -> None:
    snapshot_dir = tmp_path / "availability/wnba/snapshots/2026-07-19"
    snapshot_dir.mkdir(parents=True)
    observed = "2026-07-19T19:45:00+00:00" if not future else "2026-07-19T21:00:00+00:00"
    snapshot = {
        "schema_version": "1",
        "sport": "wnba",
        "observed_at_utc": observed,
        "report_at_utc": "2026-07-19T19:30:00+00:00",
        "teams_listed": ["Away Team", "Home Team"],
        "team_report_status": {
            "Away Team": "submitted",
            "Home Team": "submitted" if submitted else "not_yet_submitted",
        },
        "entries": [
            {
                "game_date": "2026-07-19",
                "team": "Home Team",
                "player_name": "Star, Home",
                "current_status": home_status,
            },
            {
                "game_date": "2026-07-19",
                "team": "Away Team",
                "player_name": "Bench, Away",
                "current_status": "Out",
            },
        ],
    }
    (snapshot_dir / ("future.json" if future else "eligible.json")).write_text(json.dumps(snapshot))
    if future:
        return
    espn_dir = tmp_path / "availability/wnba/espn_event_snapshots/2026-07-19"
    espn_dir.mkdir(parents=True)
    (espn_dir / "target-20260719T200000+0000-test.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "sport": "wnba",
                "source": "espn_event_injuries",
                "event_id": "target",
                "observed_at_utc": "2026-07-19T20:00:00+00:00",
                "entries": [],
            }
        )
    )
    prior_dir = tmp_path / "player_priors/wnba"
    prior_dir.mkdir(parents=True)
    players = []
    for team, side in (("Home Team", "Home"), ("Away Team", "Away")):
        for index in range(10):
            name = f"Player {index} {side}"
            impact = 2.0
            if team == "Home Team" and index == 0:
                name, impact = "Home Star", 8.0
            if team == "Away Team" and index == 9:
                name, impact = "Away Bench", 1.0
            players.append(
                {
                    "team": team,
                    "player_name": name,
                    "projected_minutes": 20.0,
                    "impact_points_per_100": impact,
                    "replacement_impact_points_per_100": 0.0,
                }
            )
    priors = {
        "schema_version": "1",
        "sport": "wnba",
        "observed_at_utc": "2026-07-19T18:00:00+00:00",
        "valid_from": "2026-07-19",
        "valid_through": "2026-07-19",
        "players": players,
    }
    (prior_dir / "priors.json").write_text(json.dumps(priors))


def test_official_report_links_are_host_validated_and_sorted() -> None:
    payload = {
        "links": [
            {
                "href": "https://ak-static.cms.nba.com/referee/wnba_injury/"
                "Injury-Report_2026-07-19_03_30PM.pdf"
            },
            {
                "href": "https://ak-static.cms.nba.com/referee/wnba_injury/"
                "Injury-Report_2026-07-19_12_30PM.pdf"
            },
        ]
    }
    links = available_report_links(payload)
    assert [value[0].hour for value in links] == [16, 19]


def test_coordinate_parser_carries_team_context_across_pages() -> None:
    tokens = [
        _Token(0, 585, 120, "Current"),
        _Token(0, 24, 148, "07/19/2026"),
        _Token(0, 120, 148, "01:00 (ET)"),
        _Token(0, 201, 148, "AWY@HME"),
        _Token(0, 265, 148, "Away Team"),
        _Token(0, 426, 148, "Bench, Away"),
        _Token(0, 586, 148, "Out"),
        _Token(0, 667, 148, "Injury"),
        _Token(1, 291, 64, "Injury"),
        _Token(1, 265, 96, "Home Team"),
        _Token(1, 426, 96, "Star, Home"),
        _Token(1, 586, 96, "Questionable"),
        _Token(1, 667, 96, "Illness"),
    ]
    entries, teams, matchups, statuses = _parse_coordinate_table(tokens)
    assert len(entries) == 2
    assert entries[1].game_date == "2026-07-19"
    assert entries[1].matchup == "AWY@HME"
    assert teams == {"Away Team", "Home Team"}
    assert matchups["Home Team"] == "AWY@HME"
    assert statuses == {"Away Team": "submitted", "Home Team": "submitted"}


def test_star_outage_moves_more_than_reserve_outage(tmp_path) -> None:
    _write_inputs(tmp_path)
    values = matchup_player_availability(
        data_root=tmp_path,
        home_team="Home Team",
        away_team="Away Team",
        game_date="2026-07-19",
        observed_at=datetime(2026, 7, 19, 20, tzinfo=timezone.utc),
        event_start=datetime(2026, 7, 19, 21, tzinfo=timezone.utc),
    )
    assert values["availability_points_gap"] == pytest.approx(-2.8)
    assert values["home_available_minutes_share"] == pytest.approx(0.9)
    assert values["away_available_minutes_share"] == pytest.approx(0.9)


def test_report_not_submitted_fails_closed(tmp_path) -> None:
    _write_inputs(tmp_path, submitted=False)
    with pytest.raises(ValueError, match="REPORT_INCOMPLETE"):
        matchup_player_availability(
            data_root=tmp_path,
            home_team="Home Team",
            away_team="Away Team",
            game_date="2026-07-19",
            observed_at=datetime(2026, 7, 19, 20, tzinfo=timezone.utc),
            event_start=datetime(2026, 7, 19, 21, tzinfo=timezone.utc),
        )


def test_snapshot_observed_after_decision_is_not_backfilled(tmp_path) -> None:
    _write_inputs(tmp_path, future=True)
    with pytest.raises(ValueError, match="UNAVAILABLE"):
        matchup_player_availability(
            data_root=tmp_path,
            home_team="Home Team",
            away_team="Away Team",
            game_date="2026-07-19",
            observed_at=datetime(2026, 7, 19, 20, tzinfo=timezone.utc),
            event_start=datetime(2026, 7, 19, 21, tzinfo=timezone.utc),
        )


def test_espn_explicit_out_fills_official_omission() -> None:
    official = {"entries": []}
    espn = {
        "entries": [
            {
                "team": "Dallas Wings",
                "player_name": "Paige Bueckers",
                "current_status": "Out",
                "status_at_utc": "2026-07-19T19:12:00+00:00",
                "reason": "Undisclosed",
                "return_date": "2026-07-20",
            }
        ]
    }
    merged = merge_availability_sources(official, espn, game_date="2026-07-20")
    assert merged["entries"][0]["player_name"] == "Paige Bueckers"
    assert merged["entries"][0]["current_status"] == "Out"


def test_conflicting_explicit_sources_fail_closed() -> None:
    official = {
        "entries": [
            {
                "game_date": "2026-07-20",
                "team": "Dallas Wings",
                "player_name": "Bueckers, Paige",
                "current_status": "Available",
            }
        ]
    }
    espn = {
        "entries": [
            {
                "team": "Dallas Wings",
                "player_name": "Paige Bueckers",
                "current_status": "Out",
                "return_date": "2026-07-20",
            }
        ]
    }
    with pytest.raises(ValueError, match="SOURCE_CONFLICT"):
        merge_availability_sources(official, espn, game_date="2026-07-20", conflict_policy="fail_closed")


def test_conflicting_sources_can_be_resolved_conservatively_for_research() -> None:
    official = {
        "entries": [
            {
                "game_date": "2026-07-20",
                "team": "Dallas Wings",
                "player_name": "Smith, Alanna",
                "current_status": "Doubtful",
            }
        ]
    }
    espn = {
        "entries": [
            {
                "team": "Dallas Wings",
                "player_name": "Alanna Smith",
                "current_status": "Out",
                "return_date": "2026-07-20",
            }
        ]
    }
    merged = merge_availability_sources(
        official,
        espn,
        game_date="2026-07-20",
        conflict_policy="most_conservative",
    )
    assert merged["entries"][0]["current_status"] == "Out"
    assert merged["source_conflicts"] == [
        {
            "team": "Dallas Wings",
            "player_name": "Smith, Alanna",
            "official_status": "Doubtful",
            "espn_status": "Out",
        }
    ]


def test_learned_forward_artifact_consumes_availability_feature(tmp_path) -> None:
    _write_inputs(tmp_path)
    history_path = tmp_path / "processed/wnba/games.jsonl"
    history_path.parent.mkdir(parents=True)
    rows = [
        {
            "event_id": f"history-{index}",
            "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
            "league": "WNBA",
            "away_team": "Away Team",
            "home_team": "Home Team",
            "away_score": 80,
            "home_score": 81,
        }
        for index in range(60)
    ]
    history_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    artifact = build_artifact(
        sport="wnba",
        model_version="wnba-availability-hook-test",
        market_models={
            "moneyline": {
                "feature_names": ["availability_points_gap"],
                "coefficients": [1.0],
                "intercept": 0.0,
                "confidence_threshold": 0.5,
                "positive_class": "home",
            }
        },
        training={"market_inputs_used": False},
        qualification={"qualified": False},
    )
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(artifact))

    class FakeWNBA:
        def scoreboard(self, league, game_date):
            assert league == "WNBA"
            assert game_date == "2026-07-19"
            return {
                "events": [
                    {
                        "id": "target",
                        "date": "2026-07-19T21:00:00Z",
                        "competitions": [
                            {
                                "competitors": [
                                    {
                                        "homeAway": "away",
                                        "team": {"displayName": "Away Team"},
                                    },
                                    {
                                        "homeAway": "home",
                                        "team": {"displayName": "Home Team"},
                                    },
                                ]
                            }
                        ],
                    }
                ]
            }

    candidates, skipped, _ = build_learned_moneyline_slate(
        sport="wnba",
        game_date="2026-07-19",
        store=FeatureStore(tmp_path),
        client=FakeWNBA(),
        artifact_path=artifact_path,
        observed_at=datetime(2026, 7, 19, 20, tzinfo=timezone.utc),
    )
    assert skipped == []
    assert candidates[0].feature_basis["availability_points_gap"] == pytest.approx(-2.8)
    assert candidates[0].selection == "away"
    assert candidates[0].home_probability < 0.1
