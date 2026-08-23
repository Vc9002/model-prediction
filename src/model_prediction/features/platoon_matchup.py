"""MLB Point-in-Time Platoon Splits & Arsenal Matchup Feature Engine.

Quantifies handedness asymmetries between starting pitchers (LHP/RHP) and
opposing lineup point-in-time empirical-Bayes offensive profiles (wOBA vs LHP/RHP, ISO vs LHP/RHP, K%).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import PROJECT_ROOT

DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"

LEAGUE_WOBA_VS_LHP = 0.315
LEAGUE_WOBA_VS_RHP = 0.318
LEAGUE_ISO_VS_LHP = 0.155
LEAGUE_ISO_VS_RHP = 0.160


from .batter_priors import BatterPriorEngine, LineupPriorVector


@dataclass(slots=True)
class PlatoonProfile:
    """Handedness-specific point-in-time batting and pitching profiles."""

    team_name: str
    woba_vs_lhp: float
    woba_vs_rhp: float
    iso_vs_lhp: float
    iso_vs_rhp: float
    k_pct_vs_lhp: float
    k_pct_vs_rhp: float


def compute_lineup_platoon_matchup(
    engine: BatterPriorEngine,
    home_team_id: str,
    away_team_id: str,
    home_sp_hand: str,  # "L" or "R"
    away_sp_hand: str,  # "L" or "R"
    as_of_date: str,
    lookback_games: int = 15,
) -> dict[str, float]:
    """Compute real lineup-level platoon matchup differentials against starting pitcher handedness."""
    # Home offense faces away starter's handedness
    home_vec: LineupPriorVector = engine.evaluate_projected_team_offense(
        team_id=home_team_id,
        as_of_date=as_of_date,
        lookback_games=lookback_games,
        opposing_pitcher_hand=away_sp_hand,
    )
    # Away offense faces home starter's handedness
    away_vec: LineupPriorVector = engine.evaluate_projected_team_offense(
        team_id=away_team_id,
        as_of_date=as_of_date,
        lookback_games=lookback_games,
        opposing_pitcher_hand=home_sp_hand,
    )

    woba_gap = round(home_vec.xwoba - away_vec.xwoba, 4)
    k_gap = round(home_vec.k_pct - away_vec.k_pct, 4)
    iso_gap = round(home_vec.iso - away_vec.iso, 4)
    sample_pa = home_vec.sample_pa + away_vec.sample_pa
    available = 1.0 if sample_pa >= 50 else 0.0

    return {
        "home_lineup_woba_vs_sp_hand": home_vec.xwoba,
        "away_lineup_woba_vs_sp_hand": away_vec.xwoba,
        "platoon_woba_gap": woba_gap,
        "home_lineup_k_pct_vs_sp_hand": home_vec.k_pct,
        "away_lineup_k_pct_vs_sp_hand": away_vec.k_pct,
        "platoon_k_pct_gap": k_gap,
        "home_lineup_iso_vs_sp_hand": home_vec.iso,
        "away_lineup_iso_vs_sp_hand": away_vec.iso,
        "platoon_iso_gap": iso_gap,
        "platoon_sample_pa": float(sample_pa),
        "platoon_available": available,
    }


def estimate_team_platoon_profile(
    team_name: str,
    as_of: datetime,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> PlatoonProfile:
    """Estimate handedness-specific point-in-time batting profile."""
    date_str = as_of.strftime("%Y-%m-%d")
    engine = BatterPriorEngine(snapshot_path=snapshot_path)
    vec_lhp = engine.evaluate_projected_team_offense(
        team_id=team_name,
        as_of_date=date_str,
        opposing_pitcher_hand="L",
    )
    vec_rhp = engine.evaluate_projected_team_offense(
        team_id=team_name,
        as_of_date=date_str,
        opposing_pitcher_hand="R",
    )
    return PlatoonProfile(
        team_name=team_name,
        woba_vs_lhp=vec_lhp.xwoba,
        woba_vs_rhp=vec_rhp.xwoba,
        iso_vs_lhp=vec_lhp.iso,
        iso_vs_rhp=vec_rhp.iso,
        k_pct_vs_lhp=vec_lhp.k_pct,
        k_pct_vs_rhp=vec_rhp.k_pct,
    )


def platoon_matchup_gaps(
    home_team: str,
    away_team: str,
    home_starter_throws: str,  # "L" or "R"
    away_starter_throws: str,  # "L" or "R"
    as_of: datetime,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, float]:
    """Compatibility interface for legacy callers."""
    date_str = as_of.strftime("%Y-%m-%d")
    engine = BatterPriorEngine(snapshot_path=snapshot_path)
    res = compute_lineup_platoon_matchup(
        engine=engine,
        home_team_id=home_team,
        away_team_id=away_team,
        home_sp_hand=home_starter_throws,
        away_sp_hand=away_starter_throws,
        as_of_date=date_str,
    )
    res["platoon_woba_advantage"] = res["platoon_woba_gap"]
    res["platoon_iso_advantage"] = res["platoon_iso_gap"]
    res["home_offense_matchup_woba"] = res["home_lineup_woba_vs_sp_hand"]
    return res
