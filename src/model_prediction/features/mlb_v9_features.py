"""MLB v9 Comprehensive Point-in-Time Feature Engineering Pipeline.

Unifies four high-signal feature families:
1. Starter State Vector & Expected Depth (K%, BB%, K-BB%, IP depth).
2. Empirical-Bayes PIT Batter Priors & Projected Lineup Offense (wOBA, ISO, discipline).
3. Reliever Talent x Multi-Day Workload Availability (FIP, fatigue, leverage tier).
4. Handedness Platoon Matchup Interactions (LHP/RHP splits).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import PROJECT_ROOT
from .bullpen_state import PointInTimeBullpenEngine
from .park_factors_pit import park_factor_at
from .platoon_matchup import platoon_matchup_gaps
from .projected_offense import projected_offense_matchup_gaps
from .starter_state import starter_state_matchup_gaps

DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"


def load_probable_starter_index(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[tuple[str, str, str], dict[str, str]]:
    """(start_utc[:16], home_team, away_team) -> probable starter names/throws.

    The games files used by the feature-table builder (mlb_games_all.jsonl and
    the walk-forward rows) carry no starter identity at all; the snapshot does
    (``side.probable_pitcher_name``). This index uses the same crosswalk key
    validation.py's ``_load_starter_era_map`` uses to join snapshots to games
    (minute-level ``game_start_utc[:16]`` + full team names). Without it,
    starter-state features never receive a name and fall back to league priors
    for every game (DEBUG.md 2026-08-26). Later rows win so a game's last
    captured snapshot (closest to first pitch) supplies the starter.
    """
    index: dict[tuple[str, str, str], dict[str, str]] = {}
    path = Path(snapshot_path)
    if not path.exists():
        return index

    def _probable(side: dict) -> tuple[str, str]:
        pid = str(side.get("probable_pitcher_id") or "")
        for p in side.get("players") or []:
            if pid and str(p.get("player_id") or "") == pid:
                return str(p.get("name") or ""), str(p.get("pitch_hand") or "R")
        return str(side.get("probable_pitcher_name") or ""), "R"

    import json

    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            start = str(snap.get("game_start_utc") or "")[:16]
            home = snap.get("home") or {}
            away = snap.get("away") or {}
            home_name = str(home.get("team_name") or "")
            away_name = str(away.get("team_name") or "")
            if not start or not home_name or not away_name:
                continue
            h_name, h_hand = _probable(home)
            a_name, a_hand = _probable(away)
            index[(start, home_name, away_name)] = {
                "home_starter_name": h_name,
                "home_starter_throws": h_hand if h_hand in ("L", "R") else "R",
                "away_starter_name": a_name,
                "away_starter_throws": a_hand if a_hand in ("L", "R") else "R",
            }
    return index


@dataclass(slots=True)
class MLBv9FeatureVector:
    """Complete unified point-in-time feature representation for an MLB matchup."""

    home_team: str
    away_team: str
    as_of: datetime

    # 1. Starter State Vector
    starter_k_pct_gap: float
    starter_bb_pct_gap: float
    starter_k_bb_gap: float
    starter_depth_gap: float
    home_expected_starter_ip: float
    away_expected_starter_ip: float

    # 2. Projected Offense
    projected_woba_gap: float
    projected_iso_gap: float
    projected_k_pct_gap: float
    projected_bb_pct_gap: float
    home_projected_woba: float
    away_projected_woba: float

    # 3. Reliever Talent x Availability
    bullpen_fip_advantage: float
    bullpen_freshness_advantage: float
    bullpen_hl_advantage: float
    home_bullpen_effective_fip: float
    away_bullpen_effective_fip: float

    # 4. Platoon Matchup
    platoon_woba_advantage: float
    platoon_iso_advantage: float

    # 5. Environment
    park_factor: float

    def to_dict(self) -> dict[str, float]:
        return {
            "starter_k_pct_gap": self.starter_k_pct_gap,
            "starter_bb_pct_gap": self.starter_bb_pct_gap,
            "starter_k_bb_gap": self.starter_k_bb_gap,
            "starter_depth_gap": self.starter_depth_gap,
            "home_expected_starter_ip": self.home_expected_starter_ip,
            "away_expected_starter_ip": self.away_expected_starter_ip,
            "projected_woba_gap": self.projected_woba_gap,
            "projected_iso_gap": self.projected_iso_gap,
            "projected_k_pct_gap": self.projected_k_pct_gap,
            "projected_bb_pct_gap": self.projected_bb_pct_gap,
            "home_projected_woba": self.home_projected_woba,
            "away_projected_woba": self.away_projected_woba,
            "bullpen_fip_advantage": self.bullpen_fip_advantage,
            "bullpen_freshness_advantage": self.bullpen_freshness_advantage,
            "bullpen_hl_advantage": self.bullpen_hl_advantage,
            "home_bullpen_effective_fip": self.home_bullpen_effective_fip,
            "away_bullpen_effective_fip": self.away_bullpen_effective_fip,
            "platoon_woba_advantage": self.platoon_woba_advantage,
            "platoon_iso_advantage": self.platoon_iso_advantage,
            "park_factor": self.park_factor,
        }


def extract_mlb_v9_features(
    home_team: str,
    away_team: str,
    as_of: datetime,
    home_starter_name: str = "",
    away_starter_name: str = "",
    home_starter_throws: str = "R",
    away_starter_throws: str = "R",
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> MLBv9FeatureVector:
    """Extract unified point-in-time v9 features for an MLB game."""
    # 1. Starter state
    starter_gaps = starter_state_matchup_gaps(
        home_starter_name, away_starter_name, as_of, snapshot_path=snapshot_path
    )

    # 2. Projected offense
    from .batter_priors import BatterPriorEngine

    engine = BatterPriorEngine(snapshot_path=snapshot_path)
    as_of_date = as_of.strftime("%Y-%m-%d")
    offense_gaps = projected_offense_matchup_gaps(
        engine,
        home_team,
        away_team,
        as_of_date,
        home_sp_hand=home_starter_throws,
        away_sp_hand=away_starter_throws,
    )

    # 3. Dynamic bullpen state (Canonical PIT Engine)
    bp_engine = PointInTimeBullpenEngine(snapshot_path=snapshot_path)
    bp_adv = bp_engine.evaluate_matchup(home_team, away_team, as_of_date)

    # 4. Platoon splits
    platoon_gaps = platoon_matchup_gaps(
        home_team, away_team, home_starter_throws, away_starter_throws, as_of, snapshot_path=snapshot_path
    )

    # 5. Park factor (Point-In-Time)
    pf_obj = park_factor_at(home_team, as_of_date)
    pf = float(pf_obj.get("park_factor", 1.0)) if isinstance(pf_obj, dict) else float(pf_obj or 1.0)

    return MLBv9FeatureVector(
        home_team=home_team,
        away_team=away_team,
        as_of=as_of,
        starter_k_pct_gap=starter_gaps.get("starter_k_pct_gap", 0.0),
        starter_bb_pct_gap=starter_gaps.get("starter_bb_pct_gap", 0.0),
        starter_k_bb_gap=starter_gaps.get("starter_k_minus_bb_pct_gap", 0.0),
        starter_depth_gap=starter_gaps.get("starter_depth_gap", 0.0),
        home_expected_starter_ip=starter_gaps.get("home_expected_starter_ip", 5.5),
        away_expected_starter_ip=starter_gaps.get("away_expected_starter_ip", 5.5),
        projected_woba_gap=offense_gaps.get("projected_offense_quality_gap", 0.0),
        projected_iso_gap=offense_gaps.get("projected_offense_power_gap", 0.0),
        projected_k_pct_gap=offense_gaps.get("projected_offense_k_pct_gap", 0.0),
        projected_bb_pct_gap=offense_gaps.get("projected_offense_bb_pct_gap", 0.0),
        home_projected_woba=offense_gaps.get("home_projected_xwoba", 0.318),
        away_projected_woba=offense_gaps.get("away_projected_xwoba", 0.318),
        bullpen_fip_advantage=bp_adv.fip_gap,
        bullpen_freshness_advantage=bp_adv.availability_gap,
        bullpen_hl_advantage=bp_adv.high_leverage_avail_gap,
        home_bullpen_effective_fip=bp_adv.home_state.available_fip,
        away_bullpen_effective_fip=bp_adv.away_state.available_fip,
        platoon_woba_advantage=platoon_gaps.get("platoon_woba_advantage", 0.0),
        platoon_iso_advantage=platoon_gaps.get("platoon_iso_advantage", 0.0),
        park_factor=pf,
    )
