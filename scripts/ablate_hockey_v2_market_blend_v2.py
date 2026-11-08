"""Walk-forward market-blend test for hockey-poisson-dc-v2 (NHL), round 2.

Round 1 (scripts/ablate_hockey_v2_market_blend.py) was blocked: the Kaggle
jonathanncoletti/nhl-historical-game-data dataset duplicated favorite_moneyline
identically across both team-rows with no real home/away split, so no honest
market probability could be derived.

This round uses minmaxer/nhl-closing-odds-2025-26 + minmaxer/nhl-odds-2026-27
(CC BY 4.0), which have explicit open_home_win_prob / close_home_win_prob
columns (de-vigged fair probabilities, already home-team-specific) plus full
team names -- no favorite-side inference needed. Opening line is tested as the
PRIMARY (realistic, pregame-available) signal; closing line is secondary /
optimistic-upper-bound only, per the NBA investigation's lesson.
"""

from __future__ import annotations

import csv
import json
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
from model_prediction.models.hockey import HockeyModel, UpcomingHockeyMatch

MARKET_CSVS = [
    Path(
        r"C:\Users\Vince\AppData\Local\Temp\claude\e--MP\55e4a91b-7011-45bc-b2e9-c87fbc97cb1f"
        r"\scratchpad\nhl_odds_search\closing_2025_26\nhl_2025_26_closing_odds.csv"
    ),
    Path(
        r"C:\Users\Vince\AppData\Local\Temp\claude\e--MP\55e4a91b-7011-45bc-b2e9-c87fbc97cb1f"
        r"\scratchpad\nhl_odds_search\odds_2026_27\nhl_2026_27_odds.csv"
    ),
]


