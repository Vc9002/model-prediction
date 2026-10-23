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
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .calibration import (
    BetaCalibrator,
    CalibrationMetadata,
    IdentityCalibrator,
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

ESPORTS_CALIBRATION_TITLES = frozenset({"cs2", "dota2", "lol", "valorant", "rainbow_six"})


def _esports_calibration_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """One latest settled decision per contract/model, ordered by decision time.

    A workbook's insertion order is not a temporal split. In particular, the
    retired research ledgers contain repeated forecasts and backfilled rows.
    Non-esports records retain the existing extraction behavior.
    """
    other: list[dict[str, Any]] = []
    latest: dict[tuple[str, ...], tuple[datetime, dict[str, Any]]] = {}
    for row in rows:
        sport = str(row.get("sport") or row.get("league") or "").strip().lower()
        if sport not in ESPORTS_CALIBRATION_TITLES:
            other.append(row)
            continue
        if row.get("status") != "settled":
            continue
        event = str(row.get("canonical_event_id") or row.get("event_id") or "").strip()
        model = str(row.get("model_version") or row.get("model_id") or "").strip()
        selection = str(row.get("selection") or "").strip().lower()
        try:
            observed = datetime.fromisoformat(str(row.get("created_at_utc") or ""))
            if observed.tzinfo is None:
                continue
            observed = observed.astimezone(UTC)
        except (TypeError, ValueError):
            continue
        if not event or not model or not selection:
            continue
        raw_line = row.get("line")
        try:
            line = "" if raw_line is None or raw_line == "" else str(float(raw_line))
            if line and not math.isfinite(float(line)):
                continue
            if line and float(line) == 0:
                line = "0.0"
        except (TypeError, ValueError):
            continue
        market = str(row.get("market_type") or row.get("market") or "moneyline").strip().lower()
        key = (sport, event, market, selection, line, model)
        if key not in latest or observed > latest[key][0]:
            latest[key] = (observed, row)
    return other + [row for _, row in sorted(latest.values(), key=lambda pair: pair[0])]


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
    scorecard: dict[str, Any] = field(default_factory=dict)
    reliability_buckets: list[dict[str, Any]] = field(default_factory=list)
    calibrated_brier_score: float = 0.0
    calibrated_log_loss: float = 0.0
    calibrated_expected_calibration_error: float = 0.0
    calibrated_slope: float = 1.0
    calibrated_intercept: float = 0.0
    calibrated_drift_score: float = 0.0
    calibrated_drift_status: str = "HEALTHY"
    recalibrated: bool = False


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
        # Retain authority even when all Flat rows fail eligibility. Otherwise
        # an empty canonical sample silently falls back to retired research.
        if tier_name == "flat":
            for row in rows:
                sport = str(row.get("sport") or row.get("league") or "").strip().lower()
                model = str(row.get("model_version") or row.get("model_id") or "").strip()
                market = str(row.get("market_type") or row.get("market") or "moneyline").strip().lower()
                if sport not in ESPORTS_CALIBRATION_TITLES or not model or model == "unknown":
                    continue
                if sport_filter and sport_filter.lower() != sport:
                    continue
                if market_filter and market_filter.lower() != market:
                    continue
                results.setdefault((sport, market, model, tier_name), ([], []))
        for row in _esports_calibration_rows(rows):
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
            raw_prob = next(
                (
                    row.get(name)
                    for name in (
                        "model_probability",
                        "probability",
                        "calibrated_probability",
                        "serving_probability",
                    )
                    if row.get(name) is not None and row.get(name) != ""
                ),
                None,
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

            model_version = str(row.get("model_version") or row.get("model_id") or "unknown").strip()
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

    brier = float(str(metrics["brier_score"]))
    log_loss = float(str(metrics["log_loss"]))
    ece = float(str(metrics["expected_calibration_error"]))
    slope = float(str(metrics.get("calibration_slope") or 1.0))
    intercept = float(str(metrics.get("calibration_intercept") or 0.0))

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
            "thresholds": [round(float(t), 6) for t in champ_calibrator.thresholds],
            "values": [round(float(v), 6) for v in champ_calibrator.values],
        }

    # Evaluate post-calibration metrics under the winning calibrator
    champ_scorecard = tournament.get("scorecard", {}).get(champ_method, {})
    cal_slope = float(champ_scorecard.get("calibration_slope", 1.0))
    cal_intercept = float(champ_scorecard.get("calibration_intercept", 0.0))
    cal_ece = float(champ_scorecard.get("oof_ece", 0.0))
    cal_brier = float(champ_scorecard.get("oof_brier_score", brier))
    cal_log_loss = float(champ_scorecard.get("oof_log_loss", log_loss))

    cal_slope_dev = abs(cal_slope - 1.0)
    cal_intercept_dev = abs(cal_intercept)
    cal_drift_score = round(cal_slope_dev * 0.4 + cal_intercept_dev * 0.3 + cal_ece * 3.0, 4)

    if cal_slope_dev > 0.25 or cal_intercept_dev > 0.25 or cal_ece > 0.06:
        cal_drift_status = "SEVERE_DRIFT"
    elif cal_slope_dev > 0.12 or cal_intercept_dev > 0.12 or cal_ece > 0.035:
        cal_drift_status = "MODERATE_DRIFT"
    else:
        cal_drift_status = "HEALTHY"

    recalibrated = (
        champ_method != "identity"
        and drift_status in ("SEVERE_DRIFT", "MODERATE_DRIFT")
        and (
            cal_drift_status == "HEALTHY"
            or (drift_status == "SEVERE_DRIFT" and cal_drift_status == "MODERATE_DRIFT")
            or cal_drift_score < drift_score * 0.75
        )
    )

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
        reliability_buckets=cast(list[dict[str, Any]], metrics.get("reliability_buckets") or []),
        calibrated_brier_score=round(cal_brier, 6),
        calibrated_log_loss=round(cal_log_loss, 6),
        calibrated_expected_calibration_error=round(cal_ece, 6),
        calibrated_slope=round(cal_slope, 4),
        calibrated_intercept=round(cal_intercept, 4),
        calibrated_drift_score=cal_drift_score,
        calibrated_drift_status=cal_drift_status,
        recalibrated=recalibrated,
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
    max_raw_drift = max((r.drift_score for r in records), default=0.0)
    raw_severe_drift_models = list(
        dict.fromkeys(
            f"{r.sport}:{r.market}:{r.model_version}" for r in records if r.drift_status == "SEVERE_DRIFT"
        )
    )
    raw_moderate_drift_models = list(
        dict.fromkeys(
            f"{r.sport}:{r.market}:{r.model_version}" for r in records if r.drift_status == "MODERATE_DRIFT"
        )
    )

    recalibrated_models = list(
        dict.fromkeys(f"{r.sport}:{r.market}:{r.model_version}" for r in records if r.recalibrated)
    )
    unresolved_severe = list(
        dict.fromkeys(
            f"{r.sport}:{r.market}:{r.model_version}"
            for r in records
            if r.calibrated_drift_status == "SEVERE_DRIFT"
        )
    )
    unresolved_moderate = list(
        dict.fromkeys(
            f"{r.sport}:{r.market}:{r.model_version}"
            for r in records
            if r.calibrated_drift_status == "MODERATE_DRIFT"
        )
    )
    max_calibrated_drift = max((r.calibrated_drift_score for r in records), default=0.0)

    # When update_artifacts is True (or automated cycle runs), models are re-calibrated.
    # The effective health status reflects post-recalibration accuracy.
    if update_artifacts:
        if not unresolved_severe:
            system_calibration_health = "MODERATE_DRIFT" if unresolved_moderate else "HEALTHY"
        else:
            system_calibration_health = "SEVERE_DRIFT"
        active_severe_models = unresolved_severe
        active_moderate_models = unresolved_moderate
        effective_drift = round(max_calibrated_drift, 4)
    else:
        system_calibration_health = (
            "SEVERE_DRIFT"
            if raw_severe_drift_models
            else ("MODERATE_DRIFT" if raw_moderate_drift_models else "HEALTHY")
        )
        active_severe_models = raw_severe_drift_models
        active_moderate_models = raw_moderate_drift_models
        effective_drift = round(max_raw_drift, 4)

    now_iso = datetime.now(UTC).isoformat()

    report = {
        "generated_at_utc": now_iso,
        "system_status": system_calibration_health,
        "raw_system_status": (
            "SEVERE_DRIFT"
            if raw_severe_drift_models
            else ("MODERATE_DRIFT" if raw_moderate_drift_models else "HEALTHY")
        ),
        "total_evaluated_models": len(records),
        "max_calibration_drift": effective_drift,
        "raw_max_calibration_drift": round(max_raw_drift, 4),
        "severe_drift_count": len(active_severe_models),
        "moderate_drift_count": len(active_moderate_models),
        "severe_drift_models": active_severe_models,
        "moderate_drift_models": active_moderate_models,
        "raw_severe_drift_models": raw_severe_drift_models,
        "raw_moderate_drift_models": raw_moderate_drift_models,
        "recalibrated_count": len(recalibrated_models),
        "recalibrated_models": recalibrated_models,
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
        "raw_status": report["raw_system_status"],
        "last_checked_utc": now_iso,
        "calibration_drift": effective_drift,
        "severe_drift_models": active_severe_models,
        "moderate_drift_models": active_moderate_models,
        "recalibrated_models": recalibrated_models,
        "recalibrated_count": len(recalibrated_models),
        "models_count": len(records),
    }
    health_file = cal_data_dir / "calibration_health.json"
    health_file.write_text(json.dumps(health_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if update_artifacts:
        models_dir = cal_data_dir / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        # The same settled picks sit in several ledger tiers (main/flat/research),
        # so a model has several records per market. One artifact per
        # (model_version, market): canonical Flat wins for esports, otherwise
        # the largest sample wins. Identity also replaces stale fitted files.
        best: dict[tuple[str, str], ModelCalibrationRecord] = {}
        canonical_flat = {
            (model, market): (sport, len(probs))
            for (sport, market, model, tier), (probs, _) in grouped_data.items()
            if sport.lower() in ESPORTS_CALIBRATION_TITLES and tier == "flat"
        }
        for r in records:
            key = (r.model_version, r.market)
            if key in canonical_flat and r.tier != "flat":
                continue
            current = best.get(key)
            # Flat is the complete canonical esports cohort after the ledger
            # merge. A larger retired research tier may contain stale history;
            # it must not displace the current cohort merely through volume.
            prefer_flat = r.sport.lower() in ESPORTS_CALIBRATION_TITLES
            candidate_rank = (int(prefer_flat and r.tier == "flat"), r.sample_size)
            current_rank = (
                int(prefer_flat and current is not None and current.tier == "flat"),
                current.sample_size if current is not None else -1,
            )
            if current is None or candidate_rank > current_rank:
                best[key] = r
        for r in best.values():
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
            _calibrator_artifact_path(cal_data_dir, r.model_version, r.market).write_text(
                json.dumps(artifact_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        for (model, market), (sport, count) in canonical_flat.items():
            if (model, market) in best:
                continue
            # Revoke any stale fitted file when the authoritative cohort cannot
            # support calibration; skipping publication would leave it active.
            payload = {
                "sport": sport,
                "market": market,
                "model_version": model,
                "champion_method": "identity",
                "parameters": {},
                "sample_size": count,
                "baseline_brier": None,
                "calibrated_oof_brier": None,
                "calibrated_at_utc": now_iso,
                "reason": "canonical_flat_insufficient_evidence",
            }
            _calibrator_artifact_path(cal_data_dir, model, market).write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

    return report


def _calibrator_artifact_path(cal_data_dir: Path, model_version: str, market: str) -> Path:
    return cal_data_dir / "models" / f"{model_version}.{market}_calibrator.json"


def load_model_calibrator(
    model_version: str,
    data_root: str | Path | None = None,
    market: str | None = None,
) -> Any | None:
    """Load the automated re-fit calibrator artifact for a given model version.

    Returns the instantiated Calibrator (TemperatureCalibrator, TrainablePlattCalibrator,
    BetaCalibrator, IsotonicCalibrator, or IdentityCalibrator) or None if absent.
    """
    data_path = Path(data_root) if data_root else PROJECT_ROOT / "data"
    artifact_path = (
        _calibrator_artifact_path(data_path / "calibration", model_version, market)
        if market
        else data_path / "calibration" / "models" / f"{model_version}_calibrator.json"
    )
    if not artifact_path.exists():
        return None

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        method = str(payload.get("champion_method") or "identity").lower()
        params = payload.get("parameters") or {}
        metadata = CalibrationMetadata(
            calibration_method=method,
            calibration_version=f"{method}-recalibrated-v1",
            base_model_version=model_version,
            training_window_start=None,
            training_window_end=None,
            sample_size=int(payload.get("sample_size") or 0),
            artifact_hash="",
        )

        if method == "temperature":
            temp = float(params.get("temperature") or 1.0)
            return TemperatureCalibrator(temp, metadata)
        elif method == "platt":
            intercept = float(params.get("intercept") or 0.0)
            slope = float(params.get("slope") or 1.0)
            return TrainablePlattCalibrator(intercept, slope, metadata)
        elif method == "beta":
            a = float(params.get("a") or 1.0)
            b = float(params.get("b") or 1.0)
            c = float(params.get("c") or 0.0)
            return BetaCalibrator(a, b, c, metadata)
        elif method == "isotonic":
            thresholds = [float(t) for t in params.get("thresholds", [])]
            values = [float(v) for v in params.get("values", [])]
            if thresholds and values:
                return IsotonicCalibrator(thresholds, values, metadata)
            return IdentityCalibrator(model_version, "isotonic-fallback-identity")
        else:
            return IdentityCalibrator(model_version, "identity")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning("Failed to load calibrator for %s: %s", model_version, exc)
        return None


def calibrate_model_probability(
    model_version: str,
    probability: float,
    data_root: str | Path | None = None,
) -> float:
    """Apply the model's fitted champion calibrator to a raw probability."""
    cal = load_model_calibrator(model_version, data_root=data_root)
    if cal is None:
        return probability
    return float(cal.transform(probability))


# A fitted calibrator is applied to live probabilities only when it is trustworthy:
# enough settled picks, and an out-of-fold Brier that actually beats the raw model.
# The tournament's champion can lose to the raw model (wnba-elo-trend-lr-v4,
# 2026-10-21: calibrated OOF Brier 0.1962 vs raw 0.1882), so the artifact's own
# numbers are re-checked here rather than trusted.
MIN_SERVING_SAMPLE = 100
MIN_SERVING_BRIER_IMPROVEMENT = 0.001
SERVING_PROBABILITY_BOUNDS = (0.01, 0.99)


@dataclass(frozen=True)
class ServingCalibration:
    probability: float
    raw_probability: float
    applied: bool
    method: str
    reason: str


def calibrate_for_serving(
    model_version: str,
    market: str,
    probability: float,
    data_root: str | Path | None = None,
) -> ServingCalibration:
    """Map a raw pick probability through the model's calibrator when it passes the guard.

    Never raises: any problem leaves the raw probability untouched and says why.
    """

    def raw(reason: str) -> ServingCalibration:
        return ServingCalibration(probability, probability, False, "identity", reason)

    data_path = Path(data_root) if data_root else PROJECT_ROOT / "data"
    path = _calibrator_artifact_path(data_path / "calibration", model_version, market)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return raw("no_artifact")
    except (OSError, ValueError):
        return raw("unreadable_artifact")
    method = str(payload.get("champion_method") or "identity").lower()
    if method == "identity":
        return raw("identity_champion")
    try:
        if str(payload.get("market")) != market or str(payload.get("model_version")) != model_version:
            return raw("artifact_mismatch")
        if int(payload.get("sample_size") or 0) < MIN_SERVING_SAMPLE:
            return raw("sample_too_small")
        baseline, oof = payload.get("baseline_brier"), payload.get("calibrated_oof_brier")
        if baseline is None or oof is None or float(baseline) - float(oof) < MIN_SERVING_BRIER_IMPROVEMENT:
            return raw("no_oof_improvement")
    except (TypeError, ValueError):
        return raw("unreadable_artifact")
    calibrator = load_model_calibrator(model_version, data_root=data_path, market=market)
    if calibrator is None:
        return raw("unreadable_artifact")
    try:
        mapped = float(calibrator.transform(probability))
    except (TypeError, ValueError, ArithmeticError):
        return raw("transform_failed")
    if not math.isfinite(mapped):
        return raw("transform_failed")
    low, high = SERVING_PROBABILITY_BOUNDS
    mapped = min(high, max(low, mapped))
    return ServingCalibration(mapped, probability, True, f"{method}-recalibrated-v1", "applied")
