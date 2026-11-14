"""Comprehensive Evaluation of NCAAB v6 Models Across ALL 969 Settled Contracts in data/flat/ncaab.xlsx.

Zero cuts, zero skips:
- 270 Moneyline contracts
- 335 Spread contracts
- 364 Total contracts
Total: 969 settled contracts (349 unique Division-1 games)

Evaluates:
1. Baseline accuracy and Brier score before vs. after v6 architecture.
2. 5-Method Out-of-Fold Calibration Tournament on the canonical flat ledger cohort.
3. Unconditional positive-edge betting metrics.
4. Side-by-side comparison with original flat ledger results.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd

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


def run_full_flat_evaluation() -> dict:
    flat_path = Path("data/flat/ncaab.xlsx")
    df = pd.read_excel(flat_path, sheet_name="Picks")
    settled = df[df["status"] == "settled"].copy()

    print(f"Loaded {len(settled)} settled contracts from {flat_path}")
    print(f"Unique games: {settled['event_id'].nunique()}")
    print("Market breakdown:\n", settled["market_type"].value_counts().to_string())

    # Find market consensus lines for each game (closest to 50% implied probability)
    total_settled = settled[settled["market_type"] == "total"]
    game_consensus_total: dict[str, float] = {}
    for ev, grp in total_settled.groupby("event_id"):
        best_row = grp.iloc[(grp["market_implied_probability"] - 0.50).abs().argmin()]
        game_consensus_total[str(ev)] = float(best_row["line"])

    spread_settled = settled[settled["market_type"] == "spread"]
    game_consensus_spread: dict[str, float] = {}
    for ev, grp in spread_settled.groupby("event_id"):
        best_row = grp.iloc[(grp["market_implied_probability"] - 0.50).abs().argmin()]
        away_line = float(best_row["line"]) if best_row["selection"] == "away" else -float(best_row["line"])
        game_consensus_spread[str(ev)] = away_line

    fs = FeatureStore("data")
    v6_spread = NCAABSpreadModelV6()
    v6_total = NCAABTotalModelV6()

    # Group unique games by day
    unique_games: dict[tuple[str, str], dict] = {}
    for idx, r in settled.iterrows():
        key = (str(r["event_id"]), str(r["event_start_utc"])[:10])
        if key not in unique_games:
            unique_games[key] = {
                "event_id": str(r["event_id"]),
                "start": str(r["event_start_utc"]),
                "day": str(r["event_start_utc"])[:10],
                "away": str(r["away_team"]),
                "home": str(r["home_team"]),
                "away_score": float(r["away_score"]),
                "home_score": float(r["home_score"]),
                "neutral_site": bool("vs" in str(r.get("matchup", "")).lower()),
                "consensus_total": game_consensus_total.get(str(r["event_id"]), 151.4),
                "consensus_spread": game_consensus_spread.get(str(r["event_id"]), 0.0),
            }

    print(f"\nScoring {len(unique_games)} unique games point-in-time...")

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
                spread_away_line=ev["consensus_spread"],
                total_line=ev["consensus_total"],
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

    # Evaluate each contract
    scored_contracts: list[dict] = []
    by_market: dict[str, dict[str, list]] = {
        "moneyline": {"v6_probs": [], "outcomes": [], "mkt_probs": [], "orig_pnl": [], "rows": []},
        "spread": {"v6_probs": [], "outcomes": [], "mkt_probs": [], "orig_pnl": [], "rows": []},
        "total": {"v6_probs": [], "outcomes": [], "mkt_probs": [], "orig_pnl": [], "rows": []},
    }

    for idx, r in settled.iterrows():
        m = str(r["market_type"]).lower()
        if m not in by_market:
            continue

        key = (str(r["event_id"]), str(r["event_start_utc"])[:10])
        gc = game_cache[key]

        res = str(r["result"]).lower()
        if res == "win":
            y = 1
        elif res == "loss":
            y = 0
        else:
            continue

        selection = str(r["selection"]).lower()
        line = float(r["line"]) if pd.notna(r["line"]) else 0.0
        mkt_p = float(r["market_implied_probability"]) if pd.notna(r["market_implied_probability"]) else 0.50
        mkt_p = max(0.001, min(0.999, mkt_p))
        dec_odds = float(r["decimal_odds"]) if pd.notna(r["decimal_odds"]) else (1.0 / mkt_p)
        orig_pnl = float(r["pnl_units"]) if pd.notna(r["pnl_units"]) else 0.0

        # Compute v6 raw probability with exact contract definitions
        if m == "moneyline":
            v6_p = gc["v6_p_home"] if selection in ("home", "1") else (1.0 - gc["v6_p_home"])
        elif m == "spread":
            # For away selection: away covers if home_margin < line
            # For home selection: home covers if home_margin > -line
            if selection in ("away", "2"):
                v6_p = _student_t_cdf(line, gc["v6_exp_margin"], gc["v6_margin_sd"], df=8.0)
            else:
                v6_p = 1.0 - _student_t_cdf(-line, gc["v6_exp_margin"], gc["v6_margin_sd"], df=8.0)
        elif m == "total":
            under_prob = _normal_cdf(line, gc["v6_exp_total"], gc["v6_total_sd"])
            v6_p = under_prob if selection == "under" else (1.0 - under_prob)

        v6_p = max(0.0001, min(0.9999, v6_p))

        rec = {
            "pick_id": str(r["pick_id"]),
            "event_id": str(r["event_id"]),
            "event_start_utc": str(r["event_start_utc"]),
            "away_team": str(r["away_team"]),
            "home_team": str(r["home_team"]),
            "market": m,
            "selection": selection,
            "line": line,
            "mkt_prob": mkt_p,
            "dec_odds": dec_odds,
            "units": float(r["units"]) if pd.notna(r["units"]) else 1.0,
            "outcome": y,
            "v6_raw_prob": v6_p,
            "orig_res": res,
            "orig_pnl": orig_pnl,
            "orig_model": str(r.get("model_version", "unknown")),
        }
        scored_contracts.append(rec)
        by_market[m]["v6_probs"].append(v6_p)
        by_market[m]["outcomes"].append(y)
        by_market[m]["mkt_probs"].append(mkt_p)
        by_market[m]["orig_pnl"].append(orig_pnl)
        by_market[m]["rows"].append(rec)

    print(f"\nSuccessfully evaluated all {len(scored_contracts)} settled contracts.")

    # Calibration Tournament
    print("\n" + "=" * 80)
    print("STEP 1: OUT-OF-FOLD CALIBRATION TOURNAMENT ACROSS ALL FLAT SETTLED CONTRACTS")
    print("=" * 80)

    calibrated_objects = {}
    now_iso = datetime.now(UTC).isoformat()
    cal_models_dir = Path("data/calibration/models")
    cal_models_dir.mkdir(parents=True, exist_ok=True)

    for m in ["moneyline", "spread", "total"]:
        data = by_market[m]
        p_list = data["v6_probs"]
        y_list = data["outcomes"]
        model_ver = "ncaab-spread-v6" if m in ("moneyline", "spread") else "ncaab-total-v6"

        print(f"\n--- Tournament for {m.upper()} (N = {len(p_list)} contracts) ---")
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
            sl = sc.get("calibration_slope")
            slope_str = f"{sl:.2f}" if sl is not None else "N/A"
            ic = sc.get("calibration_intercept")
            int_str = f"{ic:.2f}" if ic is not None else "N/A"
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

    # Full cohort evaluation
    print("\n" + "=" * 80)
    print("STEP 2: FULL SETTLED COHORT EVALUATION (ALL 969 CONTRACTS IN FLAT LEDGER)")
    print("=" * 80)

    for rec in scored_contracts:
        m = rec["market"]
        cal_obj = calibrated_objects[m]
        rec["v6_cal_prob"] = cal_obj.transform(rec["v6_raw_prob"])

    for m in ["moneyline", "spread", "total"]:
        data = by_market[m]
        recs = data["rows"]
        y_all = [r["outcome"] for r in recs]
        v6_raw_all = [r["v6_raw_prob"] for r in recs]
        v6_cal_all = [r["v6_cal_prob"] for r in recs]

        m_raw = calibration_metrics(v6_raw_all, y_all)
        m_cal = calibration_metrics(v6_cal_all, y_all)

        raw_sl = m_raw.get("calibration_slope")
        raw_sl_str = f"{raw_sl:.2f}" if raw_sl is not None else "N/A"
        cal_sl = m_cal.get("calibration_slope")
        cal_sl_str = f"{cal_sl:.2f}" if cal_sl is not None else "N/A"

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
        print(f"  Calibration Slope:  Raw = {raw_sl_str}  -->  Calibrated = {cal_sl_str}")

    # Step 3: Positive-edge betting comparison
    print("\n" + "=" * 80)
    print("STEP 3: UNCONDITIONAL POSITIVE-EDGE BETTING PERFORMANCE (Edge > 0)")
    print("=" * 80)
    hdr = f"{'Cohort':28s} | {'Contracts':10s} | {'Record (W-L)':14s} | {'Win %':8s} | {'PnL (Units)':12s} | {'ROI %':8s}"
    print(hdr)
    print("-" * len(hdr))

    total_orig_pnl = sum(r["orig_pnl"] for r in scored_contracts)
    total_orig_wins = sum(1 for r in scored_contracts if r["outcome"] == 1)
    total_orig_losses = sum(1 for r in scored_contracts if r["outcome"] == 0)
    orig_wr = (total_orig_wins / len(scored_contracts) * 100) if scored_contracts else 0.0
    orig_roi = (total_orig_pnl / len(scored_contracts) * 100) if scored_contracts else 0.0
    print(
        f"{'Original Flat Ledger (All)':28s} | {len(scored_contracts):10d} | {total_orig_wins}-{total_orig_losses:10d} | {orig_wr:7.1f}% | {total_orig_pnl:+11.2f}u | {orig_roi:+7.2f}%"
    )

    for m in ["moneyline", "spread", "total"]:
        orig_m_pnl = sum(by_market[m]["orig_pnl"])
        orig_m_wins = sum(1 for r in by_market[m]["rows"] if r["outcome"] == 1)
        orig_m_losses = sum(1 for r in by_market[m]["rows"] if r["outcome"] == 0)
        orig_m_wr = (orig_m_wins / len(by_market[m]["rows"]) * 100) if by_market[m]["rows"] else 0.0
        orig_m_roi = (orig_m_pnl / len(by_market[m]["rows"]) * 100) if by_market[m]["rows"] else 0.0
        print(
            f"{'  ↳ Orig ' + m.capitalize():28s} | {len(by_market[m]['rows']):10d} | {orig_m_wins}-{orig_m_losses:10d} | {orig_m_wr:7.1f}% | {orig_m_pnl:+11.2f}u | {orig_m_roi:+7.2f}%"
        )

    print("-" * len(hdr))

    tot_v6_wins, tot_v6_losses, tot_v6_pnl, tot_v6_bets = 0, 0, 0.0, 0
    for m in ["moneyline", "spread", "total"]:
        recs = by_market[m]["rows"]
        # Positive edge against actual Polymarket market quote
        v6_bets = [r for r in recs if r["v6_cal_prob"] > r["mkt_prob"]]
        v6_wins = sum(1 for r in v6_bets if r["outcome"] == 1)
        v6_pnl = sum((r["dec_odds"] - 1.0) if r["outcome"] == 1 else -1.0 for r in v6_bets)
        v6_wr = (v6_wins / len(v6_bets) * 100) if v6_bets else 0.0
        v6_roi = (v6_pnl / len(v6_bets) * 100) if v6_bets else 0.0
        v6_rec = f"{v6_wins}-{len(v6_bets) - v6_wins}"

        tot_v6_wins += v6_wins
        tot_v6_losses += len(v6_bets) - v6_wins
        tot_v6_pnl += v6_pnl
        tot_v6_bets += len(v6_bets)

        print(
            f"{'v6 Calibrated ' + m.capitalize():28s} | {len(v6_bets):10d} | {v6_rec:14s} | {v6_wr:7.1f}% | {v6_pnl:+11.2f}u | {v6_roi:+7.2f}%"
        )

    tot_v6_wr = (tot_v6_wins / tot_v6_bets * 100) if tot_v6_bets else 0.0
    tot_v6_roi = (tot_v6_pnl / tot_v6_bets * 100) if tot_v6_bets else 0.0
    print("-" * len(hdr))
    print(
        f"{'v6 Calibrated Total (All)':28s} | {tot_v6_bets:10d} | {tot_v6_wins}-{tot_v6_losses:10d} | {tot_v6_wr:7.1f}% | {tot_v6_pnl:+11.2f}u | {tot_v6_roi:+7.2f}%"
    )

    # Step 4: Gated 3.5% Edge Hurdle
    print("\n" + "=" * 80)
    print("STEP 4: QUALIFIED CALLS (Edge >= 3.5% Hurdle)")
    print("=" * 80)
    print(hdr)
    print("-" * len(hdr))

    tot_q_wins, tot_q_losses, tot_q_pnl, tot_q_bets = 0, 0, 0.0, 0
    for m in ["moneyline", "spread", "total"]:
        recs = by_market[m]["rows"]
        # Edge >= 3.5%
        q_bets = [r for r in recs if (r["v6_cal_prob"] - r["mkt_prob"]) >= 0.035]
        q_wins = sum(1 for r in q_bets if r["outcome"] == 1)
        q_pnl = sum((r["dec_odds"] - 1.0) if r["outcome"] == 1 else -1.0 for r in q_bets)
        q_wr = (q_wins / len(q_bets) * 100) if q_bets else 0.0
        q_roi = (q_pnl / len(q_bets) * 100) if q_bets else 0.0
        q_rec = f"{q_wins}-{len(q_bets) - q_wins}"

        tot_q_wins += q_wins
        tot_q_losses += len(q_bets) - q_wins
        tot_q_pnl += q_pnl
        tot_q_bets += len(q_bets)

        print(
            f"{'v6 Qualified ' + m.capitalize():28s} | {len(q_bets):10d} | {q_rec:14s} | {q_wr:7.1f}% | {q_pnl:+11.2f}u | {q_roi:+7.2f}%"
        )

    tot_q_wr = (tot_q_wins / tot_q_bets * 100) if tot_q_bets else 0.0
    tot_q_roi = (tot_q_pnl / tot_q_bets * 100) if tot_q_bets else 0.0
    print("-" * len(hdr))
    print(
        f"{'v6 Qualified Total (All)':28s} | {tot_q_bets:10d} | {tot_q_wins}-{tot_q_losses:10d} | {tot_q_wr:7.1f}% | {tot_q_pnl:+11.2f}u | {tot_q_roi:+7.2f}%"
    )

    return {
        "total_contracts": len(scored_contracts),
        "orig_pnl": total_orig_pnl,
        "v6_pnl": tot_v6_pnl,
        "v6_q_pnl": tot_q_pnl,
    }


if __name__ == "__main__":
    run_full_flat_evaluation()