def load_hockey_games(path: Path) -> list[GameRecord]:
    with open(path, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = [
        r
        for r in rows
        if r.get("status") == "completed"
        or (r.get("home_score") is not None and r.get("away_score") is not None)
    ]
    rows.sort(key=lambda r: r["event_start_utc"])
    out = []
    for r in rows:
        try:
            hs, as_ = int(r["home_score"]), int(r["away_score"])
        except (TypeError, ValueError):
            continue
        out.append(
            GameRecord(
                event_id=str(r["event_id"]),
                event_start_utc=r["event_start_utc"],
                league=r.get("league", "NHL"),
                away_team=r["away_team"],
                home_team=r["home_team"],
                away_score=as_,
                home_score=hs,
                season_type=r.get("season_type", "unknown"),
                season_year=r.get("season_year"),
            )
        )
    return out


def load_market(paths: list[Path]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Return {(date, home_team, away_team): {open_p_home, close_p_home}}."""
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date = row["date_et"][:10]
                home = row["home_team"]
                away = row["away_team"]
                try:
                    open_p = float(row["open_home_win_prob"]) if row.get("open_home_win_prob") else None
                except ValueError:
                    open_p = None
                try:
                    close_p = float(row["close_home_win_prob"]) if row.get("close_home_win_prob") else None
                except ValueError:
                    close_p = None
                if open_p is None and close_p is None:
                    continue
                out[(date, home, away)] = {"open_p_home": open_p, "close_p_home": close_p}
    return out


@dataclass
class EvalMetrics:
    sample_size: int
    matched_with_market: int
    moneyline_accuracy: float
    moneyline_brier: float


def evaluate_model(
    model: HockeyModel,
    all_games: list[GameRecord],
    eval_games: list[GameRecord],
    market: dict[tuple[str, str, str], dict[str, Any]],
    blend_weight: float,
    line_field: str,
) -> EvalMetrics:
    dates_map: dict[str, list[GameRecord]] = defaultdict(list)
    for g in eval_games:
        dates_map[g.event_start_utc[:10]].append(g)
    sorted_dates = sorted(dates_map.keys())

    rows: list[dict[str, Any]] = []
    matched = 0
    for d in sorted_dates:
        games_today = dates_map[d]
        first_start = games_today[0].event_start_utc
        history = [g for g in all_games if g.event_start_utc < first_start]
        upcoming = [
            UpcomingHockeyMatch(
                event_id=g.event_id,
                event_start_utc=g.event_start_utc,
                away_team=g.away_team,
                home_team=g.home_team,
                league="NHL",
            )
            for g in games_today
        ]
        preds = model.predict_games(history, upcoming)
        by_event: dict[str, dict[str, Any]] = defaultdict(dict)
        for p in preds:
            by_event[p.event_id][p.market_type] = p

        for g in games_today:
            actual_margin = g.home_score - g.away_score
            actual_home_win = 1.0 if actual_margin > 0 else 0.0
            ml_pred = by_event[g.event_id].get("moneyline")
            p_home_model = max(
                1e-4, min(1 - 1e-4, float(ml_pred.probabilities.get("home", 0.5)) if ml_pred else 0.5)
            )

            key = (d, g.home_team, g.away_team)
            mkt = market.get(key)
            p_home_final = p_home_model
            if mkt is not None and blend_weight > 0.0:
                p_home_market = mkt.get(line_field)
                if p_home_market is not None:
                    p_home_final = (1.0 - blend_weight) * p_home_model + blend_weight * p_home_market
                    matched += 1

            rows.append(
                {
                    "brier": (p_home_final - actual_home_win) ** 2,
                    "correct": 1.0 if (p_home_final >= 0.5) == bool(actual_home_win) else 0.0,
                }
            )

    n = len(rows)
    return EvalMetrics(
        sample_size=n,
        matched_with_market=matched,
        moneyline_accuracy=round(float(np.mean([r["correct"] for r in rows])), 4) if rows else float("nan"),
        moneyline_brier=round(float(np.mean([r["brier"] for r in rows])), 4) if rows else float("nan"),
    )


def run_for_line_field(
    label: str,
    line_field: str,
    model: HockeyModel,
    nhl_games: list[GameRecord],
    val_games: list[GameRecord],
    holdout_games: list[GameRecord],
    market: dict[tuple[str, str, str], dict[str, Any]],
) -> None:
    print(f"\n==================== {label} ====================")
    weights = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    results: dict[float, EvalMetrics] = {}
    for w in weights:
        m = evaluate_model(model, nhl_games, val_games, market, w, line_field)
        results[w] = m
        print(f"val blend={w:.1f}: {m}")

    valid = {w: r for w, r in results.items() if not np.isnan(r.moneyline_brier)}
    if not valid:
        print("No valid validation results (no market coverage) -- skipping holdout.")
        return
    best_w = min(valid, key=lambda w: valid[w].moneyline_brier)
    print(f"\nSelected blend weight on validation ({label}): {best_w}")

    print(f"--- {label}: SELECTED CANDIDATE ON LOCKED HOLDOUT (touched once) ---")
    holdout_baseline = evaluate_model(model, nhl_games, holdout_games, market, 0.0, line_field)
    holdout_selected = evaluate_model(model, nhl_games, holdout_games, market, best_w, line_field)
    print("holdout baseline (blend=0.0):", holdout_baseline)
    print(f"holdout selected (blend={best_w}):", holdout_selected)


def run() -> None:
    games_path = Path("data/historical/hockey_games_all.jsonl")
    all_games = load_hockey_games(games_path)
    nhl_games = [g for g in all_games if g.league.upper() == "NHL"]
    print(
        f"NHL-only: {len(nhl_games)} games, {nhl_games[0].event_start_utc if nhl_games else 'n/a'} to "
        f"{nhl_games[-1].event_start_utc if nhl_games else 'n/a'}"
    )

    market = load_market(MARKET_CSVS)
    print(f"Market rows parsed into {len(market)} unique games")

    # The market dataset only covers a narrow recent window (2025-26 + 2026-27
    # seasons). A plain chronological 60/20/20 split over the FULL 2021-2026
    # history puts that entire window inside the holdout slice, leaving zero
    # market coverage in validation -- which makes blend-weight selection
    # impossible under the walk-forward rule (can't legitimately pick a weight
    # on a validation set with no signal to pick from). Instead: restrict the
    # eval pool itself to the market-covered date range, split THAT 50/50 into
    # val/holdout, while keeping the full 2021+ history available as model
    # context for every prediction (so the model still has a normal amount of
    # prior-games history at each decision point).
    market_dates = sorted({d for (d, _, _) in market})
    min_market_date, max_market_date = market_dates[0], market_dates[-1]
    eval_pool = [g for g in nhl_games if min_market_date <= g.event_start_utc[:10] <= max_market_date]
    print(f"Market date range: {min_market_date} to {max_market_date}; eval pool: {len(eval_pool)} games")

    _train_games, val_games, holdout_games, meta = split_complete_dates(eval_pool, 0.0, 0.50)
    print("Data split (restricted to market-covered window):", meta)
    print(f"val={len(val_games)} holdout={len(holdout_games)}")

    all_eval = val_games + holdout_games
    n_eval = len(all_eval)
    n_cov = sum(1 for g in all_eval if (g.event_start_utc[:10], g.home_team, g.away_team) in market)
    print(
        f"Coverage: {n_cov}/{n_eval} eval games matched to market data ({n_cov / n_eval:.1%})"
        if n_eval
        else "no eval games"
    )

    model = HockeyModel()  # v2, the current champion

    run_for_line_field(
        "OPENING LINE (primary, realistic)", "open_p_home", model, nhl_games, val_games, holdout_games, market
    )
    run_for_line_field(
        "CLOSING LINE (secondary, optimistic upper bound)",
        "close_p_home",
        model,
        nhl_games,
        val_games,
        holdout_games,
        market,
    )


if __name__ == "__main__":
    run()
