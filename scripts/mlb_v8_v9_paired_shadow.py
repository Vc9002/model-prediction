"""Automated Paired Prospective Shadow Harness (Roadmap Phase 21-22).

Evaluates the frozen v8 production champion against the v9 candidate
on untouched prospective games:
  - Generates and logs side-by-side forecasts before first pitch.
  - Computes paired delta scores: ΔLogLoss_i = Loss(v9_i) - Loss(v8_i).
  - Evaluates 2,000-resample date-clustered bootstrap significance.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

SHADOW_LOG_PATH = Path("data/point_in_time/mlb_v8_v9_shadow_logs.jsonl")


def _log_loss(p: float, y: int, eps: float = 1e-15) -> float:
    p_c = max(eps, min(1.0 - eps, p))
    return -math.log(p_c if y == 1 else (1.0 - p_c))


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def log_shadow_game(
    event_id: str,
    game_date: str,
    home_team: str,
    away_team: str,
    v8_prob: float,
    v9_prob_raw: float,
    v9_prob_calibrated: float,
    v9_features: dict[str, Any],
) -> dict[str, Any]:
    """Append one prospective shadow game observation before first pitch."""
    SHADOW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "event_id": event_id,
        "game_date": game_date,
        "home_team": home_team,
        "away_team": away_team,
        "v8_probability": round(v8_prob, 4),
        "v9_probability_raw": round(v9_prob_raw, 4),
        "v9_probability_calibrated": round(v9_prob_calibrated, 4),
        "v9_features": v9_features,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "status": "open",
        "home_win": None,
    }
    with SHADOW_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def evaluate_paired_shadow(logs: list[dict[str, Any]], n_bootstrap: int = 2000) -> dict[str, Any]:
    """Compute paired LogLoss and Brier deltas with date-clustered bootstrap."""
    settled = [
        r
        for r in logs
        if r.get("status") == "settled"
        and r.get("home_win") is not None
        and r.get("status") != "VOID_MOCK_SHADOW"
    ]
    if not settled:
        return {"status": "insufficient_data", "settled_games": 0}

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in settled:
        by_date[str(r["game_date"])].append(r)

    dates = list(by_date.keys())
    n_dates = len(dates)

    v8_losses = [_log_loss(r["v8_probability"], int(r["home_win"])) for r in settled]
    v9_losses = [_log_loss(r["v9_probability_calibrated"], int(r["home_win"])) for r in settled]
    v8_briers = [_brier(r["v8_probability"], int(r["home_win"])) for r in settled]
    v9_briers = [_brier(r["v9_probability_calibrated"], int(r["home_win"])) for r in settled]

    mean_v8_ll = float(np.mean(v8_losses))
    mean_v9_ll = float(np.mean(v9_losses))
    mean_v8_br = float(np.mean(v8_briers))
    mean_v9_br = float(np.mean(v9_briers))

    # Date-clustered bootstrap
    rng = np.random.default_rng(20260823)
    boot_delta_ll = []
    boot_delta_br = []

    for _ in range(n_bootstrap):
        sampled_dates = rng.choice(dates, size=n_dates, replace=True)
        sampled_rows = [r for d in sampled_dates for r in by_date[d]]
        if not sampled_rows:
            continue
        ll_8 = [_log_loss(r["v8_probability"], int(r["home_win"])) for r in sampled_rows]
        ll_9 = [_log_loss(r["v9_probability_calibrated"], int(r["home_win"])) for r in sampled_rows]
        br_8 = [_brier(r["v8_probability"], int(r["home_win"])) for r in sampled_rows]
        br_9 = [_brier(r["v9_probability_calibrated"], int(r["home_win"])) for r in sampled_rows]

        boot_delta_ll.append(float(np.mean(ll_9) - np.mean(ll_8)))
        boot_delta_br.append(float(np.mean(br_9) - np.mean(br_8)))

    p_ll_better = float(np.mean([d < 0 for d in boot_delta_ll])) if boot_delta_ll else 0.5
    p_br_better = float(np.mean([d < 0 for d in boot_delta_br])) if boot_delta_br else 0.5

    return {
        "status": "evaluated",
        "settled_games": len(settled),
        "unique_dates": n_dates,
        "v8_log_loss": round(mean_v8_ll, 4),
        "v9_log_loss": round(mean_v9_ll, 4),
        "delta_log_loss": round(mean_v9_ll - mean_v8_ll, 4),
        "p_log_loss_better": round(p_ll_better, 4),
        "v8_brier": round(mean_v8_br, 4),
        "v9_brier": round(mean_v9_br, 4),
        "delta_brier": round(mean_v9_br - mean_v8_br, 4),
        "p_brier_better": round(p_br_better, 4),
    }


def capture_today_slate() -> list[dict[str, Any]]:
    """Fetch live MLB scoreboard for today and record prospective shadow predictions.

    FAILS CLOSED: Candidate-1 is VOID_INVALID_FEATURE_PROVENANCE.
    Prospective shadow collection is locked until mlb-v9-candidate-2 is trained on real data.
    """
    candidate_artifact_path = Path("config/models/research/mlb-v9-candidate-1.json")
    if candidate_artifact_path.exists():
        candidate_data = json.loads(candidate_artifact_path.read_text(encoding="utf-8"))
        if candidate_data.get("status") == "VOID_INVALID_FEATURE_PROVENANCE":
            raise RuntimeError(
                "ABORT_SHADOW_CANDIDATE_VOID: mlb-v9-candidate-1 is VOID due to synthetic feature provenance. "
                "Prospective shadow logging is gated until mlb-v9-candidate-2 is frozen."
            )

    raise NotImplementedError("Candidate-2 shadow logging will be activated upon candidate-2 freeze.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Paired v8 vs v9 shadow evaluation")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate settled shadow rows")
    parser.add_argument(
        "--capture-today", action="store_true", help="Capture live prospective slate for today"
    )
    args = parser.parse_args()

    if args.capture_today:
        capture_today_slate()
    elif args.evaluate and SHADOW_LOG_PATH.exists():
        lines = [json.loads(l) for l in SHADOW_LOG_PATH.read_text().splitlines() if l.strip()]
        res = evaluate_paired_shadow(lines)
        print(json.dumps(res, indent=2))
    else:
        print("Shadow harness ready. Log path:", SHADOW_LOG_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
