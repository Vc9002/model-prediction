"""Replay and Compare Soccer Trading Strategies on Settled Games in Ledgers.db.

Compares 5 distinct execution strategies across all 340 settled moneyline games:
1. Incumbent Baseline: Blind argmax selection (86.8% home picks).
2. Long-Only Value Gating: Only bet when model edge >= 4.0% over market ask.
3. Synthetic Double Chance (YES + DRAW): Buy Team YES + Draw YES to eliminate draw loss.
4. Two-Sided Market (Buy NO on Overvalued Favorites): Monetizes Draw + Underdog.
5. NO + DRAW Supercharged: Buy NO on Overvalued Favorite + YES on Draw (double payout on tie).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

LEDGER_DB = Path("E:/model-prediction-runtime/ledgers/ledgers.db")


def run_settled_replay():
    conn = sqlite3.connect(f"file:{LEDGER_DB}?mode=ro", uri=True)
    c = conn.cursor()
    c.execute("""
        SELECT pick_id, selection, result, pnl_units, decision_payload_json
        FROM ledger_records
        WHERE sport = 'soccer' AND market_type = 'moneyline' AND status = 'settled'
        ORDER BY event_start_utc
    """)
    rows = c.fetchall()
    conn.close()

    print(f"Total settled soccer moneyline games loaded: {len(rows)}")

    # Containers for results
    incumbent_trades = []
    long_value_trades = []
    double_chance_trades = []
    two_sided_no_trades = []
    no_plus_draw_trades = []

    for pick_id, sel, res, pnl, payload_raw in rows:
        payload = json.loads(payload_raw) if payload_raw else {}
        try:
            h_score = int(payload.get("home_score", 0))
            a_score = int(payload.get("away_score", 0))
        except (ValueError, TypeError):
            continue

        actual_outcome = "home" if h_score > a_score else ("away" if a_score > h_score else "draw")

        # Extract model probabilities
        snap_raw = payload.get("model_input_snapshot_json")
        if not snap_raw:
            continue
        snap = json.loads(snap_raw)
        probs = snap.get("probabilities", {})
        p_h = float(probs.get("home", 0.0))
        p_d = float(probs.get("draw", 0.0))
        p_a = float(probs.get("away", 0.0))
        if p_h <= 0 or p_a <= 0:
            continue

        # Extract market odds and asks
        try:
            dec_odds = float(payload.get("decimal_odds") or payload.get("decision_decimal_odds") or 2.0)
            q_sel = float(payload.get("market_implied_probability") or (1.0 / dec_odds))
        except (ValueError, TypeError, ZeroDivisionError):
            dec_odds = 2.0
            q_sel = 0.50

        # Market implied probabilities for home, draw, away
        # In the incumbent ledger, sel was almost always home.
        if sel == "home":
            q_h = q_sel
            # Typical draw ask on Polymarket is ~0.26 to 0.32
            q_d = 0.28
            q_a = max(0.05, min(0.85, 1.0 - q_h - q_d + 0.04))
        else:
            q_a = q_sel
            q_d = 0.28
            q_h = max(0.05, min(0.85, 1.0 - q_a - q_d + 0.04))

        # --- 1. Incumbent Baseline ---
        incumbent_trades.append(
            {
                "pick_id": pick_id,
                "won": (res == "win"),
                "pnl": float(pnl or 0.0),
            }
        )

        # --- 2. Long-Only Value Gating (>= 4.0% edge) ---
        edge_h = p_h - q_h
        edge_a = p_a - q_a
        best_long = "home" if edge_h >= edge_a else "away"
        best_long_edge = max(edge_h, edge_a)
        best_long_ask = q_h if edge_h >= edge_a else q_a

        if best_long_edge >= 0.04 and 0.10 <= best_long_ask <= 0.85:
            won_long = actual_outcome == best_long
            pnl_long = (1.0 / best_long_ask - 1.0) if won_long else -1.0
            long_value_trades.append(
                {
                    "pick_id": pick_id,
                    "selection": best_long,
                    "won": won_long,
                    "pnl": pnl_long,
                    "edge": best_long_edge,
                }
            )

        # --- 3. Synthetic Double Chance (YES + DRAW) ---
        # When model favors a team (e.g. Home), buy Team YES + Draw YES to eliminate draw loss!
        # Cost = ask(Team) + ask(Draw). Payout = 1.0 if Team wins OR Draw.
        favored_team = "home" if p_h >= p_a else "away"
        p_dc = (p_h + p_d) if favored_team == "home" else (p_a + p_d)
        cost_dc = (q_h + q_d) if favored_team == "home" else (q_a + q_d)
        edge_dc = p_dc - cost_dc

        if edge_dc >= 0.035 and cost_dc < 0.92:
            won_dc = actual_outcome == favored_team or actual_outcome == "draw"
            pnl_dc = (1.0 - cost_dc) / cost_dc if won_dc else -1.0
            double_chance_trades.append(
                {
                    "pick_id": pick_id,
                    "selection": f"{favored_team}+draw",
                    "won": won_dc,
                    "pnl": pnl_dc,
                    "edge": edge_dc,
                }
            )

        # --- 4. Two-Sided Market (Buy NO on Overvalued Favorites) ---
        # If market overprices Home (q_h > p_h + 4%), buy NO on Home!
        # NO on Home wins if Draw OR Away wins!
        # Cost of NO on Home ~= 1.0 - q_h + 0.02
        if q_h - p_h >= 0.04:
            cost_no = max(0.10, min(0.90, 1.0 - q_h + 0.02))
            won_no = actual_outcome in {"draw", "away"}
            pnl_no = (1.0 - cost_no) / cost_no if won_no else -1.0
            two_sided_no_trades.append(
                {
                    "pick_id": pick_id,
                    "selection": "NO_home",
                    "won": won_no,
                    "pnl": pnl_no,
                    "edge": (1.0 - p_h) - cost_no,
                }
            )
        elif q_a - p_a >= 0.04:
            cost_no = max(0.10, min(0.90, 1.0 - q_a + 0.02))
            won_no = actual_outcome in {"draw", "home"}
            pnl_no = (1.0 - cost_no) / cost_no if won_no else -1.0
            two_sided_no_trades.append(
                {
                    "pick_id": pick_id,
                    "selection": "NO_away",
                    "won": won_no,
                    "pnl": pnl_no,
                    "edge": (1.0 - p_a) - cost_no,
                }
            )

        # --- 5. NO + DRAW Supercharged ---
        # Buy NO on overvalued favorite + Buy YES on Draw.
        # If underdog wins -> NO pays 1.0.
        # If match draws -> NO pays 1.0 AND Draw pays 1.0 = 2.0 payout!
        if q_h - p_h >= 0.04:
            cost_no = max(0.10, min(0.90, 1.0 - q_h + 0.02))
            total_stake = cost_no + q_d
            if actual_outcome == "draw":
                # Double payout: 1.0 from NO + 1.0 from Draw = 2.0 total payout
                pnl_combo = (2.0 - total_stake) / total_stake
                won_combo = True
            elif actual_outcome == "away":
                # Single payout: 1.0 from NO
                pnl_combo = (1.0 - total_stake) / total_stake
                won_combo = True
            else:
                pnl_combo = -1.0
                won_combo = False

            no_plus_draw_trades.append(
                {
                    "pick_id": pick_id,
                    "won": won_combo,
                    "pnl": pnl_combo,
                    "draw_boost": (actual_outcome == "draw"),
                }
            )

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
    print("SETTLED SOCCER GAMES REPLAY COMPARISON (340 Settled Moneyline Games)")
    print("=" * 80)
    print(df.to_string(index=False))
    print("=" * 80)

    # Save to outputs/research
    out_file = Path("E:/model-prediction/outputs/research/soccer_settled_strategies_replay.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved evaluation summary to: {out_file}")


if __name__ == "__main__":
    run_settled_replay()
