"""Walk-forward test of pace/possession efficiency, travel/timezone, and real-market-blend
features against NBA Structural v7, standalone and in combination (Phase 2 of the v8
overhaul). Reuses the same chronological 60/20/20 date-clustered split as ablate_nba_v7.py.
Fits/selects on train+validation only; holdout is touched once per committed candidate.

All new "features" here are injected as research-only overrides via UpcomingGame's existing
availability_points_gap / spread_away_line fields -- NOT new engine parameters wired into
nba_v7.py. This is a backtest-harness proxy to test the hypotheses cheaply; if any of them
clear baseline, promoting them to real engine parameters + train/serve-parity-safe serving
code is separate follow-up work, not done here.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from nba_v5_pit_walkforward import split_complete_dates

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v7 import NBAStructuralV7Engine

BOXSCORE_DIR = Path("data/availability/nba/espn_boxscores")

# Approximate arena coordinates (lat, lon) for the 30 current NBA franchises.
TEAM_LOCATIONS: dict[str, tuple[float, float]] = {
    "Atlanta Hawks": (33.7573, -84.3963),
    "Boston Celtics": (42.3662, -71.0621),
    "Brooklyn Nets": (40.6826, -73.9754),
    "Charlotte Hornets": (35.2251, -80.8392),
    "Chicago Bulls": (41.8807, -87.6742),
    "Cleveland Cavaliers": (41.4965, -81.6882),
    "Dallas Mavericks": (32.7905, -96.8103),
    "Denver Nuggets": (39.7487, -105.0077),
    "Detroit Pistons": (42.3410, -83.0552),
    "Golden State Warriors": (37.7680, -122.3877),
    "Houston Rockets": (29.7508, -95.3621),
    "Indiana Pacers": (39.7640, -86.1555),
    "LA Clippers": (33.9535, -118.3420),
    "Los Angeles Lakers": (34.0430, -118.2673),
    "Memphis Grizzlies": (35.1382, -90.0506),
    "Miami Heat": (25.7814, -80.1870),
    "Milwaukee Bucks": (43.0451, -87.9172),
    "Minnesota Timberwolves": (44.9795, -93.2761),
    "New Orleans Pelicans": (29.9490, -90.0821),
    "New York Knicks": (40.7505, -73.9934),
    "Oklahoma City Thunder": (35.4634, -97.5151),
    "Orlando Magic": (28.5392, -81.3839),
    "Philadelphia 76ers": (39.9012, -75.1720),
    "Phoenix Suns": (33.4457, -112.0712),
    "Portland Trail Blazers": (45.5316, -122.6668),
    "Sacramento Kings": (38.6491, -121.5181),
    "San Antonio Spurs": (29.4269, -98.4375),
    "Toronto Raptors": (43.6435, -79.3791),
    "Utah Jazz": (40.7683, -111.9011),
    "Washington Wizards": (38.8981, -77.0209),
}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


@dataclass
class EvalMetrics:
    sample_size: int
    mae_margin: float
    mae_total: float
    moneyline_accuracy: float
    moneyline_brier: float
    home_spread_acc: float
    away_spread_acc: float


def load_nba_games(path: Path) -> list[GameRecord]:
    with open(path, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows.sort(key=lambda r: r["event_start_utc"])
    return [
        GameRecord(
            event_id=str(r["event_id"]),
            event_start_utc=r["event_start_utc"],
            league="NBA",
            away_team=r["away_team"],
            home_team=r["home_team"],
            away_score=int(r["away_score"]),
            home_score=int(r["home_score"]),
            season_type=r.get("season_type", "unknown"),
            season_year=r.get("season_year"),
        )
        for r in rows
    ]


def load_boxscore_efficiency() -> dict[str, dict[str, dict[str, float]]]:
    """event_id -> team_name -> {points, possessions} from team-level box score stats."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    if not BOXSCORE_DIR.exists():
        return out
    for fp in BOXSCORE_DIR.glob("*.json"):
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))["payload"]
            teams = payload.get("boxscore", {}).get("teams", [])
            if len(teams) != 2:
                continue
            event_id = str(payload.get("header", {}).get("id") or fp.stem)
            per_team: dict[str, dict[str, float]] = {}
            for t in teams:
                name = t.get("team", {}).get("displayName")
                stats = {s["name"]: s.get("displayValue") for s in t.get("statistics", [])}
                fga = _attempt(stats.get("fieldGoalsMade-fieldGoalsAttempted"))
                fta = _attempt(stats.get("freeThrowsMade-freeThrowsAttempted"))
                oreb = _num(stats.get("offensiveRebounds"))
                tov = _num(stats.get("turnovers"))
                if None in (fga, fta, oreb, tov) or not name:
                    continue
                poss = fga - oreb + tov + 0.44 * fta
                per_team[name] = {"possessions": poss}
            if len(per_team) == 2:
                out[event_id] = per_team
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return out


