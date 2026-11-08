"""Calibrate NBA v9 champions on settled games and write artifacts.

Fits calibrator tournaments across settled contracts for NBA moneyline, spread, and total:
1. data/calibration/models/nba-moneyline-v9.moneyline_calibrator.json
2. data/calibration/models/nba-spread-margin-v9.spread_calibrator.json
3. data/calibration/models/nba-total-margin-v9.total_calibrator.json
"""

from __future__ import annotations

import json
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
from model_prediction.models.nba_v9 import (
    NBA_MONEYLINE_V9_VERSION,
    NBA_SPREAD_V9_VERSION,
    NBA_TOTAL_V9_VERSION,
    NBAStructuralV9Engine,
)


def run_nba_v9_calibration():
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
        WHERE sport = 'nba' AND status = 'settled'
        ORDER BY event_start_utc ASC, created_at_utc ASC
        """
    ).fetchall()

    print(f"Total Settled NBA Picks Loaded: {len(rows)}")

    fs = FeatureStore("data")
    v9_engine = NBAStructuralV9Engine()

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
            }

    print(f"Unique game dates to score: {len(unique_games)}")

    by_day_unique: dict[str, list[tuple[tuple[str, str], dict]]] = defaultdict(list)
    for key, ev in unique_games.items():
        by_day_unique[ev["day"]].append((key, ev))

    # Pre-cache history and games
    by_market_data: dict[str, dict[str, list]] = {
        "moneyline": {"v9_probs": [], "outcomes": [], "rows": []},
        "spread": {"v9_probs": [], "outcomes": [], "rows": []},
        "total": {"v9_probs": [], "outcomes": [], "rows": []},
    }

    # Score each day point-in-time
    day_preds_map: dict[str, dict[str, dict]] = {}
    for day, ev_list in sorted(by_day_unique.items()):
        history = fs.games_before("nba", day)
        upcoming = [
            UpcomingGame(
                event_id=ev["event_id"],
                event_start_utc=ev["start"],
                away_team=ev["away"],
                home_team=ev["home"],
                spread_away_line=0.0,
                total_line=228.0,
                league="NBA",
            )
            for _, ev in ev_list
        ]
        preds = v9_engine.predict_games(history, upcoming)
        day_preds_map[day] = {}
        for p in preds:
            ev_id = p.event_id
            if ev_id not in day_preds_map[day]:
                day_preds_map[day][ev_id] = {}
            day_preds_map[day][ev_id][p.market_type] = p

    for row in rows:
        r = dict(row)
        m = r["market_type"]
        if m not in by_market_data:
            continue

        res = r["result"]
        if res == "win":
            y = 1
        elif res == "loss":
            y = 0
        else:
            continue

        day = r["event_start_utc"][:10]
        ev_id = r["event_id"]
        preds_for_ev = day_preds_map.get(day, {}).get(ev_id, {})
        pred = preds_for_ev.get(m)
        if not pred:
            continue

        selection = r["selection"]
        line = float(r["line"]) if r["line"] is not None else None

        if m == "moneyline":
            v9_p = float(pred.probabilities.get(selection, 0.5))
        elif m == "spread":
            # Recalculate cover prob for this specific line
            basis = pred.feature_basis
            exp_margin = float(basis["expected_margin"])
            sd = float(basis["effective_margin_sd"])
            from model_prediction.models.nba_v9 import _normal_cdf

            away_cover = _normal_cdf(line if line is not None else 0.0, exp_margin, sd)
            v9_p = away_cover if selection == "away" else (1.0 - away_cover)
        elif m == "total":
            basis = pred.feature_basis
            exp_tot = float(basis["expected_total"])
            sd = float(basis["effective_total_sd"])
            from model_prediction.models.nba_v9 import _normal_cdf

            under_prob = _normal_cdf(line if line is not None else 228.0, exp_tot, sd)
            v9_p = under_prob if selection == "under" else (1.0 - under_prob)

        v9_p = max(0.0001, min(0.9999, v9_p))
        by_market_data[m]["v9_probs"].append(v9_p)
        by_market_data[m]["outcomes"].append(y)
        by_market_data[m]["rows"].append(r)

    print("\n" + "=" * 80)
    print("NBA v9 CALIBRATION TOURNAMENT ACROSS ALL SETTLED CONTRACTS")
    print("=" * 80)

    now_iso = datetime.now(UTC).isoformat()
    cal_models_dir = Path("data/calibration/models")
    cal_models_dir.mkdir(parents=True, exist_ok=True)

    version_map = {
        "moneyline": NBA_MONEYLINE_V9_VERSION,
        "spread": NBA_SPREAD_V9_VERSION,
        "total": NBA_TOTAL_V9_VERSION,
    }

    for m in ["moneyline", "spread", "total"]:
        data = by_market_data[m]
        p_list = data["v9_probs"]
        y_list = data["outcomes"]
        model_ver = version_map[m]

        print(f"\n--- Tournament for {m.upper()} (N = {len(p_list)} settled picks) ---")
        t_res = run_calibration_tournament(
            p_list, y_list, model_ver, n_splits=5, minimum_sample=20, temporal=True
        )

        champ_method = t_res["champion_method"]
        champ_obj = t_res["champion_calibrator"]
        methods_scorecard = t_res.get("scorecard", {})

        print(f"  Champion Method: {champ_method.upper()}")
        print(f"  {'Method':14s} | {'OOF Brier':10s} | {'OOF Log Loss':12s} | {'OOF ECE':10s}")
        print("  " + "-" * 55)
        for meth, sc in methods_scorecard.items():
            brier_str = f"{sc.get('oof_brier_score', 0):.4f}"
            ll_str = f"{sc.get('oof_log_loss', 0):.4f}"
            ece_str = f"{sc.get('oof_ece', 0):.4f}"
            star = " *" if meth == champ_method else ""
            print(f"  {meth:14s} | {brier_str:10s} | {ll_str:12s} | {ece_str:10s}{star}")

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

        base_metrics = calibration_metrics(p_list, y_list)
        art_payload = {
            "sport": "nba",
            "market": m,
            "model_version": model_ver,
            "champion_method": champ_method,
            "parameters": params,
            "sample_size": len(p_list),
            "baseline_brier": round(float(base_metrics.get("brier_score", 0)), 6),
            "calibrated_oof_brier": round(
                float(methods_scorecard.get(champ_method, {}).get("oof_brier_score", 0)), 6
            ),
            "calibrated_at_utc": now_iso,
        }

        art_path.write_text(json.dumps(art_payload, indent=2))
        print(f"  Saved calibrator artifact: {art_path}")


if __name__ == "__main__":
    run_nba_v9_calibration()
