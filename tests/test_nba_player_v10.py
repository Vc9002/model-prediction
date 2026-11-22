"""nba-player-v10 player layer: point-in-time and fail-closed behaviour (synthetic data)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from model_prediction.features.nba_player_ratings import (
    HOLDOUT_START,
    N_STATS,
    NO_CALL_INJURY_GAME_ABSENT,
    NO_CALL_INJURY_NAME_AMBIGUOUS,
    NO_CALL_INJURY_NOT_SUBMITTED,
    NO_CALL_INJURY_REPORT_MISSING,
    BoxPrior,
    GameBox,
    InjuryReportIndex,
    MinutesModel,
    PlayerLayerState,
    PlayerLine,
    RatingParams,
    game_features,
    load_box_games,
    match_report,
    normalize_name,
    walk,
)
from model_prediction.models.nba_player_v10 import fit, predict

TEAMS = ("1", "2", "3", "4")
NAMES = {t: f"Team {t}" for t in TEAMS}
PLAYERS_PER_TEAM = 9


def _player_name(team: str, k: int) -> str:
    return f"Player{k} Team{team}"


def _side(rng: np.random.Generator, team: str, home: bool) -> list[PlayerLine]:
    minutes = np.full(PLAYERS_PER_TEAM, 240.0 / PLAYERS_PER_TEAM)
    out = []
    for k in range(PLAYERS_PER_TEAM):
        stats = tuple(float(x) for x in rng.poisson(3.0, N_STATS))
        out.append(PlayerLine(f"{team}-{k}", _player_name(team, k), home, float(minutes[k]), k < 5, stats))
    return out


def _games(start: date, days: int, seed: int = 7) -> list[GameBox]:
    rng = np.random.default_rng(seed)
    games = []
    for d in range(days):
        day = start + timedelta(days=d)
        pairs = (("1", "2"), ("3", "4")) if d % 2 == 0 else (("1", "3"), ("2", "4"))
        for home, away in pairs:
            hs, as_ = int(rng.integers(95, 125)), int(rng.integers(95, 125))
            games.append(
                GameBox(
                    event_id=f"{day.isoformat()}-{home}-{away}",
                    date=day.isoformat(),
                    start_utc=f"{day.isoformat()}T23:30:00Z",
                    season_year=2025,
                    season_type="regular-season",
                    home_id=home,
                    away_id=away,
                    home_name=NAMES[home],
                    away_name=NAMES[away],
                    home_score=hs,
                    away_score=as_,
                    players=tuple(_side(rng, home, True) + _side(rng, away, False)),
                )
            )
    return games


PRIOR = BoxPrior(
    gamma=tuple(0.1 * ((-1) ** i) for i in range(N_STATS)),
    delta=tuple(0.05 for _ in range(N_STATS)),
    hca=2.0,
    total_const=-10.0,
    fringe_rates=tuple(3.0 for _ in range(N_STATS)),
)
MINUTES = MinutesModel(
    blowout_starter=-0.01,
    blowout_bench=0.005,
    p_play={
        "unknown|full": 0.95,
        "unknown|mid": 0.7,
        "unknown|low": 0.4,
        "unknown|zero": 0.1,
        "unlisted|*": 0.9,
        "out|*": 0.0,
        "questionable|*": 0.5,
        "available|*": 0.97,
    },
)


def _state() -> PlayerLayerState:
    return PlayerLayerState(RatingParams(120.0, (10.0,), (30.0,)), PRIOR, MINUTES)


def _capture(games: list[GameBox], day: str, injuries: InjuryReportIndex | None = None) -> dict:
    captured: dict = {}

    def on_day(d: str, day_games: list[GameBox], st: PlayerLayerState) -> None:
        if d != day:
            return
        captured["ratings_m"] = st.ratings_m[0].copy()
        captured["ratings_t"] = st.ratings_t[0].copy()
        captured["features"] = {g.event_id: game_features(st, g, injuries) for g in day_games}

    walk(games, _state(), start=day, end=HOLDOUT_START, on_day=on_day)
    return captured


def test_ratings_as_of_date_ignore_games_on_or_after_that_date() -> None:
    base = _games(date(2025, 11, 1), 20)
    day = "2025-11-15"
    before = [g for g in base if g.date < day]
    on_day_games = [g for g in base if g.date == day]
    reference = _capture(before + on_day_games, day)
    # Append later games AND rewrite the same-day games' box scores and results.
    later = _games(date(2025, 11, 16), 10, seed=99)
    rewritten = [
        replace(
            g,
            home_score=g.home_score + 40,
            players=tuple(
                replace(p, minutes=p.minutes * (0.0 if i % 3 == 0 else 1.5))
                for i, p in enumerate(g.players or ())
            ),
        )
        for g in on_day_games
    ]
    perturbed = _capture(before + rewritten + later, day)
    np.testing.assert_array_equal(reference["ratings_m"], perturbed["ratings_m"])
    np.testing.assert_array_equal(reference["ratings_t"], perturbed["ratings_t"])
    assert reference["features"] == perturbed["features"]
    # Sanity: the state is not frozen, a game BEFORE the date does move the ratings.
    shifted = [replace(g, home_score=g.home_score + 30) if g.date == "2025-11-14" else g for g in before]
    moved = _capture(shifted + on_day_games, day)
    assert not np.array_equal(reference["ratings_m"], moved["ratings_m"])


def _index(reports: list[tuple[str, list[str]]], teams: dict) -> InjuryReportIndex:
    return InjuryReportIndex([{"game_date": "2025-11-15", "reports": reports, "teams": teams}])


def test_injury_rows_observed_after_decision_time_are_ignored() -> None:
    games = _games(date(2025, 11, 1), 15)
    day = "2025-11-15"
    game = next(g for g in games if g.date == day)  # tips 23:30Z, decision 22:30Z
    matchup = f"{game.away_name}@{game.home_name}"
    star = _player_name(game.home_id, 0)
    last, first = star.split(" ")[1], star.split(" ")[0]
    early = _index(
        [["2025-11-15T17:30:00Z", [matchup]], ["2025-11-15T22:45:00Z", [matchup]]],
        {
            game.home_name: [
                ["2025-11-15T17:30:00Z", []],
                ["2025-11-15T22:45:00Z", [[f"{last}, {first}", "Out"]]],
            ]
        },
    )
    none = _index([["2025-11-15T17:30:00Z", [matchup]]], {game.home_name: [["2025-11-15T17:30:00Z", []]]})
    late_out = _capture(games, day, early)["features"][game.event_id]["variants"]["avail"]
    no_report = _capture(games, day, none)["features"][game.event_id]["variants"]["avail"]
    assert late_out == no_report
    # The same Out row observed before the decision time does change the projection.
    in_time = _index(
        [["2025-11-15T17:30:00Z", [matchup]], ["2025-11-15T22:15:00Z", [matchup]]],
        {
            game.home_name: [
                ["2025-11-15T17:30:00Z", []],
                ["2025-11-15T22:15:00Z", [[f"{last}, {first}", "Out"]]],
            ]
        },
    )
    out_row = _capture(games, day, in_time)["features"][game.event_id]["variants"]["avail"]
    assert out_row["S_home"] != no_report["S_home"]


def test_team_report_fail_closed_reasons() -> None:
    from model_prediction.features.nba_player_ratings import parse_utc

    decision = parse_utc("2025-11-15T22:30:00Z")
    idx = _index(
        [["2025-11-15T17:30:00Z", ["A@B"]], ["2025-11-15T21:30:00Z", ["A@B"]]],
        {
            "B": [["2025-11-15T17:30:00Z", [["Doe, John", "Questionable"]]], ["2025-11-15T21:30:00Z", None]],
            "A": [["2025-11-15T17:30:00Z", None]],
        },
    )
    # NOT YET SUBMITTED falls back to the latest submitted content for the same game date.
    assert idx.team_report("2025-11-15", "A", "B", "B", decision).players == (("Doe, John", "questionable"),)
    assert idx.team_report("2025-11-15", "A", "B", "A", decision).reason == NO_CALL_INJURY_NOT_SUBMITTED
    assert idx.team_report("2025-11-15", "C", "D", "D", decision).reason == NO_CALL_INJURY_GAME_ABSENT
    assert idx.team_report("2025-11-16", "A", "B", "B", decision).reason == NO_CALL_INJURY_REPORT_MISSING
    stale = parse_utc("2025-11-16T22:30:00Z")
    assert idx.team_report("2025-11-15", "A", "B", "B", stale).reason == NO_CALL_INJURY_REPORT_MISSING
    early = parse_utc("2025-11-15T12:00:00Z")
    assert idx.team_report("2025-11-15", "A", "B", "B", early).reason == NO_CALL_INJURY_REPORT_MISSING


def test_name_matching_and_ambiguity() -> None:
    assert normalize_name("Oubre Jr., Kelly") == normalize_name("Kelly Oubre Jr.") == "kelly oubre"
    assert normalize_name("Jokić, Nikola") == "nikola jokic"
    cands = {"1": "Kelly Oubre Jr.", "2": "Nikola Jokic", "3": "Jalen Williams", "4": "Jaylin Williams"}
    matched, unmatched = match_report(cands, [("Oubre Jr., Kelly", "out"), ("Nobody, Some", "out")])
    assert matched == {"1": "out"} and unmatched == 1
    # Initial + last name fallback that hits two candidates is ambiguous -> NO_CALL.
    assert match_report(cands, [("Williams, J.", "out")])[0] is None


def test_out_player_minutes_go_to_rotation_and_replacement() -> None:
    games = _games(date(2025, 11, 1), 15)
    day = "2025-11-15"
    game = next(g for g in games if g.date == day)
    state = _state()
    seen: dict = {}

    def on_day(d: str, day_games: list[GameBox], st: PlayerLayerState) -> None:
        if d == day:
            from model_prediction.features.nba_player_ratings import TeamReport

            seen["base"] = st.project_team(game.home_id, 2025, TeamReport((), None, None))
            names = [(f"Team{game.home_id}, Player{k}", "out") for k in range(4)]
            seen["out"] = st.project_team(game.home_id, 2025, TeamReport(tuple(names), None, None))
            seen["ambiguous"] = st.project_team(
                game.home_id, 2025, TeamReport((("Team9, P", "out"),), None, None)
            )

    walk(games, state, start=day, end="2025-11-16", on_day=on_day)
    base, out = seen["base"], seen["out"]
    assert base.reason is None and out.reason is None
    assert math.isclose(sum(base.minutes.values()) + base.replacement_minutes, 240.0)
    assert math.isclose(sum(out.minutes.values()) + out.replacement_minutes, 240.0)
    for k in range(4):
        assert out.minutes[f"{game.home_id}-{k}"] == 0.0
    # Available players absorb minutes up to the 1.25 cap; the rest is replacement level.
    assert out.minutes[f"{game.home_id}-8"] > base.minutes[f"{game.home_id}-8"]
    assert out.replacement_minutes > 0.0
    assert seen["ambiguous"].reason is None  # unmatched name is ignored, not ambiguous
    assert NO_CALL_INJURY_NAME_AMBIGUOUS.startswith("NO_CALL_")


def test_loaders_refuse_the_locked_holdout(tmp_path) -> None:
    path = tmp_path / "box.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        load_box_games(path, end_date="2026-03-01")
    with pytest.raises(ValueError):
        walk([], _state(), start="2025-11-01", end="2026-03-01", on_day=lambda *a: None)


def test_team_model_fit_recovers_coefficients() -> None:
    rng = np.random.default_rng(3)
    rows = []
    for _ in range(2000):
        s_h, s_a = rng.normal(0, 5, 2)
        u_h, u_a = rng.normal(0, 3, 2)
        flags = rng.integers(0, 2, 4).astype(bool)
        row = {
            "variants": {
                "exavail": {"reason": None, "S_home": s_h, "S_away": s_a, "U_home": u_h, "U_away": u_a}
            },
            "home_b2b": flags[0],
            "away_b2b": flags[1],
            "home_3in4": flags[2] and not flags[0],
            "away_3in4": flags[3] and not flags[1],
            "playoff": False,
            "poss_hat": 100.0 + rng.normal(0, 2),
            "eff_base": 114.0,
            "total_const": -10.0,
        }
        margin = 2.0 + 0.9 * (s_h - s_a) + 1.5 * (float(flags[1]) - float(flags[0])) + rng.normal(0, 1)
        raw = row["poss_hat"] * (2 * 114.0 - 10.0 + u_h + u_a) / 100.0
        total = 5.0 + 0.95 * raw - 1.0 * (float(flags[0]) + float(flags[1])) + rng.normal(0, 1)
        row["home_score"] = (total + margin) / 2
        row["away_score"] = (total - margin) / 2
        rows.append(row)
    coefs = fit(rows, "exavail")
    assert coefs.margin[0] == pytest.approx(2.0, abs=0.15)
    assert coefs.margin[1] == pytest.approx(0.9, abs=0.02)
    assert coefs.total[1] == pytest.approx(0.95, abs=0.05)
    nocall = {**rows[0], "variants": {"exavail": {"reason": "NO_CALL_BOX_GAP"}}}
    assert predict(nocall, coefs) == (None, None)