def _attempt(display: str | None) -> float | None:
    if not display or "-" not in display:
        return None
    try:
        return float(display.split("-")[1])
    except ValueError:
        return None


def _num(display: str | None) -> float | None:
    if display is None:
        return None
    try:
        return float(display)
    except ValueError:
        return None


def load_odds() -> dict[str, dict[str, float]]:
    """event_id -> {spread_away_line, total_line} from the first available provider."""
    out: dict[str, dict[str, float]] = {}
    if not BOXSCORE_DIR.exists():
        return out
    for fp in BOXSCORE_DIR.glob("*.json"):
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))["payload"]
            pc = payload.get("pickcenter") or payload.get("odds") or []
            if not pc:
                continue
            event_id = str(payload.get("header", {}).get("id") or fp.stem)
            entry = pc[0]
            spread = entry.get("spread")
            ou = entry.get("overUnder")
            if spread is None or ou is None:
                continue
            out[event_id] = {"spread_away_line": float(spread), "total_line": float(ou)}
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return out


def build_pace_margin_overrides(
    all_games: list[GameRecord],
    eval_games: list[GameRecord],
    boxscore_eff: dict[str, dict[str, dict[str, float]]],
) -> dict[str, float]:
    """event_id -> possession-adjusted net-rating margin delta (home perspective, points)."""
    # team -> list of (start_iso, points_scored, points_allowed, possessions)
    by_team: dict[str, list[tuple[str, float, float, float]]] = defaultdict(list)
    for g in all_games:
        eff = boxscore_eff.get(g.event_id)
        if not eff or g.home_team not in eff or g.away_team not in eff:
            continue
        home_poss = eff[g.home_team]["possessions"]
        away_poss = eff[g.away_team]["possessions"]
        if home_poss <= 0 or away_poss <= 0:
            continue
        by_team[g.home_team].append((g.event_start_utc, float(g.home_score), float(g.away_score), home_poss))
        by_team[g.away_team].append((g.event_start_utc, float(g.away_score), float(g.home_score), away_poss))
    for games in by_team.values():
        games.sort(key=lambda r: r[0])

    def rolling_net_rtg(team: str, before: str, window: int = 10) -> float | None:
        games = [r for r in by_team.get(team, []) if r[0] < before]
        if len(games) < 3:
            return None
        games = games[-window:]
        ortg = sum(100.0 * r[1] / r[3] for r in games) / len(games)
        drtg = sum(100.0 * r[2] / r[3] for r in games) / len(games)
        return ortg - drtg

    overrides: dict[str, float] = {}
    for g in eval_games:
        home_net = rolling_net_rtg(g.home_team, g.event_start_utc)
        away_net = rolling_net_rtg(g.away_team, g.event_start_utc)
        if home_net is None or away_net is None:
            continue
        # Net-rating differential scaled to league-average pace (~99.2 poss) to express it
        # in raw expected-margin points, independent of the engine's own raw-point trends.
        overrides[g.event_id] = (home_net - away_net) * (99.2 / 100.0)
    return overrides


