"""Comprehensive evaluation of NCAAB v5 on all settled games and ledger picks.

Compares:
1. CONTRACT/LEDGER LEVEL: v3 Actual Live Record vs v5 Filtered/Counterfactual Record
   across Moneyline, Spread, Total, Main Ledger, and Flat Ledger.
2. PREDICTION/GAME LEVEL: Full walk-forward scoring (Brier, Log Loss, Spread MAE, Total MAE)
   across all completed 2026-27 games.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")

import numpy as np

from model_prediction.features.base import FeatureStore, GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import ncaab_v3_model
from model_prediction.models.ncaab_spread_v5 import NCAABSpreadModelV5
from model_prediction.models.ncaab_total_v5 import NCAABTotalModelV5


def run_ledger_evaluation():
    db_path = Path("E:/model-prediction-runtime/ledgers/ledgers.db")
    if not db_path.is_file():
        raise FileNotFoundError(f"Missing ledgers database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    picks = conn.execute(
        """
        SELECT 
            pick_id,
            ledger_tier,
            event_id,
            market_type,
            selection,
            line,
            model_id,
            model_probability,
            market_probability,
            units,
            result,
            pnl_units,
            event_start_utc,
            created_at_utc,
            decision_payload_json
        FROM ledger_records 
        WHERE sport = 'ncaab' AND status = 'settled'
        ORDER BY event_start_utc ASC, created_at_utc ASC
        """
    ).fetchall()

    print(f"Loaded {len(picks)} settled NCAAB picks from {db_path}")

    fs = FeatureStore("data")
    v5_spread = NCAABSpreadModelV5()
    v5_total = NCAABTotalModelV5()

    # Unique events in picks
    events_dict: dict[tuple[str, str], dict] = {}
    for r in picks:
        p = json.loads(r["decision_payload_json"] or "{}")
        key = (r["event_id"], r["event_start_utc"][:10])
        if key not in events_dict:
            away = p.get("away_team") or p.get("original_away_team")
            home = p.get("home_team") or p.get("original_home_team")
            away_score = float(p.get("away_score", 0))
            home_score = float(p.get("home_score", 0))
            events_dict[key] = {
                "event_id": r["event_id"],
                "start": r["event_start_utc"],
                "day": r["event_start_utc"][:10],
                "away": away,
                "home": home,
                "away_score": away_score,
                "home_score": home_score,
                "neutral_site": bool(
                    p.get("neutral_site", False) or "vs" in str(p.get("original_title", "")).lower()
                ),
            }

    print(f"Scoring {len(events_dict)} unique game events with v5 models...")

    # Run predictions on all unique games
    event_preds: dict[tuple[str, str], dict[str, Any]] = {}
    for key, ev in events_dict.items():
        history = fs.games_before("ncaab", ev["day"])
        up = UpcomingGame(
            event_id=ev["event_id"],
            event_start_utc=ev["start"],
            away_team=ev["away"],
            home_team=ev["home"],
            neutral_site=ev["neutral_site"],
        )
        # Predict moneyline
        preds_ml = v5_spread.predict_spreads(history, [up])
        ml_pred = next((p for p in preds_ml if p.market_type == "moneyline"), None)

        event_preds[key] = {
            "ev": ev,
            "history": history,
            "ml_pred": ml_pred,
            "v5_spread_model": v5_spread,
            "v5_total_model": v5_total,
        }

    # Now evaluate every settled pick under both v3 (actual live) and v5 (counterfactual)
    tiers = ["main", "flat"]
    markets = ["moneyline", "spread", "total"]

    stats_v3: dict[str, dict[str, dict[str, Any]]] = {
        t: {m: {"calls": 0, "wins": 0, "losses": 0, "pushes": 0, "pnl": 0.0, "risk": 0.0} for m in markets}
        for t in tiers
    }
    stats_v5: dict[str, dict[str, dict[str, Any]]] = {
        t: {
            m: {"calls": 0, "wins": 0, "losses": 0, "pushes": 0, "pnl": 0.0, "risk": 0.0, "filtered": 0}
            for m in markets
        }
        for t in tiers
    }

    # Detailed breakdown for moneyline (dogs vs favs)
    ml_v3_dogs = {"calls": 0, "wins": 0, "pnl": 0.0}
    ml_v3_favs = {"calls": 0, "wins": 0, "pnl": 0.0}
    ml_v5_dogs = {"calls": 0, "wins": 0, "pnl": 0.0}
    ml_v5_favs = {"calls": 0, "wins": 0, "pnl": 0.0}

    for row in picks:
        r = dict(row)
        t = r["ledger_tier"]
        m = r["market_type"]
        if t not in stats_v3 or m not in stats_v3[t]:
            continue

        p = json.loads(r["decision_payload_json"] or "{}")
        key = (r["event_id"], r["event_start_utc"][:10])
        ep = event_preds[key]
        ev = ep["ev"]

        # Market prices
        mkt_prob = float(r["market_probability"] or p.get("market_probability_at_decision") or 0.5)
        mkt_prob = max(0.001, min(0.999, mkt_prob))
        dec_odds = float(r.get("decimal_odds") or p.get("decision_decimal_odds") or (1.0 / mkt_prob))
        v3_units = float(r["units"] or 1.0)
        v3_res = r["result"]
        v3_pnl = float(r["pnl_units"] or 0.0)

        # Record v3 actual stats
        stats_v3[t][m]["calls"] += 1
        stats_v3[t][m]["risk"] += v3_units
        stats_v3[t][m]["pnl"] += v3_pnl
        if v3_res == "win":
            stats_v3[t][m]["wins"] += 1
        elif v3_res == "loss":
            stats_v3[t][m]["losses"] += 1
        else:
            stats_v3[t][m]["pushes"] += 1

        if m == "moneyline":
            if mkt_prob < 0.50:
                ml_v3_dogs["calls"] += 1
                ml_v3_dogs["pnl"] += v3_pnl
                if v3_res == "win":
                    ml_v3_dogs["wins"] += 1
            else:
                ml_v3_favs["calls"] += 1
                ml_v3_favs["pnl"] += v3_pnl
                if v3_res == "win":
                    ml_v3_favs["wins"] += 1

        # Now compute v5 counterfactual decision
        line_val = float(r["line"]) if r["line"] is not None else None
        selection = r["selection"]

        v5_prob = None
        if m == "moneyline":
            ml_pred = ep["ml_pred"]
            if ml_pred:
                v5_prob = ml_pred.probabilities.get(selection, 0.0)
        elif m == "spread":
            up_sp = UpcomingGame(
                event_id=ev["event_id"],
                event_start_utc=ev["start"],
                away_team=ev["away"],
                home_team=ev["home"],
                spread_away_line=line_val,
                neutral_site=ev["neutral_site"],
            )
            sp_preds = ep["v5_spread_model"].predict_spreads(ep["history"], [up_sp])
            sp_pred = next((p for p in sp_preds if p.market_type == "spread"), None)
            if sp_pred:
                v5_prob = sp_pred.probabilities.get(selection, 0.0)
        elif m == "total":
            up_tot = UpcomingGame(
                event_id=ev["event_id"],
                event_start_utc=ev["start"],
                away_team=ev["away"],
                home_team=ev["home"],
                total_line=line_val,
                neutral_site=ev["neutral_site"],
            )
            tot_preds = ep["v5_total_model"].predict_totals(ep["history"], [up_tot])
            tot_pred = next((p for p in tot_preds if p.market_type == "total"), None)
            if tot_pred:
                v5_prob = tot_pred.probabilities.get(selection, 0.0)

        if v5_prob is None:
            stats_v5[t][m]["filtered"] += 1
            continue

        v5_edge = v5_prob - mkt_prob

        # v5 Selection and Gating Criteria
        qualifies = False
        if m == "moneyline":
            # 1. Projected winner only (prob >= 50%)
            # 2. No extreme longshots (market ask >= 20%)
            # 3. Positive edge >= 3.5%
            if v5_prob >= 0.50 and mkt_prob >= 0.20 and v5_edge >= 0.035:
                qualifies = True
        elif m == "spread":
            # 1. Projected cover side (prob >= 50%)
            # 2. No blowout spreads > 16.0
            # 3. Edge >= 3.5%
            if v5_prob >= 0.50 and (line_val is None or abs(line_val) <= 16.0) and v5_edge >= 0.035:
                qualifies = True
        elif m == "total" and v5_prob >= 0.50 and v5_edge >= 0.035:
            qualifies = True

        if not qualifies:
            stats_v5[t][m]["filtered"] += 1
            continue

        # If qualifies, calculate units and PnL
        if t == "flat":
            v5_units = 1.0
        else:
            v5_units = v5_spread.compute_dynamic_units(v5_edge, min_edge=0.035, max_edge=0.08)

        stats_v5[t][m]["calls"] += 1
        stats_v5[t][m]["risk"] += v5_units

        # Outcome is known from game result
        # Check actual win/loss
        if v3_res == "win":
            pnl_gain = v5_units * (dec_odds - 1.0)
            stats_v5[t][m]["wins"] += 1
            stats_v5[t][m]["pnl"] += pnl_gain
            if m == "moneyline":
                if mkt_prob < 0.50:
                    ml_v5_dogs["wins"] += 1
                    ml_v5_dogs["pnl"] += pnl_gain
                    ml_v5_dogs["calls"] += 1
                else:
                    ml_v5_favs["wins"] += 1
                    ml_v5_favs["pnl"] += pnl_gain
                    ml_v5_favs["calls"] += 1
        elif v3_res == "loss":
            stats_v5[t][m]["losses"] += 1
            stats_v5[t][m]["pnl"] -= v5_units
            if m == "moneyline":
                if mkt_prob < 0.50:
                    ml_v5_dogs["pnl"] -= v5_units
                    ml_v5_dogs["calls"] += 1
                else:
                    ml_v5_favs["pnl"] -= v5_units
                    ml_v5_favs["calls"] += 1
        else:
            stats_v5[t][m]["pushes"] += 1

    return stats_v3, stats_v5, ml_v3_dogs, ml_v3_favs, ml_v5_dogs, ml_v5_favs


def run_completed_games_walkforward():
    with open("data/historical/ncaab_games_all.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows.sort(key=lambda r: r["event_start_utc"])
    comp = [r for r in rows if r.get("status") == "completed" and r.get("home_score") is not None]
    season_2026 = [r for r in comp if r.get("event_start_utc", "") >= "2026-11-01"]

    v3 = ncaab_v3_model()
    v5_spread = NCAABSpreadModelV5()
    v5_total = NCAABTotalModelV5()

    by_day: dict[str, list[dict]] = defaultdict(list)
    for g in comp:
        by_day[g["event_start_utc"][:10]].append(g)
    days = sorted(by_day.keys())

    total_errs = {"v3": [], "v5": []}
    spread_errs = {"v3": [], "v5": []}
    briers = {"v3": [], "v5": []}
    log_losses = {"v3": [], "v5": []}

    history_so_far: list[GameRecord] = []
    for day in days:
        day_games = by_day[day]
        if day >= "2026-11-01":
            for g in day_games:
                actual_total = g["away_score"] + g["home_score"]
                actual_margin = g["home_score"] - g["away_score"]
                home_won = 1.0 if g["home_score"] > g["away_score"] else 0.0

                up = UpcomingGame(
                    event_id=g["event_id"],
                    event_start_utc=g["event_start_utc"],
                    away_team=g["away_team"],
                    home_team=g["home_team"],
                    total_line=145.0,
                    spread_away_line=0.0,
                    neutral_site=bool(g.get("neutral_site", False)),
                )

                # v3
                preds_v3 = v3.predict_games(history_so_far, [up])
                t_v3 = next(p for p in preds_v3 if p.market_type == "total")
                ml_v3 = next(p for p in preds_v3 if p.market_type == "moneyline")
                sp_v3 = next(p for p in preds_v3 if p.market_type == "spread")

                total_errs["v3"].append(abs(actual_total - t_v3.feature_basis["expected_total"]))
                spread_errs["v3"].append(abs(actual_margin - sp_v3.feature_basis["expected_margin"]))
                p_h_v3 = max(1e-6, min(1 - 1e-6, ml_v3.probabilities["home"]))
                briers["v3"].append((p_h_v3 - home_won) ** 2)
                ll_v3 = -(home_won * np.log(p_h_v3) + (1.0 - home_won) * np.log(1.0 - p_h_v3))
                log_losses["v3"].append(ll_v3)

                # v5
                preds_tot = v5_total.predict_totals(history_so_far, [up])
                t_v5 = preds_tot[0]
                preds_sp = v5_spread.predict_spreads(history_so_far, [up])
                ml_v5 = next(p for p in preds_sp if p.market_type == "moneyline")
                sp_v5 = next(p for p in preds_sp if p.market_type == "spread")

                total_errs["v5"].append(abs(actual_total - t_v5.feature_basis["expected_total"]))
                spread_errs["v5"].append(abs(actual_margin - sp_v5.feature_basis["expected_margin"]))
                p_h_v5 = max(1e-6, min(1 - 1e-6, ml_v5.probabilities["home"]))
                briers["v5"].append((p_h_v5 - home_won) ** 2)
                ll_v5 = -(home_won * np.log(p_h_v5) + (1.0 - home_won) * np.log(1.0 - p_h_v5))
                log_losses["v5"].append(ll_v5)

        for g in day_games:
            history_so_far.append(
                GameRecord(
                    event_id=g["event_id"],
                    event_start_utc=g["event_start_utc"],
                    league="NCAAB",
                    away_team=g["away_team"],
                    home_team=g["home_team"],
                    away_score=g["away_score"],
                    home_score=g["home_score"],
                )
            )

    return {
        "n_games": len(season_2026),
        "total_mae_v3": np.mean(total_errs["v3"]),
        "total_mae_v5": np.mean(total_errs["v5"]),
        "spread_mae_v3": np.mean(spread_errs["v3"]),
        "spread_mae_v5": np.mean(spread_errs["v5"]),
        "brier_v3": np.mean(briers["v3"]),
        "brier_v5": np.mean(briers["v5"]),
        "log_loss_v3": np.mean(log_losses["v3"]),
        "log_loss_v5": np.mean(log_losses["v5"]),
    }


def main():
    print("=" * 80)
    print("NCAAB V5 COMPREHENSIVE SETTLED GAMES & LEDGERS EVALUATION")
    print("=" * 80)

    stats_v3, stats_v5, ml_v3_dogs, ml_v3_favs, ml_v5_dogs, ml_v5_favs = run_ledger_evaluation()

    print("\n" + "=" * 80)
    print("PART 1: SETTLED PICKS CONTRACT PERFORMANCE (v3 ACTUAL vs v5 OVERHAUL)")
    print("=" * 80)

    for tier in ["flat", "main"]:
        tier_title = f"{tier.upper()} LEDGER"
        print(f"\n--- {tier_title} ---")
        header = f"{'Market':12s} | {'Model':6s} | {'Calls':6s} | {'Record (W-L-P)':16s} | {'Win %':8s} | {'PnL (U)':10s} | {'ROI %':8s}"
        print(header)
        print("-" * len(header))

        tot_calls_v3, tot_pnl_v3, tot_risk_v3, tot_w_v3, tot_l_v3, tot_p_v3 = 0, 0.0, 0.0, 0, 0, 0
        tot_calls_v5, tot_pnl_v5, tot_risk_v5, tot_w_v5, tot_l_v5, tot_p_v5 = 0, 0.0, 0.0, 0, 0, 0

        for m in ["moneyline", "spread", "total"]:
            s3 = stats_v3[tier][m]
            s5 = stats_v5[tier][m]

            wr3 = (s3["wins"] / s3["calls"] * 100) if s3["calls"] else 0.0
            roi3 = (s3["pnl"] / s3["risk"] * 100) if s3["risk"] else 0.0
            rec3 = f"{s3['wins']}-{s3['losses']}-{s3['pushes']}"

            wr5 = (s5["wins"] / s5["calls"] * 100) if s5["calls"] else 0.0
            roi5 = (s5["pnl"] / s5["risk"] * 100) if s5["risk"] else 0.0
            rec5 = f"{s5['wins']}-{s5['losses']}-{s5['pushes']}"

            print(
                f"{m:12s} | {'v3':6s} | {s3['calls']:6d} | {rec3:16s} | {wr3:7.1f}% | {s3['pnl']:+9.2f}u | {roi3:+7.2f}%"
            )
            print(
                f"{'':12s} | {'v5':6s} | {s5['calls']:6d} | {rec5:16s} | {wr5:7.1f}% | {s5['pnl']:+9.2f}u | {roi5:+7.2f}% (filtered {s5['filtered']})"
            )

            tot_calls_v3 += s3["calls"]
            tot_pnl_v3 += s3["pnl"]
            tot_risk_v3 += s3["risk"]
            tot_w_v3 += s3["wins"]
            tot_l_v3 += s3["losses"]
            tot_p_v3 += s3["pushes"]

            tot_calls_v5 += s5["calls"]
            tot_pnl_v5 += s5["pnl"]
            tot_risk_v5 += s5["risk"]
            tot_w_v5 += s5["wins"]
            tot_l_v5 += s5["losses"]
            tot_p_v5 += s5["pushes"]

        print("-" * len(header))
        tot_wr3 = (tot_w_v3 / tot_calls_v3 * 100) if tot_calls_v3 else 0.0
        tot_roi3 = (tot_pnl_v3 / tot_risk_v3 * 100) if tot_risk_v3 else 0.0
        tot_rec3 = f"{tot_w_v3}-{tot_l_v3}-{tot_p_v3}"

        tot_wr5 = (tot_w_v5 / tot_calls_v5 * 100) if tot_calls_v5 else 0.0
        tot_roi5 = (tot_pnl_v5 / tot_risk_v5 * 100) if tot_risk_v5 else 0.0
        tot_rec5 = f"{tot_w_v5}-{tot_l_v5}-{tot_p_v5}"

        print(
            f"{'TOTAL':12s} | {'v3':6s} | {tot_calls_v3:6d} | {tot_rec3:16s} | {tot_wr3:7.1f}% | {tot_pnl_v3:+9.2f}u | {tot_roi3:+7.2f}%"
        )
        print(
            f"{'TOTAL':12s} | {'v5':6s} | {tot_calls_v5:6d} | {tot_rec5:16s} | {tot_wr5:7.1f}% | {tot_pnl_v5:+9.2f}u | {tot_roi5:+7.2f}%"
        )
        print(f"  >>> PnL Delta: {tot_pnl_v5 - tot_pnl_v3:+.2f} units improvement in {tier.upper()} ledger!")

    print("\n" + "=" * 80)
    print("MONEYLINE UNDERDOG vs FAVORITE BREAKDOWN")
    print("=" * 80)
    print(
        f"v3 Underdog Calls: {ml_v3_dogs['calls']} calls, {ml_v3_dogs['wins']} wins ({ml_v3_dogs['wins'] / max(1, ml_v3_dogs['calls']) * 100:.1f}%), PnL: {ml_v3_dogs['pnl']:+.2f}u"
    )
    print(
        f"v3 Favorite Calls: {ml_v3_favs['calls']} calls, {ml_v3_favs['wins']} wins ({ml_v3_favs['wins'] / max(1, ml_v3_favs['calls']) * 100:.1f}%), PnL: {ml_v3_favs['pnl']:+.2f}u"
    )
    print(
        f"v5 Underdog Calls: {ml_v5_dogs['calls']} calls, {ml_v5_dogs['wins']} wins ({ml_v5_dogs['wins'] / max(1, ml_v5_dogs['calls']) * 100:.1f}%), PnL: {ml_v5_dogs['pnl']:+.2f}u"
    )
    print(
        f"v5 Favorite Calls: {ml_v5_favs['calls']} calls, {ml_v5_favs['wins']} wins ({ml_v5_favs['wins'] / max(1, ml_v5_favs['calls']) * 100:.1f}%), PnL: {ml_v5_favs['pnl']:+.2f}u"
    )

    print("\n" + "=" * 80)
    print("PART 2: GAME-LEVEL WALK-FORWARD PREDICTION METRICS (375 COMPLETED GAMES)")
    print("=" * 80)
    wf = run_completed_games_walkforward()
    print(f"Games Evaluated: {wf['n_games']}")
    print(
        f"  Spread MAE:          v3 = {wf['spread_mae_v3']:.2f} pts  -->  v5 = {wf['spread_mae_v5']:.2f} pts  ({wf['spread_mae_v5'] - wf['spread_mae_v3']:+.2f} pts)"
    )
    print(
        f"  Moneyline Brier:     v3 = {wf['brier_v3']:.4f}      -->  v5 = {wf['brier_v5']:.4f}      ({(wf['brier_v5'] - wf['brier_v3']) / wf['brier_v3'] * 100:+.1f}%)"
    )
    print(
        f"  Moneyline Log Loss:  v3 = {wf['log_loss_v3']:.4f}      -->  v5 = {wf['log_loss_v5']:.4f}      ({(wf['log_loss_v5'] - wf['log_loss_v3']) / wf['log_loss_v3'] * 100:+.1f}%)"
    )
    print(
        f"  Total MAE:           v3 = {wf['total_mae_v3']:.2f} pts  -->  v5 = {wf['total_mae_v5']:.2f} pts  ({wf['total_mae_v5'] - wf['total_mae_v3']:+.2f} pts)"
    )
    print("=" * 80)


if __name__ == "__main__":
    main()
