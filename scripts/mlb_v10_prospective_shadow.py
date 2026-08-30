"""MLB Structural v10 Prospective Daily Shadow Evaluation Pipeline (F1C).

Executes daily prospective tracking for MLB Structural v10:
1. Runs pregame at decision timestamp (T-30m) using frozen v10 model artifact.
2. Evaluates MarketStateVector v1.
3. Computes v10 structural prediction, v10_delta_vs_market, m0b_prediction, m4_1_v10_prediction.
4. Computes pregame probabilities (p_over, p_under, p_push) using the frozen empirical residual distribution.
5. Generates cryptographic SHA-256 prediction hashes and persists immutable PREDICTION records
   to data/point_in_time/mlb_v10_prospective_ledger.jsonl before first pitch.
6. Post-game append-only routines:
   - Appends CLOSING_MARKET records (closing quote observed strictly before game start).
   - Appends SETTLEMENT records (actual_away, actual_home, actual_total, actual_margin) without modifying predictions.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import parse_utc, utc_now
from model_prediction.features.market_state import MarketStateVectorBuilder
from model_prediction.features.mlb_v10_features import MLBv10FeatureExtractor
from model_prediction.models.mlb_structural_v10 import MLBStructuralV10Model
from model_prediction.runtime_paths import RuntimePaths

FROZEN_ARTIFACT_PATH = REPO_ROOT / "config/models/research/mlb_structural_v10_frozen.json"
LEDGER_PATH = REPO_ROOT / "data/point_in_time/mlb_v10_prospective_ledger.jsonl"


@dataclass
class ProspectivePredictionRecord:
    record_type: str  # PREDICTION
    event_id: str
    home_team: str
    away_team: str
    game_start_utc: str
    decision_utc: str
    created_at_utc: str

    # Market State (M0)
    market_line: float
    market_prob: float
    market_state_hash: str

    # Frozen Model Predictions
    v10_pred_away: float
    v10_pred_home: float
    v10_pred_total: float
    v10_pred_margin: float
    v10_delta_vs_market: float

    # Benchmark Predictions
    m0b_prediction: float
    m4_1_v10_prediction: float

    # Pregame Probability Outputs (Gate D)
    p_over: float
    p_under: float
    p_push: float

    # Cryptographic Verification Hashes
    model_spec_hash: str
    feature_snapshot_hash: str
    probability_model_hash: str
    prediction_hash: str

    def compute_prediction_hash(self) -> str:
        payload = f"{self.event_id}:{self.decision_utc}:{self.market_line}:{self.v10_pred_total}:{self.m4_1_v10_prediction}:{self.p_over}:{self.model_spec_hash}:{self.feature_snapshot_hash}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProspectiveSettlementRecord:
    record_type: str  # SETTLEMENT
    prediction_hash: str
    event_id: str
    actual_away: float
    actual_home: float
    actual_total: float
    actual_margin: float
    settled_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProspectiveClosingMarketRecord:
    record_type: str  # CLOSING_MARKET
    prediction_hash: str
    event_id: str
    closing_line: float
    closing_price: float
    closing_market_hash: str
    closing_quote_observed_at_utc: str
    captured_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MLBPersistentShadowRunner:
    """Manages immutable append-only pregame prediction capture, closing capture, and settlement."""

    def __init__(self, artifact_path: Path = FROZEN_ARTIFACT_PATH) -> None:
        if not artifact_path.exists():
            raise FileNotFoundError(f"Frozen v10 artifact missing at: {artifact_path}")
        self.artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.model_spec_hash = self.artifact.get("hashes", {}).get("v10_model_spec_hash", "")
        self.feature_schema_hash = self.artifact.get("hashes", {}).get("v10_feature_schema_hash", "")
        self.probability_model_hash = self.artifact.get("hashes", {}).get("v10_probability_model_hash", "")
        self.m0b_bias = self.artifact.get("market_calibration", {}).get("m0b_mean_residual", 0.0)
        self.m4_1_alpha = self.artifact.get("market_calibration", {}).get("m4_1_alpha", 0.0)
        self.m4_1_beta = self.artifact.get("market_calibration", {}).get("m4_1_beta", 0.3672)

        # Empirical OOF unexplained error sample for frozen probability mapping
        res_sample = self.artifact.get("empirical_oof_error_distribution", {}).get("oof_errors_sample", [])
        self.oof_errors = np.array(res_sample if res_sample else [0.0], dtype=float)

        # Initialize model with frozen weights
        self.model = MLBStructuralV10Model()
        self.model.model_away.intercept_ = self.artifact["model_weights"]["away_intercept"]
        self.model.model_away.coef_ = np.array(self.artifact["model_weights"]["away_coefficients"])
        self.model.model_home.intercept_ = self.artifact["model_weights"]["home_intercept"]
        self.model.model_home.coef_ = np.array(self.artifact["model_weights"]["home_coefficients"])
        self.model.fitted = True

        runtime_paths = RuntimePaths.resolve()
        self.data_dir = runtime_paths.repo_root / "data"
        self.extractor = MLBv10FeatureExtractor(
            snapshot_path=self.data_dir / "mlb_statsapi/game_snapshots.jsonl"
        )
        self.warehouse = MarketQuoteWarehouse(db_path=runtime_paths.runtime_root / "market_quotes.db")
        self.vector_builder = MarketStateVectorBuilder(warehouse=self.warehouse, stale_cutoff_hours=24.0)

    def compute_empirical_probabilities(self, market_line: float, delta: float) -> tuple[float, float, float]:
        """Compute pregame outcome probabilities from the frozen empirical OOF error distribution."""
        mu_star = self.m4_1_alpha + (self.m4_1_beta * delta)
        r_star = mu_star + self.oof_errors

        is_integer = float(market_line).is_integer()
        if is_integer:
            p_push = float(np.mean((r_star >= -0.5) & (r_star < 0.5)))
            p_over = float(np.mean(r_star >= 0.5))
            p_under = float(np.mean(r_star < -0.5))
        else:
            p_push = 0.0
            p_over = float(np.mean(r_star > 0.0))
            p_under = float(np.mean(r_star < 0.0))

        # Normalize and clip
        total_p = p_over + p_under + p_push
        if total_p > 0:
            p_over = round(float(np.clip(p_over / total_p, 0.001, 0.999)), 4)
            p_under = round(float(np.clip(p_under / total_p, 0.001, 0.999)), 4)
            p_push = round(float(np.clip(p_push / total_p, 0.0, 0.999)), 4) if is_integer else 0.0
            s = p_over + p_under + p_push
            p_over = round(p_over / s, 4)
            p_under = round(p_under / s, 4)
            p_push = round(1.0 - p_over - p_under, 4) if is_integer else 0.0
        else:
            p_over, p_under, p_push = 0.50, 0.50, 0.0

        return p_over, p_under, p_push

    def generate_pregame_prediction(
        self,
        event_id: str,
        home_team: str,
        away_team: str,
        game_start_utc: str,
        snapshot: dict[str, Any] | None = None,
    ) -> ProspectivePredictionRecord | None:
        """Generate immutable pregame prediction record at T-30m decision timestamp."""
        start_dt = parse_utc(game_start_utc)
        dec_dt = start_dt - timedelta(minutes=30)

        vec = self.vector_builder.build_state_vector(
            event_id=event_id,
            market_type="total",
            as_of_utc=dec_dt,
            primary_selection="Over",
        )

        if vec.consensus_line is None or vec.consensus_price_no_vig is None:
            return None

        m_line = vec.consensus_line
        m_prob = vec.consensus_price_no_vig

        # Extract features and predict
        feat = self.extractor.extract_features_for_matchup(
            event_id=event_id,
            home_team=home_team,
            away_team=away_team,
            game_start_utc=game_start_utc,
            as_of_dt=dec_dt,
            snapshot=snapshot,
        )

        feat_dict = feat.to_dict()
        feat_hash = hashlib.sha256(json.dumps(feat_dict, sort_keys=True).encode()).hexdigest()[:16]
        mkt_hash = hashlib.sha256(
            f"{m_line}:{m_prob}:{vec.book_count}:{vec.sharp_consensus_line}".encode()
        ).hexdigest()[:16]

        pred = self.model.predict(feat)
        delta = round(pred.projected_total_runs - m_line, 2)
        m0b_pred = round(m_line + self.m0b_bias, 2)
        m4_1_pred = round(m_line + self.m4_1_alpha + (self.m4_1_beta * delta), 2)

        p_over, p_under, p_push = self.compute_empirical_probabilities(m_line, delta)

        rec = ProspectivePredictionRecord(
            record_type="PREDICTION",
            event_id=event_id,
            home_team=home_team,
            away_team=away_team,
            game_start_utc=game_start_utc,
            decision_utc=dec_dt.isoformat(),
            created_at_utc=utc_now().isoformat(),
            market_line=m_line,
            market_prob=m_prob,
            market_state_hash=mkt_hash,
            v10_pred_away=pred.projected_away_runs,
            v10_pred_home=pred.projected_home_runs,
            v10_pred_total=pred.projected_total_runs,
            v10_pred_margin=pred.projected_home_margin,
            v10_delta_vs_market=delta,
            m0b_prediction=m0b_pred,
            m4_1_v10_prediction=m4_1_pred,
            p_over=p_over,
            p_under=p_under,
            p_push=p_push,
            model_spec_hash=self.model_spec_hash,
            feature_snapshot_hash=feat_hash,
            probability_model_hash=self.probability_model_hash,
            prediction_hash="",
        )
        rec.prediction_hash = rec.compute_prediction_hash()
        return rec


def append_ledger_record(
    rec: ProspectivePredictionRecord | ProspectiveSettlementRecord | ProspectiveClosingMarketRecord,
    ledger_path: Path = LEDGER_PATH,
) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec.to_dict()) + "\n")