def build_travel_overrides(all_games: list[GameRecord], eval_games: list[GameRecord]) -> dict[str, float]:
    """event_id -> home-margin delta (points) from away-vs-home relative travel distance."""
    by_team: dict[str, list[tuple[str, str]]] = defaultdict(list)  # team -> [(start, city_team)]
    for g in all_games:
        by_team[g.home_team].append((g.event_start_utc, g.home_team))
        by_team[g.away_team].append((g.event_start_utc, g.home_team))
    for games in by_team.values():
        games.sort(key=lambda r: r[0])

    def prior_location(team: str, before: str) -> str | None:
        games = [r for r in by_team.get(team, []) if r[0] < before]
        return games[-1][1] if games else None

    overrides: dict[str, float] = {}
    for g in eval_games:
        if g.home_team not in TEAM_LOCATIONS or g.away_team not in TEAM_LOCATIONS:
            continue
        home_prev_city = prior_location(g.home_team, g.event_start_utc) or g.home_team
        away_prev_city = prior_location(g.away_team, g.event_start_utc) or g.away_team
        if home_prev_city not in TEAM_LOCATIONS or away_prev_city not in TEAM_LOCATIONS:
            continue
        home_travel_km = haversine_km(TEAM_LOCATIONS[home_prev_city], TEAM_LOCATIONS[g.home_team])
        away_travel_km = haversine_km(TEAM_LOCATIONS[away_prev_city], TEAM_LOCATIONS[g.home_team])
        # Away team traveling further than home team hurts the away team -> raises home margin.
        overrides[g.event_id] = (away_travel_km - home_travel_km) / 1000.0
    return overrides


def evaluate_engine(
    engine: NBAStructuralV7Engine,
    all_games: list[GameRecord],
    eval_games: list[GameRecord],
    margin_overrides: dict[str, float] | None = None,
    odds: dict[str, dict[str, float]] | None = None,
    use_real_odds: bool = False,
) -> EvalMetrics:
    margin_overrides = margin_overrides or {}
    odds = odds or {}
    dates_map: dict[str, list[GameRecord]] = defaultdict(list)
    for g in eval_games:
        dates_map[g.event_start_utc[:10]].append(g)
    sorted_dates = sorted(dates_map.keys())

    rows: list[dict[str, Any]] = []
    for d in sorted_dates:
        games_today = dates_map[d]
        first_start = games_today[0].event_start_utc
        history = [g for g in all_games if g.event_start_utc < first_start]
        upcoming = []
        for g in games_today:
            o = odds.get(g.event_id) if use_real_odds else None
            upcoming.append(
                UpcomingGame(
                    event_id=g.event_id,
                    event_start_utc=g.event_start_utc,
                    away_team=g.away_team,
                    home_team=g.home_team,
                    spread_away_line=(o["spread_away_line"] if o else 0.0),
                    total_line=(o["total_line"] if o else 228.5),
                    availability_points_gap=margin_overrides.get(g.event_id, 0.0),
                    league="NBA",
                )
            )
        preds = engine.predict_games(history, upcoming)
        by_event: dict[str, dict[str, Any]] = defaultdict(dict)
        for p in preds:
            by_event[p.event_id][p.market_type] = p

        for g in games_today:
            if use_real_odds and g.event_id not in odds:
                continue  # only score games where a real line exists, for the market-blend test
            actual_margin = g.home_score - g.away_score
            actual_total = g.home_score + g.away_score
            actual_home_win = 1.0 if actual_margin > 0 else 0.0

            ml_pred = by_event[g.event_id].get("moneyline")
            spread_pred = by_event[g.event_id].get("spread")
            total_pred = by_event[g.event_id].get("total")

            exp_margin = float(spread_pred.feature_basis.get("expected_margin", 0.0)) if spread_pred else 0.0
            exp_total = float(total_pred.feature_basis.get("expected_total", 228.5)) if total_pred else 228.5
            p_home = max(
                1e-4, min(1 - 1e-4, float(ml_pred.probabilities.get("home", 0.5)) if ml_pred else 0.5)
            )

            pred_home_covers_zero = 1.0 if exp_margin > 0 else 0.0
            actual_home_covers_zero = 1.0 if actual_margin > 0 else 0.0
            spread_correct = 1.0 if pred_home_covers_zero == actual_home_covers_zero else 0.0

            rows.append(
                {
                    "abs_err_margin": abs(exp_margin - actual_margin),
                    "abs_err_total": abs(exp_total - actual_total),
                    "brier": (p_home - actual_home_win) ** 2,
                    "correct": 1.0 if (p_home >= 0.5) == bool(actual_home_win) else 0.0,
                    "home_spread_correct": spread_correct,
                    "is_home_side_pred": pred_home_covers_zero,
                }
            )

    n = len(rows)
    if n == 0:
        return EvalMetrics(
            0, float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), float("nan")
        )
    home_side = [r for r in rows if r["is_home_side_pred"] == 1.0]
    away_side = [r for r in rows if r["is_home_side_pred"] == 0.0]
    return EvalMetrics(
        sample_size=n,
        mae_margin=round(float(np.mean([r["abs_err_margin"] for r in rows])), 4),
        mae_total=round(float(np.mean([r["abs_err_total"] for r in rows])), 4),
        moneyline_accuracy=round(float(np.mean([r["correct"] for r in rows])), 4),
        moneyline_brier=round(float(np.mean([r["brier"] for r in rows])), 4),
        home_spread_acc=round(float(np.mean([r["home_spread_correct"] for r in home_side])), 4)
        if home_side
        else float("nan"),
        away_spread_acc=round(float(np.mean([r["home_spread_correct"] for r in away_side])), 4)
        if away_side
        else float("nan"),
    )


