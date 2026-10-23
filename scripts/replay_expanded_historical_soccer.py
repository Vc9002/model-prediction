"""Expanded Historical Soccer Replay across Processed Games.

Evaluates the 5 trading strategies on the large processed historical dataset (9,181 matches):
1. Incumbent Blind Favorite (Home argmax)
2. Long-Only Value Gated (>= 4% edge)
3. Synthetic Double Chance (YES + DRAW)
4. Two-Sided Market (Buy NO on Overpriced Favorites)
5. NO + DRAW Supercharged (Double Payout on Tie)
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from model_prediction.models.soccer import SoccerModel

PROCESSED_GAMES = Path("E:/MP/data/processed/soccer/games.jsonl")


def run_historical_replay(sample_size: int = 1500):
    if not PROCESSED_GAMES.exists():
        print(f"File not found: {PROCESSED_GAMES}")
        return

    # Load recent completed games with valid scores
    games = []
    with PROCESSED_GAMES.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                row.get("status") in {"completed", "STATUS_FULL_TIME", "final"}
                and row.get("home_score") is not None
                and row.get("away_score") is not None
            ):
                games.append(row)

    print(f"Total completed matches loaded: {len(games)}")
    if len(games) > sample_size:
        games = games[-sample_size:]  # Evaluate the most recent completed games

    model = SoccerModel()
    # Calculate baseline strengths from prior history
    strengths, baseline = model._strengths(
        [
            type(
                "Game",
                (),
                {
                    "start": g.get("event_start_utc", ""),
                    "home_team": g.get("home_team", ""),
                    "away_team": g.get("away_team", ""),
                    "home_score": float(g.get("home_score", 0)),
                    "away_score": float(g.get("away_score", 0)),
                },
            )()
            for g in games[:800]
        ]
    )

    test_games = games[800:]
    print(f"Evaluating {len(test_games)} holdout matches...")

    incumbent_trades = []
    long_value_trades = []
    double_chance_trades = []
    two_sided_no_trades = []
    no_plus_draw_trades = []

    for g in test_games:
        home = str(g.get("home_team", ""))
        away = str(g.get("away_team", ""))
        h_score = int(g.get("home_score", 0))
        a_score = int(g.get("away_score", 0))
        actual = "home" if h_score > a_score else ("away" if a_score > h_score else "draw")

        h_stat = strengths.get(home, {"attack": 1.0, "defense": 1.0})
        a_stat = strengths.get(away, {"attack": 1.0, "defense": 1.0})

        home_rate = baseline * h_stat["attack"] * a_stat["defense"] * 1.15
        away_rate = baseline * a_stat["attack"] * h_stat["defense"]

        matrix = model.score_matrix(home_rate, away_rate)
        max_g = len(matrix)
        p_h = sum(matrix[h][a] for h in range(max_g) for a in range(h))
        p_a = sum(matrix[h][a] for a in range(max_g) for h in range(a))
        p_d = sum(matrix[i][i] for i in range(max_g))

        # Synthetic market odds with standard public home favorite bias and vig
        q_h = max(0.15, min(0.75, p_h * 1.10))  # Public overvalues home favorite
        q_d = 0.28
        q_a = max(0.10, min(0.75, 1.05 - q_h - q_d))

        # 1. Incumbent (Blind Home Favorite)
        won_inc = actual == "home"
        pnl_inc = (1.0 / q_h - 1.0) if won_inc else -1.0
        incumbent_trades.append({"won": won_inc, "pnl": pnl_inc})

        # 2. Long-Only Value Gated (>= 4% edge)
        edge_h = p_h - q_h
        edge_a = p_a - q_a
        best_long = "home" if edge_h >= edge_a else "away"
        best_edge = max(edge_h, edge_a)
        best_ask = q_h if edge_h >= edge_a else q_a

        if best_edge >= 0.04:
            won_long = actual == best_long
            pnl_long = (1.0 / best_ask - 1.0) if won_long else -1.0
            long_value_trades.append({"won": won_long, "pnl": pnl_long})

        # 3. Synthetic Double Chance (YES + DRAW)
        favored = "home" if p_h >= p_a else "away"
        p_dc = (p_h + p_d) if favored == "home" else (p_a + p_d)
        cost_dc = (q_h + q_d) if favored == "home" else (q_a + q_d)
        edge_dc = p_dc - cost_dc

        if edge_dc >= 0.03 and cost_dc < 0.90:
            won_dc = actual == favored or actual == "draw"
            pnl_dc = (1.0 - cost_dc) / cost_dc if won_dc else -1.0
            double_chance_trades.append({"won": won_dc, "pnl": pnl_dc})

        # 4. Two-Sided Market (Buy NO on Overpriced Favs)
        if q_h - p_h >= 0.04:
            cost_no = max(0.10, min(0.88, 1.0 - q_h + 0.02))
            won_no = actual in {"draw", "away"}
            pnl_no = (1.0 - cost_no) / cost_no if won_no else -1.0
            two_sided_no_trades.append({"won": won_no, "pnl": pnl_no})

        # 5. NO + DRAW Supercharged
        if q_h - p_h >= 0.04:
            cost_no = max(0.10, min(0.88, 1.0 - q_h + 0.02))
            total_stake = cost_no + q_d
            if actual == "draw":
                pnl_combo = (2.0 - total_stake) / total_stake
                won_combo = True
            elif actual == "away":
                pnl_combo = (1.0 - total_stake) / total_stake
                won_combo = True
            else:
                pnl_combo = -1.0
                won_combo = False
            no_plus_draw_trades.append({"won": won_combo, "pnl": pnl_combo})

    def summarize(name: str, trades: list[dict]):
        if not trades:
            return {"strategy": name, "bets": 0, "win_rate": 0.0, "total_pnl": 0.0, "roi_pct": 0.0}
        n = len(trades)
        wins = sum(1 for t in trades if t["won"])
        total_pnl = sum(t["pnl"] for t in trades)
        win_rate = wins / n
        roi = (total_pnl / n) * 100.0
        return {
            "strategy": name,
            "bets": n,
            "wins": wins,
            "losses": n - wins,
            "win_rate": round(win_rate * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "roi_pct": round(roi, 1),
        }

    results = [
        summarize("1. Incumbent Baseline (Blind Home Argmax)", incumbent_trades),
        summarize("2. Long-Only Value Gated (>= 4% edge)", long_value_trades),
        summarize("3. Synthetic Double Chance (YES + DRAW)", double_chance_trades),
        summarize("4. Two-Sided Market (Buy NO on Overpriced Favs)", two_sided_no_trades),
        summarize("5. NO + DRAW Supercharged (Double Payout on Tie)", no_plus_draw_trades),
    ]

    df = pd.DataFrame(results)
    print("\n" + "=" * 80)
    print(f"HISTORICAL HOLDOUT GAMES REPLAY COMPARISON ({len(test_games)} Matches)")
    print("=" * 80)
    print(df.to_string(index=False))
    print("=" * 80)


if __name__ == "__main__":
    run_historical_replay()
