"""Unit tests for the manual bet ledger (portfolio/manual_bet_ledger.py)."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import Mock

import pytest

from model_prediction.dashboard.routes import Handler
from model_prediction.portfolio.manual_bet_ledger import (
    get_manual_bankroll,
    mark_manual_bet_result,
    read_manual_bets,
    record_manual_bet,
    set_manual_bankroll,
    settle_manual_bets,
    sync_manual_bets_from_polymarket,
)

VALID_BET = {
    "league": "MLB",
    "away_team": "New York Yankees",
    "home_team": "Boston Red Sox",
    "market_type": "moneyline",
    "selection": "home",
    "entry_price": 0.55,
    "stake_usd": 15.0,
    "event_start_utc": "2026-08-20T17:00:00Z",
    "sportsbook": "polymarket",
    "note": "Value on the road dog price",
}


def test_record_manual_bet_writes_row(tmp_path):
    res = record_manual_bet(VALID_BET, data_root=tmp_path)
    assert res["status"] == "ok"
    assert res["stake_usd"] == 15.0

    rows = read_manual_bets(data_root=tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["league"] == "MLB"
    assert row["status"] == "open"
    assert float(row["units"]) == 15.0
    assert row["model_probability"] == ""
    assert row["record_type"] == "manual_bet"


def test_record_manual_bet_rejects_missing_fields(tmp_path):
    bad = dict(VALID_BET)
    del bad["home_team"]
    with pytest.raises(ValueError, match="required"):
        record_manual_bet(bad, data_root=tmp_path)


def test_record_manual_bet_rejects_bad_price_and_stake(tmp_path):
    with pytest.raises(ValueError, match="entry_price"):
        record_manual_bet({**VALID_BET, "entry_price": 1.5}, data_root=tmp_path)
    with pytest.raises(ValueError, match="stake_usd"):
        record_manual_bet({**VALID_BET, "stake_usd": -5}, data_root=tmp_path)


def test_record_manual_bet_deduplicates(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)
    res = record_manual_bet(VALID_BET, data_root=tmp_path)
    assert res["status"] == "duplicate"
    assert len(read_manual_bets(data_root=tmp_path)) == 1


def test_settle_manual_bets_grades_against_espn(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)

    mock_espn = Mock()
    mock_espn.scoreboard.return_value = {
        "events": [
            {
                "status": {"type": {"completed": True}},
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Boston Red Sox"}, "score": 5},
                            {"homeAway": "away", "team": {"displayName": "New York Yankees"}, "score": 3},
                        ]
                    }
                ],
            }
        ]
    }

    res = settle_manual_bets(data_root=tmp_path, espn_client=mock_espn)
    assert res["status"] == "ok"
    assert res["settled_count"] == 1

    rows = read_manual_bets(data_root=tmp_path)
    assert rows[0]["status"] == "settled"
    assert rows[0]["result"] == "win"
    assert float(rows[0]["pnl_units"]) > 0


def test_mark_manual_bet_result_override(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)
    rows = read_manual_bets(data_root=tmp_path)
    pick_id = rows[0]["pick_id"]

    res = mark_manual_bet_result(pick_id, "loss", data_root=tmp_path)
    assert res["status"] == "ok"
    assert res["pnl_usd"] == -15.0

    rows = read_manual_bets(data_root=tmp_path)
    assert rows[0]["status"] == "settled"
    assert rows[0]["result"] == "loss"
    assert rows[0]["void_reason"] == "manual_override"


def test_mark_manual_bet_result_rejects_bad_result(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)
    rows = read_manual_bets(data_root=tmp_path)
    with pytest.raises(ValueError, match="win/loss/push"):
        mark_manual_bet_result(rows[0]["pick_id"], "maybe", data_root=tmp_path)


def test_mark_manual_bet_result_unknown_pick_id(tmp_path):
    record_manual_bet(VALID_BET, data_root=tmp_path)
    with pytest.raises(ValueError, match="no manual bet found"):
        mark_manual_bet_result("does-not-exist", "win", data_root=tmp_path)


def test_bankroll_defaults_and_round_trip(tmp_path):
    assert get_manual_bankroll(data_root=tmp_path) == 350.0

    res = set_manual_bankroll(500, data_root=tmp_path)
    assert res["status"] == "ok"
    assert res["bankroll_usd"] == 500.0
    assert get_manual_bankroll(data_root=tmp_path) == 500.0


def test_set_manual_bankroll_rejects_invalid(tmp_path):
    with pytest.raises(ValueError):
        set_manual_bankroll(-10, data_root=tmp_path)
    with pytest.raises(ValueError):
        set_manual_bankroll("not-a-number", data_root=tmp_path)


def test_dashboard_manual_bets_get_endpoint():
    handler = Handler.__new__(Handler)
    handler.path = "/api/manual-bets"
    handler.headers = {}
    handler.wfile = BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()

    handler.do_GET()

    data = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "rows" in data
    assert "bankroll_usd" in data
    assert "open_exposure_pct" in data
    assert isinstance(data["rows"], list)


def test_dashboard_manual_bets_record_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "model_prediction.dashboard.routes.record_manual_bet",
        lambda bet: record_manual_bet(bet, data_root=tmp_path),
    )
    body = json.dumps({"confirm": True, "bet": VALID_BET}).encode()
    handler = Handler.__new__(Handler)
    handler.path = "/api/manual-bets/record"
    handler.headers = {"Content-Length": str(len(body)), "X-Dashboard-Token": _dashboard_token()}
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()

    handler.do_POST()

    data = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert data["status"] == "ok"
    assert len(read_manual_bets(data_root=tmp_path)) == 1


def _dashboard_token() -> str:
    from model_prediction.dashboard.common import _DASHBOARD_TOKEN

    return _DASHBOARD_TOKEN


def test_sync_manual_bets_from_polymarket_imports_unmatched_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "model_prediction.portfolio.auto_buyer_ledger.read_auto_buyer_ledger",
        lambda jsonl_path=None: [{"market_slug": "aec-cs2-known-auto-2026-09-05"}],
    )

    class FakeExecutor:
        def portfolio_snapshot(self):
            return {
                "positions": {
                    "aec-cs2-known-auto-2026-09-05": {
                        "netPosition": "10",
                        "cost": {"value": "5.0"},
                        "marketMetadata": {"title": "A vs. B", "outcome": "A", "team": {"league": "cs2"}},
                        "updateTime": "2026-09-04T20:00:00Z",
                    },
                    "asc-cfb-liub-kan-2026-09-04-pos-40pt5": {
                        "netPosition": "-29",
                        "cost": {"value": "17.499"},
                        "marketMetadata": {
                            "title": "LIU vs. Kansas",
                            "outcome": "-40.50",
                            "eventSlug": "cfb-liub-kan-2026-09-04",
                            "team": {"league": "cfb"},
                        },
                        "updateTime": "2026-09-04T18:31:32Z",
                    },
                }
            }

    res = sync_manual_bets_from_polymarket(data_root=tmp_path, executor=FakeExecutor())
    assert res["status"] == "ok"
    assert res["synced_count"] == 1
    assert res["synced_slugs"] == ["asc-cfb-liub-kan-2026-09-04-pos-40pt5"]

    rows = read_manual_bets(data_root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["league"] == "NCAAF"
    assert rows[0]["market_type"] == "spread"
    assert rows[0]["selection"] == "home"
    assert float(rows[0]["line"]) == -40.5
    assert rows[0]["event_id"] == "asc-cfb-liub-kan-2026-09-04-pos-40pt5"
    assert rows[0]["event_start_utc"] == "2026-09-04T12:00:00Z"

    # Re-running the sync must not duplicate the already-synced position.
    res2 = sync_manual_bets_from_polymarket(data_root=tmp_path, executor=FakeExecutor())
    assert res2["synced_count"] == 0
    assert res2["skipped_existing"] == 1
    assert len(read_manual_bets(data_root=tmp_path)) == 1


def _espn_team(location, mascot, abbreviation, score, home_away):
    # The real ESPN scoreboard shape: displayName is "location mascot", location is the school only,
    # shortDisplayName is the abbreviated school ("Kansas St").
    return {
        "homeAway": home_away,
        "score": str(score),
        "team": {
            "displayName": f"{location} {mascot}",
            "name": mascot,
            "abbreviation": abbreviation,
            "location": location,
            "shortDisplayName": location.replace("State", "St"),
        },
    }


def _espn_event(event_id, away, home, completed=True):
    return {
        "id": event_id,
        "status": {"type": {"completed": completed}},
        "competitions": [{"competitors": [home, away]}],
    }


class _FakeScoreboard:
    def __init__(self, by_league):
        self.by_league = by_league

    def scoreboard(self, league, day):
        return {"events": self.by_league.get(league, [])}


_COLLEGE_BET = {
    "league": "NCAAF",
    "away_team": "Michigan",
    "home_team": "Ohio State",
    "market_type": "moneyline",
    "selection": "home",
    "entry_price": 0.55,
    "stake_usd": 15.0,
    "event_start_utc": "2026-11-21T17:00:00Z",
    "sportsbook": "polymarket",
}


def test_settle_picks_the_exact_scoreboard_match_over_an_earlier_substring_match(tmp_path):
    record_manual_bet(_COLLEGE_BET, data_root=tmp_path)
    espn = _FakeScoreboard(
        {
            "NCAAF": [
                # Listed first: Michigan State is a substring hit for "michigan"; Ohio State wins it.
                _espn_event(
                    "1",
                    _espn_team("Michigan State", "Spartans", "MSU", 10, "away"),
                    _espn_team("Ohio State", "Buckeyes", "OSU", 20, "home"),
                ),
                _espn_event(
                    "2",
                    _espn_team("Michigan", "Wolverines", "MICH", 30, "away"),
                    _espn_team("Ohio State", "Buckeyes", "OSU", 20, "home"),
                ),
            ]
        }
    )
    res = settle_manual_bets(data_root=tmp_path, espn_client=espn)
    assert res["settled_count"] == 1
    row = read_manual_bets(data_root=tmp_path)[0]
    assert (row["away_score"], row["home_score"], row["result"]) == ("30", "20", "loss")


def test_settle_leaves_the_row_open_when_two_games_match_and_none_is_exact(tmp_path):
    record_manual_bet({**_COLLEGE_BET, "event_start_utc": "2026-11-01T17:00:00Z"}, data_root=tmp_path)
    espn = _FakeScoreboard(
        {
            "NCAAF": [
                _espn_event(
                    "1",
                    _espn_team("Michigan State", "Spartans", "MSU", 10, "away"),
                    _espn_team("Ohio State", "Buckeyes", "OSU", 20, "home"),
                ),
                _espn_event(
                    "2",
                    _espn_team("Michigan Tech", "Huskies", "MTU", 30, "away"),
                    _espn_team("Ohio State", "Buckeyes", "OSU", 20, "home"),
                ),
            ]
        }
    )
    res = settle_manual_bets(data_root=tmp_path, espn_client=espn)
    assert res["settled_count"] == 0
    row = read_manual_bets(data_root=tmp_path)[0]
    # Long past the 48h unresolved-void window, and still not voided: the game is decided, our matching is not.
    assert row["status"] == "open" and row["result"] == ""


def test_settle_inverted_orientation_swaps_scores_and_prefers_the_exact_event(tmp_path):
    # The scoreboard lists the row's home team as the away side.
    record_manual_bet(_COLLEGE_BET, data_root=tmp_path)
    espn = _FakeScoreboard(
        {
            "NCAAF": [
                _espn_event(
                    "2",
                    _espn_team("Ohio State", "Buckeyes", "OSU", 7, "away"),
                    _espn_team("Michigan State", "Spartans", "MSU", 9, "home"),
                ),
                _espn_event(
                    "1",
                    _espn_team("Ohio State", "Buckeyes", "OSU", 21, "away"),
                    _espn_team("Michigan", "Wolverines", "MICH", 14, "home"),
                ),
            ]
        }
    )
    assert settle_manual_bets(data_root=tmp_path, espn_client=espn)["settled_count"] == 1
    row = read_manual_bets(data_root=tmp_path)[0]
    assert (row["away_score"], row["home_score"], row["result"]) == ("14", "21", "win")


def _set_row_fields(tmp_path, **fields):
    from model_prediction.ledger import FIELDNAMES
    from model_prediction.portfolio.manual_bet_ledger import _get_ledger_path
    from model_prediction.xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic

    path = _get_ledger_path(tmp_path)
    _, rows = read_xlsx_rows(path)
    rows[0].update(fields)
    write_xlsx_rows_atomic(path, FIELDNAMES, rows)


class _ResolvedMarketClient:
    def __init__(self, outcomes, prices):
        self.outcomes, self.prices = outcomes, prices

    def market(self, slug):
        return {"status": "MARKET_STATUS_RESOLVED", "outcomes": self.outcomes, "outcomePrices": self.prices}


def test_resolution_fallback_refuses_an_outcome_that_matches_both_row_teams(tmp_path, monkeypatch):
    # "Wildcats" is a substring of both row teams and equal to neither.
    bet = {
        **_COLLEGE_BET,
        "league": "NCAAB",
        "away_team": "Arizona Wildcats",
        "home_team": "Kansas State Wildcats",
    }
    record_manual_bet(bet, data_root=tmp_path)
    _set_row_fields(tmp_path, event_id="aec-cbb-ariz-ksu-2026-11-21")
    client = _ResolvedMarketClient(["Wildcats", "Jayhawks"], ["1", "0"])
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.PolymarketUSClient", lambda: client)
    res = settle_manual_bets(data_root=tmp_path, espn_client=_FakeScoreboard({}))
    assert res["settled_count"] == 0
    assert read_manual_bets(data_root=tmp_path)[0]["status"] == "open"


def test_resolution_fallback_uses_the_event_id_slug_of_a_manual_row(tmp_path, monkeypatch):
    bet = {**_COLLEGE_BET, "league": "NCAAB", "away_team": "Boise State", "home_team": "Kansas"}
    record_manual_bet(bet, data_root=tmp_path)
    _set_row_fields(tmp_path, event_id="aec-cbb-bsu-ku-2026-11-21")
    client = _ResolvedMarketClient(["Kansas", "Boise State"], ["1", "0"])
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.PolymarketUSClient", lambda: client)
    res = settle_manual_bets(data_root=tmp_path, espn_client=_FakeScoreboard({}))
    assert res["settled_count"] == 1
    assert read_manual_bets(data_root=tmp_path)[0]["result"] == "win"


def test_resolution_fallback_takes_the_exactly_named_outcome_over_a_substring_hit(tmp_path, monkeypatch):
    # "Kansas State" contains the home row team "Kansas" but equals only the away row team.
    bet = {**_COLLEGE_BET, "league": "NCAAB", "away_team": "Kansas State", "home_team": "Kansas"}
    record_manual_bet(bet, data_root=tmp_path)
    _set_row_fields(tmp_path, event_id="aec-cbb-ksu-ku-2026-11-21")
    client = _ResolvedMarketClient(["Kansas", "Kansas State"], ["0", "1"])
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.PolymarketUSClient", lambda: client)
    res = settle_manual_bets(data_root=tmp_path, espn_client=_FakeScoreboard({}))
    assert res["settled_count"] == 1
    row = read_manual_bets(data_root=tmp_path)[0]
    assert (row["away_score"], row["home_score"], row["result"]) == ("1", "0", "loss")


@pytest.mark.parametrize(
    ("short", "short_mascot", "short_abbr", "long_mascot", "long_abbr"),
    [
        ("Kansas", "Jayhawks", "KU", "Wildcats", "KSU"),
        ("Iowa", "Hawkeyes", "IOWA", "Cyclones", "ISU"),
    ],
)
def test_settle_flipped_orientation_with_a_school_and_its_state_namesake(
    tmp_path, short, short_mascot, short_abbr, long_mascot, long_abbr
):
    # Row: "<X> State" away at "<X>" home. ESPN lists <X> away at <X> State home, and <X> State wins.
    # The normal orientation used to win on a substring hit ("kansas" inside "kansas state"), so the
    # scores were swapped for good.
    bet = {**_COLLEGE_BET, "away_team": f"{short} State", "home_team": short}
    record_manual_bet(bet, data_root=tmp_path)
    espn = _FakeScoreboard(
        {
            "NCAAF": [
                _espn_event(
                    "5",
                    _espn_team(short, short_mascot, short_abbr, 14, "away"),
                    _espn_team(f"{short} State", long_mascot, long_abbr, 28, "home"),
                )
            ]
        }
    )
    assert settle_manual_bets(data_root=tmp_path, espn_client=espn)["settled_count"] == 1
    row = read_manual_bets(data_root=tmp_path)[0]
    # The row's home pick is <X>, which lost 14-28.
    assert (row["away_score"], row["home_score"], row["result"]) == ("28", "14", "loss")


def test_nba_row_does_not_settle_on_a_college_game_between_the_same_locations(tmp_path):
    from datetime import timedelta

    from model_prediction.domain import utc_now

    start = (utc_now() - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bet = {
        **_COLLEGE_BET,
        "league": "NBA",
        "away_team": "Minnesota Timberwolves",
        "home_team": "Indiana Pacers",
        "event_start_utc": start,
    }
    record_manual_bet(bet, data_root=tmp_path)
    espn = _FakeScoreboard(
        {
            "NCAAB": [
                _espn_event(
                    "9",
                    _espn_team("Minnesota", "Golden Gophers", "MINN", 60, "away"),
                    _espn_team("Indiana", "Hoosiers", "INDI", 70, "home"),
                )
            ]
        }
    )
    res = settle_manual_bets(data_root=tmp_path, espn_client=espn)
    assert res["settled_count"] == 0
    row = read_manual_bets(data_root=tmp_path)[0]
    assert row["status"] == "open" and row["result"] == ""


def test_event_that_is_exact_in_both_orientations_stays_open_and_names_the_event(tmp_path, caplog):
    bet = {**_COLLEGE_BET, "league": "NFL", "away_team": "New York", "home_team": "New York"}
    record_manual_bet(bet, data_root=tmp_path)
    espn = _FakeScoreboard(
        {
            "NFL": [
                _espn_event(
                    "77",
                    _espn_team("New York", "Jets", "NYJ", 10, "away"),
                    _espn_team("New York", "Giants", "NYG", 20, "home"),
                )
            ]
        }
    )
    with caplog.at_level("WARNING"):
        res = settle_manual_bets(data_root=tmp_path, espn_client=espn)
    assert res["settled_count"] == 0
    row = read_manual_bets(data_root=tmp_path)[0]
    assert row["status"] == "open"
    assert any("ambiguous scoreboard match" in r.message and "77" in r.message for r in caplog.records)


def test_selection_naming_a_team_is_matched_exactly_before_by_substring(tmp_path):
    bet = {**_COLLEGE_BET, "away_team": "Kansas State", "home_team": "Kansas"}
    record_manual_bet(bet, data_root=tmp_path)
    _set_row_fields(tmp_path, selection="kansas state")
    espn = _FakeScoreboard(
        {
            "NCAAF": [
                _espn_event(
                    "3",
                    _espn_team("Kansas State", "Wildcats", "KSU", 28, "away"),
                    _espn_team("Kansas", "Jayhawks", "KU", 14, "home"),
                )
            ]
        }
    )
    assert settle_manual_bets(data_root=tmp_path, espn_client=espn)["settled_count"] == 1
    row = read_manual_bets(data_root=tmp_path)[0]
    # "kansas state" is the away team and it won; a substring-first mapping graded it as the home pick.
    assert row["result"] == "win"


def test_resettle_all_does_not_count_an_ambiguous_settled_row_as_open(tmp_path):
    record_manual_bet({**_COLLEGE_BET, "event_start_utc": "2026-11-01T17:00:00Z"}, data_root=tmp_path)
    _set_row_fields(tmp_path, status="settled", result="win")
    espn = _FakeScoreboard(
        {
            "NCAAF": [
                _espn_event(
                    "1",
                    _espn_team("Michigan State", "Spartans", "MSU", 10, "away"),
                    _espn_team("Ohio State", "Buckeyes", "OSU", 20, "home"),
                ),
                _espn_event(
                    "2",
                    _espn_team("Michigan Tech", "Huskies", "MTU", 30, "away"),
                    _espn_team("Ohio State", "Buckeyes", "OSU", 20, "home"),
                ),
            ]
        }
    )
    res = settle_manual_bets(data_root=tmp_path, espn_client=espn, resettle_all=True)
    assert res["settled_count"] == 0 and res["open_count"] == 0
    assert read_manual_bets(data_root=tmp_path)[0]["status"] == "settled"


def _nba_row(tmp_path, away, home):
    from datetime import timedelta

    from model_prediction.domain import utc_now

    # Started 5h ago: inside the 48h unresolved-void window, so "open" means "not graded".
    start = (utc_now() - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    record_manual_bet(
        {**_COLLEGE_BET, "league": "NBA", "away_team": away, "home_team": home, "event_start_utc": start},
        data_root=tmp_path,
    )


@pytest.mark.parametrize("nba_events", ["no_nba_event", "nba_unfinished"])
def test_nba_row_is_not_graded_off_a_college_game_whose_abbreviations_sit_in_the_nba_names(
    tmp_path, nba_events
):
    # "MINN" / "WASH" are substrings of "minnesota timberwolves" / "washington wizards".
    _nba_row(tmp_path, "Minnesota Timberwolves", "Washington Wizards")
    nba = []
    if nba_events == "nba_unfinished":
        nba = [
            _espn_event(
                "n1",
                _espn_team("Minnesota", "Timberwolves", "MIN", 0, "away"),
                _espn_team("Washington", "Wizards", "WSH", 0, "home"),
                completed=False,
            )
        ]
    college = _espn_event(
        "c1",
        _espn_team("Minnesota", "Golden Gophers", "MINN", 60, "away"),
        _espn_team("Washington", "Huskies", "WASH", 70, "home"),
    )
    res = settle_manual_bets(
        data_root=tmp_path, espn_client=_FakeScoreboard({"NBA": nba, "NCAAB": [college]})
    )
    assert res["settled_count"] == 0
    row = read_manual_bets(data_root=tmp_path)[0]
    assert row["status"] == "open" and row["result"] == ""


def test_nba_row_settles_on_its_own_nba_game_once_final_not_on_the_college_one(tmp_path):
    _nba_row(tmp_path, "Minnesota Timberwolves", "Washington Wizards")
    nba = _espn_event(
        "n1",
        _espn_team("Minnesota", "Timberwolves", "MIN", 101, "away"),
        _espn_team("Washington", "Wizards", "WSH", 99, "home"),
    )
    college = _espn_event(
        "c1",
        _espn_team("Minnesota", "Golden Gophers", "MINN", 60, "away"),
        _espn_team("Washington", "Huskies", "WASH", 70, "home"),
    )
    res = settle_manual_bets(
        data_root=tmp_path, espn_client=_FakeScoreboard({"NBA": [nba], "NCAAB": [college]})
    )
    assert res["settled_count"] == 1
    row = read_manual_bets(data_root=tmp_path)[0]
    assert (row["away_score"], row["home_score"], row["result"]) == ("101", "99", "loss")


def test_mascot_only_nba_row_waits_for_its_own_game_and_ignores_an_exact_college_namesake(tmp_path):
    # Buffalo Bulls @ Toledo Rockets matches 'Bulls' @ 'Rockets' exactly by mascot, but it is a
    # NCAAB event and the NBA row's own Bulls @ Rockets game is the one that counts.
    _nba_row(tmp_path, "Bulls", "Rockets")
    college = _espn_event(
        "c1",
        _espn_team("Buffalo", "Bulls", "BUFF", 60, "away"),
        _espn_team("Toledo", "Rockets", "TOL", 70, "home"),
    )
    nba_pending = _espn_event(
        "n1",
        _espn_team("Chicago", "Bulls", "CHI", 0, "away"),
        _espn_team("Houston", "Rockets", "HOU", 0, "home"),
        completed=False,
    )
    res = settle_manual_bets(
        data_root=tmp_path, espn_client=_FakeScoreboard({"NBA": [nba_pending], "NCAAB": [college]})
    )
    assert res["settled_count"] == 0
    assert read_manual_bets(data_root=tmp_path)[0]["status"] == "open"

    nba_final = _espn_event(
        "n1",
        _espn_team("Chicago", "Bulls", "CHI", 110, "away"),
        _espn_team("Houston", "Rockets", "HOU", 100, "home"),
    )
    res = settle_manual_bets(
        data_root=tmp_path, espn_client=_FakeScoreboard({"NBA": [nba_final], "NCAAB": [college]})
    )
    assert res["settled_count"] == 1
    row = read_manual_bets(data_root=tmp_path)[0]
    assert (row["away_score"], row["home_score"]) == ("110", "100")


def test_single_non_exact_candidate_is_blocked_by_a_separate_ambiguous_event(tmp_path, caplog):
    record_manual_bet({**_COLLEGE_BET, "event_start_utc": "2026-11-01T17:00:00Z"}, data_root=tmp_path)
    one_sided = _espn_event(
        "1",
        _espn_team("Michigan State", "Spartans", "MSU", 10, "away"),
        _espn_team("Ohio State", "Buckeyes", "OSU", 20, "home"),
    )
    # A joint listing whose names contain both row teams matches in both orientations, not exactly.
    both_ways = _espn_event(
        "2",
        {"homeAway": "away", "score": "7", "team": {"displayName": "Michigan Ohio State Wolverines"}},
        {"homeAway": "home", "score": "9", "team": {"displayName": "Michigan Ohio State Buckeyes"}},
    )
    with caplog.at_level("WARNING"):
        res = settle_manual_bets(
            data_root=tmp_path, espn_client=_FakeScoreboard({"NCAAF": [one_sided, both_ways]})
        )
    assert res["settled_count"] == 0
    row = read_manual_bets(data_root=tmp_path)[0]
    assert row["status"] == "open"
    assert any("ambiguous scoreboard match" in r.message and "'2'" in r.message for r in caplog.records)