def combine(a: dict[str, float], b: dict[str, float], wa: float, wb: float) -> dict[str, float]:
    keys = set(a) | set(b)
    return {k: wa * a.get(k, 0.0) + wb * b.get(k, 0.0) for k in keys}


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    _train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    boxscore_eff = load_boxscore_efficiency()
    odds = load_odds()
    print(f"Box-score efficiency coverage: {len(boxscore_eff)}/{len(all_games)} games")
    print(f"Real odds coverage: {len(odds)}/{len(all_games)} games")
    val_odds_n = sum(1 for g in val_games if g.event_id in odds)
    hold_odds_n = sum(1 for g in holdout_games if g.event_id in odds)
    print(
        f"Odds coverage in validation: {val_odds_n}/{len(val_games)}; in holdout: {hold_odds_n}/{len(holdout_games)}"
    )

    baseline = NBAStructuralV7Engine()
    baseline_val = evaluate_engine(baseline, all_games, val_games)
    print("\n--- BASELINE v7 ON VALIDATION (full set) ---")
    print(baseline_val)

    pace_overrides_val = build_pace_margin_overrides(all_games, val_games, boxscore_eff)
    travel_overrides_val = build_travel_overrides(all_games, val_games)
    print(f"\nPace overrides computed for {len(pace_overrides_val)}/{len(val_games)} validation games")
    print(f"Travel overrides computed for {len(travel_overrides_val)}/{len(val_games)} validation games")

    results: dict[str, EvalMetrics] = {}
    weight_candidates = [0.0, 0.25, 0.5, 1.0]

    print("\n=== FEATURE 3 (pace/possession net-rating) STANDALONE, VALIDATION ===")
    for w in weight_candidates:
        ov = {k: w * v for k, v in pace_overrides_val.items()}
        m = evaluate_engine(baseline, all_games, val_games, margin_overrides=ov)
        results[f"pace_w{w}"] = m
        print(f"weight={w}: {m}")

    print("\n=== FEATURE 4 (travel distance) STANDALONE, VALIDATION ===")
    for w in weight_candidates:
        ov = {k: w * v for k, v in travel_overrides_val.items()}
        m = evaluate_engine(baseline, all_games, val_games, margin_overrides=ov)
        results[f"travel_w{w}"] = m
        print(f"weight={w}: {m}")

    print("\n=== MARKET BLEND (real odds subset only), VALIDATION ===")
    for bw in [0.0, 0.2, 0.35, 0.5]:
        eng = NBAStructuralV7Engine(market_blend_weight=bw)
        m = evaluate_engine(eng, all_games, val_games, odds=odds, use_real_odds=True)
        results[f"market_blend_{bw}"] = m
        print(f"blend_weight={bw}: {m}")

    print("\n=== COMBINATIONS (pace+travel), VALIDATION, full set ===")
    combo_candidates = {
        "pace0.5_travel0.5": combine(pace_overrides_val, travel_overrides_val, 0.5, 0.5),
        "pace1.0_travel0.5": combine(pace_overrides_val, travel_overrides_val, 1.0, 0.5),
        "pace0.5_travel1.0": combine(pace_overrides_val, travel_overrides_val, 0.5, 1.0),
        "pace1.0_travel1.0": combine(pace_overrides_val, travel_overrides_val, 1.0, 1.0),
    }
    for name, ov in combo_candidates.items():
        m = evaluate_engine(baseline, all_games, val_games, margin_overrides=ov)
        results[name] = m
        print(f"{name}: {m}")

    # Select best candidate by Brier (primary metric per prior-round lesson), with a
    # minimum-coverage-eligible pool (exclude market_blend_* from this pool since it's scored
    # against the odds-subset, not the full validation set -- not comparable).
    pool = {
        k: v
        for k, v in results.items()
        if not k.startswith("market_blend_") and not math.isnan(v.moneyline_brier)
    }
    best_name = min(pool, key=lambda k: pool[k].moneyline_brier)
    print(f"\n=== SELECTED ON VALIDATION (full-set pool): {best_name} -> {pool[best_name]} ===")
    print(f"(baseline for reference: {baseline_val})")

    best_market_name = min(
        (k for k in results if k.startswith("market_blend_")),
        key=lambda k: results[k].moneyline_brier,
    )
    print(
        f"Best market-blend candidate (odds subset only): {best_market_name} -> {results[best_market_name]}"
    )

    # --- Locked holdout, touched once, for the two selected candidates ---
    pace_overrides_hold = build_pace_margin_overrides(all_games, holdout_games, boxscore_eff)
    travel_overrides_hold = build_travel_overrides(all_games, holdout_games)

    def overrides_for(name: str) -> dict[str, float]:
        if name.startswith("pace_w"):
            w = float(name.split("pace_w")[1])
            return {k: w * v for k, v in pace_overrides_hold.items()}
        if name.startswith("travel_w"):
            w = float(name.split("travel_w")[1])
            return {k: w * v for k, v in travel_overrides_hold.items()}
        if name.startswith("pace") and "travel" in name:
            parts = name.replace("pace", "").replace("travel", " ").split()
            wa, wb = float(parts[0]), float(parts[1])
            return combine(pace_overrides_hold, travel_overrides_hold, wa, wb)
        return {}

    print("\n--- BASELINE v7 ON LOCKED HOLDOUT (touched once, for comparison) ---")
    baseline_hold = evaluate_engine(baseline, all_games, holdout_games)
    print(baseline_hold)

    print(f"\n--- SELECTED CANDIDATE ({best_name}) ON LOCKED HOLDOUT (touched once) ---")
    hold_metrics = evaluate_engine(
        baseline, all_games, holdout_games, margin_overrides=overrides_for(best_name)
    )
    print(hold_metrics)

    bw = float(best_market_name.split("market_blend_")[1])
    eng = NBAStructuralV7Engine(market_blend_weight=bw)
    print(f"\n--- MARKET BLEND {best_market_name} ON LOCKED HOLDOUT ODDS SUBSET (touched once) ---")
    market_hold = evaluate_engine(eng, all_games, holdout_games, odds=odds, use_real_odds=True)
    print(market_hold)
    market_hold_baseline = evaluate_engine(baseline, all_games, holdout_games, odds=odds, use_real_odds=True)
    print(f"(baseline v7 on same odds subset, for fair comparison): {market_hold_baseline}")

    out = {
        "split_meta": meta,
        "boxscore_coverage": len(boxscore_eff),
        "odds_coverage": len(odds),
        "validation_results": {k: vars(v) for k, v in results.items()},
        "baseline_validation": vars(baseline_val),
        "selected_full_set_candidate": best_name,
        "selected_full_set_holdout": vars(hold_metrics),
        "baseline_holdout": vars(baseline_hold),
        "selected_market_blend_candidate": best_market_name,
        "selected_market_blend_holdout": vars(market_hold),
        "baseline_holdout_on_odds_subset": vars(market_hold_baseline),
    }
    Path("scratch").mkdir(exist_ok=True)
    Path("scratch/nba_v7_combo_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_v7_combo_result.json")


if __name__ == "__main__":
    run()
