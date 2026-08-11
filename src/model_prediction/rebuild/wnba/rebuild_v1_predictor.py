"""Loads the `wnba-elo-trend-lr-rebuild-v1` challenger + calibrator
artifacts and turns a `WalkForwardRow` (real Elo/trend features from
`elo_trend.py`) into a real prediction.

Evidence-only by design: this module is **not** wired into
`sport_adapter.py`'s `rebuild-shadow --sport wnba` adapter registry.
`_BasicEloAdapter` stays the sole, unmodified, primary WNBA rebuild
adapter. See `docs/model_audit/models/WNBA_ELO_TREND_LR_REBUILD_V1.md`'s
"Serving integration" section for why: this challenger's validation is
real but capture-time-only descriptive backtesting (not genuine
prospective evidence), and no live current-season WNBA schedule/box data
was backfilled in this pass, so there is no real "today's slate" this
module could honestly serve without a separate live-collection step. This
module exists to prove the artifact loads and produces a real prediction
for a real historical game (task requirement), not to replace live
serving.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from model_prediction.rebuild.calibration import Calibrator, IdentityCalibrator, load_calibrator

from .elo_trend import WalkForwardRow


def load_model_artifact(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def load_calibrator_artifact(path: str | Path) -> Calibrator:
    payload = json.loads(Path(path).read_text())
    method = payload["method"]
    if method == "identity":
        return IdentityCalibrator()
    return load_calibrator(method, payload["parameters"])


_FEATURE_VALUES = {
    "elo_probability": lambda row: row.elo_probability,
    "trend_gap": lambda row: row.trend_gap,
    "defensive_trend_gap": lambda row: row.defensive_trend_gap,
}


def raw_probability(artifact: dict[str, Any], row: WalkForwardRow) -> float:
    """Real logistic-regression forward pass from the artifact's own
    persisted coefficients/feature_names -- no incumbent code involved."""
    market = artifact["market_models"]["moneyline"]
    feature_names: list[str] = market["feature_names"]
    coefficients: list[float] = market["coefficients"]
    intercept: float = market["intercept"]
    z = intercept + sum(
        coef * _FEATURE_VALUES[name](row) for coef, name in zip(coefficients, feature_names, strict=True)
    )
    return 1.0 / (1.0 + math.exp(-z))


def predict_row(artifact: dict[str, Any], calibrator: Calibrator, row: WalkForwardRow) -> dict[str, Any]:
    raw = raw_probability(artifact, row)
    calibrated = calibrator.transform(raw)
    return {
        "event_id": row.event_id,
        "event_start_utc": row.event_start_utc,
        "home_team_id": row.home_team_id,
        "away_team_id": row.away_team_id,
        "home_win_probability_raw": raw,
        "home_win_probability_calibrated": calibrated,
        "model_version": artifact["model_version"],
        "calibration_method": calibrator.method,
    }
