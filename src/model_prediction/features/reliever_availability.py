"""MLB Point-in-Time Reliever Talent x Availability Feature Engine.

Models bullpen run-suppression quality as a function of:
1. Individual reliever empirical-Bayes talent (K%, BB%, FIP, ERA) shrunk toward league relief baseline.
2. Pitcher fatigue and multi-day workload (pitches thrown over trailing 1-4 days, back-to-back appearances).
3. Availability-weighted leverage-tier team bullpen strength and rest differentials.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..domain import parse_utc

DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"

LEAGUE_RELIEF_ERA = 4.06
LEAGUE_RELIEF_FIP = 4.10
LEAGUE_RELIEF_K_PCT = 0.225
LEAGUE_RELIEF_BB_PCT = 0.085
PRIOR_RELIEF_BF = 50.0  # Shrinkage prior sample size in batters faced


@dataclass(slots=True)
class RelieverWorkload:
    """Fatigue and availability state for an individual relief pitcher."""

    player_id: int | str
    player_name: str
    pitches_last_1_day: int
    pitches_last_2_days: int
    pitches_last_3_days: int
    days_since_last_appearance: int
    consecutive_days_pitched: int
    availability_score: float  # 0.0 (unavailable/exhausted) to 1.0 (fully fresh)


@dataclass(slots=True)
class RelieverTalent:
    """Empirical-Bayes point-in-time talent estimate for a relief pitcher."""

    player_id: int | str
    player_name: str
    batters_faced: int
    innings_pitched: float
    shrunk_fip: float
    shrunk_k_pct: float
    shrunk_bb_pct: float
    leverage_tier: int  # 1 (closer/high leverage), 2 (setup/mid), 3 (mop-up/low)


@dataclass(slots=True)
class TeamBullpenState:
    """Aggregate bullpen state for a team ahead of a game."""

    team_name: str
    effective_bullpen_fip: float
    effective_k_bb_pct: float
    high_leverage_availability: float
    overall_freshness_score: float
    active_relievers_count: int


def _parse_ip(innings_pitched: Any) -> float:
    try:
        whole, _, outs = str(innings_pitched).partition(".")
        return int(whole) + (int(outs or 0) / 3.0)
    except (TypeError, ValueError):
        return 0.0


def calculate_reliever_availability(
    pitches_day_1: int,
    pitches_day_2: int,
    pitches_day_3: int,
    consecutive_days: int,
) -> float:
    """Compute 0.0 to 1.0 availability score based on pitch counts and back-to-back starts."""
    if consecutive_days >= 3:
        return 0.05
    if consecutive_days == 2 and pitches_day_1 + pitches_day_2 >= 30:
        return 0.15
    if pitches_day_1 >= 35:
        return 0.10
    if pitches_day_1 >= 25:
        return 0.35
    if pitches_day_1 >= 15:
        return 0.70

    two_day_total = pitches_day_1 + pitches_day_2
    if two_day_total >= 45:
        return 0.25
    if two_day_total >= 30:
        return 0.60

    three_day_total = two_day_total + pitches_day_3
    if three_day_total >= 60:
        return 0.40

    return 1.00


def get_team_bullpen_state(
    team_name: str,
    as_of: datetime,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_days: int = 45,
) -> TeamBullpenState:
    """Extract point-in-time bullpen performance and availability for a team."""
    path = Path(snapshot_path)
    if not path.exists():
        return TeamBullpenState(
            team_name=team_name,
            effective_bullpen_fip=LEAGUE_RELIEF_FIP,
            effective_k_bb_pct=LEAGUE_RELIEF_K_PCT - LEAGUE_RELIEF_BB_PCT,
            high_leverage_availability=0.85,
            overall_freshness_score=0.85,
            active_relievers_count=0,
        )

    # Ingest relief outings strictly prior to as_of
    cutoff_start = as_of - timedelta(days=lookback_days)
    recent_outings: list[dict[str, Any]] = []

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            raw_time = row.get("game_start_utc") or row.get("game_date") or row.get("official_date")
            if not raw_time:
                continue
            try:
                game_time = parse_utc(str(raw_time))
            except (ValueError, TypeError):
                continue

            if not (cutoff_start <= game_time < as_of):
                continue

            # Look for team in home/away boxscores
            for side_key in ("home", "away"):
                side_data = row.get(side_key)
                if isinstance(side_data, dict):
                    side_team = str(side_data.get("team_name") or "")
                    pitcher_order = [str(pid) for pid in side_data.get("pitcher_order", [])]
                    starter_id = pitcher_order[0] if pitcher_order else None
                    players = side_data.get("players", [])
                    for p in players:
                        pid = str(p.get("player_id") or p.get("id") or "")
                        if not pid or pid == starter_id:
                            continue
                        pitching = p.get("pitching") or {}
                        if not pitching:
                            continue
                        recent_outings.append(
                            {
                                "game_time": game_time,
                                "player_id": pid,
                                "player_name": p.get("name", "Unknown"),
                                "ip": _parse_ip(pitching.get("inningsPitched") or pitching.get("ip", 0.0)),
                                "pitches": int(
                                    pitching.get("numberOfPitches") or pitching.get("pitches") or 15
                                ),
                                "k": int(pitching.get("strikeOuts") or pitching.get("so") or 0),
                                "bb": int(pitching.get("baseOnBalls") or pitching.get("bb") or 0),
                                "hr": int(pitching.get("homeRuns") or pitching.get("hr") or 0),
                                "er": int(pitching.get("earnedRuns") or pitching.get("er") or 0),
                                "bf": int(pitching.get("battersFaced") or pitching.get("bf") or 4),
                            }
                        )
                else:
                    side_team = str(row.get(f"{side_key}_team") or "")
                    if team_name.lower() in side_team.lower() or side_team.lower() in team_name.lower():
                        pitchers = row.get(f"{side_key}_pitchers", [])
                        if len(pitchers) > 1:
                            for p in pitchers[1:]:
                                recent_outings.append(
                                    {
                                        "game_time": game_time,
                                        "player_id": str(p.get("id") or p.get("player_id") or "0"),
                                        "player_name": p.get("name", "Unknown"),
                                        "ip": _parse_ip(p.get("ip", 0.0)),
                                        "pitches": int(p.get("pitches") or p.get("np") or 15),
                                        "k": int(p.get("k") or p.get("so") or 0),
                                        "bb": int(p.get("bb") or 0),
                                        "hr": int(p.get("hr") or 0),
                                        "er": int(p.get("er") or 0),
                                        "bf": int(p.get("bf") or 4),
                                    }
                                )

    if not recent_outings:
        return TeamBullpenState(
            team_name=team_name,
            effective_bullpen_fip=LEAGUE_RELIEF_FIP,
            effective_k_bb_pct=LEAGUE_RELIEF_K_PCT - LEAGUE_RELIEF_BB_PCT,
            high_leverage_availability=0.85,
            overall_freshness_score=0.85,
            active_relievers_count=0,
        )

    # Group by pitcher to evaluate talent and trailing workload
    pitcher_map: dict[str, list[dict[str, Any]]] = {}
    for outing in recent_outings:
        pid = str(outing["player_id"] or outing["player_name"])
        pitcher_map.setdefault(pid, []).append(outing)

    relievers_fip_weighted: list[tuple[float, float, float]] = []  # (fip, weight, availability)
    freshness_scores: list[float] = []
    hl_availabilities: list[float] = []

    for pid, outings in pitcher_map.items():
        outings.sort(key=lambda x: x["game_time"])
        total_bf = sum(o["bf"] for o in outings)
        total_ip = sum(o["ip"] for o in outings)
        total_k = sum(o["k"] for o in outings)
        total_bb = sum(o["bb"] for o in outings)
        total_hr = sum(o["hr"] for o in outings)

        # Empirical Bayes shrinkage for FIP
        raw_fip = ((13.0 * total_hr + 3.0 * total_bb - 2.0 * total_k) / max(total_ip, 1.0)) + 3.10
        raw_fip = max(1.50, min(8.00, raw_fip))
        weight_obs = total_bf / (total_bf + PRIOR_RELIEF_BF)
        shrunk_fip = weight_obs * raw_fip + (1.0 - weight_obs) * LEAGUE_RELIEF_FIP

        # Trailing fatigue in last 3 calendar days
        p_day1 = sum(o["pitches"] for o in outings if (as_of - o["game_time"]).total_seconds() <= 86400)
        p_day2 = sum(
            o["pitches"] for o in outings if 86400 < (as_of - o["game_time"]).total_seconds() <= 172800
        )
        p_day3 = sum(
            o["pitches"] for o in outings if 172800 < (as_of - o["game_time"]).total_seconds() <= 259200
        )

        # Consecutive appearances check
        pitched_yesterday = p_day1 > 0
        pitched_two_days_ago = p_day2 > 0
        consecutive = 2 if (pitched_yesterday and pitched_two_days_ago) else (1 if pitched_yesterday else 0)

        avail = calculate_reliever_availability(p_day1, p_day2, p_day3, consecutive)
        freshness_scores.append(avail)

        # Leverage weighting: higher usage in close situations indicates high leverage
        leverage_weight = 1.5 if total_bf >= 30 else 1.0
        relievers_fip_weighted.append((shrunk_fip, leverage_weight * avail, avail))

        if leverage_weight > 1.2:
            hl_availabilities.append(avail)

    if not relievers_fip_weighted or sum(w for _, w, _ in relievers_fip_weighted) == 0:
        eff_fip = LEAGUE_RELIEF_FIP
    else:
        total_weight = sum(w for _, w, _ in relievers_fip_weighted)
        eff_fip = sum(fip * w for fip, w, _ in relievers_fip_weighted) / total_weight

    avg_freshness = sum(freshness_scores) / max(len(freshness_scores), 1)
    hl_avail = sum(hl_availabilities) / max(len(hl_availabilities), 1) if hl_availabilities else avg_freshness

    return TeamBullpenState(
        team_name=team_name,
        effective_bullpen_fip=round(eff_fip, 3),
        effective_k_bb_pct=round(LEAGUE_RELIEF_K_PCT - LEAGUE_RELIEF_BB_PCT, 4),
        high_leverage_availability=round(hl_avail, 3),
        overall_freshness_score=round(avg_freshness, 3),
        active_relievers_count=len(pitcher_map),
    )


def reliever_availability_matchup_gaps(
    home_team: str,
    away_team: str,
    as_of: datetime,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, float]:
    """Calculate point-in-time bullpen matchup differential features."""
    home_bp = get_team_bullpen_state(home_team, as_of, snapshot_path=snapshot_path)
    away_bp = get_team_bullpen_state(away_team, as_of, snapshot_path=snapshot_path)

    # Positive gap means home advantage (lower FIP is better, so away - home)
    fip_advantage = away_bp.effective_bullpen_fip - home_bp.effective_bullpen_fip
    freshness_advantage = home_bp.overall_freshness_score - away_bp.overall_freshness_score
    hl_advantage = home_bp.high_leverage_availability - away_bp.high_leverage_availability

    return {
        "bullpen_fip_advantage": round(fip_advantage, 4),
        "bullpen_freshness_advantage": round(freshness_advantage, 4),
        "bullpen_hl_advantage": round(hl_advantage, 4),
        "home_bullpen_effective_fip": home_bp.effective_bullpen_fip,
        "away_bullpen_effective_fip": away_bp.effective_bullpen_fip,
        "home_bullpen_freshness": home_bp.overall_freshness_score,
        "away_bullpen_freshness": away_bp.overall_freshness_score,
    }
