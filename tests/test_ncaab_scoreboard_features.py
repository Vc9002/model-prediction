"""Tests for the scoreboard-derived NCAAB features (2026-11-12 feature round).

- ``features/ncaab_scoreboard_stats.py``: parsing, point-in-time keying, last-known ranks.
- spread v6: non-D1 prior read from history, AP-rank prior, both default-off.
- total v6: attempts-based tempo, default-off.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_prediction.features.base import GameRecord
from model_prediction.features.ncaab_scoreboard_stats import NCAABScoreboardStats, TeamGameStats
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v6 import NCAABSpreadModelV6
from model_prediction.models.ncaab_total_v6 import NCAABTotalModelV6

NOW = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)


def _competitor(team: str, home_away: str, score: int, fga: int, fta: int, rank: int | None = None) -> dict:
    stats = {
        "fieldGoalsAttempted": fga,
        "fieldGoalsMade": fga // 2,
        "threePointFieldGoalsAttempted": fga // 3,
        "threePointFieldGoalsMade": fga // 9,
        "freeThrowsAttempted": fta,
        "freeThrowsMade": int(fta * 0.7),
        "rebounds": 35,
        "assists": 14,
    }
    comp = {
        "homeAway": home_away,
        "score": str(score),
        "team": {"displayName": team},
        "statistics": [{"name": k, "displayValue": str(v)} for k, v in stats.items()],
    }
    if rank is not None:
        comp["curatedRank"] = {"current": rank}
    return comp


def _scoreboard(
    tmp_path: Path, day: str, events: list[tuple[str, str, str, int, int, int, int, int | None, int | None]]
) -> None:
    payload = {
        "events": [
            {
                "id": eid,
                "date": f"{day}T23:00Z",
                "competitions": [
                    {
                        "status": {"type": {"completed": True}},
                        "neutralSite": False,
                        "conferenceCompetition": False,
                        "competitors": [
                            _competitor(home, "home", hs, h_fga, 20, h_rank),
                            _competitor(away, "away", as_, a_fga, 20, a_rank),
                        ],
                    }
                ],
            }
            for eid, home, away, hs, as_, h_fga, a_fga, h_rank, a_rank in events
        ]
    }
    (tmp_path / day).mkdir(parents=True)
    (tmp_path / day / "scores_ncaab.json").write_text(json.dumps(payload), encoding="utf-8")


def test_stats_are_keyed_by_event_and_only_served_for_the_callers_history(tmp_path):
    _scoreboard(tmp_path, "2026-11-08", [("e1", "Duke Blue Devils", "Rider Broncs", 90, 60, 70, 55, 2, None)])
    _scoreboard(tmp_path, "2026-11-09", [("e2", "Rider Broncs", "Duke Blue Devils", 50, 95, 50, 80, None, 1)])
    stats = NCAABScoreboardStats(tmp_path)
    e1 = stats.get("e1")
    assert (
        e1 is not None and e1.home.team == "Duke Blue Devils" and e1.home.rank == 2 and e1.away.rank is None
    )
    assert e1.home.shot_attempts == pytest.approx(70 + 0.44 * 20)
    assert e1.game_attempts == pytest.approx(((70 + 8.8) + (55 + 8.8)) / 2)
    g1 = GameRecord("e1", "2026-11-08T23:00:00Z", "NCAAB", "Rider Broncs", "Duke Blue Devils", 60, 90)
    g2 = GameRecord("e2", "2026-11-09T23:00:00Z", "NCAAB", "Duke Blue Devils", "Rider Broncs", 95, 50)
    # Only the history the caller may see is served: e2 is invisible when history stops at e1.
    assert [s.event_id for _g, s in stats.for_history([g1])] == ["e1"]
    assert stats.last_known_ranks([g1]) == {"Duke Blue Devils": 2}
    assert stats.last_known_ranks([g1, g2]) == {"Duke Blue Devils": 1}
    assert stats.get("missing") is None


def test_team_stats_parsing_rejects_incomplete_rows(tmp_path):
    (tmp_path / "2026-11-08").mkdir(parents=True)
    comp = _competitor("Duke Blue Devils", "home", 90, 70, 20, 30)
    comp["statistics"] = comp["statistics"][:3]  # missing attempts
    payload = {
        "events": [
            {
                "id": "e1",
                "date": "2026-11-08T23:00Z",
                "competitions": [
                    {
                        "status": {"type": {"completed": True}},
                        "competitors": [comp, _competitor("Rider Broncs", "away", 60, 55, 20)],
                    }
                ],
            }
        ]
    }
    (tmp_path / "2026-11-08" / "scores_ncaab.json").write_text(json.dumps(payload), encoding="utf-8")
    assert NCAABScoreboardStats(tmp_path).get("e1") is None
    assert TeamGameStats("x", 80, 60, 30, 20, 7, 20, 14, 30, 10, rank=None).shot_attempts == pytest.approx(
        68.8
    )


def _history_vs_non_d1(n: int) -> list[GameRecord]:
    # Alabama (SEC, in the conference map) hosting a team that is not in the map, winning by 40.
    return [
        GameRecord(
            str(i),
            (NOW - timedelta(days=i + 2)).isoformat(),
            "NCAAB",
            "Nobody College",
            "Alabama Crimson Tide",
            50,
            90,
        )
        for i in range(n)
    ]


def test_non_d1_prior_from_history_tracks_the_observed_gap_and_fails_closed_on_thin_data():
    history = _history_vs_non_d1(25)
    model = NCAABSpreadModelV6(non_d1_prior_from_history=True, home_court_points=5.0)
    priors, non_d1, _ranks = model._effective_priors(history)
    assert "Alabama Crimson Tide" in priors and "Nobody College" not in priors
    # 40-point margins less 5 HCA = 35 points = 980 Elo below the SEC prior (1680) -> 700.
    assert non_d1 == pytest.approx(1680.0 - 28.0 * 35.0, abs=1.0)
    thin = NCAABSpreadModelV6(non_d1_prior_from_history=True, non_d1_min_games=30)
    assert thin._effective_priors(history)[1] == 1180.0  # not enough games -> constant prior
    off = NCAABSpreadModelV6()
    assert off._effective_priors(history)[1] == 1180.0
    # The lower prior shows up in the served margin for a D1 host against an unmapped visitor.
    game = UpcomingGame("g", NOW.isoformat(), "Nobody College", "Alabama Crimson Tide")
    p_on = next(p for p in model.predict_spreads(history, [game]) if p.market_type == "moneyline")
    p_off = next(p for p in off.predict_spreads(history, [game]) if p.market_type == "moneyline")
    assert p_on.feature_basis["non_d1_prior_effective"] == pytest.approx(non_d1, abs=0.1)
    assert p_on.feature_basis["expected_margin"] > p_off.feature_basis["expected_margin"] + 5.0


def test_rank_prior_lifts_ranked_teams_and_is_inert_when_off(tmp_path):
    _scoreboard(tmp_path, "2026-11-08", [("e1", "Duke Blue Devils", "Rider Broncs", 90, 60, 70, 55, 1, None)])
    history = [GameRecord("e1", "2026-11-08T23:00:00Z", "NCAAB", "Rider Broncs", "Duke Blue Devils", 60, 90)]
    ranked = NCAABSpreadModelV6(rank_prior_per_slot=8.0, stats_root=tmp_path)
    plain = NCAABSpreadModelV6(stats_root=tmp_path)
    priors_r, _n, ranks = ranked._effective_priors(history)
    priors_p, _n2, ranks_p = plain._effective_priors(history)
    assert ranks == {"Duke Blue Devils": 1} and ranks_p == {}
    assert priors_r["Duke Blue Devils"] == pytest.approx(priors_p["Duke Blue Devils"] + 8.0 * 25)
    assert priors_r["Rider Broncs"] == priors_p["Rider Broncs"]  # unranked teams untouched
    game = UpcomingGame("g", NOW.isoformat(), "Rider Broncs", "Duke Blue Devils")
    pr = next(p for p in ranked.predict_spreads(history, [game]) if p.market_type == "moneyline")
    pp = next(p for p in plain.predict_spreads(history, [game]) if p.market_type == "moneyline")
    assert pr.feature_basis["rank_home"] == 1 and pp.feature_basis["rank_home"] is None
    assert pr.feature_basis["expected_margin"] > pp.feature_basis["expected_margin"]


def test_attempts_tempo_drives_pace_and_defaults_off(tmp_path):
    # Fast pair plays 80-attempt games, slow pair 56-attempt games, equal scoring.
    events = []
    history = []
    for i in range(8):
        day = (NOW - timedelta(days=i + 2)).date().isoformat()
        events.append((f"f{i}", "Fast Home", "Fast Away", 75, 75, 80, 80, None, None))
        events.append((f"s{i}", "Slow Home", "Slow Away", 75, 75, 56, 56, None, None))
        _scoreboard(tmp_path / f"d{i}", day, events[-2:])
        history.append(GameRecord(f"f{i}", f"{day}T23:00:00Z", "NCAAB", "Fast Away", "Fast Home", 75, 75))
        history.append(GameRecord(f"s{i}", f"{day}T23:00:00Z", "NCAAB", "Slow Away", "Slow Home", 75, 75))
    root = tmp_path / "all"
    root.mkdir()
    for i in range(8):
        day = (NOW - timedelta(days=i + 2)).date().isoformat()
        (root / f"{day}").mkdir()
        (root / day / "scores_ncaab.json").write_text(
            (tmp_path / f"d{i}" / day / "scores_ncaab.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
    attempts = NCAABTotalModelV6(tempo_source="attempts", pace_sensitivity=1.0, stats_root=root)
    points = NCAABTotalModelV6(stats_root=root)
    fast = UpcomingGame("g1", NOW.isoformat(), "Fast Away", "Fast Home", total_line=150.5)
    slow = UpcomingGame("g2", NOW.isoformat(), "Slow Away", "Slow Home", total_line=150.5)
    pa = {p.event_id: p for p in attempts.predict_totals(history, [fast, slow])}
    pp = {p.event_id: p for p in points.predict_totals(history, [fast, slow])}
    assert pa["g1"].feature_basis["tempo_source"] == "attempts"
    assert (
        pa["g1"].feature_basis["tempo_home"]
        > pa["g1"].feature_basis["league_tempo"]
        > pa["g2"].feature_basis["tempo_home"]
    )
    assert pa["g1"].feature_basis["expected_pace"] > pa["g2"].feature_basis["expected_pace"]
    # Points cannot tell the pairs apart (identical scoring), attempts can.
    assert pp["g1"].feature_basis["expected_pace"] == pytest.approx(
        pp["g2"].feature_basis["expected_pace"], abs=0.2
    )
    assert pp["g1"].feature_basis["tempo_source"] == "points" and pp["g1"].feature_basis["tempo_home"] is None
    with pytest.raises(ValueError):
        NCAABTotalModelV6(tempo_source="shots")


def test_new_feature_parameters_default_off_in_from_artifact():
    spread = NCAABSpreadModelV6.from_artifact({})
    total = NCAABTotalModelV6.from_artifact({})
    assert spread.non_d1_prior_from_history is False and spread.rank_prior_per_slot == 0.0
    assert total.tempo_source == "points" and total.pace_sensitivity == 0.55


# --------------------------------------------------------------------------- possessions (box scores)

from model_prediction.features.ncaab_possessions import NCAABPossessionStore


def _boxscore(
    root: Path, day: str, event_id: str, home: str, away: str, fga: int, oreb: int, tov: int, fta: int
) -> None:
    (root / day).mkdir(parents=True, exist_ok=True)
    rec = {
        "schema": "ncaab-boxscore-components-v1",
        "status": "completed",
        "event_id": event_id,
        "event_start_utc": f"{day}T23:00Z",
        "observed_at_utc": "2026-11-13T00:00:00+00:00",
        "source": "espn:NCAAB:summary",
        "home": {
            "team_name": home,
            "team_id": "1",
            "points": 75,
            "field_goal_attempts": fga,
            "free_throw_attempts": fta,
            "offensive_rebounds": oreb,
            "turnovers": tov,
        },
        "away": {
            "team_name": away,
            "team_id": "2",
            "points": 75,
            "field_goal_attempts": fga,
            "free_throw_attempts": fta,
            "offensive_rebounds": oreb,
            "turnovers": tov,
        },
    }
    (root / day / f"{event_id}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_possession_store_applies_the_standard_formula_and_keys_by_history(tmp_path):
    _boxscore(
        tmp_path, "2026-11-08", "e1", "Duke Blue Devils", "Rider Broncs", fga=60, oreb=10, tov=12, fta=20
    )
    store = NCAABPossessionStore(tmp_path)
    rec = store.get("e1")
    assert rec is not None
    assert rec.home.possessions == pytest.approx(60 - 10 + 12 + 0.44 * 20)
    assert rec.game_possessions == pytest.approx(rec.home.possessions)
    assert rec.efficiency("home") == pytest.approx(100 * 75 / rec.game_possessions)
    g1 = GameRecord("e1", "2026-11-08T23:00:00Z", "NCAAB", "Rider Broncs", "Duke Blue Devils", 75, 75)
    g2 = GameRecord("e2", "2026-11-09T23:00:00Z", "NCAAB", "Rider Broncs", "Duke Blue Devils", 70, 80)
    assert [r.event_id for _g, r in store.for_history([g1, g2])] == ["e1"]  # e2 has no box score
    assert store.coverage([g1, g2]) == 0.5
    assert NCAABPossessionStore(tmp_path / "nowhere").get("e1") is None


def test_possessions_tempo_source_drives_pace_like_attempts_and_falls_back_when_absent(tmp_path):
    history = []
    for i in range(8):
        day = (NOW - timedelta(days=i + 2)).date().isoformat()
        _boxscore(
            tmp_path, day, f"f{i}", "Fast Home", "Fast Away", fga=70, oreb=10, tov=16, fta=20
        )  # ~84.8 poss
        _boxscore(
            tmp_path, day, f"s{i}", "Slow Home", "Slow Away", fga=50, oreb=10, tov=8, fta=10
        )  # ~52.4 poss
        history.append(GameRecord(f"f{i}", f"{day}T23:00:00Z", "NCAAB", "Fast Away", "Fast Home", 75, 75))
        history.append(GameRecord(f"s{i}", f"{day}T23:00:00Z", "NCAAB", "Slow Away", "Slow Home", 75, 75))
    model = NCAABTotalModelV6(tempo_source="possessions", pace_sensitivity=1.0, boxscore_root=tmp_path)
    fast = UpcomingGame("g1", NOW.isoformat(), "Fast Away", "Fast Home", total_line=150.5)
    slow = UpcomingGame("g2", NOW.isoformat(), "Slow Away", "Slow Home", total_line=150.5)
    preds = {p.event_id: p for p in model.predict_totals(history, [fast, slow])}
    assert preds["g1"].feature_basis["tempo_source"] == "possessions"
    assert (
        preds["g1"].feature_basis["tempo_home"]
        > preds["g1"].feature_basis["league_tempo"]
        > preds["g2"].feature_basis["tempo_home"]
    )
    assert preds["g1"].feature_basis["expected_pace"] > preds["g2"].feature_basis["expected_pace"]
    # No box scores at all -> no tempo table -> falls back to the points-based pace, no crash.
    empty = NCAABTotalModelV6(tempo_source="possessions", boxscore_root=tmp_path / "empty")
    p = empty.predict_totals(history, [fast])[0]
    points_pace = NCAABTotalModelV6().predict_totals(history, [fast])[0].feature_basis["expected_pace"]
    assert p.feature_basis["tempo_home"] is None and p.feature_basis["expected_pace"] == points_pace
