"""NFL v4 calibration challenger (Step 8 item 2) -- same temperature treatment as WNBA.

Backlog directive (docs/RESEARCH_BACKLOG.md P1): "incumbent OOF probs: Identity /
Platt / Temperature / Isotonic. Calibration first; QB/EPA/CPOE/OL/injuries/
weather only after." This tests the frozen nfl-elo-trend-lr-v4 artifact's OWN
served probabilities (no refit) on its own recorded validation/holdout split --
exactly the check that found the WNBA v4 underconfidence (slope 1.38).

NFL v4's qualification block already shows slope 1.23 / ECE 0.10 on n=87 called
predictions -- weaker miscalibration signal than WNBA's, and a smaller sample.
Reported honestly either way; only promoted to a frozen candidate if it clears
the same -0.002 Brier-delta bar used for WNBA.
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.calibration import TemperatureCalibrator
from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.models.learned_market import LearnedMarketArtifact
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from model_prediction.validation import build_walk_forward_rows, chronological_split
from scripts.mlb_v9_calibration_xgb import _safe_metrics

ARTIFACT_PATH = PROJECT_ROOT / "config/models/nfl-elo-trend-lr-v4.json"


class _Row:
    __slots__ = ("date", "outcome")

    def __init__(self, date: str, outcome: int) -> None:
        self.date = date
        self.outcome = outcome


def _bootstrap_p_better(identity_probs, calibrated_probs, rows, seed=20260818):
    bootstrap = _cluster_bootstrap_brier_delta(identity_probs, calibrated_probs, rows, seed=seed)
    by_date: dict[str, list[float]] = defaultdict(list)
    for ident, cal, row in zip(identity_probs, calibrated_probs, rows, strict=True):
        by_date[row.date].append((cal - row.outcome) ** 2 - (ident - row.outcome) ** 2)
    rng = random.Random(seed)
    dates_sorted = sorted(by_date)
    better = 0
    for _ in range(2000):
        sampled = [rng.choice(dates_sorted) for _ in dates_sorted]
        vals = [v for day in sampled for v in by_date[day]]
        if mean(vals) < 0:
            better += 1
    return bootstrap, round(better / 2000, 4)


def main() -> int:
    raw = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    training = raw["training"]
    train_end = training["coefficient_fit"]["end"]
    val_end = training["threshold_selection"]["end"]
    hold_end = training["locked_holdout"]["end"]
    feature_names = tuple(raw["market_models"]["moneyline"]["feature_names"])

    artifact = LearnedMarketArtifact(raw)

    rows_end = (date.fromisoformat(hold_end) + timedelta(days=1)).isoformat()
    store = FeatureStore(PROJECT_ROOT / "data")
    all_rows = build_walk_forward_rows(store, "nfl", end_date=rows_end)
    all_rows = [
        r for r in all_rows if all(float(getattr(r, f)) == float(getattr(r, f)) for f in feature_names)
    ]
    train, validation, holdout, _ = chronological_split(
        all_rows, train_end_date=train_end, validation_end_date=val_end
    )

    def _served_probs(rows) -> list[float]:
        return [
            artifact.probability("moneyline", {f: float(getattr(r, f)) for f in feature_names}) for r in rows
        ]

    val_probs = _served_probs(validation)
    val_outcomes = [r.outcome for r in validation]
    hold_probs = _served_probs(holdout)
    hold_outcomes = [r.outcome for r in holdout]
    holdout_rows = [_Row(r.date, r.outcome) for r in holdout]

    calibrator = TemperatureCalibrator.fit(
        val_probs,
        val_outcomes,
        base_model_version="nfl-elo-trend-lr-v4",
        version="nfl-v4-temperature-rolling-v1",
    )
    fitted_temperature = getattr(calibrator, "temperature", 1.0)
    calibrated_hold_probs = [calibrator.transform(p) for p in hold_probs]

    identity_metrics = _safe_metrics(hold_probs, hold_outcomes)
    calibrated_metrics = _safe_metrics(calibrated_hold_probs, hold_outcomes)
    bootstrap, p_better = _bootstrap_p_better(hold_probs, calibrated_hold_probs, holdout_rows)

    report = {
        "model_id": "nfl-elo-trend-lr-v4",
        "splits": {"train": len(train), "validation": len(validation), "holdout": len(holdout)},
        "fitted_temperature": fitted_temperature,
        "fitted_on": "identity fallback (validation n<100)"
        if fitted_temperature == 1.0 and len(validation) < 100
        else f"validation n={len(validation)}",
        "identity": identity_metrics,
        "temperature": calibrated_metrics,
        "brier_delta": round(calibrated_metrics["brier"] - identity_metrics["brier"], 6),
        "log_loss_delta": round(calibrated_metrics["log_loss"] - identity_metrics["log_loss"], 6),
        "ece_delta": round(calibrated_metrics["ece"] - identity_metrics["ece"], 6),
        "cluster_bootstrap": bootstrap,
        "bootstrap_p_better": p_better,
        "verdict": (
            "promote_candidate"
            if round(calibrated_metrics["brier"] - identity_metrics["brier"], 6) <= -0.002
            else "reject_noise"
        ),
    }
    out_dir = PROJECT_ROOT / "outputs/research/nfl_calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "nfl_v4_temperature_test.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
