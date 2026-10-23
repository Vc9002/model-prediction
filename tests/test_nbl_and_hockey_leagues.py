"""NBL ESPN ingestion and the Liiga/AHL/SHL hockey feeds."""

from __future__ import annotations

import json
from datetime import date

from model_prediction.data_sources.hockey_leagues import (
    HockeyLeagueClient,
    fold_name,
    refresh_other_hockey_leagues,
)
from model_prediction.data_sources.nbl import normalize_event, refresh_nbl_history
from model_prediction.nbl_forward import _resolver


def _event(i, home, away, hs, as_, completed=True):
    return {
        "id": str(i),
        "date": "2026-10-01T08:00Z",
        "seasonType": {"abbreviation": "reg"},
        "competitions": [
            {
                "status": {"type": {"completed": completed}},
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": home}, "score": {"value": hs}},
                    {"homeAway": "away", "team": {"displayName": away}, "score": {"value": as_}},
                ],
            }
        ],
    }


def test_nbl_normalize_skips_unplayed_and_reads_value_scores():
    assert normalize_event(_event(1, "A", "B", 90.0, 80.0, completed=False)) is None
    row = normalize_event(_event(2, "Perth Wildcats", "Sydney Kings", 90.0, 85.0))
    assert row["home_score"] == 90 and row["event_id"] == "nbl:2" and row["league"] == "NBL"


def test_nbl_refresh_bootstraps_then_current_season_only(tmp_path):
    class Client:
        def __init__(self):
            self.seasons = []

        def season_games(self, season):
            self.seasons.append(season)
            return [normalize_event(_event(season, "A", "B", 90.0, 80.0))]

    client = Client()
    first = refresh_nbl_history(tmp_path, "2026-10-21", client=client)
    assert client.seasons == [2025, 2026, 2027] and first["added"] == 3
    client.seasons.clear()
    assert refresh_nbl_history(tmp_path, "2026-10-22", client=client)["added"] == 0 and client.seasons == [
        2027
    ]


def test_nbl_resolver_needs_a_unique_match():
    known = {"Perth Wildcats", "Sydney Kings", "Melbourne United", "South East Melbourne Phoenix"}
    assert _resolver("Perth Wildcats", None, known) == "Perth Wildcats"
    assert _resolver("Unknown Club", None, known) is None


def test_fold_name_matches_polymarket_spelling():
    assert fold_name("FrÃ¶lunda HC") == "Froelunda HC"
    assert fold_name("BjÃ¶rklÃ¶ven") == "Bjoerkloeven"
    assert fold_name("Malmö") == "Malmoe"


def test_other_hockey_leagues_merge_only_finished_games_and_survive_one_failure():
    class Client(HockeyLeagueClient):
        def __init__(self):
            pass

        def liiga_games(self, year):
            raise ValueError("boom")

        def ahl_regular_season_ids(self):
            return ["90"]

        def ahl_games(self, season_id):
            return [
                {
                    "event_id": "ahl:1",
                    "start": "2025-10-10T19:00:00Z",
                    "league": "AHL",
                    "home_team": "A",
                    "away_team": "B",
                    "home_score": 1,
                    "away_score": 2,
                    "completed": True,
                }
            ]

        def shl_season_uuids(self):
            return ["x"]

        def shl_games(self, season_uuid):
            return []

    existing: dict = {}
    result = refresh_other_hockey_leagues(existing, date(2026, 10, 21), Client())
    assert result["added"]["ahl"] == 1 and "ahl:1" in existing
    assert any(e["league"] == "liiga" for e in result["errors"])
    assert json.dumps(existing)
