"""Phase 2 validation (real-data variant): does a box-score-derived real
per-player availability signal beat the v7 baseline (which carries zero
historical availability_points_gap -- there is no historical injury-report
archive in this repo, only live-forward injury snapshots) on the same
60/20/20 chronological walk-forward used by ``ablate_nba_v7.py``?

Methodology (point-in-time valid, same proxy convention as
``features/wnba_player_logs.py``'s "recently missing" inference): for each
evaluated game, a player counts as "out" if they appear in their team's
rolling rotation (>=8 mean minutes over the trailing window) but logged
zero minutes in that team's most recently completed game BEFORE the
evaluated game. Their point value comes from
``features/nba_player_logs.rolling_player_values`` (box-score-derived,
Empirical-Bayes-shrunk), built only from games strictly before the
evaluated game -- no leakage.

This does not require a historical injury-report archive (which does not
exist for past seasons); it tests the real per-player rating itself as an
availability signal, using actual confirmed box-score absences as ground
truth instead of a text status that was never archived historically.

Compares the SAME v7 engine fed (a) availability_points_gap=0.0 always
(today's real historical behavior -- no availability signal exists for
past games) vs (b) the real computed gap above, on validation then the
locked holdout touched once.
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
    load_nba_player_boxscores,
    rolling_player_values,
)
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v7 import NBAStructuralV7Engine

BOX_DIR = Path("data/availability/nba/espn_boxscores")
LOOKBACK = 15
MEAN_MINUTES_FLOOR = 8.0


def build_team_player_timeline(all_games: list, boxscores: dict) -> dict[str, list[tuple[str, list[dict]]]]:
    """``{team_name: [(event_id, [player_row, ...]), ...]}`` in chronological order."""
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
    recent_minutes = {r["player_id"]: r["minutes"] for r in most_recent_rows}

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
        if mean_minutes >= MEAN_MINUTES_FLOOR and recent_minutes.get(pid, 0.0) == 0.0:
            loss += val
    return min(MAX_TEAM_LOSS_PTS, loss)


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    boxscores = load_nba_player_boxscores(BOX_DIR)
    coverage = sum(1 for g in all_games if g.event_id in boxscores)
    print(f"boxscore coverage: {coverage}/{len(all_games)} games ({coverage / max(1, len(all_games)):.1%})")

    team_timeline_full = build_team_player_timeline(all_games, boxscores)
    event_start: dict[str, str] = {g.event_id: g.event_start_utc for g in all_games}

    _train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    def gap_for_game(home_team: str, away_team: str, cutoff_start_utc: str) -> float:
        def loss_for(team: str) -> float:
            entries = team_timeline_full.get(team, [])
            prior = [(eid, rows) for eid, rows in entries if event_start.get(eid, "") < cutoff_start_utc]
            return team_loss_from_window(prior[-LOOKBACK:])

        home_loss = loss_for(home_team)
        away_loss = loss_for(away_team)
        return round(away_loss - home_loss, 3)

    def build_upcoming(games_today: list, use_real_gap: bool) -> list[UpcomingGame]:
        first_start = games_today[0].event_start_utc
        out = []
        for g in games_today:
            gap = gap_for_game(g.home_team, g.away_team, first_start) if use_real_gap else 0.0
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

    def evaluate(engine: NBAStructuralV7Engine, eval_games: list, use_real_gap: bool) -> EvalMetrics:
        dates_map: dict[str, list] = defaultdict(list)
        for g in eval_games:
            dates_map[g.event_start_utc[:10]].append(g)
        rows = []
        nonzero_gaps = 0
        for d in sorted(dates_map.keys()):
            games_today = dates_map[d]
            first_start = games_today[0].event_start_utc
            history = [g for g in all_games if g.event_start_utc < first_start]
            upcoming = build_upcoming(games_today, use_real_gap)
            nonzero_gaps += sum(1 for u in upcoming if u.availability_points_gap != 0.0)
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
        print(f"  (games with nonzero availability gap: {nonzero_gaps}/{n})")
        return EvalMetrics(
            sample_size=n,
            mae_margin=round(float(np.mean([r["abs_err_margin"] for r in rows])), 4)
            if rows
            else float("nan"),
            mae_total=float("nan"),
            moneyline_accuracy=round(float(np.mean([r["correct"] for r in rows])), 4)
            if rows
            else float("nan"),
            moneyline_brier=round(float(np.mean([r["brier"] for r in rows])), 4) if rows else float("nan"),
            home_spread_acc=float("nan"),
            away_spread_acc=float("nan"),
            early_season_over_rate=float("nan"),
        )

    baseline = NBAStructuralV7Engine()

    print("\n--- VALIDATION: zero-gap (current historical behavior) ---")
    val_zero = evaluate(baseline, val_games, use_real_gap=False)
    print(val_zero)

    print("\n--- VALIDATION: real box-score-derived gap ---")
    val_real = evaluate(baseline, val_games, use_real_gap=True)
    print(val_real)

    better = val_real.moneyline_brier < val_zero.moneyline_brier and val_real.mae_margin < val_zero.mae_margin
    print(f"\nreal gap beats zero-gap on BOTH brier and margin MAE on validation: {better}")

    print("\n--- LOCKED HOLDOUT (touched once, both variants) ---")
    holdout_zero = evaluate(baseline, holdout_games, use_real_gap=False)
    print("zero-gap:", holdout_zero)
    holdout_real = evaluate(baseline, holdout_games, use_real_gap=True)
    print("real-gap:", holdout_real)

    out = {
        "boxscore_coverage": f"{coverage}/{len(all_games)}",
        "validation_zero_gap": vars(val_zero),
        "validation_real_gap": vars(val_real),
        "holdout_zero_gap": vars(holdout_zero),
        "holdout_real_gap": vars(holdout_real),
        "split_meta": meta,
    }
    Path("scratch").mkdir(exist_ok=True)
    Path("scratch/nba_real_availability_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_real_availability_result.json")


if __name__ == "__main__":
    run()
