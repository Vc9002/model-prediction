"""Walk-forward market-blend test for hockey-poisson-dc-v2 (NHL), mirroring the
NBA v7 investigation pattern (scripts/ablate_nba_v7.py). Uses the same
chronological 60/20/20 date-clustered split as nba_v5_pit_walkforward.split_complete_dates.
Fits/selects blend weight on validation only; holdout is touched once, at the end,
for the single selected candidate.

Market data: Kaggle `jonathanncoletti/nhl-historical-game-data` (CC0-1.0), matched
by (date, home_team, away_team) full ESPN team names. favorite_moneyline/spread/
over_under are CLOSING lines only -- this dataset has no opening-line field, so
results here are an optimistic upper bound, not a realistic deployable estimate
(same caveat the NBA investigation flagged for its closing-line-only round).
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

KAGGLE_CSV = Path(
    r"C:\Users\Vince\AppData\Local\Temp\claude\e--MP\55e4a91b-7011-45bc-b2e9-c87fbc97cb1f"
    r"\scratchpad\kaggle_nhl_odds\nhl_data_extensive.csv"
)


def american_to_prob(ml: float) -> float:
    if ml < 0:
        return (-ml) / ((-ml) + 100.0)
    return 100.0 / (ml + 100.0)


def load_hockey_games(path: Path) -> list[GameRecord]:
    with open(path, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = [
        r
        for r in rows
        if r.get("status") == "final" or (r.get("home_score") is not None and r.get("away_score") is not None)
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


def load_kaggle_market(path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Return {(date, home_team, away_team): {home_ml_prob, away_ml_prob}} using
    is_home rows only, deriving each side's moneyline from favorite_moneyline +
    whos_favored (the CSV stores one moneyline per row keyed to whichever team is
    favored, not separate home/away columns)."""
    by_game: dict[str, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gid = row["game_id"]
            date = row["date"][:10]
            is_home = row["is_home"] == "1" or row["is_home"].strip() == "1"
            entry = by_game.setdefault(gid, {"date": date})
            team = row["team_name"]
            if is_home:
                entry["home_team"] = team
            else:
                entry["away_team"] = team
            fav_ml = row.get("favorite_moneyline", "")
            entry["favorite_moneyline_" + ("home" if is_home else "away")] = fav_ml
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for gid, e in by_game.items():
        if "home_team" not in e or "away_team" not in e:
            continue
        # The favorite_moneyline column is duplicated per-row (team-centric), but it
        # is the SAME game-level market price regardless of which row reports it, so
        # take whichever side actually has a non-empty, non-zero value.
        ml_home_raw = e.get("favorite_moneyline_home", "")
        ml_away_raw = e.get("favorite_moneyline_away", "")
        try:
            ml_home = float(ml_home_raw) if ml_home_raw not in ("", None) else None
        except ValueError:
            ml_home = None
        try:
            ml_away = float(ml_away_raw) if ml_away_raw not in ("", None) else None
        except ValueError:
            ml_away = None
        if ml_home is None and ml_away is None:
            continue
        out[(e["date"], e["home_team"], e["away_team"])] = {
            "ml_home": ml_home,
            "ml_away": ml_away,
        }
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
                ml_home, ml_away = mkt.get("ml_home"), mkt.get("ml_away")
                if ml_home is not None and ml_away is not None:
                    raw_home = american_to_prob(ml_home)
                    raw_away = american_to_prob(ml_away)
                    total = raw_home + raw_away
                    if total > 0:
                        p_home_market = raw_home / total  # de-vig
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


def run() -> None:
    games_path = Path("data/historical/hockey_games_all.jsonl")
    all_games = load_hockey_games(games_path)
    print(f"Loaded {len(all_games)} completed hockey games total (all leagues present in file)")
    nhl_games = [g for g in all_games if g.league.upper() == "NHL"]
    print(
        f"NHL-only: {len(nhl_games)} games, {nhl_games[0].event_start_utc if nhl_games else 'n/a'} to "
        f"{nhl_games[-1].event_start_utc if nhl_games else 'n/a'}"
    )

    train_games, val_games, holdout_games, meta = split_complete_dates(nhl_games, 0.60, 0.20)
    print("Data split:", meta)
    print(f"train={len(train_games)} val={len(val_games)} holdout={len(holdout_games)}")

    market = load_kaggle_market(KAGGLE_CSV)
    print(f"Kaggle market rows parsed into {len(market)} unique games")

    # Coverage check against val+holdout
    all_eval = val_games + holdout_games
    n_eval = len(all_eval)
    n_cov = sum(1 for g in all_eval if (g.event_start_utc[:10], g.home_team, g.away_team) in market)
    print(
        f"Coverage: {n_cov}/{n_eval} eval games matched to Kaggle market data ({n_cov / n_eval:.1%})"
        if n_eval
        else "no eval games"
    )

    model = HockeyModel()  # v2, the current champion

    print("\n--- BASELINE (blend=0.0) ON VALIDATION ---")
    baseline_val = evaluate_model(model, nhl_games, val_games, market, 0.0)
    print(baseline_val)

    weights = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    results: dict[float, EvalMetrics] = {}
    for w in weights:
        m = evaluate_model(model, nhl_games, val_games, market, w)
        results[w] = m
        print(f"blend={w:.1f}: {m}")

    best_w = min(
        weights, key=lambda w: results[w].moneyline_brier if not np.isnan(results[w].moneyline_brier) else 999
    )
    print(f"\nSelected blend weight on validation: {best_w}")

    print("\n--- SELECTED CANDIDATE ON LOCKED HOLDOUT (touched once) ---")
    holdout_baseline = evaluate_model(model, nhl_games, holdout_games, market, 0.0)
    holdout_selected = evaluate_model(model, nhl_games, holdout_games, market, best_w)
    print("holdout baseline (blend=0.0):", holdout_baseline)
    print(f"holdout selected (blend={best_w}):", holdout_selected)


if __name__ == "__main__":
    run()
