"""Regression tests for the MLB v9 feature-table lookup bug (DEBUG.md 2026-08-26).

Thirteen v9 feature-table columns were dead (zero variance -- every game got the
identical league-prior fallback). Root causes, all in how the snapshot-driven
feature engines build their lookup keys vs. how callers query them:

1. batter_priors._load_from_snapshot read flat keys the real snapshot schema
   does not have (``home_team``/``game_date``), so every batter was registered
   under an empty team id and every projected-offense / platoon query missed.
2. bullpen_state._load_from_snapshots keyed reliever appearances by the numeric
   MLB Stats API team_id while the engine's public API (evaluate_matchup) is
   called with full team names.
3. The feature-table builder never passed starter names (neither the games file
   nor the walk-forward rows carry them), so starter_state always fell back to
   league priors; and both callers read ``starter_k_bb_gap`` while the engine
   actually returns ``starter_k_minus_bb_pct_gap``.

These tests exercise the real snapshot schema (nested home/away dicts with
team_name / players / game_start_utc) and fail on the unfixed code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from model_prediction.features.batter_priors import PRIOR_HYPERPARAMETERS, BatterPriorEngine
from model_prediction.features.bullpen_state import PointInTimeBullpenEngine
from model_prediction.features.mlb_v9_features import extract_mlb_v9_features
from model_prediction.features.platoon_matchup import platoon_matchup_gaps
from model_prediction.features.projected_offense import projected_offense_matchup_gaps


def _player(
    pid: int,
    name: str,
    *,
    pitching_order: int | None = None,
    pitch_hand: str = "R",
    batting: dict | None = None,
    pitching: dict | None = None,
) -> dict:
    return {
        "player_id": pid,
        "name": name,
        "pitch_hand": pitch_hand,
        "pitching_order": pitching_order,
        "batting": batting or {},
        "pitching": pitching or {},
    }


def _starter_pitching(k: int, bb: int, ip: str) -> dict:
    return {
        "inningsPitched": ip,
        "earnedRuns": 0.0,
        "strikeOuts": float(k),
        "baseOnBalls": float(bb),
        "homeRuns": 0.0,
        "battersFaced": float(int(float(ip)) * 4 + 3),
    }


def _reliever_pitching(k: int, bb: int, ip: str) -> dict:
    return _starter_pitching(k, bb, ip)


def _snapshot_line(
    game_start_utc: str,
    *,
    home_name: str,
    away_name: str,
    home_players: list[dict],
    away_players: list[dict],
    home_pitcher_order: list[int],
    away_pitcher_order: list[int],
    home_probable: tuple[int, str, str],
    away_probable: tuple[int, str, str],
) -> str:
    """One game_snapshots.jsonl record in the real collector schema."""
    home_pid, home_pname, _ = home_probable
    away_pid, away_pname, _ = away_probable
    return json.dumps(
        {
            "game_start_utc": game_start_utc,
            "status": "Final",
            "home": {
                "team_id": 111,
                "team_name": home_name,
                "pitcher_order": home_pitcher_order,
                "players": home_players,
                "probable_pitcher_id": home_pid,
                "probable_pitcher_name": home_pname,
            },
            "away": {
                "team_id": 222,
                "team_name": away_name,
                "pitcher_order": away_pitcher_order,
                "players": away_players,
                "probable_pitcher_id": away_pid,
                "probable_pitcher_name": away_pname,
            },
        }
    )


def _write_fixture(tmp_path, *, include_target: bool = True) -> str:
    """2026-05-01, 2026-05-02 (history) and 2026-05-10 (target).

    Home Team is strong (batters hit HRs, starter strikes out 8), Away Team is
    weak (no hits, starter strikes out 2, bullpen is worse). The target game
    carries a 4-HR slugger and a left-handed away probable starter.
    """
    home_bat = {
        "plateAppearances": 5.0,
        "atBats": 4.0,
        "hits": 2.0,
        "doubles": 0.0,
        "triples": 0.0,
        "homeRuns": 1.0,
        "baseOnBalls": 1.0,
        "strikeOuts": 0.0,
    }
    away_bat = {
        "plateAppearances": 5.0,
        "atBats": 5.0,
        "hits": 0.0,
        "doubles": 0.0,
        "triples": 0.0,
        "homeRuns": 0.0,
        "baseOnBalls": 0.0,
        "strikeOuts": 3.0,
    }
    hist_home_players = [
        _player(11, "Home Starter", pitching_order=1, pitching=_starter_pitching(8, 1, "6.0")),
        _player(12, "Home Reliever", pitching_order=2, pitching=_reliever_pitching(4, 0, "2.0")),
        _player(31, "Home Batter", batting=home_bat),
    ]
    hist_away_players = [
        _player(21, "Away Starter", pitching_order=1, pitching=_starter_pitching(2, 3, "6.0")),
        _player(22, "Away Reliever", pitching_order=2, pitching=_reliever_pitching(0, 0, "2.0")),
        _player(41, "Away Batter", batting=away_bat),
    ]
    lines = [
        _snapshot_line(
            "2026-05-01T23:05:00Z",
            home_name="Home Team",
            away_name="Away Team",
            home_players=hist_home_players,
            away_players=hist_away_players,
            home_pitcher_order=[11],
            away_pitcher_order=[21],
            home_probable=(11, "Home Starter", "R"),
            away_probable=(21, "Away Starter", "R"),
        ),
        _snapshot_line(
            "2026-05-02T23:05:00Z",
            home_name="Home Team",
            away_name="Away Team",
            home_players=hist_home_players,
            away_players=hist_away_players,
            home_pitcher_order=[11],
            away_pitcher_order=[21],
            home_probable=(11, "Home Starter", "R"),
            away_probable=(21, "Away Starter", "R"),
        ),
    ]
    if include_target:
        # Target game: must not leak into priors for the two history games.
        lines.append(
            _snapshot_line(
                "2026-05-10T23:05:00Z",
                home_name="Home Team",
                away_name="Away Team",
                home_players=[
                    _player(
                        51,
                        "Target Slugger",
                        batting={
                            "plateAppearances": 4.0,
                            "atBats": 4.0,
                            "hits": 4.0,
                            "doubles": 0.0,
                            "triples": 0.0,
                            "homeRuns": 4.0,
                            "baseOnBalls": 0.0,
                            "strikeOuts": 0.0,
                        },
                    )
                ],
                away_players=[
                    _player(
                        21,
                        "Away Starter",
                        pitching_order=1,
                        pitch_hand="L",
                        pitching=_starter_pitching(2, 3, "6.0"),
                    )
                ],
                home_pitcher_order=[11],
                away_pitcher_order=[21],
                home_probable=(11, "Home Starter", "R"),
                away_probable=(21, "Away Starter", "L"),
            )
        )
    path = tmp_path / "game_snapshots.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_projected_offense_lookup_uses_real_team_key(tmp_path) -> None:
    """Batter priors must resolve real per-team projected offense, not the
    league-prior constant that an empty team-history index produces."""
    snap = _write_fixture(tmp_path)
    engine = BatterPriorEngine(snapshot_path=snap)
    home = engine.evaluate_projected_team_offense("Home Team", as_of_date="2026-05-10")
    away = engine.evaluate_projected_team_offense("Away Team", as_of_date="2026-05-10")
    # Home hit HRs in history; away did not. Both must see real PA history.
    assert home.sample_pa > 0
    assert away.sample_pa > 0
    assert home.xwoba != PRIOR_HYPERPARAMETERS["xwoba"][0]
    assert home.xwoba > away.xwoba


def test_projected_offense_gap_features_nonzero(tmp_path) -> None:
    snap = _write_fixture(tmp_path)
    engine = BatterPriorEngine(snapshot_path=snap)
    gaps = projected_offense_matchup_gaps(engine, "Home Team", "Away Team", "2026-05-10")
    # Pre-fix these were all 0.0/league priors for every game.
    assert gaps["projected_offense_quality_gap"] != 0.0
    assert gaps["home_projected_xwoba"] != PRIOR_HYPERPARAMETERS["xwoba"][0]


def test_bullpen_lookup_by_team_name(tmp_path) -> None:
    snap = _write_fixture(tmp_path)
    bp = PointInTimeBullpenEngine(snapshot_path=snap)
    adv = bp.evaluate_matchup("Home Team", "Away Team", "2026-05-10")
    # Pre-fix the name-keyed lookup missed and fell back to the neutral
    # bullpen (0 active relievers, league-prior FIP 3.90).
    assert adv.home_state.active_relievers_count > 0
    assert adv.away_state.active_relievers_count > 0
    assert adv.home_state.available_fip != 3.90
    assert adv.fip_gap != 0.0


def test_platoon_gaps_nonzero(tmp_path) -> None:
    snap = _write_fixture(tmp_path)
    as_of = datetime(2026, 5, 10, 23, 5, tzinfo=UTC)
    gaps = platoon_matchup_gaps("Home Team", "Away Team", "R", "L", as_of, snapshot_path=snap)
    # Pre-fix both were exactly 0.0 (league priors on both sides).
    assert gaps["platoon_woba_advantage"] != 0.0
    assert gaps["platoon_iso_advantage"] != 0.0


def test_starter_kbb_gap_uses_engine_key(tmp_path) -> None:
    """extract_mlb_v9_features must read the starter engine's real dict key
    (starter_k_minus_bb_pct_gap). Pre-fix it read starter_k_bb_gap and got the
    0.0 default even when both starters had real history."""
    snap = _write_fixture(tmp_path)
    as_of = datetime(2026, 5, 10, 23, 5, tzinfo=UTC)
    vec = extract_mlb_v9_features(
        home_team="Home Team",
        away_team="Away Team",
        as_of=as_of,
        home_starter_name="Home Starter",
        away_starter_name="Away Starter",
        home_starter_throws="R",
        away_starter_throws="L",
        snapshot_path=snap,
    )
    assert vec.starter_k_bb_gap != 0.0
    assert vec.starter_k_pct_gap != 0.0
    assert vec.home_expected_starter_ip != 5.3


def test_probable_starter_index_resolves_from_snapshot(tmp_path) -> None:
    """The feature-table builder's snapshot join must resolve probable starter
    names/throws (the games file and walk-forward rows carry none)."""
    from model_prediction.features.mlb_v9_features import load_probable_starter_index

    snap = _write_fixture(tmp_path)
    index = load_probable_starter_index(snap)
    entry = index.get(("2026-05-10T23:05", "Home Team", "Away Team"))
    assert entry is not None
    assert entry["home_starter_name"] == "Home Starter"
    assert entry["away_starter_name"] == "Away Starter"
    assert entry["home_starter_throws"] == "R"
    assert entry["away_starter_throws"] == "L"


def test_snapshot_loader_pit_no_self_leak(tmp_path) -> None:
    """The target game's own boxscore must not enter its own priors (the new
    snapshot loader path must keep the strict-before PIT invariant)."""
    snap = _write_fixture(tmp_path, include_target=True)
    snap_no_target = _write_fixture(tmp_path / "no_target", include_target=False)
    engine = BatterPriorEngine(snapshot_path=snap)
    # 2026-05-10's own 4-HR slugger must not move the projected offense.
    with_self = engine.evaluate_projected_team_offense("Home Team", as_of_date="2026-05-10")
    engine2 = BatterPriorEngine(snapshot_path=snap_no_target)
    without_self = engine2.evaluate_projected_team_offense("Home Team", as_of_date="2026-05-10")
    assert with_self.xwoba == without_self.xwoba
    assert with_self.sample_pa == without_self.sample_pa


def test_builder_games_file_key_join(tmp_path) -> None:
    """The feature-table builder joins the snapshot-derived starter index with
    games-file keys -- (event_start_utc[:16], home_team, away_team) where the
    games file stores minute-precision Z-suffixed starts ("2026-05-10T23:05Z")
    and full team names. The index must resolve under that exact key format;
    pre-fix no starter identity ever reached the starter engine, which is why
    every home_expected_starter_ip cell was the 5.3 league prior."""
    from model_prediction.features.mlb_v9_features import load_probable_starter_index

    snap = _write_fixture(tmp_path)
    index = load_probable_starter_index(snap)
    # Same shape as data/historical/mlb_games_all.jsonl rows the builder
    # reads: event_start_utc at minute precision with a Z suffix.
    games_file_row = {
        "event_id": "401234567",
        "event_start_utc": "2026-05-10T23:05Z",
        "home_team": "Home Team",
        "away_team": "Away Team",
    }
    start_utc = games_file_row["event_start_utc"]
    entry = index.get((start_utc[:16], games_file_row["home_team"], games_file_row["away_team"]))
    assert entry is not None
    assert entry["home_starter_name"] == "Home Starter"
    assert entry["away_starter_name"] == "Away Starter"
    assert entry["away_starter_throws"] == "L"


def test_builder_shared_engine_platoon_equals_wrapper(tmp_path) -> None:
    """The builder de-duplicated platoon_matchup_gaps() -- which constructs a
    fresh BatterPriorEngine per game, the multi-hour rebuild cost -- onto its
    shared batter_engine via compute_lineup_platoon_matchup. Pin that the
    optimized path returns the identical platoon features (value parity,
    not just similarity)."""
    from model_prediction.features.platoon_matchup import (
        compute_lineup_platoon_matchup,
        platoon_matchup_gaps,
    )

    snap = _write_fixture(tmp_path)
    engine = BatterPriorEngine(snapshot_path=snap)
    as_of = datetime(2026, 5, 10, 23, 5, tzinfo=UTC)
    wrapper = platoon_matchup_gaps("Home Team", "Away Team", "R", "L", as_of, snapshot_path=snap)
    shared = compute_lineup_platoon_matchup(
        engine, "Home Team", "Away Team", "R", "L", as_of.strftime("%Y-%m-%d")
    )
    assert wrapper["platoon_woba_advantage"] == shared["platoon_woba_gap"]
    assert wrapper["platoon_iso_advantage"] == shared["platoon_iso_gap"]
