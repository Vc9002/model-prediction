"""Sweep the box-score-derived availability gap's point-value scaling against
the NBA v7 engine, using the confirmed-DNP-reason signal from
``ablate_nba_real_availability.py`` (not the noisy zero-minutes heuristic).

The underlying per-player values (``rolling_player_values``, Empirical-Bayes
shrunk BPM proxy) were never fit against actual NBA margin/total outcomes --
they were carried over from the WNBA convention. This sweeps a single scale
multiplier on the resulting team-level point gap (and proportionally on the
total-loss term) to see whether the *signal* just needs rescaling, fit on
validation only, confirmed once on the locked holdout.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ablate_nba_v7 import EvalMetrics, load_nba_games
from nba_v5_pit_walkforward import split_complete_dates

from model_prediction.features.nba_player_availability import MAX_TEAM_LOSS_PTS
from model_prediction.features.nba_player_logs import (
    box_plus_minus_proxy,
    is_confirmed_unavailable,
    load_nba_player_boxscores,
    rolling_player_values,
)
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v7 import NBAStructuralV7Engine

BOX_DIR = Path("data/availability/nba/espn_boxscores")
LOOKBACK = 15
MEAN_MINUTES_FLOOR = 8.0
SCALE_GRID = [0.0, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]


def build_team_player_timeline(all_games: list, boxscores: dict) -> dict[str, list[tuple[str, list[dict]]]]:
    by_team: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for g in all_games:
        box = boxscores.get(g.event_id)
        if not box:
            continue
        for team in (g.home_team, g.away_team):
            rows = box.get(team)
            if rows:
                by_team[team].append((g.event_id, rows))
    return by_team


def team_loss_from_window(window: list[tuple[str, list[dict]]]) -> float:
    if len(window) < 2:
        return 0.0
    _, most_recent_rows = window[-1]
    recent_out = {r["player_id"] for r in most_recent_rows if is_confirmed_unavailable(r)}

    logs_by_player: dict[str, list[tuple[str, float]]] = defaultdict(list)
    minutes_by_player: dict[str, list[float]] = defaultdict(list)
    for eid, rows in window:
        for r in rows:
            pid = r["player_id"]
            logs_by_player[pid].append((eid, box_plus_minus_proxy(r)))
            minutes_by_player[pid].append(r["minutes"])

    values = rolling_player_values(logs_by_player, lookback=LOOKBACK)
    loss = 0.0
    for pid, val in values.items():
        mean_minutes = sum(minutes_by_player[pid]) / len(minutes_by_player[pid])
        if mean_minutes >= MEAN_MINUTES_FLOOR and pid in recent_out:
            loss += val
    return min(MAX_TEAM_LOSS_PTS, loss)


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    boxscores = load_nba_player_boxscores(BOX_DIR)
    team_timeline_full = build_team_player_timeline(all_games, boxscores)
    event_start: dict[str, str] = {g.event_id: g.event_start_utc for g in all_games}

    _train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    def raw_gap_for_game(home_team: str, away_team: str, cutoff_start_utc: str) -> float:
        def loss_for(team: str) -> float:
            entries = team_timeline_full.get(team, [])
            prior = [(eid, rows) for eid, rows in entries if event_start.get(eid, "") < cutoff_start_utc]
            return team_loss_from_window(prior[-LOOKBACK:])

        return loss_for(away_team) - loss_for(home_team)

    def build_upcoming(games_today: list, raw_gaps: dict[str, float], scale: float) -> list[UpcomingGame]:
        out = []
        for g in games_today:
            gap = round(raw_gaps[g.event_id] * scale, 3)
            out.append(
                UpcomingGame(
                    event_id=g.event_id,
                    event_start_utc=g.event_start_utc,
                    away_team=g.away_team,
                    home_team=g.home_team,
                    spread_away_line=0.0,
                    total_line=228.5,
                    league="NBA",
                    availability_points_gap=gap,
                    availability_total_loss=abs(gap) * 0.5,
                )
            )
        return out

    def evaluate(engine: NBAStructuralV7Engine, eval_games: list, scale: float) -> EvalMetrics:
        dates_map: dict[str, list] = defaultdict(list)
        for g in eval_games:
            dates_map[g.event_start_utc[:10]].append(g)
        rows = []
        for d in sorted(dates_map.keys()):
            games_today = dates_map[d]
            first_start = games_today[0].event_start_utc
            history = [g for g in all_games if g.event_start_utc < first_start]
            raw_gaps = {
                g.event_id: raw_gap_for_game(g.home_team, g.away_team, first_start) for g in games_today
            }
            upcoming = build_upcoming(games_today, raw_gaps, scale)
            preds = engine.predict_games(history, upcoming)
            by_event: dict[str, dict] = defaultdict(dict)
            for p in preds:
                by_event[p.event_id][p.market_type] = p
            for g in games_today:
                actual_margin = g.home_score - g.away_score
                actual_home_win = 1.0 if actual_margin > 0 else 0.0
                ml_pred = by_event[g.event_id].get("moneyline")
                spread_pred = by_event[g.event_id].get("spread")
                exp_margin = (
                    float(spread_pred.feature_basis.get("expected_margin", 0.0)) if spread_pred else 0.0
                )
                p_home = max(
                    1e-4, min(1 - 1e-4, float(ml_pred.probabilities.get("home", 0.5)) if ml_pred else 0.5)
                )
                rows.append(
                    {
                        "abs_err_margin": abs(exp_margin - actual_margin),
                        "brier": (p_home - actual_home_win) ** 2,
                        "correct": 1.0 if (p_home >= 0.5) == bool(actual_home_win) else 0.0,
                    }
                )
        n = len(rows)
        return EvalMetrics(
            sample_size=n,
            mae_margin=round(float(np.mean([r["abs_err_margin"] for r in rows])), 4),
            mae_total=float("nan"),
            moneyline_accuracy=round(float(np.mean([r["correct"] for r in rows])), 4),
            moneyline_brier=round(float(np.mean([r["brier"] for r in rows])), 4),
            home_spread_acc=float("nan"),
            away_spread_acc=float("nan"),
            early_season_over_rate=float("nan"),
        )

    engine = NBAStructuralV7Engine()

    print("\n--- VALIDATION sweep over availability-gap scale ---")
    val_results: dict[str, dict] = {}
    for scale in SCALE_GRID:
        m = evaluate(engine, val_games, scale)
        val_results[str(scale)] = vars(m)
        print(
            f"scale={scale}: brier={m.moneyline_brier} acc={m.moneyline_accuracy} mae_margin={m.mae_margin}"
        )

    baseline_brier = val_results["0.0"]["moneyline_brier"]
    baseline_mae = val_results["0.0"]["mae_margin"]
    candidates = [
        float(s)
        for s, m in val_results.items()
        if float(s) > 0.0 and m["moneyline_brier"] <= baseline_brier and m["mae_margin"] <= baseline_mae
    ]
    selected = min(candidates, key=lambda s: val_results[str(s)]["moneyline_brier"]) if candidates else 0.0
    print(f"\nSelected scale (best brier among those beating baseline on both metrics): {selected}")

    print("\n--- LOCKED HOLDOUT (touched once): baseline (scale=0.0) vs selected scale ---")
    holdout_baseline = evaluate(engine, holdout_games, 0.0)
    holdout_selected = evaluate(engine, holdout_games, selected)
    print("baseline:", holdout_baseline)
    print("selected:", holdout_selected)

    out = {
        "split_meta": meta,
        "validation_sweep": val_results,
        "selected_scale": selected,
        "holdout_baseline": vars(holdout_baseline),
        "holdout_selected": vars(holdout_selected),
    }
    Path("scratch").mkdir(exist_ok=True)
    Path("scratch/nba_availability_scale_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_availability_scale_result.json")


if __name__ == "__main__":
    run()
