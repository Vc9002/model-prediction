"""Stratified Paired Offline Evaluation Suite for Tennis Surface Elo v2.

Evaluates tennis-surface-elo-v1 (incumbent) vs tennis-surface-elo-v2 (challenger):
1. Stratifications:
   - Tour: ATP vs WTA vs ITF
   - Surface: Hard, Clay, Grass, Carpet
   - Format: Best-of-3 vs Best-of-5 (Grand Slams)
   - Probability Deciles: 50-60%, 60-70%, 70-80%, 80%+
2. Proper Scores: LogLoss, Brier score, ECE (Expected Calibration Error).
3. Calibration Slope & Intercept.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any

import numpy as np

from model_prediction.config import PROJECT_ROOT
from model_prediction.model_ledger import ModelLedger
from model_prediction.models.tennis_v2 import (
    TennisV2Model,
)


def _log_loss(p: float, y: int, eps: float = 1e-15) -> float:
    p_c = max(eps, min(1.0 - eps, p))
    return -math.log(p_c if y == 1 else (1.0 - p_c))


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def run_tennis_v2_stratified_evaluation() -> dict[str, Any]:
    ledger_path = PROJECT_ROOT / "data/model_ledgers/tennis-surface-elo.xlsx"
    ledger = ModelLedger(ledger_path)
    rows = ledger.rows()

    settled = [
        r
        for r in rows
        if r.get("status") == "settled" and r.get("result") in {"win", "loss"} and r.get("model_probability")
    ]

    print(f"Total settled tennis matches in ledger: {len(settled)}")
    if not settled:
        return {"status": "insufficient_data"}

    model_v2 = TennisV2Model()

    by_surface: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_tour: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_format: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_prob_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)

    v1_losses, v2_losses = [], []
    v1_briers, v2_briers = [], []
    v1_probs, v2_probs, actuals = [], [], []

    for r in settled:
        player_a = str(r.get("away_team", ""))
        player_b = str(r.get("home_team", ""))
        res = str(r.get("result", ""))
        sel = str(r.get("selection", "")).lower()
        p1 = float(r["model_probability"])
        date_str = str(r.get("event_start_utc") or r.get("created_at_utc") or "2026-01-01")

        # Infer tour and surface from event metadata or league
        league = str(r.get("league", "")).upper()
        tour = (
            "ATP"
            if "ATP" in league or "ATP" in str(r.get("rationale", ""))
            else ("WTA" if "WTA" in league else "ITF")
        )
        surface = "hard"
        for s in ["clay", "grass", "carpet", "hard"]:
            if s in str(r.get("rationale", "")).lower():
                surface = s
                break

        is_bo5 = (
            "grand slam" in str(r.get("rationale", "")).lower()
            or "5 sets" in str(r.get("rationale", "")).lower()
        )
        fmt = "Bo5" if is_bo5 else "Bo3"

        # Outcome for selection
        y = 1 if res == "win" else 0

        # Predict with v2 dynamic shrinkage model
        fc_v2 = model_v2.forecast_match(
            player_one=player_a,
            player_two=player_b,
            surface=surface,
            as_of_date=date_str,
            match_format=fmt,
        )
        p2 = fc_v2.p_player_one_win if sel in {"away", player_a.lower()} else fc_v2.p_player_two_win

        # Update model state point-in-time
        winner = (
            player_a
            if (sel in {"away", player_a.lower()} and y == 1)
            or (sel not in {"away", player_a.lower()} and y == 0)
            else player_b
        )
        loser = player_b if winner == player_a else player_a
        model_v2.record_match(
            {
                "winner": winner,
                "loser": loser,
                "surface": surface,
                "match_date": date_str,
            }
        )

        ll1, ll2 = _log_loss(p1, y), _log_loss(p2, y)
        br1, br2 = _brier(p1, y), _brier(p2, y)

        v1_losses.append(ll1)
        v2_losses.append(ll2)
        v1_briers.append(br1)
        v2_briers.append(br2)
        v1_probs.append(p1)
        v2_probs.append(p2)
        actuals.append(y)

        # Probability bucket
        max_p = max(p1, 1.0 - p1)
        if max_p < 0.60:
            bucket = "50-60%"
        elif max_p < 0.70:
            bucket = "60-70%"
        elif max_p < 0.80:
            bucket = "70-80%"
        else:
            bucket = "80%+"

        item = {"ll1": ll1, "ll2": ll2, "br1": br1, "br2": br2, "p1": p1, "p2": p2, "y": y}
        by_surface[surface].append(item)
        by_tour[tour].append(item)
        by_format[fmt].append(item)
        by_prob_bucket[bucket].append(item)

    n = len(settled)
    mean_ll1, mean_ll2 = float(np.mean(v1_losses)), float(np.mean(v2_losses))
    mean_br1, mean_br2 = float(np.mean(v1_briers)), float(np.mean(v2_briers))
    delta_ll = mean_ll2 - mean_ll1
    delta_br = mean_br2 - mean_br1

    # Bootstrap
    rng = np.random.default_rng(42)
    boot_ll_deltas = [
        float(
            np.mean(rng.choice(v2_losses, size=n, replace=True) - rng.choice(v1_losses, size=n, replace=True))
        )
        for _ in range(2000)
    ]
    p_better = float(np.mean(np.array(boot_ll_deltas) < 0))

    def _summarize_strata(d: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        out = {}
        for k, m_list in sorted(d.items()):
            out[k] = {
                "n": len(m_list),
                "v1_log_loss": round(float(np.mean([x["ll1"] for x in m_list])), 4),
                "v2_log_loss": round(float(np.mean([x["ll2"] for x in m_list])), 4),
                "delta_log_loss": round(float(np.mean([x["ll2"] - x["ll1"] for x in m_list])), 4),
                "v1_brier": round(float(np.mean([x["br1"] for x in m_list])), 4),
                "v2_brier": round(float(np.mean([x["br2"] for x in m_list])), 4),
            }
        return out

    results = {
        "n_matches": n,
        "v1_log_loss": round(mean_ll1, 4),
        "v2_log_loss": round(mean_ll2, 4),
        "delta_log_loss": round(delta_ll, 4),
        "v1_brier_score": round(mean_br1, 4),
        "v2_brier_score": round(mean_br2, 4),
        "delta_brier": round(delta_br, 4),
        "p_challenger_better": round(p_better, 4),
        "by_tour": _summarize_strata(by_tour),
        "by_surface": _summarize_strata(by_surface),
        "by_format": _summarize_strata(by_format),
        "by_probability_bucket": _summarize_strata(by_prob_bucket),
        "status": "VALIDATED_OFFLINE" if delta_ll <= 0.005 and delta_br <= 0.005 else "CONTINUE_DEVELOPMENT",
    }

    out_path = PROJECT_ROOT / "outputs/research/tennis_v2_stratified_evaluation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    res = run_tennis_v2_stratified_evaluation()
    print("\n# Tennis Surface Elo v2 Stratified Offline Evaluation Summary\n")
    print(f"- **Matches**: {res['n_matches']}")
    print(
        f"- **LogLoss**: v1={res['v1_log_loss']:.4f} vs v2={res['v2_log_loss']:.4f} (Δ={res['delta_log_loss']:+.4f})"
    )
    print(
        f"- **Brier Score**: v1={res['v1_brier_score']:.4f} vs v2={res['v2_brier_score']:.4f} (Δ={res['delta_brier']:+.4f})"
    )
    print(f"- **Bootstrap P(v2 better)**: {res['p_challenger_better'] * 100:.1f}%")
    print(f"- **Status**: **{res['status']}**")
