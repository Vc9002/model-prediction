from datetime import datetime, timezone

from model_prediction.data_sources.espn import (
    parse_pitcher_form,
    parse_pregame_and_closing_markets,
    parse_team_form,
)


DECISION = datetime(2026, 7, 12, 18, tzinfo=timezone.utc)


def test_team_form_rejects_future_and_nonfinal_games() -> None:
    def event(event_id, date, completed, team_score, opponent_score):
        return {
            "id": event_id,
            "date": date,
            "competitions": [
                {
                    "status": {"type": {"completed": completed}},
                    "competitors": [
                        {"team": {"id": "1"}, "score": {"value": team_score}},
                        {"team": {"id": "2"}, "score": {"value": opponent_score}},
                    ],
                }
            ],
        }

    schedule = {
        "events": [
            event("past", "2026-07-11T18:00:00Z", True, 5, 3),
            event("future", "2026-07-13T18:00:00Z", True, 99, 0),
            event("open", "2026-07-12T17:00:00Z", False, 99, 0),
        ]
    }
    form = parse_team_form(schedule, "1", DECISION)
    assert form.runs_scored == (5,)
    assert form.runs_allowed == (3,)


def test_pitcher_parser_excludes_current_and_future_games_and_marks_xfip_missing() -> None:
    gamelog = {
        "names": ["innings", "earnedRuns", "walks", "strikeouts", "battersFaced"],
        "events": {
            "past": {"gameDate": "2026-07-05T18:00:00Z"},
            "current": {"gameDate": "2026-07-12T18:10:00Z"},
        },
        "seasonTypes": [
            {
                "categories": [
                    {
                        "type": "event",
                        "events": [
                            {"eventId": "past", "stats": ["6.2", "2", "1", "7", "25"]},
                            {"eventId": "current", "stats": ["9.0", "9", "9", "0", "50"]},
                        ],
                    }
                ]
            }
        ],
    }
    profile = {
        "athlete": {
            "id": "7",
            "displayName": "Pitcher",
            "displayBatsThrows": "Left/Right",
        }
    }
    pitcher = parse_pitcher_form(gamelog, profile, DECISION)
    assert round(pitcher.season_innings, 6) == round(6 + 2 / 3, 6)
    assert pitcher.season_earned_runs == 2
    assert pitcher.throwing_hand == "Right"
    assert pitcher.xfip is None
    assert pitcher.xfip_status == "unavailable_from_source"


def test_espn_open_and_close_are_kept_separate() -> None:
    summary = {
        "pickcenter": [
            {
                "provider": {"name": "Book"},
                "moneyline": {
                    "away": {"open": {"odds": "+120"}, "close": {"odds": "+105"}},
                    "home": {"open": {"odds": "-140"}, "close": {"odds": "-126"}},
                },
                "pointSpread": {
                    "away": {
                        "open": {"line": "+1.5", "odds": "-165"},
                        "close": {"line": "+1.5", "odds": "-172"},
                    },
                    "home": {
                        "open": {"line": "-1.5", "odds": "+145"},
                        "close": {"line": "-1.5", "odds": "+150"},
                    },
                },
                "total": {
                    "over": {
                        "open": {"line": "o8.5", "odds": "-110"},
                        "close": {"line": "o9", "odds": "-121"},
                    },
                    "under": {
                        "open": {"line": "u8.5", "odds": "-110"},
                        "close": {"line": "u9", "odds": "+101"},
                    },
                },
            }
        ]
    }
    markets = parse_pregame_and_closing_markets(summary)
    assert markets["moneyline"]["away"] == {"decision_odds": 120, "closing_odds": 105}
    assert markets["spread"]["away"]["decision_line"] == 1.5
    assert markets["spread"]["home"]["closing_odds"] == 150
    assert markets["total"]["over"]["decision_line"] == 8.5
    assert markets["total"]["over"]["closing_line"] == 9.0
