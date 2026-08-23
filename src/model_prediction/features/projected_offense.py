"""Point-in-time projected offense feature engine for MLB.

Computes pregame projected team offense from preceding games without target-game
batting order leakage. Combines Empirical Bayes batter priors with projected
plate appearance weights to generate matchup differential features.
"""

from __future__ import annotations

from dataclasses import dataclass

from .batter_priors import BatterPriorEngine, LineupPriorVector


@dataclass(frozen=True)
class ProjectedOffenseFeatureVector:
    """Team-level projected offense state vector."""

    team_id: str
    quality_xwoba: float
    k_pct: float
    bb_pct: float
    k_minus_bb_pct: float
    power_iso: float
    barrel_pct: float
    hard_hit_pct: float
    sample_strength_pa: int
    opposing_pitcher_hand: str = "all"


def compute_team_projected_offense(
    engine: BatterPriorEngine,
    team_id: str,
    as_of_date: str,
    lookback_games: int = 15,
    opposing_pitcher_hand: str | None = None,
) -> ProjectedOffenseFeatureVector:
    """Compute projected team offense vector strictly as of a pregame cutoff date."""
    lineup_vec: LineupPriorVector = engine.evaluate_projected_team_offense(
        team_id=team_id,
        as_of_date=as_of_date,
        lookback_games=lookback_games,
        opposing_pitcher_hand=opposing_pitcher_hand,
    )
    k_minus_bb = round(lineup_vec.k_pct - lineup_vec.bb_pct, 4)
    return ProjectedOffenseFeatureVector(
        team_id=team_id,
        quality_xwoba=lineup_vec.xwoba,
        k_pct=lineup_vec.k_pct,
        bb_pct=lineup_vec.bb_pct,
        k_minus_bb_pct=k_minus_bb,
        power_iso=lineup_vec.iso,
        barrel_pct=lineup_vec.barrel_pct,
        hard_hit_pct=lineup_vec.hard_hit_pct,
        sample_strength_pa=lineup_vec.sample_pa,
        opposing_pitcher_hand=opposing_pitcher_hand or "all",
    )


def projected_offense_matchup_gaps(
    engine: BatterPriorEngine,
    home_team_id: str,
    away_team_id: str,
    as_of_date: str,
    home_sp_hand: str | None = None,
    away_sp_hand: str | None = None,
) -> dict[str, float]:
    """Compute home vs away differential features for the projected offense family."""
    # Home offense faces away starting pitcher's hand
    home_off = compute_team_projected_offense(
        engine, home_team_id, as_of_date, opposing_pitcher_hand=away_sp_hand
    )
    # Away offense faces home starting pitcher's hand
    away_off = compute_team_projected_offense(
        engine, away_team_id, as_of_date, opposing_pitcher_hand=home_sp_hand
    )

    quality_gap = round(home_off.quality_xwoba - away_off.quality_xwoba, 4)
    k_gap = round(home_off.k_pct - away_off.k_pct, 4)
    bb_gap = round(home_off.bb_pct - away_off.bb_pct, 4)
    kbb_gap = round(home_off.k_minus_bb_pct - away_off.k_minus_bb_pct, 4)
    power_gap = round(home_off.power_iso - away_off.power_iso, 4)
    sample_strength = home_off.sample_strength_pa + away_off.sample_strength_pa

    return {
        "projected_offense_quality_gap": quality_gap,
        "projected_offense_k_pct_gap": k_gap,
        "projected_offense_bb_pct_gap": bb_gap,
        "projected_offense_k_minus_bb_pct_gap": kbb_gap,
        "projected_offense_kbb_gap": kbb_gap,
        "projected_offense_power_gap": power_gap,
        "projected_offense_sample_strength": float(sample_strength),
        "home_projected_xwoba": home_off.quality_xwoba,
        "away_projected_xwoba": away_off.quality_xwoba,
        "home_projected_k_pct": home_off.k_pct,
        "away_projected_k_pct": away_off.k_pct,
        "home_projected_bb_pct": home_off.bb_pct,
        "away_projected_bb_pct": away_off.bb_pct,
        "home_projected_iso": home_off.power_iso,
        "away_projected_iso": away_off.power_iso,
    }
