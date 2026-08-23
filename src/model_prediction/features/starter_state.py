"""Comprehensive starting pitcher state vector & expected depth model for MLB.

Represents starting pitchers not as scalar ERA/FIP numbers, but as multi-dimensional
talent vectors (xwOBA allowed, K%, BB%, K-BB%, CSW%, velo, handedness) with
expected starter depth (innings per start) modeling bullpen exposure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .starter_history import (
    DEFAULT_SNAPSHOT_PATH,
    _normalize_name,
    load_starter_index,
    starter_rolling_rates,
)


@dataclass(frozen=True)
class StarterStateVector:
    """Multi-dimensional starting pitcher state representation."""

    pitcher_name: str
    handedness: str  # "R" or "L"
    k_pct: float
    bb_pct: float
    k_minus_bb_pct: float
    csw_pct: float  # Called Strikes + Whiffs %
    xwoba_allowed: float
    fastball_velo: float
    expected_depth_ip: float
    sample_bf: int
    starts_count: int
    status: str = "available"


def estimate_expected_starter_depth(
    starts: list[tuple],
    prior_mean_ip: float = 5.30,
    shrinkage_starts: float = 3.0,
) -> float:
    """Empirical Bayes shrinkage estimate of expected innings pitched per start.

    Combines recent starts' actual IP with league/prior baseline to prevent
    wild volatility from single early exits.
    """
    if not starts:
        return prior_mean_ip
    n = len(starts)
    total_ip = sum(item[1] for item in starts)
    # Shrink toward prior
    w = n / (n + shrinkage_starts)
    obs_ip_per_start = total_ip / max(1, n)
    shrunk_depth = (w * obs_ip_per_start) + ((1.0 - w) * prior_mean_ip)
    return round(max(3.0, min(7.5, shrunk_depth)), 2)


def get_starter_state_vector(
    starter_name: str,
    decision: datetime,
    handedness: str = "R",
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_starts: int = 5,
) -> StarterStateVector:
    """Build PIT starting pitcher state vector strictly prior to decision timestamp."""
    rates = starter_rolling_rates(
        starter_name,
        decision,
        snapshot_path=snapshot_path,
        lookback_starts=lookback_starts,
        minimum_prior_starts=1,
    )

    index = load_starter_index(snapshot_path)
    all_starts = [s for s in index.get(_normalize_name(starter_name), []) if s[0] < decision]
    recent_starts = all_starts[-lookback_starts:] if all_starts else []

    expected_depth = estimate_expected_starter_depth(recent_starts)

    k_pct = rates.get("k_pct") or 0.225
    bb_pct = rates.get("bb_pct") or 0.082
    k_minus_bb = rates.get("k_minus_bb_pct") or 0.143
    # Approximated CSW% from K% correlation (~ 0.15 + 0.6 * K%)
    csw_pct = round(0.15 + 0.60 * k_pct, 4)
    # Approximated xwOBA allowed from K% and BB% (~ 0.38 - 0.5 * K% + 0.4 * BB%)
    xwoba_allowed = round(0.38 - 0.50 * k_pct + 0.40 * bb_pct, 4)

    return StarterStateVector(
        pitcher_name=starter_name,
        handedness=handedness.upper() if handedness in ("R", "L", "r", "l") else "R",
        k_pct=k_pct,
        bb_pct=bb_pct,
        k_minus_bb_pct=k_minus_bb,
        csw_pct=csw_pct,
        xwoba_allowed=xwoba_allowed,
        fastball_velo=93.8,  # baseline MLB average fastball velo
        expected_depth_ip=expected_depth,
        sample_bf=rates.get("batters_faced", 0),
        starts_count=len(recent_starts),
        status=rates["status"],
    )


def starter_state_matchup_gaps(
    home_starter_name: str,
    away_starter_name: str,
    decision: datetime,
    home_sp_hand: str = "R",
    away_sp_hand: str = "R",
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, float]:
    """Compute differential features for starting pitcher state and expected depth."""
    home_sp = get_starter_state_vector(
        home_starter_name, decision, handedness=home_sp_hand, snapshot_path=snapshot_path
    )
    away_sp = get_starter_state_vector(
        away_starter_name, decision, handedness=away_sp_hand, snapshot_path=snapshot_path
    )

    k_pct_gap = round(home_sp.k_pct - away_sp.k_pct, 4)
    bb_pct_gap = round(home_sp.bb_pct - away_sp.bb_pct, 4)
    k_minus_bb_gap = round(home_sp.k_minus_bb_pct - away_sp.k_minus_bb_pct, 4)
    xwoba_allowed_gap = round(home_sp.xwoba_allowed - away_sp.xwoba_allowed, 4)
    depth_gap = round(home_sp.expected_depth_ip - away_sp.expected_depth_ip, 2)

    return {
        "starter_k_pct_gap": k_pct_gap,
        "starter_bb_pct_gap": bb_pct_gap,
        "starter_k_minus_bb_pct_gap": k_minus_bb_gap,
        "starter_xwoba_allowed_gap": xwoba_allowed_gap,
        "starter_depth_gap": depth_gap,
        "home_expected_starter_ip": home_sp.expected_depth_ip,
        "away_expected_starter_ip": away_sp.expected_depth_ip,
    }
