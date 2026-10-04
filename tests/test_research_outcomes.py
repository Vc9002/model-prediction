import pytest

from model_prediction.research_outcomes import completed_espn_events
from model_prediction.research_scoring import outcome_label


def board():
    return {
        "events": [
            {
                "id": "e",
                "date": "2026-09-09T12:00:00Z",
                "competitions": [
                    {
                        "status": {"type": {"completed": True, "state": "post"}},
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {"id": "H", "displayName": "Home"},
                                "score": "5",
                                "linescores": [{"period": 1, "value": 0}],
                            },
                            {
                                "homeAway": "away",
                                "team": {"id": "A", "displayName": "Away"},
                                "score": "3",
                                "linescores": [{"period": 1, "value": 0}],
                            },
                        ],
                    }
                ],
            }
        ]
    }


def test_explicit_zero_first_inning_is_independent_of_full_game_scores():
    rows, errors = completed_espn_events(board(), "MLB", "2026-09-10T00:00:00Z")
    assert not errors
    row = rows[0]
    assert row["home_score"] == 5 and row["first_inning_runs_home"] == 0
    p = {
        "sport": "MLB",
        "market": "nrfi",
        "observed_at_utc": "2026-09-09T11:00:00Z",
        "raw_fixture": {
            "event_id": "e",
            "event_start_utc": "2026-09-09T12:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "home_team_id": "H",
            "away_team_id": "A",
        },
    }
    assert outcome_label(p, row, "2026-09-10T01:00:00Z") == "nrfi"
    with pytest.raises(ValueError, match="identity"):
        outcome_label(p, row | {"event_id": "other"}, "2026-09-10T01:00:00Z")


@pytest.mark.parametrize("change", ["unfinished", "missing_score", "negative_score", "duplicate_side"])
def test_incomplete_or_invalid_results_never_become_zero_scores(change):
    raw = board()
    competition = raw["events"][0]["competitions"][0]
    if change == "unfinished":
        competition["status"]["type"]["completed"] = False
    elif change == "missing_score":
        del competition["competitors"][0]["score"]
    elif change == "negative_score":
        competition["competitors"][0]["score"] = -1
    else:
        competition["competitors"][1]["homeAway"] = "home"
    rows, errors = completed_espn_events(raw, "MLB", "2026-09-10T00:00:00Z")
    assert not rows and len(errors) == 1


def test_missing_first_inning_is_unavailable_not_a_scoreless_inning():
    raw = board()
    raw["events"][0]["competitions"][0]["competitors"][0]["linescores"] = [{"period": 2, "value": 0}]
    [row], errors = completed_espn_events(raw, "MLB", "2026-09-10T00:00:00Z")
    assert not errors and row["first_inning_runs_home"] is None
    p = {
        "sport": "MLB",
        "market": "nrfi",
        "observed_at_utc": "2026-09-09T11:00:00Z",
        "raw_fixture": {
            "event_id": "e",
            "event_start_utc": "2026-09-09T12:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "home_team_id": "H",
            "away_team_id": "A",
        },
    }
    with pytest.raises(ValueError, match="first_inning"):
        outcome_label(p, row, "2026-09-10T01:00:00Z")
