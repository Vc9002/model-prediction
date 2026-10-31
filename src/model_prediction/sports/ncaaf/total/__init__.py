"""NCAAF Totals Models."""

from model_prediction.models.cfb_distribution import (
    CFBDistributionType,
    CFBJointDistributionEngine,
    CFBJointMarketProbabilities,
)
from model_prediction.models.cfb_structural_v3 import (
    CFB_V3_MODEL_VERSION,
    CFBStructuralV3Model,
    cfb_structural_v3_model,
)
from model_prediction.models.cfb_structural_v4 import (
    CFB_V4_MODEL_VERSION,
    CFBStructuralV4Model,
    cfb_structural_v4_model,
)

__all__ = [
    "CFB_V3_MODEL_VERSION",
    "CFB_V4_MODEL_VERSION",
    "CFBDistributionType",
    "CFBJointDistributionEngine",
    "CFBJointMarketProbabilities",
    "CFBStructuralV3Model",
    "CFBStructuralV4Model",
    "cfb_structural_v3_model",
    "cfb_structural_v4_model",
]
