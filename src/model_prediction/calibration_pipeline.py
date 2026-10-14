"""Automated Model Calibration Pipeline & Drift Monitor.

Evaluates calibration across all settled predictions in Main, Flat, and
Research ledgers:
1. Computes Brier score, Log Loss, Expected Calibration Error (ECE),
   calibration slope and intercept, and Murphy reliability decomposition.
2. Detects calibration drift (overconfidence/underconfidence, base-rate shift).
3. Executes the 5-method Out-Of-Fold Calibration Tournament (Identity,
   Temperature, Platt, Beta, Isotonic) to identify winning calibrators.
4. Generates structured health state and calibration reports.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .calibration import (
    BetaCalibrator,
    IsotonicCalibrator,
    TemperatureCalibrator,
    TrainablePlattCalibrator,
    calibration_metrics,
    run_calibration_tournament,
)
from .config import PROJECT_ROOT
from .main_ledgers import MultiSportPickLedger
from .research_ledgers import existing_research_ledgers

logger = logging.getLogger("model_prediction.calibration")

CALIBRATION_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "calibration"
CALIBRATION_DATA_DIR = PROJECT_ROOT / "data" / "calibration"


@dataclass
class ModelCalibrationRecord:
    sport: str
    market: str
    model_version: str
    tier: str
    sample_size: int
    brier_score: float
    log_loss: float
    expected_calibration_error: float
    calibration_slope: float
    calibration_intercept: float
    drift_status: str  # HEALTHY, MODERATE_DRIFT, SEVERE_DRIFT
    drift_score: float
    champion_calibrator_method: str
    champion_calibrator_params: dict[str, Any]
    scorecard: dict[str, Any]
    reliability_buckets: list[dict[str, Any]] = field(default_factory=list)


def extract_settled_model_predictions(
    data_root: str | Path | None = None,
    tier_filter: str | None = None,
    sport_filter: str | None = None,
    market_filter: str | None = None,
) -> dict[tuple[str, str, str, str], tuple[list[float], list[int]]]:
    """Extract (probabilities, binary_outcomes) grouped by (sport, market, model_version, tier)."""
    root = Path(data_root) if data_root else PROJECT_ROOT / "data"
    results: dict[tuple[str, str, str, str], tuple[list[float], list[int]]] = {}

    def _ingest_rows(rows: Sequence[dict[str, Any]], tier_name: str) -> None:
        if tier_filter and tier_filter != tier_name:
            return
        for row in rows:
            if row.get("status") != "settled":
                continue

            # Parse binary outcome: win -> 1, loss -> 0
            res = str(row.get("result") or row.get("outcome") or "").strip().lower()
            if res in ("win", "1", "1.0", "hit"):
                outcome = 1
            elif res in ("loss", "0", "0.0", "miss"):
                outcome = 0
            else:
                continue

            # Parse model probability
            raw_prob = (
                row.get("model_probability")
                or row.get("probability")
                or row.get("calibrated_probability")
                or row.get("serving_probability")
            )
            if raw_prob is None or raw_prob == "":
                continue
            try:
                prob = float(raw_prob)
            except (ValueError, TypeError):
                continue
            if not 0.0 <= prob <= 1.0:
                continue

            sport = str(row.get("sport") or row.get("league") or "unknown").strip().lower()
            if sport_filter and sport_filter.lower() != sport:
                continue

            market = str(row.get("market_type") or row.get("market") or "moneyline").strip().lower()
            if market_filter and market_filter.lower() != market:
                continue

            model_version = str(row.get("model_version") or "unknown").strip()
            if not model_version or model_version == "unknown":
                continue

            key = (sport, market, model_version, tier_name)
            if key not in results:
                results[key] = ([], [])
            results[key][0].append(prob)
            results[key][1].append(outcome)

    # 1. Main Ledgers
    try:
        main_ml = MultiSportPickLedger(root, flat=False)
        for l in main_ml._ledgers.values():
            _ingest_rows(l.rows(), "main")
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Error reading main ledgers: %s", exc)

    # 2. Flat Ledgers
    try:
        flat_ml = MultiSportPickLedger(root, flat=True)
        for l in flat_ml._ledgers.values():
            _ingest_rows(l.rows(), "flat")
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Error reading flat ledgers: %s", exc)

    # 3. Research Ledgers
    try:
        for r_ledger in existing_research_ledgers(root):
            tier = "gated" if "gated" in str(r_ledger.path).lower() else "research"
            _ingest_rows(r_ledger.rows(), tier)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Error reading research ledgers: %s", exc)

    return results


def evaluate_calibration_for_group(
    sport: str,
    market: str,
    model_version: str,
    tier: str,
    probabilities: list[float],
    outcomes: list[int],
    min_sample: int = 20,
) -> ModelCalibrationRecord | None:
    """Run calibration analysis and tournament for a single (sport, market, model, tier) group."""
    n = len(probabilities)
    if n < min_sample:
        return None

    metrics = calibration_metrics(probabilities, outcomes, minimum_sample=min_sample)
    if metrics.get("status") != "ok":
        return None

    brier = float(metrics["brier_score"])
    log_loss = float(metrics["log_loss"])
    ece = float(metrics["expected_calibration_error"])
    slope = float(metrics.get("calibration_slope") or 1.0)
    intercept = float(metrics.get("calibration_intercept") or 0.0)

    # Drift scoring: deviation from ideal slope (1.0) and intercept (0.0), plus ECE
    slope_dev = abs(slope - 1.0)
    intercept_dev = abs(intercept)
    drift_score = round(slope_dev * 0.4 + intercept_dev * 0.3 + ece * 3.0, 4)

    if slope_dev > 0.25 or intercept_dev > 0.25 or ece > 0.06:
        drift_status = "SEVERE_DRIFT"
    elif slope_dev > 0.12 or intercept_dev > 0.12 or ece > 0.035:
        drift_status = "MODERATE_DRIFT"
    else:
        drift_status = "HEALTHY"

    # Run tournament
    tournament = run_calibration_tournament(
        probabilities,
        outcomes,
        base_model_version=model_version,
        n_splits=5 if n >= 50 else 3,
        minimum_sample=min_sample,
    )

    champ_method = tournament.get("champion_method", "identity")
    champ_calibrator = tournament.get("champion_calibrator")
    champ_params: dict[str, Any] = {}

    if isinstance(champ_calibrator, TemperatureCalibrator):
        champ_params = {"temperature": round(champ_calibrator.temperature, 4)}
    elif isinstance(champ_calibrator, TrainablePlattCalibrator):
        champ_params = {
            "intercept": round(champ_calibrator.intercept, 4),
            "slope": round(champ_calibrator.slope, 4),
        }
    elif isinstance(champ_calibrator, BetaCalibrator):
        champ_params = {
            "a": round(champ_calibrator.a, 4),
            "b": round(champ_calibrator.b, 4),
            "c": round(champ_calibrator.c, 4),
        }
    elif isinstance(champ_calibrator, IsotonicCalibrator):
        champ_params = {
            "num_thresholds": len(champ_calibrator.thresholds),
            "min_val": round(min(champ_calibrator.values), 4) if champ_calibrator.values else 0.0,
            "max_val": round(max(champ_calibrator.values), 4) if champ_calibrator.values else 1.0,
        }

    return ModelCalibrationRecord(
        sport=sport,
        market=market,
        model_version=model_version,
        tier=tier,
        sample_size=n,
        brier_score=round(brier, 6),
        log_loss=round(log_loss, 6),
        expected_calibration_error=round(ece, 6),
        calibration_slope=round(slope, 4),
        calibration_intercept=round(intercept, 4),
        drift_status=drift_status,
        drift_score=drift_score,
        champion_calibrator_method=champ_method,
        champion_calibrator_params=champ_params,
        scorecard=tournament.get("scorecard", {}),
        reliability_buckets=metrics.get("reliability_buckets", []),
    )


def run_full_calibration_pipeline(
    data_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    sport_filter: str | None = None,
    market_filter: str | None = None,
    min_sample: int = 20,
    update_artifacts: bool = False,
) -> dict[str, Any]:
    """Run full calibration pipeline across all models, generating audit and health reports."""
    data_path = Path(data_root) if data_root else PROJECT_ROOT / "data"
    out_dir = Path(output_dir) if output_dir else CALIBRATION_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    cal_data_dir = data_path / "calibration"
    cal_data_dir.mkdir(parents=True, exist_ok=True)

    grouped_data = extract_settled_model_predictions(
        data_root=data_path,
        sport_filter=sport_filter,
        market_filter=market_filter,
    )

    records: list[ModelCalibrationRecord] = []
    for (sport, market, model_ver, tier), (probs, outs) in grouped_data.items():
        rec = evaluate_calibration_for_group(
            sport=sport,
            market=market,
            model_version=model_ver,
            tier=tier,
            probabilities=probs,
            outcomes=outs,
            min_sample=min_sample,
        )
        if rec is not None:
            records.append(rec)

    # Sort records by sample size descending
    records.sort(key=lambda r: r.sample_size, reverse=True)

    # Compute aggregate system metrics
    max_drift = max((r.drift_score for r in records), default=0.0)
    severe_drift_models = [
        f"{r.sport}:{r.market}:{r.model_version}" for r in records if r.drift_status == "SEVERE_DRIFT"
    ]
    moderate_drift_models = [
        f"{r.sport}:{r.market}:{r.model_version}" for r in records if r.drift_status == "MODERATE_DRIFT"
    ]

    system_calibration_health = "HEALTHY"
    if severe_drift_models:
        system_calibration_health = "SEVERE_DRIFT"
    elif moderate_drift_models:
        system_calibration_health = "MODERATE_DRIFT"

    now_iso = datetime.now(UTC).isoformat()

    report = {
        "generated_at_utc": now_iso,
        "system_status": system_calibration_health,
        "total_evaluated_models": len(records),
        "max_calibration_drift": round(max_drift, 4),
        "severe_drift_count": len(severe_drift_models),
        "moderate_drift_count": len(moderate_drift_models),
        "severe_drift_models": severe_drift_models,
        "moderate_drift_models": moderate_drift_models,
        "models": [asdict(r) for r in records],
    }

    # Save outputs
    latest_file = out_dir / "latest.json"
    latest_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    audit_file = out_dir / "calibration_audit.json"
    audit_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Save lightweight health file for runtime/dashboard
    health_payload = {
        "status": system_calibration_health,
        "last_checked_utc": now_iso,
        "calibration_drift": round(max_drift, 4),
        "severe_drift_models": severe_drift_models,
        "moderate_drift_models": moderate_drift_models,
        "models_count": len(records),
    }
    health_file = cal_data_dir / "calibration_health.json"
    health_file.write_text(json.dumps(health_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if update_artifacts:
        models_dir = cal_data_dir / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        for r in records:
            if r.champion_calibrator_method != "identity":
                artifact_path = models_dir / f"{r.model_version}_calibrator.json"
                artifact_payload = {
                    "sport": r.sport,
                    "market": r.market,
                    "model_version": r.model_version,
                    "champion_method": r.champion_calibrator_method,
                    "parameters": r.champion_calibrator_params,
                    "sample_size": r.sample_size,
                    "baseline_brier": r.brier_score,
                    "calibrated_oof_brier": r.scorecard.get(r.champion_calibrator_method, {}).get(
                        "oof_brier_score"
                    ),
                    "calibrated_at_utc": now_iso,
                }
                artifact_path.write_text(
                    json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )

    return report
