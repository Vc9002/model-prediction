"""Rebuild package — clean-slate data platform modules.

Part 1: Storage, metadata, identity, collectors
Part 2: Validation, models, calibration, ensemble
Part 3: Market residuals, economic evaluation, monitoring
"""

from .calibration import (
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    TemperatureScaling,
    fit_calibrator,
)
from .collectors import (
    EsportsCollector,
    MLBCollector,
    NBACollector,
    NFLCollector,
    SoccerCollector,
    TennisCollector,
)
from .economic import (
    HEALTH_STATES,
    EconomicResult,
    Exposure,
    MonitorState,
    SizeLimits,
    edge_scaled_units,
    evaluate_portfolio,
    kelly_fraction,
)
from .ensemble import Ensemble, equal_weight_ensemble, logistic_stacking
from .identity import CanonicalIdentity, IdentityRegistry, normalize_name
from .market_residual import (
    MarketResidualFeatures,
    MarketResidualModel,
    executable_edge,
    is_tradeable,
)
from .metadata import MetadataDB
from .models import GamePrediction, JointScoreDistribution, MLBTwoHeadModel
from .schemas import (
    MARKET_SNAPSHOT_CONTRACT,
    SCOREBOARD_CONTRACT,
    ColumnSpec,
    TableContract,
    validate_against_contract,
    validate_or_raise,
)
from .storage import (
    PROVENANCE_COLUMNS,
    FeatureStore,
    MarketStore,
    NormalizedStore,
    RawStore,
    provenance_row,
)
from .validation import (
    ChronologicalEvaluator,
    ChronologicalFold,
    brier_score,
    calibration_curve,
    date_cluster_bootstrap,
    ece,
    expanding_folds,
    log_loss,
    rolling_folds,
)

__all__ = [  # noqa: RUF022 -- grouped by subsystem with comments, not alphabetized, intentionally
    # Storage
    "RawStore", "NormalizedStore", "FeatureStore", "MarketStore",
    "provenance_row", "PROVENANCE_COLUMNS",
    # Metadata
    "MetadataDB",
    # Identity
    "IdentityRegistry", "CanonicalIdentity", "normalize_name",
    # Schemas
    "TableContract", "ColumnSpec", "validate_against_contract", "validate_or_raise",
    "SCOREBOARD_CONTRACT", "MARKET_SNAPSHOT_CONTRACT",
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
