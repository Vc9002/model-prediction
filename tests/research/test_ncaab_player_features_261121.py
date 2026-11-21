"""Synthetic PIT tests for the NCAAB player features (quick 261121-hmc lane P). No archive reads."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ncaab_research"))
sys.path.insert(0, str(REPO / "scripts"))

import backfill_ncaab_player_boxes as bf
import player_features_261121 as pf

ROSTER = [f"a{i}" for i in range(9)]
MINUTES = [36, 34, 32, 30, 28, 20, 12, 5, 3]  # sums to 200


def tg(
    event_id: str,
    team: str,
    season: int,
    game_date: str,
    minutes: dict[str, float],
    start_utc: str | None = None,
    box: bool = True,
) -> pf.TeamGame:
    players = tuple(pf.PlayerLine(a, float(m), m / 2.0 if m > 0 else 0.0) for a, m in minutes.items())
    return pf.TeamGame(
        event_id=event_id,
        team_id=team,
        opponent_id="opp",
        season=season,
        game_date=game_date,
        event_start_utc=start_utc or f"{game_date}T23:00Z",
        side="home",
        team_conference_id="1",
        box_available=box,
        players=players,
    )


def full(except_: tuple[str, ...] = ()) -> dict[str, float]:
    return {a: (0.0 if a in except_ else float(m)) for a, m in zip(ROSTER, MINUTES, strict=True)}


def season_games(team: str = "T", season: int = 2025) -> list[pf.TeamGame]:
    dates = ["2024-11-04", "2024-11-08", "2024-11-12", "2024-11-16", "2024-11-20", "2024-11-24"]
    games = []
    for i, day in enumerate(dates):
        out = ("a0",) if i in (3, 4) else ()  # top player misses games 4 and 5
        games.append(tg(f"e{i}", team, season, day, full(out)))
    return games


def row_for(rows: list[dict], event_id: str, team: str = "T") -> dict:
    return next(r for r in rows if r["event_id"] == event_id and r["team_id"] == team)


def test_availability_uses_only_prior_games_and_tracks_absence() -> None:
    rows = pf.build_feature_rows(season_games())
    first = row_for(rows, "e0")
    assert first["n_prior_games_box"] == 0
    assert first["avail_top7_prev1"] is None
    assert first["availability_null_reason"] == "insufficient_history"
    assert row_for(rows, "e2")["availability_null_reason"] == "insufficient_history"

    e3 = row_for(rows, "e3")  # a0 played games 0-2, the absence in e3 itself must not leak
    assert e3["n_prior_games_box"] == 3
    assert e3["avail_top7_prev1"] == pytest.approx(1.0)
    assert e3["top1_out_prev1"] is False

    e5 = row_for(rows, "e5")  # a0 missed e3 and e4
    assert e5["top1_out_prev1"] is False  # once a0 sits, a1 leads season-to-date minutes
    assert e5["top7_n_out_prev1"] == 1
    assert e5["top7_max_absence_streak"] == 2
    assert 0 < e5["avail_top7_prev1"] < 1
    assert e5["avail_top7_prev2_any"] < 1  # missed both of the previous two
    assert e5["top7_minutes_share_out_3plus"] == 0


def test_same_day_and_later_boxes_never_change_a_row() -> None:
    games = season_games()
    target_date = "2024-11-16"
    base = row_for(pf.build_feature_rows(games), "e3")

    def scramble(g: pf.TeamGame) -> pf.TeamGame:
        if g.game_date < target_date:
            return g
        players = tuple(pf.PlayerLine(f"x{i}", 40.0, 99.0) for i in range(5))
        return dataclasses.replace(g, players=players)

    perturbed = [scramble(g) for g in games]
    # plus an extra same-day game for the team (a contrived double) and a later one
    perturbed.append(tg("same", "T", 2025, target_date, {"zz": 200.0}, start_utc="2024-11-16T15:00Z"))
    perturbed.append(tg("later", "T", 2025, "2024-12-30", {"zz": 200.0}))
    assert row_for(pf.build_feature_rows(perturbed), "e3") == base


def test_late_tip_crossing_utc_midnight_is_prior_by_eastern_date() -> None:
    games = season_games()[:3]
    # tips 2024-11-13 02:00Z but is Eastern 2024-11-12, so it precedes a 2024-11-13 game
    late = tg("late", "T", 2025, "2024-11-12", full(("a1",)), start_utc="2024-11-13T02:00Z")
    games = [games[0], games[1], late, tg("next", "T", 2025, "2024-11-13", full())]
    nxt = row_for(pf.build_feature_rows(games), "next")
    assert nxt["n_prior_games_box"] == 3
    assert nxt["top7_n_out_prev1"] == 1  # a1 missed the late game


def test_prev_game_box_missing_fails_closed() -> None:
    games = season_games()
    present = [g for g in games if g.event_id != "e4"]
    schedule = {("T", 2025): sorted((g.game_date, g.event_id) for g in games)}
    e5 = row_for(pf.build_feature_rows(present, schedule), "e5")
    assert e5["avail_top7_prev1"] is None
    assert e5["availability_null_reason"] == "prev_game_box_missing"
    assert e5["n_prior_games_scheduled"] == 5 and e5["n_prior_games_box"] == 4


def test_prior_season_features_require_prior_data_and_appearance() -> None:
    rows = pf.build_feature_rows(season_games(season=2025))
    assert row_for(rows, "e3")["prior_null_reason"] == "no_prior_season_data"

    prior = [
        tg(f"p{i}", "T", 2024, f"2024-01-{i + 10:02d}", {"a0": 40, "a1": 40, "r0": 120}) for i in range(10)
    ]
    # a2 transferred in from team U, where he played half of U's minutes
    prior += [tg(f"u{i}", "U", 2024, f"2024-01-{i + 10:02d}", {"a2": 100, "u1": 100}) for i in range(10)]
    current = [
        tg("c0", "T", 2025, "2024-11-04", {"a0": 100, "n0": 100}),
        tg("c1", "T", 2025, "2024-11-08", {"a0": 100, "a2": 100}),
        tg("c2", "T", 2025, "2024-11-12", {"a1": 100, "a2": 100}),
    ]
    rows = pf.build_feature_rows(prior + current)
    c0 = row_for(rows, "c0")
    assert c0["prior_null_reason"] == "no_current_season_history"
    c1 = row_for(rows, "c1")  # only a0 and n0 have appeared
    assert c1["continuity_same_team"] == pytest.approx(0.5)
    assert c1["ret_min_share_same"] == pytest.approx(0.2)
    assert c1["ret_min_share_transfer_in"] == 0  # a2 has not appeared yet: not counted
    c2 = row_for(rows, "c2")  # a2 appeared in c1
    assert c2["ret_min_share_transfer_in"] == pytest.approx(0.5)
    assert c2["continuity_same_team"] == pytest.approx(0.5)
    assert c2["continuity_any_team"] == pytest.approx(0.75)
    assert c2["ret_min_share_same"] == pytest.approx(0.2)  # a1 still unseen before c2

    # A team with no prior-season box history (new D1 member) is null, never zero.
    new_team = [tg(f"n{i}", "N", 2025, f"2024-11-{i + 4:02d}", {"q": 200}) for i in range(2)]
    n1 = row_for(pf.build_feature_rows(prior + new_team), "n1", team="N")
    assert n1["prior_null_reason"] == "team_absent_prior_season"
    assert n1["ret_min_share_same"] is None


def test_prior_season_overlapping_current_game_is_refused() -> None:
    prior = pf.build_prior_season([tg("p", "T", 2024, "2024-11-20", {"a0": 200})], 2024)
    target = tg("c", "T", 2025, "2024-11-10", {"a0": 200})
    with pytest.raises(ValueError, match="prior_season_overlaps"):
        pf.features_for_game(target, [target], prior, None)


def _summary(players: list[dict], keys: list[str]) -> dict:
    return {
        "header": {
            "competitions": [
                {
                    "id": "1",
                    "status": {"type": {"completed": True, "detail": "Final/OT"}},
                    "competitors": [
                        {"id": "10", "score": "70", "linescores": [{}, {}, {}]},
                        {"id": "20", "score": "60", "linescores": [{}, {}, {}]},
                    ],
                }
            ]
        },
        "boxscore": {
            "players": [
                {"team": {"id": "10"}, "statistics": [{"keys": keys, "totals": [], "athletes": players}]},
                {"team": {"id": "20"}, "statistics": [{"keys": keys, "totals": [], "athletes": []}]},
            ]
        },
    }


def test_backfill_parser_maps_by_key_and_keeps_no_id_rows() -> None:
    keys = ["points", "minutes", "fieldGoalsMade-fieldGoalsAttempted", "offensiveRebounds"]
    players = [
        {
            "athlete": {"id": "7", "displayName": "A"},
            "starter": True,
            "didNotPlay": False,
            "stats": ["70", "45", "25-50", "3"],
        },
        {"athlete": {"shortName": "B. X"}, "starter": False, "didNotPlay": True, "stats": []},
    ]
    game = {
        "event_id": "1",
        "game_date": "2025-01-01",
        "season_year": 2025,
        "event_start_utc": "2025-01-01T20:00Z",
        "home_team_id": "10",
        "away_team_id": "20",
        "home_team": "H",
        "away_team": "A",
        "home_score": 70,
        "away_score": 60,
    }
    rec = bf.parse_player_boxes(game, _summary(players, keys), "2026-11-21T00:00:00+00:00")
    home = rec["home"]
    assert home["periods"] == 3 and home["player_box_available"]
    p = home["players"][0]
    assert (p["points"], p["minutes"], p["fgm"], p["fga"], p["oreb"]) == (70, 45, 25, 50, 3)
    assert home["players"][1]["athlete_id"] is None and home["players"][1]["minutes"] is None
    assert rec["away"]["player_box_available"] is False
    with pytest.raises(ValueError, match="observed_before_start"):
        bf.parse_player_boxes(game, _summary(players, keys), "2025-01-01T19:00:00+00:00")


def _team(points: list[int], minutes: list[int], score: int, ids: bool = True) -> dict:
    players = [
        {"athlete_id": (str(i) if ids else None), "points": p, "minutes": m}
        for i, (p, m) in enumerate(zip(points, minutes, strict=True))
    ]
    return {
        "team_id": "T",
        "player_box_available": True,
        "players": players,
        "score_header": score,
        "periods": 2,
        "conference_id": "1",
    }


def test_box_quality_gate_rejects_broken_boxes() -> None:
    assert pf.box_quality(_team([40, 30], [100, 100], 70)) is None
    assert pf.box_quality(_team([40, 20], [100, 100], 70)) == "points_sum_mismatch"
    assert pf.box_quality(_team([40, 30], [100, 40], 70)) == "minutes_sum_mismatch"
    assert pf.box_quality(_team([40, 30], [100, 100], 70, ids=False)) == "played_without_athlete_id"
    assert pf.box_quality({"player_box_available": False, "players": []}) == "no_player_box"
    record = {
        "event_id": "9",
        "season_year": 2025,
        "game_date": "2025-01-01",
        "event_start_utc": "2025-01-01T20:00Z",
        "home": _team([40, 20], [100, 100], 70),
        "away": dict(_team([40, 30], [100, 100], 70), team_id="U"),
    }
    home, away = pf.team_games_from_record(record)
    assert home.box_available is False and home.box_quality_reason == "points_sum_mismatch"
    assert away.box_available is True
