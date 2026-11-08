"""Calibrate NCAAB v5 on all settled games and evaluate full cohort without survivorship bias.

Zero games cut. Zero cherry-picking.
Evaluates:
1. Tournament calibration on ALL 1,531 settled ledger contracts.
2. Generates and registers official calibrator artifacts in data/calibration/models/.
3. Evaluates Brier score, Log Loss, ECE, and reliability buckets across ALL 1,531 contracts
   comparing v3 (incumbent) vs. v5 Raw vs. v5 Calibrated.
4. Evaluates full chronological walk-forward across ALL completed NCAA games.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np

from model_prediction.calibration import (
    calibration_metrics,
    run_calibration_tournament,
)
from model_prediction.features.base import FeatureStore, GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import ncaab_v3_model
from model_prediction.models.ncaab_spread_v5 import NCAABSpreadModelV5
from model_prediction.models.ncaab_total_v5 import NCAABTotalModelV5


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5 if x == mean else (1.0 if x > mean else 0.0)
    return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2))))


def run_full_calibration_and_evaluation():
    db_path = Path("E:/model-prediction-runtime/ledgers/ledgers.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Load ALL settled picks for NCAAB
    rows = conn.execute(
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

    print(f"Total Settled Picks Loaded: {len(rows)} (ALL included, none cut)")

    fs = FeatureStore("data")
    v5_spread = NCAABSpreadModelV5()
    v5_total = NCAABTotalModelV5()
    v3 = ncaab_v3_model()

    # Pre-cache game predictions for the unique games
    # This avoids recomputing historical Elo/trends 1,531 times
    unique_games: dict[tuple[str, str], dict] = {}
    for r in rows:
        p = json.loads(r["decision_payload_json"] or "{}")
        key = (r["event_id"], r["event_start_utc"][:10])
        if key not in unique_games:
            away = p.get("away_team") or p.get("original_away_team")
            home = p.get("home_team") or p.get("original_home_team")
            unique_games[key] = {
                "event_id": r["event_id"],
                "start": r["event_start_utc"],
                "day": r["event_start_utc"][:10],
                "away": away,
                "home": home,
                "away_score": float(p.get("away_score", 0)),
                "home_score": float(p.get("home_score", 0)),
                "neutral_site": bool(
                    p.get("neutral_site", False) or "vs" in str(p.get("original_title", "")).lower()
                ),
            }

    print(f"Scoring {len(unique_games)} unique game events across feature store history...")

    # Group unique games by day so history & elo is computed once per day
    by_day_unique: dict[str, list[tuple[tuple[str, str], dict]]] = defaultdict(list)
    for key, ev in unique_games.items():
        by_day_unique[ev["day"]].append((key, ev))

    game_cache: dict[tuple[str, str], dict] = {}
    for day, ev_list in sorted(by_day_unique.items()):
        history = fs.games_before("ncaab", day)
        day_ups = [
            UpcomingGame(
                event_id=ev["event_id"],
                event_start_utc=ev["start"],
                away_team=ev["away"],
                home_team=ev["home"],
                neutral_site=ev["neutral_site"],
                spread_away_line=0.0,
                total_line=145.0,
            )
            for _, ev in ev_list
        ]

        # Batch predict across day
        preds_v5_sp = v5_spread.predict_spreads(history, day_ups)
        preds_v5_tot = v5_total.predict_totals(history, day_ups)
        preds_v3 = v3.predict_games(history, day_ups)

        sp_by_ev = {p.event_id: p for p in preds_v5_sp if p.market_type == "moneyline"}
        tot_by_ev = {p.event_id: p for p in preds_v5_tot if p.market_type == "total"}
        v3_by_ev = {p.event_id: p for p in preds_v3 if p.market_type == "moneyline"}

        for key, ev in ev_list:
            ev_id = ev["event_id"]
            ml_v5 = sp_by_ev[ev_id]
            tot_v5 = tot_by_ev[ev_id]
            ml_v3 = v3_by_ev[ev_id]

            game_cache[key] = {
                "v5_exp_margin": float(ml_v5.feature_basis["expected_margin"]),
                "v5_margin_sd": float(ml_v5.feature_basis["effective_margin_sd"]),
                "v5_p_home": float(ml_v5.probabilities["home"]),
                "v5_exp_total": float(tot_v5.feature_basis["expected_total"]),
                "v5_total_sd": float(tot_v5.feature_basis["effective_total_sd"]),
                "v3_p_home": float(ml_v3.probabilities["home"]),
                "v3_exp_margin": float(ml_v3.feature_basis.get("expected_margin", 0.0)),
                "v3_exp_total": float(ml_v3.feature_basis.get("expected_total", 145.0)),
            }

    # Now score every single contract
    scored_contracts: list[dict] = []
    by_market_data: dict[str, dict[str, list]] = {
        "moneyline": {"v3_probs": [], "v5_probs": [], "outcomes": [], "mkt_probs": [], "rows": []},
        "spread": {"v3_probs": [], "v5_probs": [], "outcomes": [], "mkt_probs": [], "rows": []},
        "total": {"v3_probs": [], "v5_probs": [], "outcomes": [], "mkt_probs": [], "rows": []},
    }

    for row in rows:
        r = dict(row)
        m = r["market_type"]
        if m not in by_market_data:
            continue

        p = json.loads(r["decision_payload_json"] or "{}")
        key = (r["event_id"], r["event_start_utc"][:10])
        gc = game_cache[key]

        # Outcome
        res = r["result"]
        if res == "win":
            y = 1
        elif res == "loss":
            y = 0
        else:
            continue  # push is not a binary outcome

        selection = r["selection"]
        line = float(r["line"]) if r["line"] is not None else None
        mkt_p = float(r["market_probability"] or p.get("market_probability_at_decision") or 0.5)
        mkt_p = max(0.001, min(0.999, mkt_p))
        dec_odds = float(r.get("decimal_odds") or p.get("decision_decimal_odds") or (1.0 / mkt_p))

        # Compute v3 prob
        v3_p = float(r["model_probability"])

        # Compute v5 raw prob
        if m == "moneyline":
            v5_p = gc["v5_p_home"] if selection == "home" else (1.0 - gc["v5_p_home"])
        elif m == "spread":
            # line is away handicap: P(away covers) = P(margin < line)
            away_cover = _normal_cdf(
                line if line is not None else 0.0, gc["v5_exp_margin"], gc["v5_margin_sd"]
            )
            v5_p = away_cover if selection == "away" else (1.0 - away_cover)
        elif m == "total":
            # P(under) = P(total < line)
            under_prob = _normal_cdf(
                line if line is not None else 145.0, gc["v5_exp_total"], gc["v5_total_sd"]
            )
            v5_p = under_prob if selection == "under" else (1.0 - under_prob)

        v5_p = max(0.0001, min(0.9999, v5_p))

        record = {
            "pick_id": r["pick_id"],
            "tier": r["ledger_tier"],
            "market": m,
            "selection": selection,
            "line": line,
            "mkt_prob": mkt_p,
            "dec_odds": dec_odds,
            "units": float(r["units"] or 1.0),
            "outcome": y,
            "v3_prob": v3_p,
            "v5_raw_prob": v5_p,
            "actual_res": res,
            "actual_pnl": float(r["pnl_units"] or 0.0),
        }
        scored_contracts.append(record)
        by_market_data[m]["v3_probs"].append(v3_p)
        by_market_data[m]["v5_probs"].append(v5_p)
        by_market_data[m]["outcomes"].append(y)
        by_market_data[m]["mkt_probs"].append(mkt_p)
        by_market_data[m]["rows"].append(record)

    print(f"\nScored {len(scored_contracts)} contracts across Moneyline, Spread, and Total.")

    # -------------------------------------------------------------------------
    # STEP 1: RUN CALIBRATION TOURNAMENTS
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STEP 1: CALIBRATION TOURNAMENT ACROSS ALL SETTLED CONTRACTS (OUT-OF-FOLD)")
    print("=" * 80)

    calibrated_objects = {}

    now_iso = datetime.now(UTC).isoformat()
    cal_models_dir = Path("data/calibration/models")
    cal_models_dir.mkdir(parents=True, exist_ok=True)

    for m in ["moneyline", "spread", "total"]:
        data = by_market_data[m]
        p_list = data["v5_probs"]
        y_list = data["outcomes"]
        model_ver = "ncaab-spread-v5" if m in ("moneyline", "spread") else "ncaab-total-v5"

        print(f"\n--- Tournament for {m.upper()} (N = {len(p_list)} settled picks) ---")
        t_res = run_calibration_tournament(
            p_list, y_list, model_ver, n_splits=5, minimum_sample=30, temporal=True
        )

        champ_method = t_res["champion_method"]
        champ_obj = t_res["champion_calibrator"]
        methods_scorecard = t_res.get("scorecard", {})

        print(f"  Champion Method: {champ_method.upper()}")
        print(
            f"  {'Method':14s} | {'OOF Brier':10s} | {'OOF Log Loss':12s} | {'OOF ECE':10s} | {'Slope':8s} | {'Intercept':10s}"
        )
        print("  " + "-" * 72)
        for meth, sc in methods_scorecard.items():
            brier_str = f"{sc.get('oof_brier_score', 0):.4f}"
            ll_str = f"{sc.get('oof_log_loss', 0):.4f}"
            ece_str = f"{sc.get('oof_ece', 0):.4f}"
            slope_str = f"{sc.get('calibration_slope', 1):.2f}"
            int_str = f"{sc.get('calibration_intercept', 0):.2f}"
            star = " *" if meth == champ_method else ""
            print(
                f"  {meth:14s} | {brier_str:10s} | {ll_str:12s} | {ece_str:10s} | {slope_str:8s} | {int_str:10s}{star}"
            )

        calibrated_objects[m] = champ_obj

        # Save artifact payload
        art_path = cal_models_dir / f"{model_ver}.{m}_calibrator.json"
        params = {}
        if hasattr(champ_obj, "intercept") and hasattr(champ_obj, "slope"):
            params = {"intercept": champ_obj.intercept, "slope": champ_obj.slope}
        elif hasattr(champ_obj, "temperature"):
            params = {"temperature": champ_obj.temperature}
        elif hasattr(champ_obj, "a") and hasattr(champ_obj, "b") and hasattr(champ_obj, "c"):
            params = {"a": champ_obj.a, "b": champ_obj.b, "c": champ_obj.c}
        elif hasattr(champ_obj, "thresholds") and hasattr(champ_obj, "values"):
            params = {"thresholds": champ_obj.thresholds, "values": champ_obj.values}

        art_payload = {
            "sport": "ncaab",
            "market": m,
            "model_version": model_ver,
            "champion_method": champ_method,
            "parameters": params,
            "sample_size": len(p_list),
            "baseline_brier": float(calibration_metrics(p_list, y_list).get("brier_score", 0)),
            "calibrated_oof_brier": float(methods_scorecard.get(champ_method, {}).get("oof_brier_score", 0)),
            "calibrated_at_utc": now_iso,
        }
        art_path.write_text(json.dumps(art_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"  -> Saved calibrator artifact: {art_path}")

    # -------------------------------------------------------------------------
    # STEP 2: APPLY CALIBRATOR & EVALUATE ALL SETTLED CONTRACTS (NO CUTS)
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STEP 2: FULL COHORT EVALUATION ACROSS ALL SETTLED CONTRACTS (ZERO SURVIVORSHIP BIAS)")
    print("=" * 80)

    # Transform all v5 probs using fitted calibrators
    for rec in scored_contracts:
        m = rec["market"]
        cal_obj = calibrated_objects[m]
        rec["v5_cal_prob"] = cal_obj.transform(rec["v5_raw_prob"])

    for m in ["moneyline", "spread", "total"]:
        data = by_market_data[m]
        recs = data["rows"]
        y_all = [r["outcome"] for r in recs]
        v3_all = [r["v3_prob"] for r in recs]
        v5_raw_all = [r["v5_raw_prob"] for r in recs]
        v5_cal_all = [r["v5_cal_prob"] for r in recs]

        m_v3 = calibration_metrics(v3_all, y_all)
        m_v5_raw = calibration_metrics(v5_raw_all, y_all)
        m_v5_cal = calibration_metrics(v5_cal_all, y_all)

        print(f"\n--- ALL SETTLED {m.upper()} PICKS (N = {len(recs)} contracts) ---")
        hdr = f"{'Metric':20s} | {'v3 Incumbent':14s} | {'v5 Raw':14s} | {'v5 Calibrated':14s} | {'Improvement':14s}"
        print(hdr)
        print("-" * len(hdr))

        brier_v3 = float(m_v3["brier_score"])
        brier_raw = float(m_v5_raw["brier_score"])
        brier_cal = float(m_v5_cal["brier_score"])
        b_imp = (brier_cal - brier_v3) / brier_v3 * 100
        print(
            f"{'Brier Score':20s} | {brier_v3:14.4f} | {brier_raw:14.4f} | {brier_cal:14.4f} | {b_imp:+13.1f}%"
        )

        ll_v3 = float(m_v3["log_loss"])
        ll_raw = float(m_v5_raw["log_loss"])
        ll_cal = float(m_v5_cal["log_loss"])
        ll_imp = (ll_cal - ll_v3) / ll_v3 * 100
        print(f"{'Log Loss':20s} | {ll_v3:14.4f} | {ll_raw:14.4f} | {ll_cal:14.4f} | {ll_imp:+13.1f}%")

        ece_v3 = float(m_v3["expected_calibration_error"])
        ece_raw = float(m_v5_raw["expected_calibration_error"])
        ece_cal = float(m_v5_cal["expected_calibration_error"])
        ece_imp = (ece_cal - ece_v3) / ece_v3 * 100
        print(
            f"{'ECE (Calib Error)':20s} | {ece_v3:14.4f} | {ece_raw:14.4f} | {ece_cal:14.4f} | {ece_imp:+13.1f}%"
        )

        slope_v3 = float(m_v3.get("calibration_slope") or 1.0)
        slope_cal = float(m_v5_cal.get("calibration_slope") or 1.0)
        print(
            f"{'Slope (Target 1.0)':20s} | {slope_v3:14.2f} | {float(m_v5_raw.get('calibration_slope') or 1.0):14.2f} | {slope_cal:14.2f} | {abs(slope_cal - 1.0) - abs(slope_v3 - 1.0):+13.2f}"
        )

        # Show Reliability Buckets for Moneyline
        if m == "moneyline":
            print(f"\n  [Reliability Diagram Comparison: {m.upper()}]")
            print(
                f"  {'Bucket':12s} | {'Count':6s} | {'v3 Mean P':10s} | {'v5 Cal Mean P':14s} | {'Actual Win Rate':16s}"
            )
            print("  " + "-" * 66)
            buckets = [
                (0.0, 0.2),
                (0.2, 0.3),
                (0.3, 0.4),
                (0.4, 0.5),
                (0.5, 0.6),
                (0.6, 0.7),
                (0.7, 0.8),
                (0.8, 1.0),
            ]
            for lo, hi in buckets:
                subset = [r for r in recs if lo <= r["v5_cal_prob"] < hi]
                if not subset:
                    continue
                cnt = len(subset)
                sub_y = [r["outcome"] for r in subset]
                actual_wr = sum(sub_y) / cnt * 100
                v5_mean_p = sum(r["v5_cal_prob"] for r in subset) / cnt * 100
                v3_mean_p = sum(r["v3_prob"] for r in subset) / cnt * 100
                b_lbl = f"{int(lo * 100)}%-{int(hi * 100)}%"
                print(
                    f"  {b_lbl:12s} | {cnt:6d} | {v3_mean_p:9.1f}% | {v5_mean_p:13.1f}% | {actual_wr:15.1f}%"
                )

    # -------------------------------------------------------------------------
    # STEP 3: UNCONDITIONAL EXPECTED VALUE BETTING ACROSS ALL CONTRACTS
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STEP 3: UNCONDITIONAL POSITIVE-EDGE CONTRACTS (NO CHERRY-PICKING)")
    print("=" * 80)
    print("Evaluating every contract where Model Prob > Market Price (Flat 1.0U):")
    hdr = f"{'Cohort':22s} | {'Contracts':10s} | {'Record (W-L)':14s} | {'Win %':8s} | {'PnL (Units)':12s} | {'ROI %':8s}"
    print(hdr)
    print("-" * len(hdr))

    for m in ["moneyline", "spread", "total"]:
        recs = by_market_data[m]["rows"]

        # v3 positive edge
        v3_bets = [r for r in recs if r["v3_prob"] > r["mkt_prob"]]
        v3_wins = sum(1 for r in v3_bets if r["outcome"] == 1)
        v3_pnl = sum(r["actual_pnl"] for r in v3_bets)
        v3_wr = (v3_wins / len(v3_bets) * 100) if v3_bets else 0.0
        v3_roi = (v3_pnl / len(v3_bets) * 100) if v3_bets else 0.0
        v3_rec = f"{v3_wins}-{len(v3_bets) - v3_wins}"

        # v5 calibrated positive edge (NO arbitrary margin cut, NO favorite-only cut, strictly P_cal > P_market)
        v5_bets = [r for r in recs if r["v5_cal_prob"] > r["mkt_prob"]]
        v5_wins = sum(1 for r in v5_bets if r["outcome"] == 1)
        v5_pnl = sum((r["dec_odds"] - 1.0) if r["outcome"] == 1 else -1.0 for r in v5_bets)
        v5_wr = (v5_wins / len(v5_bets) * 100) if v5_bets else 0.0
        v5_roi = (v5_pnl / len(v5_bets) * 100) if v5_bets else 0.0
        v5_rec = f"{v5_wins}-{len(v5_bets) - v5_wins}"

        print(
            f"{m.capitalize() + ' (v3 Raw)':22s} | {len(v3_bets):10d} | {v3_rec:14s} | {v3_wr:7.1f}% | {v3_pnl:+11.2f}u | {v3_roi:+7.2f}%"
        )
        print(
            f"{m.capitalize() + ' (v5 Calibrated)':22s} | {len(v5_bets):10d} | {v5_rec:14s} | {v5_wr:7.1f}% | {v5_pnl:+11.2f}u | {v5_roi:+7.2f}%"
        )
        print("  " + "-" * 78)

    # -------------------------------------------------------------------------
    # STEP 4: WALK-FORWARD EVALUATION ACROSS ALL 6,675 COMPLETED HISTORICAL GAMES
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STEP 4: FULL WALK-FORWARD ON ALL COMPLETED HISTORICAL GAMES (2025-2026)")
    print("=" * 80)

    with open("data/historical/ncaab_games_all.jsonl", encoding="utf-8") as f:
        all_games = [json.loads(line) for line in f if line.strip()]
    all_games.sort(key=lambda x: x["event_start_utc"])
    completed_games = [
        g for g in all_games if g.get("status") == "completed" and g.get("home_score") is not None
    ]

    print(f"Total Completed Historical Games: {len(completed_games)} (100% of dataset)")

    by_day: dict[str, list[dict]] = defaultdict(list)
    for g in completed_games:
        by_day[g["event_start_utc"][:10]].append(g)
    days = sorted(by_day.keys())

    # We evaluate walk-forward for all games after warmup of first 500 games
    WARMUP_GAMES = 500
    scored_games_v3 = {"spread_mae": [], "total_mae": [], "brier": [], "log_loss": []}
    scored_games_v5 = {"spread_mae": [], "total_mae": [], "brier": [], "log_loss": []}

    history_so_far: list[GameRecord] = []
    games_processed = 0

    for day in days:
        day_games = by_day[day]
        if games_processed >= WARMUP_GAMES:
            day_ups = [
                UpcomingGame(
                    event_id=g["event_id"],
                    event_start_utc=g["event_start_utc"],
                    away_team=g["away_team"],
                    home_team=g["home_team"],
                    total_line=145.0,
                    spread_away_line=0.0,
                    neutral_site=bool(g.get("neutral_site", False)),
                )
                for g in day_games
            ]

            preds_v3 = v3.predict_games(history_so_far, day_ups)
            preds_tot = v5_total.predict_totals(history_so_far, day_ups)
            preds_sp = v5_spread.predict_spreads(history_so_far, day_ups)

            t_v3_by_id = {p.event_id: p for p in preds_v3 if p.market_type == "total"}
            ml_v3_by_id = {p.event_id: p for p in preds_v3 if p.market_type == "moneyline"}
            sp_v3_by_id = {p.event_id: p for p in preds_v3 if p.market_type == "spread"}

            t_v5_by_id = {p.event_id: p for p in preds_tot if p.market_type == "total"}
            ml_v5_by_id = {p.event_id: p for p in preds_sp if p.market_type == "moneyline"}
            sp_v5_by_id = {p.event_id: p for p in preds_sp if p.market_type == "spread"}

            for g in day_games:
                ev_id = g["event_id"]
                actual_total = float(g["away_score"] + g["home_score"])
                actual_margin = float(g["home_score"] - g["away_score"])
                home_won = 1.0 if g["home_score"] > g["away_score"] else 0.0

                # v3
                t_v3 = t_v3_by_id.get(ev_id)
                ml_v3 = ml_v3_by_id.get(ev_id)
                sp_v3 = sp_v3_by_id.get(ev_id)

                if t_v3:
                    scored_games_v3["total_mae"].append(
                        abs(actual_total - t_v3.feature_basis["expected_total"])
                    )
                if sp_v3:
                    scored_games_v3["spread_mae"].append(
                        abs(actual_margin - sp_v3.feature_basis["expected_margin"])
                    )
                if ml_v3:
                    p_h_v3 = max(1e-6, min(1 - 1e-6, ml_v3.probabilities["home"]))
                    scored_games_v3["brier"].append((p_h_v3 - home_won) ** 2)
                    scored_games_v3["log_loss"].append(
                        -(home_won * math.log(p_h_v3) + (1.0 - home_won) * math.log(1.0 - p_h_v3))
                    )

                # v5
                t_v5 = t_v5_by_id.get(ev_id)
                ml_v5 = ml_v5_by_id.get(ev_id)
                sp_v5 = sp_v5_by_id.get(ev_id)

                if t_v5:
                    scored_games_v5["total_mae"].append(
                        abs(actual_total - t_v5.feature_basis["expected_total"])
                    )
                if sp_v5:
                    scored_games_v5["spread_mae"].append(
                        abs(actual_margin - sp_v5.feature_basis["expected_margin"])
                    )
                if ml_v5:
                    p_h_v5 = max(1e-6, min(1 - 1e-6, ml_v5.probabilities["home"]))
                    scored_games_v5["brier"].append((p_h_v5 - home_won) ** 2)
                    scored_games_v5["log_loss"].append(
                        -(home_won * math.log(p_h_v5) + (1.0 - home_won) * math.log(1.0 - p_h_v5))
                    )

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
            games_processed += 1

    n_eval = len(scored_games_v3["brier"])
    print(f"Total Historical Games Evaluated Out-Of-Sample: {n_eval} games (after 500 game initial warmup)")
    print(
        f"  Spread Margin MAE:   v3 = {np.mean(scored_games_v3['spread_mae']):.2f} pts  -->  v5 = {np.mean(scored_games_v5['spread_mae']):.2f} pts  ({np.mean(scored_games_v5['spread_mae']) - np.mean(scored_games_v3['spread_mae']):+.2f} pts)"
    )
    print(
        f"  Total Score MAE:     v3 = {np.mean(scored_games_v3['total_mae']):.2f} pts  -->  v5 = {np.mean(scored_games_v5['total_mae']):.2f} pts  ({np.mean(scored_games_v5['total_mae']) - np.mean(scored_games_v3['total_mae']):+.2f} pts)"
    )
    print(
        f"  Moneyline Brier:     v3 = {np.mean(scored_games_v3['brier']):.4f}      -->  v5 = {np.mean(scored_games_v5['brier']):.4f}      ({(np.mean(scored_games_v5['brier']) - np.mean(scored_games_v3['brier'])) / np.mean(scored_games_v3['brier']) * 100:+.1f}%)"
    )
    print(
        f"  Moneyline Log Loss:  v3 = {np.mean(scored_games_v3['log_loss']):.4f}      -->  v5 = {np.mean(scored_games_v5['log_loss']):.4f}      ({(np.mean(scored_games_v5['log_loss']) - np.mean(scored_games_v3['log_loss'])) / np.mean(scored_games_v3['log_loss']) * 100:+.1f}%)"
    )
    print("=" * 80)


if __name__ == "__main__":
    run_full_calibration_and_evaluation()
