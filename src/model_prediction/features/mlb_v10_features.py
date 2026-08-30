"""MLB Structural v10 Point-In-Time Feature Engineering Pipeline.

Comprehensive feature extraction adhering to F1S feature prioritization:
1. Starting Pitcher Depth & TTO Degradation (E[IP], K%, BB%, rest, handedness).
2. Actual Lineup Quality & PA Weights (1-9 order, Empirical Bayes xwOBA, K%, BB%, ISO, Barrel%).
3. Pitcher x Lineup Matchup Interactions (K% x K%, BB% x BB%, Platoon edge).
4. Bullpen Availability Tonight (Active reliever talent, fatigue, pitches 1d-3d, expected BP IP).
5. Park & Conditional Physics (Air density, fly-ball distance factor, wind out x barrel).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .air_density import air_density
from .batter_priors import BATTING_ORDER_WEIGHTS, BatterPriorEngine
from .bullpen_state import PointInTimeBullpenEngine
from .park_factors_pit import park_factor_at
from .starter_history import (
    DEFAULT_SNAPSHOT_PATH,
    _normalize_name,
    load_starter_index,
    starter_rolling_rates,
)
from .starter_state import estimate_expected_starter_depth

LEAGUE_XWOBA = 0.315
LEAGUE_K_PCT = 0.225
LEAGUE_BB_PCT = 0.082
LEAGUE_ISO = 0.155
LEAGUE_BARREL_PCT = 0.075
LEAGUE_HARD_HIT_PCT = 0.380
LEAGUE_FIP = 4.10

DOME_VENUES = {
    "Tropicana Field",
    "Rogers Centre",
    "Chase Field",
    "Minute Maid Park",
    "American Family Field",
    "loanDepot park",
    "Globe Life Field",
    "T-Mobile Park",
}


@dataclass(slots=True)
class MLBv10FeatureVector:
    """Decomposed Point-In-Time structural feature vector for MLB matchup."""

    # Matchup Identification
    event_id: str
    home_team: str
    away_team: str
    game_start_utc: str
    as_of_utc: str

    # 1. Starting Pitcher Depth & Quality (Home SP faced by Away offense, Away SP faced by Home offense)
    home_sp_name: str
    home_sp_throws: str
    home_sp_expected_ip: float
    home_sp_k_pct: float
    home_sp_bb_pct: float
    home_sp_k_minus_bb: float
    home_sp_tto_penalty: float
    home_sp_rest_days: float

    away_sp_name: str
    away_sp_throws: str
    away_sp_expected_ip: float
    away_sp_k_pct: float
    away_sp_bb_pct: float
    away_sp_k_minus_bb: float
    away_sp_tto_penalty: float
    away_sp_rest_days: float

    # 2. Lineup Quality & Power (PA-weighted 1-9)
    away_lineup_xwoba_vs_sp: float
    away_lineup_k_pct: float
    away_lineup_bb_pct: float
    away_lineup_iso: float
    away_lineup_barrel_pct: float
    away_lineup_hard_hit_pct: float

    home_lineup_xwoba_vs_sp: float
    home_lineup_k_pct: float
    home_lineup_bb_pct: float
    home_lineup_iso: float
    home_lineup_barrel_pct: float
    home_lineup_hard_hit_pct: float

    # 3. Matchup Interactions
    away_matchup_k_interaction: float  # Home SP K% * Away Lineup K%
    home_matchup_k_interaction: float  # Away SP K% * Home Lineup K%
    away_matchup_bb_interaction: float  # Home SP BB% * Away Lineup BB%
    home_matchup_bb_interaction: float  # Away SP BB% * Home Lineup BB%
    away_platoon_edge: float  # Away Lineup vs Home SP Hand
    home_platoon_edge: float  # Home Lineup vs Away SP Hand

    # 4. Bullpen Availability Tonight & Expected Inning Demand
    home_bp_expected_ip: float  # 9.0 - Home SP IP
    home_bp_effective_fip: float
    home_bp_freshness: float
    home_bp_hl_available: float
    home_bp_pitches_3d: int

    away_bp_expected_ip: float  # 8.5 - Away SP IP
    away_bp_effective_fip: float
    away_bp_freshness: float
    away_bp_hl_available: float
    away_bp_pitches_3d: int

    # 5. Park & Conditional Physics Environment
    park_factor: float
    is_dome: float
    temp_f: float
    air_density_ratio: float
    fly_ball_distance_factor: float
    wind_out_x_barrel: float
    temp_x_iso: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MLBv10FeatureExtractor:
    """Point-In-Time Feature Extractor for MLB Structural v10."""

    def __init__(self, snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH) -> None:
        self.snapshot_path = Path(snapshot_path)
        self.batter_engine = BatterPriorEngine(snapshot_path=self.snapshot_path)
        self.bullpen_engine = PointInTimeBullpenEngine(snapshot_path=self.snapshot_path)
        self.starter_index = load_starter_index(self.snapshot_path)

    def extract_features_for_matchup(
        self,
        event_id: str,
        home_team: str,
        away_team: str,
        game_start_utc: str,
        as_of_dt: datetime,
        snapshot: dict[str, Any] | None = None,
    ) -> MLBv10FeatureVector:
        """Extract PIT v10 features strictly using data available prior to as_of_dt."""
        as_of_date = as_of_dt.strftime("%Y-%m-%d")

        # 1. Identify Probable Starters & Throws
        home_snap = (snapshot or {}).get("home") or {}
        away_snap = (snapshot or {}).get("away") or {}

        def _get_sp_info(side: dict) -> tuple[str, str]:
            prob_name = str(side.get("probable_pitcher_name") or "")
            prob_id = str(side.get("probable_pitcher_id") or "")
            players = side.get("players") or []
            if prob_id:
                for p in players:
                    if str(p.get("player_id")) == prob_id:
                        return str(p.get("name") or prob_name), str(p.get("pitch_hand") or "R")
            for p in players:
                if p.get("pitching_order") == 1:
                    return str(p.get("name") or prob_name), str(p.get("pitch_hand") or "R")
            return prob_name, "R"

        h_sp_name, h_sp_hand = _get_sp_info(home_snap)
        a_sp_name, a_sp_hand = _get_sp_info(away_snap)

        if h_sp_hand not in ("L", "R"):
            h_sp_hand = "R"
        if a_sp_hand not in ("L", "R"):
            a_sp_hand = "R"

        # 2. Starter Depth & Rolling Rates
        h_norm = _normalize_name(h_sp_name)
        a_norm = _normalize_name(a_sp_name)

        h_starts = [s for s in self.starter_index.get(h_norm, []) if s[0] < as_of_dt]
        a_starts = [s for s in self.starter_index.get(a_norm, []) if s[0] < as_of_dt]

        h_exp_ip = estimate_expected_starter_depth(h_starts[-5:] if h_starts else [])
        a_exp_ip = estimate_expected_starter_depth(a_starts[-5:] if a_starts else [])

        h_rates = starter_rolling_rates(h_sp_name, as_of_dt, snapshot_path=self.snapshot_path)
        a_rates = starter_rolling_rates(a_sp_name, as_of_dt, snapshot_path=self.snapshot_path)

        h_k_pct = h_rates.get("k_pct") or LEAGUE_K_PCT
        h_bb_pct = h_rates.get("bb_pct") or LEAGUE_BB_PCT
        h_k_minus_bb = h_rates.get("k_minus_bb_pct") or (h_k_pct - h_bb_pct)

        a_k_pct = a_rates.get("k_pct") or LEAGUE_K_PCT
        a_bb_pct = a_rates.get("bb_pct") or LEAGUE_BB_PCT
        a_k_minus_bb = a_rates.get("k_minus_bb_pct") or (a_k_pct - a_bb_pct)

        # Starter Rest Days
        h_rest = (as_of_dt - h_starts[-1][0]).total_seconds() / 86400.0 if h_starts else 5.0
        a_rest = (as_of_dt - a_starts[-1][0]).total_seconds() / 86400.0 if a_starts else 5.0
        h_rest = min(10.0, max(1.0, h_rest))
        a_rest = min(10.0, max(1.0, a_rest))

        # TTO Penalty (Higher expected IP implies facing 3rd time through order)
        # Inning 6+ carries ~1.20x scoring rate penalty
        h_tto = 1.0 + max(0.0, (h_exp_ip - 5.0) * 0.05)
        a_tto = 1.0 + max(0.0, (a_exp_ip - 5.0) * 0.05)

        # 3. Lineup Quality & PA Weights (Home vs Away SP hand, Away vs Home SP hand)
        def _compute_lineup_metrics(side: dict, opp_sp_hand: str) -> dict[str, float]:
            batting_order = side.get("batting_order") or []
            players = side.get("players") or []
            player_map = {str(p.get("player_id")): p for p in players}

            ordered_players = []
            if batting_order:
                for pid in batting_order[:9]:
                    if str(pid) in player_map:
                        ordered_players.append(player_map[str(pid)])
            if not ordered_players:
                # Filter players by batting_order field
                b_players = [p for p in players if p.get("batting_order") is not None]
                b_players.sort(key=lambda p: p.get("batting_order") or 99)
                ordered_players = b_players[:9]

            weights = list(BATTING_ORDER_WEIGHTS)
            n_lineup = len(ordered_players)
            if n_lineup < 9:
                # Pad with neutral prior batters
                weights = weights[:n_lineup] + [weights[i] for i in range(n_lineup, 9)]
            w_sum = sum(weights) or 1.0
            norm_weights = [w / w_sum for w in weights]

            xwoba_list, k_list, bb_list, iso_list, barrel_list, hh_list = [], [], [], [], [], []
            for i in range(9):
                if i < n_lineup:
                    p = ordered_players[i]
                    pid = str(p.get("player_id") or "")
                    prior = self.batter_engine.get_player_prior(
                        pid, as_of_date=as_of_date, vs_hand=opp_sp_hand
                    )
                    xwoba_list.append(prior.shrunk_xwoba())
                    k_list.append(prior.shrunk_k_pct())
                    bb_list.append(prior.shrunk_bb_pct())
                    iso_list.append(prior.shrunk_iso())
                    barrel_list.append(prior.shrunk_barrel_pct())
                    hh_list.append(prior.shrunk_hard_hit_pct())
                else:
                    xwoba_list.append(LEAGUE_XWOBA)
                    k_list.append(LEAGUE_K_PCT)
                    bb_list.append(LEAGUE_BB_PCT)
                    iso_list.append(LEAGUE_ISO)
                    barrel_list.append(LEAGUE_BARREL_PCT)
                    hh_list.append(LEAGUE_HARD_HIT_PCT)

            return {
                "xwoba": sum(w * v for w, v in zip(norm_weights, xwoba_list)),
                "k_pct": sum(w * v for w, v in zip(norm_weights, k_list)),
                "bb_pct": sum(w * v for w, v in zip(norm_weights, bb_list)),
                "iso": sum(w * v for w, v in zip(norm_weights, iso_list)),
                "barrel_pct": sum(w * v for w, v in zip(norm_weights, barrel_list)),
                "hard_hit_pct": sum(w * v for w, v in zip(norm_weights, hh_list)),
            }

        away_lineup = _compute_lineup_metrics(away_snap, h_sp_hand)
        home_lineup = _compute_lineup_metrics(home_snap, a_sp_hand)

        # 4. Bullpen State Tonight
        bp_matchup = self.bullpen_engine.evaluate_matchup(home_team, away_team, as_of_date)
        h_bp = bp_matchup.home_state
        a_bp = bp_matchup.away_state

        h_bp_exp_ip = max(1.5, 9.0 - h_exp_ip)
        a_bp_exp_ip = max(1.5, 8.5 - a_exp_ip)

        # 5. Park & Conditional Physics
        pf_obj = park_factor_at(home_team, as_of_date)
        pf = float(pf_obj.get("park_factor", 1.0)) if isinstance(pf_obj, dict) else float(pf_obj or 1.0)

        venue_name = (snapshot or {}).get("venue_name") or ""
        venue_id = (snapshot or {}).get("venue_id") or 0
        is_dome = (
            1.0
            if (venue_name in DOME_VENUES or "dome" in venue_name.lower() or venue_id in (2, 3, 10, 15, 20))
            else 0.0
        )

        weather = (snapshot or {}).get("weather") or {}
        raw_temp = weather.get("temperature_f")
        temp_f = 70.0 if (is_dome or raw_temp is None) else float(raw_temp)

        # Air Density from temperature, standard sea-level pressure, and nominal 50% RH
        temp_c = (temp_f - 32.0) * 5.0 / 9.0
        ad_res = air_density(temp_c=temp_c, pressure_pa=101325.0, relative_humidity=50.0)
        density_ratio = 1.0 if is_dome else ad_res.density_ratio
        fly_ball_factor = (1.0 / density_ratio) ** 0.4

        # Wind
        wind_str = str(weather.get("wind") or "")
        wind_out_comp = 0.0
        if not is_dome and "Out" in wind_str:
            try:
                mph = float(wind_str.split("mph")[0].strip())
                wind_out_comp = mph / 10.0
            except (ValueError, IndexError):
                wind_out_comp = 0.5

        wind_x_barrel = wind_out_comp * (home_lineup["barrel_pct"] + away_lineup["barrel_pct"])
        temp_x_iso = ((temp_f - 70.0) / 20.0) * (home_lineup["iso"] + away_lineup["iso"])

        return MLBv10FeatureVector(
            event_id=event_id,
            home_team=home_team,
            away_team=away_team,
            game_start_utc=game_start_utc,
            as_of_utc=as_of_dt.isoformat(),
            home_sp_name=h_sp_name,
            home_sp_throws=h_sp_hand,
            home_sp_expected_ip=h_exp_ip,
            home_sp_k_pct=h_k_pct,
            home_sp_bb_pct=h_bb_pct,
            home_sp_k_minus_bb=h_k_minus_bb,
            home_sp_tto_penalty=h_tto,
            home_sp_rest_days=h_rest,
            away_sp_name=a_sp_name,
            away_sp_throws=a_sp_hand,
            away_sp_expected_ip=a_exp_ip,
            away_sp_k_pct=a_k_pct,
            away_sp_bb_pct=a_bb_pct,
            away_sp_k_minus_bb=a_k_minus_bb,
            away_sp_tto_penalty=a_tto,
            away_sp_rest_days=a_rest,
            away_lineup_xwoba_vs_sp=away_lineup["xwoba"],
            away_lineup_k_pct=away_lineup["k_pct"],
            away_lineup_bb_pct=away_lineup["bb_pct"],
            away_lineup_iso=away_lineup["iso"],
            away_lineup_barrel_pct=away_lineup["barrel_pct"],
            away_lineup_hard_hit_pct=away_lineup["hard_hit_pct"],
            home_lineup_xwoba_vs_sp=home_lineup["xwoba"],
            home_lineup_k_pct=home_lineup["k_pct"],
            home_lineup_bb_pct=home_lineup["bb_pct"],
            home_lineup_iso=home_lineup["iso"],
            home_lineup_barrel_pct=home_lineup["barrel_pct"],
            home_lineup_hard_hit_pct=home_lineup["hard_hit_pct"],
            away_matchup_k_interaction=h_k_pct * away_lineup["k_pct"],
            home_matchup_k_interaction=a_k_pct * home_lineup["k_pct"],
            away_matchup_bb_interaction=h_bb_pct * away_lineup["bb_pct"],
            home_matchup_bb_interaction=a_bb_pct * home_lineup["bb_pct"],
            away_platoon_edge=away_lineup["xwoba"] - LEAGUE_XWOBA,
            home_platoon_edge=home_lineup["xwoba"] - LEAGUE_XWOBA,
            home_bp_expected_ip=h_bp_exp_ip,
            home_bp_effective_fip=h_bp.available_fip,
            home_bp_freshness=h_bp.effective_availability,
            home_bp_hl_available=h_bp.high_leverage_availability,
            home_bp_pitches_3d=sum(r.workload.total_pitches_3d for r in h_bp.relievers),
            away_bp_expected_ip=a_bp_exp_ip,
            away_bp_effective_fip=a_bp.available_fip,
            away_bp_freshness=a_bp.effective_availability,
            away_bp_hl_available=a_bp.high_leverage_availability,
            away_bp_pitches_3d=sum(r.workload.total_pitches_3d for r in a_bp.relievers),
            park_factor=pf,
            is_dome=is_dome,
            temp_f=temp_f,
            air_density_ratio=density_ratio,
            fly_ball_distance_factor=fly_ball_factor,
            wind_out_x_barrel=wind_x_barrel,
            temp_x_iso=temp_x_iso,
        )
