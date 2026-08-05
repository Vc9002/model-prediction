"""Rebuild package — clean-slate data platform modules.

Part 1: Storage, metadata, identity, collectors
Part 2: Validation, models, calibration, ensemble
Part 3: Market residuals, economic evaluation, monitoring
"""

from .storage import (
    RawStore, NormalizedStore, FeatureStore, MarketStore,
    provenance_row, PROVENANCE_COLUMNS,
)
from .metadata import MetadataDB
from .identity import IdentityRegistry, CanonicalIdentity, normalize_name
from .collectors import MLBCollector, NBACollector, NFLCollector, SoccerCollector, TennisCollector, EsportsCollector
from .validation import (
    ChronologicalFold, ChronologicalEvaluator, expanding_folds, rolling_folds,
    log_loss, brier_score, ece, calibration_curve, date_cluster_bootstrap,
)
from .models import MLBTwoHeadModel, JointScoreDistribution, GamePrediction
from .calibration import (
    IdentityCalibrator, PlattCalibrator, IsotonicCalibrator,
    TemperatureScaling, fit_calibrator,
)
from .ensemble import Ensemble, equal_weight_ensemble, logistic_stacking
from .market_residual import (
    MarketResidualModel, MarketResidualFeatures,
    executable_edge, is_tradeable,
)
from .economic import (
    SizeLimits, Exposure, EconomicResult, MonitorState,
    kelly_fraction, edge_scaled_units, evaluate_portfolio, HEALTH_STATES,
)

__all__ = [
    # Storage
    "RawStore", "NormalizedStore", "FeatureStore", "MarketStore",
    "provenance_row", "PROVENANCE_COLUMNS",
    # Metadata
    "MetadataDB",
    # Identity
    "IdentityRegistry", "CanonicalIdentity", "normalize_name",
    # Collectors
    "MLBCollector", "NBACollector", "NFLCollector",
    "SoccerCollector", "TennisCollector", "EsportsCollector",
    # Validation
    "ChronologicalFold", "ChronologicalEvaluator",
    "expanding_folds", "rolling_folds",
    "log_loss", "brier_score", "ece", "calibration_curve",
    "date_cluster_bootstrap",
    # Models
    "MLBTwoHeadModel", "JointScoreDistribution", "GamePrediction",
    # Calibration
    "IdentityCalibrator", "PlattCalibrator", "IsotonicCalibrator",
    "TemperatureScaling", "fit_calibrator",
    # Ensemble
    "Ensemble", "equal_weight_ensemble", "logistic_stacking",
    # Market Residual
    "MarketResidualModel", "MarketResidualFeatures",
    "executable_edge", "is_tradeable",
    # Economic
    "SizeLimits", "Exposure", "EconomicResult", "MonitorState",
    "kelly_fraction", "edge_scaled_units", "evaluate_portfolio",
    "HEALTH_STATES",
]
