"""NCAAF Structural v2 Chronological Walk-Forward Offline Evaluation Suite.

Evaluates cfb-structural-v2 against college-football-v1 incumbent on 3,931 real historical
ESPN games (2019-2024) using expanding chronological walk-forward evaluation.
Refuses synthetic fallback.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from model_prediction.domain import parse_utc
from model_prediction.features.cfb_features import CFBFeatureExtractor
from model_prediction.models.cfb_structural_v2 import CFBStructuralV2Model


def _log_loss(p: float, y: int, eps: float = 1e-15) -> float:
    p_c = max(eps, min(1.0 - eps, p))
    return -math.log(p_c if y == 1 else (1.0 - p_c))


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def run_cfb_v2_real_walkforward(data_root: Path | None = None) -> dict[str, Any]:
    root = data_root or Path(__file__).resolve().parent.parent
    ncaaf_hist_path = root / "data/historical/ncaaf_games_all.jsonl"

    if not ncaaf_hist_path.is_file():
        raise RuntimeError(
            f"Real NCAAF qualification dataset unavailable at {ncaaf_hist_path}; refusing synthetic fallback."
        )

    # Compute SHA-256 dataset hash
    dataset_bytes = ncaaf_hist_path.read_bytes()
    dataset_hash = hashlib.sha256(dataset_bytes).hexdigest()

    games: list[dict[str, Any]] = []
    for line in dataset_bytes.decode("utf-8").splitlines():
        if line.strip():
            games.append(json.loads(line))

    # Sort strictly by event start timestamp for chronological walk-forward
    games.sort(key=lambda g: str(g.get("event_start_utc", "")))

    model_v2 = CFBStructuralV2Model()
    extractor = CFBFeatureExtractor()

    # Expanding window walk-forward: Warmup on 2019-2022, Evaluate on 2023-2024
    eval_games = [g for g in games if int(g.get("season_year", 0)) >= 2023]
    if len(eval_games) < 100:
        eval_games = games[len(games) // 2 :]

    home_errors = []
    away_errors = []
    margin_errors = []
    total_errors = []

    v2_ml_loglosses = []
    v2_ml_briers = []
    v1_ml_loglosses = []
    v1_ml_briers = []

    spread_briers = []
    total_briers = []

    dates_seen = set()

    for idx, g in enumerate(eval_games):
        g_start = str(g.get("event_start_utc", ""))
        g_dt = parse_utc(g_start)
        dates_seen.add(g_dt.date().isoformat())

        # History includes only games strictly before this game
        history_before = [past for past in games if parse_utc(past.get("event_start_utc")) < g_dt]

        h_score = float(g.get("home_score", 0))
        a_score = float(g.get("away_score", 0))
        actual_margin = h_score - a_score
        actual_total = h_score + a_score
        spread_line = float(g.get("spread_line", -3.5)) if g.get("spread_line") is not None else -3.5
        total_line = float(g.get("total_line", 52.5)) if g.get("total_line") is not None else 52.5
        wind = float(g.get("wind_mph", 5.0))
        temp = float(g.get("temperature_f", 65.0))

        feat = extractor.extract_features(
            history=history_before,
            away_team=str(g.get("away_team", "")),
            home_team=str(g.get("home_team", "")),
            event_id=str(g.get("event_id", "")),
            game_start_utc=g_start,
            season_year=int(g.get("season_year", 2023)),
            week=int(g.get("week", 1)),
            wind_mph=wind,
            temperature_f=temp,
            is_neutral_site=bool(g.get("is_neutral_site", False)),
        )

        fc_v2 = model_v2.forecast_game(feat, spread_home_line=spread_line, total_line=total_line)

        # Points & margin errors
        home_errors.append(fc_v2.home_expected_points - h_score)
        away_errors.append(fc_v2.away_expected_points - a_score)
        margin_errors.append(abs(fc_v2.projected_margin_home - actual_margin))
        total_errors.append(abs(fc_v2.projected_total - actual_total))

        # Outcomes
        y_ml = 1 if h_score > a_score else 0

        # Incumbent v1 baseline: raw Elo logistic probability
        p_v1 = feat.elo_home_win_prob
        v1_ml_loglosses.append(_log_loss(p_v1, y_ml))
        v1_ml_briers.append(_brier(p_v1, y_ml))

        # Challenger v2 metrics
        v2_ml_loglosses.append(_log_loss(fc_v2.prob_home_win, y_ml))
        v2_ml_briers.append(_brier(fc_v2.prob_home_win, y_ml))

        # Spread & Totals
        y_spread = 1 if actual_margin > -spread_line else 0
        spread_briers.append(_brier(fc_v2.prob_home_cover, y_spread))

        y_total = 1 if actual_total > total_line else 0
        total_briers.append(_brier(fc_v2.prob_over, y_total))

    # Bootstrap paired difference
    rng = np.random.default_rng(42)
    deltas = np.array(v2_ml_loglosses) - np.array(v1_ml_loglosses)
    boot_means = [np.mean(rng.choice(deltas, size=len(deltas), replace=True)) for _ in range(1000)]
    p_v2_beats_v1 = float(np.mean(np.array(boot_means) < 0.0))

    delta_ll = float(np.mean(v2_ml_loglosses) - np.mean(v1_ml_loglosses))
    is_qualified = delta_ll < 0.0 and p_v2_beats_v1 >= 0.90
    verdict = "VALIDATED_OFFLINE" if is_qualified else "MECHANICS_VALIDATED"

    results = {
        "model_id": "cfb-structural-v2",
        "dataset_source": "espn_historical_scoreboard",
        "dataset_hash": dataset_hash,
        "protocol_hash": "cfb_walkforward_expanding_v1",
        "chronological_walk_forward": True,
        "n_evaluated": len(eval_games),
        "n_dates": len(dates_seen),
        "score_accuracy": {
            "home_mae": round(float(np.mean(np.abs(home_errors))), 3),
            "home_bias": round(float(np.mean(home_errors)), 3),
            "away_mae": round(float(np.mean(np.abs(away_errors))), 3),
            "away_bias": round(float(np.mean(away_errors)), 3),
            "margin_mae": round(float(np.mean(margin_errors)), 3),
            "total_mae": round(float(np.mean(total_errors)), 3),
        },
        "paired_comparison": {
            "incumbent_v1_logloss": round(float(np.mean(v1_ml_loglosses)), 4),
            "incumbent_v1_brier": round(float(np.mean(v1_ml_briers)), 4),
            "challenger_v2_logloss": round(float(np.mean(v2_ml_loglosses)), 4),
            "challenger_v2_brier": round(float(np.mean(v2_ml_briers)), 4),
            "delta_logloss": round(delta_ll, 4),
            "delta_brier": round(float(np.mean(v2_ml_briers) - np.mean(v1_ml_briers)), 4),
            "p_paired_bootstrap_beats_incumbent": round(p_v2_beats_v1, 4),
        },
        "spread_metrics": {
            "brier_score": round(float(np.mean(spread_briers)), 4),
        },
        "totals_metrics": {
            "brier_score": round(float(np.mean(total_briers)), 4),
        },
        "verdict": verdict,
        "recommendation": (
            f"cfb-structural-v2 evaluated on {len(eval_games)} real historical games across {len(dates_seen)} dates. "
            + (
                "Passes offline paired gate; eligible for freeze."
                if is_qualified
                else "Mechanics validated on real cohort, but uncalibrated score distributions underperform incumbent (Delta LL: "
                + f"{round(delta_ll, 4)}). Continue parameter fitting before freeze."
            )
        ),
    }

    out_file = root / "outputs/research/cfb_structural_v2_offline_evaluation.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def main() -> int:
    res = run_cfb_v2_real_walkforward()
    print("# NCAAF Structural v2 Real Chronological Offline Evaluation Report\n")
    print(f"- **Dataset Source**: {res['dataset_source']} (SHA-256: `{res['dataset_hash'][:16]}...`)")
    print(f"- **Evaluated Games**: {res['n_evaluated']} across {res['n_dates']} distinct dates")
    print(
        f"- **Score Accuracy**: Margin MAE: {res['score_accuracy']['margin_mae']}, Total MAE: {res['score_accuracy']['total_mae']}"
    )
    print("- **Paired Comparison vs v1 Incumbent**:")
    print(
        f"  - Incumbent v1 LogLoss: `{res['paired_comparison']['incumbent_v1_logloss']}`, Brier: `{res['paired_comparison']['incumbent_v1_brier']}`"
    )
    print(
        f"  - Challenger v2 LogLoss: `{res['paired_comparison']['challenger_v2_logloss']}`, Brier: `{res['paired_comparison']['challenger_v2_brier']}`"
    )
    print(
        f"  - Delta LogLoss: `{res['paired_comparison']['delta_logloss']}` (Bootstrap P: `{res['paired_comparison']['p_paired_bootstrap_beats_incumbent']}`)"
    )
    print(f"- **Verdict**: **{res['verdict']}**")
    print(f"- **Recommendation**: {res['recommendation']}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
