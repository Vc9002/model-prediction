"""NBA v10 research backfill parsers against recorded real payloads (quick-261120-nbf)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from model_prediction.data_sources.nba_backfill_box import parse_player_rows, parse_team_rows, trim_payload
from model_prediction.data_sources.nba_backfill_common import american_to_implied, resolve_out_root
from model_prediction.data_sources.nba_backfill_odds import select_lines, trim_item
from model_prediction.data_sources.nba_injury_report import parse_report, row_is_consistent
from model_prediction.features.nba_player_logs import parse_nba_player_boxscore

FIX = Path(__file__).parent / "fixtures" / "nba_v10"


def test_player_rows_are_a_superset_of_the_serving_parser() -> None:
    payload = json.loads((FIX / "espn_summary_400900029_trimmed.json").read_text(encoding="utf-8"))
    rows = parse_player_rows(payload, "400900029")
    legacy = parse_nba_player_boxscore(payload)
    assert sum(len(v) for v in legacy.values()) == len(rows) == 26
    by_id = {r["player_id"]: r for r in rows}
    for team, team_rows in legacy.items():
        for old in team_rows:
            new = by_id[old["player_id"]]
            assert new["team"] == team
            assert {k: new[k] for k in old} == old
    wiggins = by_id["3059319"]
    assert (wiggins["fgm"], wiggins["fga"], wiggins["fg3m"], wiggins["fg3a"]) == (8, 15, 1, 3)
    assert (wiggins["ftm"], wiggins["oreb"], wiggins["plus_minus"], wiggins["home_away"]) == (2, 1, 8, "away")


def test_team_rows_split_made_attempted_and_carry_score() -> None:
    payload = json.loads((FIX / "espn_summary_400900029_trimmed.json").read_text(encoding="utf-8"))
    teams = {r["team_abbreviation"]: r for r in parse_team_rows(payload, "400900029")}
    assert set(teams) == {"MIN", "DAL"}
    assert teams["MIN"]["fieldGoalsMade"] == 35 and teams["MIN"]["fieldGoalsAttempted"] == 80
    assert teams["MIN"]["points"] == sum(teams["MIN"]["linescores"]) == 87
    assert trim_payload({"boxscore": {}, "news": {}, "plays": []}) == {"boxscore": {}}


def test_odds_selection_open_close_and_no_american_prices() -> None:
    items = json.loads((FIX / "espn_odds_401585183.json").read_text(encoding="utf-8"))["items"]
    providers = [trim_item(it) for it in items]
    sel = select_lines(providers)
    # HOU at PHI 2024-01-15: books close PHI -7.5..-8.5, opens about -7.
    assert sel["close_home_spread"] == -8.0
    assert sel["open_home_spread_books"] == -7.0
    assert sel["n_books"] == 10  # live item and accuscore excluded
    assert 0.70 < sel["close_ml_devig_home"] < 0.80
    for p in providers:
        for key, value in p.items():
            if key.startswith("ml_implied") and value is not None:
                assert 0.0 < value < 1.0


def test_odds_2017_opening_provider() -> None:
    items = json.loads((FIX / "espn_odds_400900029.json").read_text(encoding="utf-8"))["items"]
    sel = select_lines([trim_item(it) for it in items])
    assert sel["opening_provider_home_spread"] == -1.5
    assert sel["opening_provider_total"] == 195.5
    assert sel["close_total"] == 197.5


def test_american_to_implied() -> None:
    assert american_to_implied(-110) == pytest.approx(0.52381, abs=1e-5)
    assert american_to_implied("+150") == pytest.approx(0.4)
    assert american_to_implied("") is None and american_to_implied(50) is None


@pytest.mark.parametrize(
    ("name", "n_rows", "header"),
    [
        ("Injury-Report_2021-11-01_05PM.pdf", 90, "11/01/21 05:30 PM"),
        ("Injury-Report_2024-03-23_11PM.pdf", 154, "03/23/24 11:30 PM"),
    ],
)
def test_injury_report_parses_both_layouts_fully(name: str, n_rows: int, header: str) -> None:
    report = parse_report(FIX / name)
    assert report.header_time_text == header
    assert len(report.rows) == n_rows
    assert report.unplaced == []
    assert all(row_is_consistent(r) for r in report.rows)


def test_injury_report_rows_content() -> None:
    old = parse_report(FIX / "Injury-Report_2021-11-01_05PM.pdf").rows
    assert old[0]["matchup"] == "CLE@CHA" and old[0]["team"] == "Charlotte Hornets"
    assert old[0]["player_name"] == "Kulboka, Arnoldas" and old[0]["reason"] == "G League - Two-Way"
    assert Counter(r["status"] for r in old)["NOT YET SUBMITTED"] == 6
    new = parse_report(FIX / "Injury-Report_2024-03-23_11PM.pdf").rows
    by_player = {r["player_name"]: r for r in new}
    # Three-line reason centred on its row; a reason wrapped across a page break.
    assert by_player["Anunoby, OG"]["reason"] == "Injury/Illness - Right Elbow; Injury Management"
    assert by_player["Okongwu, Onyeka"]["reason"] == "Injury/Illness - Left Big Toe; Injury Management"
    assert by_player["Smith Jr., Dennis"]["status"] == "Available"


def test_out_root_guard_refuses_live_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NBA_V10_ALLOW_ANY_ROOT", raising=False)
    with pytest.raises(SystemExit):
        resolve_out_root("E:/MP/data/historical")
    assert resolve_out_root("E:/MP/data/research/nba_v10/games").name == "games"
