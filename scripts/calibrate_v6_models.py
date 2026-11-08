"""Calibrate and evaluate NCAAB v6 across all 1,658 settled contracts."""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")


from model_prediction.calibration import (
    calibration_metrics,
    run_calibration_tournament,
)
from model_prediction.features.base import FeatureStore
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v6 import NCAABSpreadModelV6, _student_t_cdf
from model_prediction.models.ncaab_total_v6 import NCAABTotalModelV6


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5 if x == mean else (1.0 if x > mean else 0.0)
    return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2))))


def run_v6_calibration():
    db_path = Path("E:/model-prediction-runtime/ledgers/ledgers.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

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
    v6_spread = NCAABSpreadModelV6()
    v6_total = NCAABTotalModelV6()

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

    print(f"Scoring {len(unique_games)} unique game events with v6...")

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

        preds_v6_sp = v6_spread.predict_spreads(history, day_ups)
        preds_v6_tot = v6_total.predict_totals(history, day_ups)

        sp_by_ev = {p.event_id: p for p in preds_v6_sp if p.market_type == "moneyline"}
        tot_by_ev = {p.event_id: p for p in preds_v6_tot if p.market_type == "total"}

        for key, ev in ev_list:
            ev_id = ev["event_id"]
            ml_v6 = sp_by_ev[ev_id]
            tot_v6 = tot_by_ev[ev_id]

            game_cache[key] = {
                "v6_exp_margin": float(ml_v6.feature_basis["expected_margin"]),
                "v6_margin_sd": float(ml_v6.feature_basis["effective_margin_sd"]),
                "v6_p_home": float(ml_v6.probabilities["home"]),
                "v6_exp_total": float(tot_v6.feature_basis["expected_total"]),
                "v6_total_sd": float(tot_v6.feature_basis["effective_total_sd"]),
            }

    scored_contracts: list[dict] = []
    by_market_data: dict[str, dict[str, list]] = {
        "moneyline": {"v6_probs": [], "outcomes": [], "mkt_probs": [], "rows": []},
        "spread": {"v6_probs": [], "outcomes": [], "mkt_probs": [], "rows": []},
        "total": {"v6_probs": [], "outcomes": [], "mkt_probs": [], "rows": []},
    }

    for row in rows:
        r = dict(row)
        m = r["market_type"]
        if m not in by_market_data:
            continue

        p = json.loads(r["decision_payload_json"] or "{}")
        key = (r["event_id"], r["event_start_utc"][:10])
        gc = game_cache[key]

        res = r["result"]
        if res == "win":
            y = 1
        elif res == "loss":
            y = 0
        else:
            continue

        selection = r["selection"]
        line = float(r["line"]) if r["line"] is not None else None
        mkt_p = float(r["market_probability"] or p.get("market_probability_at_decision") or 0.5)
        mkt_p = max(0.001, min(0.999, mkt_p))
        dec_odds = float(r.get("decimal_odds") or p.get("decision_decimal_odds") or (1.0 / mkt_p))

        # Compute v6 raw prob
        if m == "moneyline":
            v6_p = gc["v6_p_home"] if selection == "home" else (1.0 - gc["v6_p_home"])
        elif m == "spread":
            away_cover = _student_t_cdf(
                line if line is not None else 0.0, gc["v6_exp_margin"], gc["v6_margin_sd"], df=8.0
            )
            v6_p = away_cover if selection == "away" else (1.0 - away_cover)
        elif m == "total":
            under_prob = _normal_cdf(
                line if line is not None else 145.0, gc["v6_exp_total"], gc["v6_total_sd"]
            )
            v6_p = under_prob if selection == "under" else (1.0 - under_prob)

        v6_p = max(0.0001, min(0.9999, v6_p))

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
            "v6_raw_prob": v6_p,
            "actual_res": res,
            "actual_pnl": float(r["pnl_units"] or 0.0),
        }
        scored_contracts.append(record)
        by_market_data[m]["v6_probs"].append(v6_p)
        by_market_data[m]["outcomes"].append(y)
        by_market_data[m]["mkt_probs"].append(mkt_p)
        by_market_data[m]["rows"].append(record)

    print(f"\nScored {len(scored_contracts)} contracts under v6.")

    # Calibration Tournament
    print("\n" + "=" * 80)
    print("STEP 1: NCAAB v6 CALIBRATION TOURNAMENT ACROSS ALL SETTLED CONTRACTS")
    print("=" * 80)

    calibrated_objects = {}
    now_iso = datetime.now(UTC).isoformat()
    cal_models_dir = Path("data/calibration/models")
    cal_models_dir.mkdir(parents=True, exist_ok=True)

    for m in ["moneyline", "spread", "total"]:
        data = by_market_data[m]
        p_list = data["v6_probs"]
        y_list = data["outcomes"]
        model_ver = "ncaab-spread-v6" if m in ("moneyline", "spread") else "ncaab-total-v6"

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

        # Save calibrator artifact
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
        print(f"  -> Saved v6 calibrator artifact: {art_path}")

    # Step 2: Full cohort evaluation
    print("\n" + "=" * 80)
    print("STEP 2: FULL SETTLED COHORT EVALUATION (ZERO CUTS)")
    print("=" * 80)

    for rec in scored_contracts:
        m = rec["market"]
        cal_obj = calibrated_objects[m]
        rec["v6_cal_prob"] = cal_obj.transform(rec["v6_raw_prob"])

    for m in ["moneyline", "spread", "total"]:
        data = by_market_data[m]
        recs = data["rows"]
        y_all = [r["outcome"] for r in recs]
        v6_raw_all = [r["v6_raw_prob"] for r in recs]
        v6_cal_all = [r["v6_cal_prob"] for r in recs]

        m_raw = calibration_metrics(v6_raw_all, y_all)
        m_cal = calibration_metrics(v6_cal_all, y_all)

        print(f"\n--- ALL SETTLED {m.upper()} PICKS (N = {len(recs)} contracts) ---")
        print(
            f"  Brier Score:        Raw = {m_raw['brier_score']:.4f}  -->  Calibrated = {m_cal['brier_score']:.4f}"
        )
        print(
            f"  Log Loss:           Raw = {m_raw['log_loss']:.4f}  -->  Calibrated = {m_cal['log_loss']:.4f}"
        )
        print(
            f"  ECE (Calib Error):  Raw = {m_raw['expected_calibration_error']:.4f}  -->  Calibrated = {m_cal['expected_calibration_error']:.4f}"
        )
        print(
            f"  Calibration Slope:  Raw = {m_raw.get('calibration_slope', 1):.2f}  -->  Calibrated = {m_cal.get('calibration_slope', 1):.2f}"
        )

    # Step 3: Positive-edge betting
    print("\n" + "=" * 80)
    print("STEP 3: UNCONDITIONAL POSITIVE-EDGE CONTRACTS (NO CHERRY-PICKING)")
    print("=" * 80)
    hdr = f"{'Cohort':24s} | {'Contracts':10s} | {'Record (W-L)':14s} | {'Win %':8s} | {'PnL (Units)':12s} | {'ROI %':8s}"
    print(hdr)
    print("-" * len(hdr))

    for m in ["moneyline", "spread", "total"]:
        recs = by_market_data[m]["rows"]
        v6_bets = [r for r in recs if r["v6_cal_prob"] > r["mkt_prob"]]
        v6_wins = sum(1 for r in v6_bets if r["outcome"] == 1)
        v6_pnl = sum((r["dec_odds"] - 1.0) if r["outcome"] == 1 else -1.0 for r in v6_bets)
        v6_wr = (v6_wins / len(v6_bets) * 100) if v6_bets else 0.0
        v6_roi = (v6_pnl / len(v6_bets) * 100) if v6_bets else 0.0
        v6_rec = f"{v6_wins}-{len(v6_bets) - v6_wins}"

        print(
            f"{m.capitalize() + ' (v6 Calibrated)':24s} | {len(v6_bets):10d} | {v6_rec:14s} | {v6_wr:7.1f}% | {v6_pnl:+11.2f}u | {v6_roi:+7.2f}%"
        )


if __name__ == "__main__":
    run_v6_calibration()
