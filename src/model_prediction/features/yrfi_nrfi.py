"""MLB Yes Run First Inning (YRFI) / No Run First Inning (NRFI) feature engineering.

Point-in-time feature extraction from MLB Stats API completed game snapshots:
1. Starting Pitcher 1st-inning run-prevention priors (ERA, FIP, K-BB, WHIP) with credibility shrinkage.
2. Top-of-the-order (Batters 1-3) offensive production priors (OBP, ISO, discipline).
3. Ballpark run environment (empirical park factor, altitude, dome).
4. Environmental weather impact (temperature, wind factor).

Strictly point-in-time: statistics for a given game are computed using only games
strictly preceding that game's start timestamp.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..domain import parse_utc
from .batter_offense import (
    _league_rates,
    player_shrunk_rates,
)
from .batter_offense import (
    _load_indexes as _load_batter_indexes,
)
from .park_factors import park_factor

DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"

# Pitcher 1st-inning shrinkage priors (sample sizes in starts/innings pitched)
PITCHER_PRIOR_STARTS = 15.0
LEAGUE_FIRST_INNING_RUN_RATE = 0.52  # average total runs per 1st inning (~0.26 per half-inning)
LEAGUE_NRFI_PROBABILITY = 0.5106  # empirical historical baseline

_PITCHER_FIRST_INNING_CACHE: dict[Path, dict[int, list[tuple[datetime, dict[str, float]]]]] = {}


def _parse_ip(innings_pitched: str | float | None) -> float:
    """Parse baseball boxscore innings pitched string (e.g. '5.2' -> 5 + 2/3)."""
    if innings_pitched is None:
        return 0.0
    if isinstance(innings_pitched, (int, float)):
        return float(innings_pitched)
    s = str(innings_pitched).strip()
    if not s:
        return 0.0
    whole, _, frac = s.partition(".")
    frac_map = {"0": 0.0, "1": 1.0 / 3.0, "2": 2.0 / 3.0}
    return float(whole or 0) + frac_map.get(frac, 0.0)


def _load_pitcher_first_inning_index(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[int, list[tuple[datetime, dict[str, float]]]]:
    """Build chronological point-in-time appearance records for starting pitchers."""
    path = Path(snapshot_path)
    if path in _PITCHER_FIRST_INNING_CACHE:
        return _PITCHER_FIRST_INNING_CACHE[path]

    by_pitcher: dict[int, list[tuple[datetime, dict[str, float]]]] = {}
    if not path.exists():
        _PITCHER_FIRST_INNING_CACHE[path] = by_pitcher
        return by_pitcher

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                game_start = parse_utc(str(snap["game_start_utc"]))
            except (KeyError, ValueError):
                continue

            for side_key, opponent_1st_runs_key in (
                ("home", "first_inning_runs_away"),
                ("away", "first_inning_runs_home"),
            ):
                side = snap.get(side_key) or {}
                pitcher_order = side.get("pitcher_order") or []
                if not pitcher_order:
                    continue
                starter_id = pitcher_order[0]
                if starter_id is None:
                    continue

                starter_stats = None
                for player in side.get("players", []):
                    if player.get("player_id") == starter_id:
                        starter_stats = player.get("pitching") or {}
                        break

                if starter_stats is None:
                    continue

                ip = _parse_ip(starter_stats.get("inningsPitched"))
                er = float(starter_stats.get("earnedRuns") or 0.0)
                runs = float(starter_stats.get("runs") or 0.0)
                so = float(starter_stats.get("strikeOuts") or 0.0)
                bb = float(starter_stats.get("baseOnBalls") or 0.0)
                hits = float(starter_stats.get("hits") or 0.0)
                hr = float(starter_stats.get("homeRuns") or 0.0)
                runs_1st = float(snap.get(opponent_1st_runs_key) or 0.0)

                by_pitcher.setdefault(int(starter_id), []).append(
                    (
                        game_start,
                        {
                            "ip": ip,
                            "er": er,
                            "runs": runs,
                            "so": so,
                            "bb": bb,
                            "hits": hits,
                            "hr": hr,
                            "runs_1st": runs_1st,
                        },
                    )
                )

    for appearances in by_pitcher.values():
        appearances.sort(key=lambda item: item[0])

    _PITCHER_FIRST_INNING_CACHE[path] = by_pitcher
    return by_pitcher


def starter_first_inning_profile(
    pitcher_id: int | str | None,
    decision: datetime,
    *,
    pitcher_name: str | None = None,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    lookback_starts: int = 15,
) -> dict[str, Any]:
    """Credibility-shrunk first-inning run suppression profile for one starting pitcher."""
    from .starter_history import starter_rolling_fip, starter_rolling_rates

    # Attempt name-based lookup first if pitcher_name is given or pitcher_id is string
    lookup_name = pitcher_name or (
        str(pitcher_id) if isinstance(pitcher_id, str) and not pitcher_id.isdigit() else None
    )
    if lookup_name:
        fip_data = starter_rolling_fip(
            lookup_name, decision, snapshot_path=snapshot_path, lookback_starts=lookback_starts
        )
        rates_data = starter_rolling_rates(
            lookup_name, decision, snapshot_path=snapshot_path, lookback_starts=lookback_starts
        )
        if fip_data.get("status") == "available" and rates_data.get("status") == "available":
            fip = float(fip_data.get("fip") or 4.10)
            k_rate = float(rates_data.get("k_pct") or 0.22)
            bb_rate = float(rates_data.get("bb_pct") or 0.08)
            starts = int(fip_data.get("starts") or 1)
            # Scale 1st inning run rate by pitcher talent relative to league average
            fip_factor = max(0.40, min(1.80, fip / 4.10))
            expected_runs_1st = (LEAGUE_FIRST_INNING_RUN_RATE / 2.0) * fip_factor
            nrfi_rate = max(0.20, min(0.85, 1.0 - (0.2855 * fip_factor)))
            return {
                "starts": starts,
                "raw_nrfi_rate": round(nrfi_rate, 4),
                "nrfi_rate": round(nrfi_rate, 4),
                "runs_per_first_inning": round(expected_runs_1st, 4),
                "fip": round(fip, 3),
                "k_rate": round(k_rate, 4),
                "bb_rate": round(bb_rate, 4),
                "status": "available",
            }

    if pitcher_id is None:
        return {
            "starts": 0,
            "nrfi_rate": LEAGUE_NRFI_PROBABILITY,
            "runs_per_first_inning": LEAGUE_FIRST_INNING_RUN_RATE / 2.0,
            "fip": 4.10,
            "k_rate": 0.22,
            "bb_rate": 0.08,
            "status": "league_baseline",
        }

    index = _load_pitcher_first_inning_index(snapshot_path)
    try:
        numeric_id = int(pitcher_id)
    except (ValueError, TypeError):
        numeric_id = -1
    lines = index.get(numeric_id, [])
    prior_starts = [item[1] for item in lines if item[0] < decision][-lookback_starts:]

    if not prior_starts:
        return {
            "starts": 0,
            "nrfi_rate": LEAGUE_NRFI_PROBABILITY,
            "runs_per_first_inning": LEAGUE_FIRST_INNING_RUN_RATE / 2.0,
            "fip": 4.10,
            "k_rate": 0.22,
            "bb_rate": 0.08,
            "status": "league_baseline",
        }

    n_starts = len(prior_starts)
    nrfi_clean = sum(1 for s in prior_starts if s["runs_1st"] == 0)
    raw_nrfi_rate = nrfi_clean / n_starts
    raw_runs_1st = sum(s["runs_1st"] for s in prior_starts) / n_starts

    total_ip = sum(s["ip"] for s in prior_starts)
    total_so = sum(s["so"] for s in prior_starts)
    total_bb = sum(s["bb"] for s in prior_starts)
    total_hr = sum(s["hr"] for s in prior_starts)

    k_rate = total_so / max(0.001, total_ip * 4.2)
    bb_rate = total_bb / max(0.001, total_ip * 4.2)
    fip = (
        ((13 * total_hr + 3 * total_bb - 2 * total_so) / max(1.0, total_ip)) + 3.10
        if total_ip >= 3.0
        else 4.10
    )

    credibility = n_starts / (n_starts + PITCHER_PRIOR_STARTS)
    shrunk_nrfi_rate = credibility * raw_nrfi_rate + (1.0 - credibility) * (
        1.0 - LEAGUE_FIRST_INNING_RUN_RATE / 2.0
    )
    shrunk_runs_1st = credibility * raw_runs_1st + (1.0 - credibility) * (LEAGUE_FIRST_INNING_RUN_RATE / 2.0)

    return {
        "starts": n_starts,
        "raw_nrfi_rate": round(raw_nrfi_rate, 4),
        "nrfi_rate": round(shrunk_nrfi_rate, 4),
        "runs_per_first_inning": round(shrunk_runs_1st, 4),
        "fip": round(fip, 3),
        "k_rate": round(k_rate, 4),
        "bb_rate": round(bb_rate, 4),
        "status": "available",
    }


def top3_lineup_offense_profile(
    team_name: str,
    top3_player_ids: list[int] | None,
    decision: datetime,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    """Credibility-shrunk composite offensive talent for the top 3 batters in the lineup."""
    if top3_player_ids and len(top3_player_ids) >= 3:
        target_ids = top3_player_ids[:3]
    else:
        _by_player, by_team_game = _load_batter_indexes(snapshot_path)
        team_games = [g for g in by_team_game.get(team_name, []) if g[0] < decision]
        if not team_games:
            league = _league_rates(snapshot_path)
            return {
                "production": round(league["production"], 4),
                "discipline": round(league["discipline"], 4),
                "power": round(league["power"], 4),
                "composite": round((league["production"] + league["discipline"] + league["power"]) / 3.0, 4),
                "status": "league_baseline",
            }
        recent_pa: dict[int, float] = {}
        for _, participants in team_games[-10:]:
            for pid, pa in participants.items():
                recent_pa[pid] = recent_pa.get(pid, 0.0) + pa
        sorted_pids = sorted(recent_pa, key=lambda pid: recent_pa[pid], reverse=True)
        target_ids = sorted_pids[:3] if len(sorted_pids) >= 3 else sorted_pids

    if not target_ids:
        league = _league_rates(snapshot_path)
        return {
            "production": round(league["production"], 4),
            "discipline": round(league["discipline"], 4),
            "power": round(league["power"], 4),
            "composite": round((league["production"] + league["discipline"] + league["power"]) / 3.0, 4),
            "status": "league_baseline",
        }

    prod = disc = powr = 0.0
    for pid in target_ids:
        rates = player_shrunk_rates(pid, decision, snapshot_path=snapshot_path)
        prod += rates["production"]
        disc += rates["discipline"]
        powr += rates["power"]

    n = len(target_ids)
    prod /= n
    disc /= n
    powr /= n
    composite = (prod + disc + powr) / 3.0

    return {
        "production": round(prod, 4),
        "discipline": round(disc, 4),
        "power": round(powr, 4),
        "composite": round(composite, 4),
        "status": "available",
    }


@dataclass(frozen=True)
class NRFIFeatures:
    """Complete pre-game feature vector for MLB 1st-inning run scoring."""

    home_sp_fip: float
    home_sp_nrfi_rate: float
    home_sp_k_rate: float
    away_sp_fip: float
    away_sp_nrfi_rate: float
    away_sp_k_rate: float
    away_top3_composite: float
    home_top3_composite: float
    park_factor: float
    weather_factor: float
    half_top_expected_runs: float
    half_bot_expected_runs: float
    nrfi_decomposed_prob: float


def compute_nrfi_features(
    home_team: str,
    away_team: str,
    decision: datetime,
    *,
    home_starter_id: int | None = None,
    away_starter_id: int | None = None,
    home_starter_name: str | None = None,
    away_starter_name: str | None = None,
    home_top3_ids: list[int] | None = None,
    away_top3_ids: list[int] | None = None,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    weather_factor: float = 1.0,
) -> NRFIFeatures:
    """Compute point-in-time NRFI / YRFI feature vector and decomposed probability."""
    home_sp = starter_first_inning_profile(
        home_starter_id, decision, pitcher_name=home_starter_name, snapshot_path=snapshot_path
    )
    away_sp = starter_first_inning_profile(
        away_starter_id, decision, pitcher_name=away_starter_name, snapshot_path=snapshot_path
    )

    away_top3 = top3_lineup_offense_profile(away_team, away_top3_ids, decision, snapshot_path=snapshot_path)
    home_top3 = top3_lineup_offense_profile(home_team, home_top3_ids, decision, snapshot_path=snapshot_path)

    pf = float(park_factor(home_team).get("park_factor", 1.0))

    top_mult = (
        (away_top3["composite"] / 0.126)
        * (home_sp["runs_per_first_inning"] / (LEAGUE_FIRST_INNING_RUN_RATE / 2.0))
        * pf
        * weather_factor
    )
    bot_mult = (
        (home_top3["composite"] / 0.126)
        * (away_sp["runs_per_first_inning"] / (LEAGUE_FIRST_INNING_RUN_RATE / 2.0))
        * pf
        * weather_factor
    )

    # Base half-inning run-scoring probability ~ 28.55% (clean probability = 71.45% -> 0.7145^2 = 0.5106 NRFI)
    p_score_top = min(0.80, max(0.05, 0.2855 * top_mult))
    p_score_bot = min(0.80, max(0.05, 0.2855 * bot_mult))

    p_clean_top = 1.0 - p_score_top
    p_clean_bot = 1.0 - p_score_bot
    p_nrfi = round(p_clean_top * p_clean_bot, 4)

    exp_top = round((LEAGUE_FIRST_INNING_RUN_RATE / 2.0) * top_mult, 4)
    exp_bot = round((LEAGUE_FIRST_INNING_RUN_RATE / 2.0) * bot_mult, 4)

    return NRFIFeatures(
        home_sp_fip=home_sp["fip"],
        home_sp_nrfi_rate=home_sp["nrfi_rate"],
        home_sp_k_rate=home_sp["k_rate"],
        away_sp_fip=away_sp["fip"],
        away_sp_nrfi_rate=away_sp["nrfi_rate"],
        away_sp_k_rate=away_sp["k_rate"],
        away_top3_composite=away_top3["composite"],
        home_top3_composite=home_top3["composite"],
        park_factor=round(pf, 3),
        weather_factor=round(weather_factor, 3),
        half_top_expected_runs=exp_top,
        half_bot_expected_runs=exp_bot,
        nrfi_decomposed_prob=p_nrfi,
    )
