"""Valorant per-title config.

K/platt/threshold frozen from the live v6 artifact's own fitted values
(runtime-root models/valorant-tiered-elo-v6.json -- k=48.0, threshold=0.03,
platt=(0.1236, 0.8574), n=14,767 matches). Engine knobs left at None
(shared defaults) until a title-specific grid says otherwise.
"""

from __future__ import annotations

from .title_config import TitleConfig

VALORANT_CONFIG = TitleConfig(
    title="valorant",
    model_id="valorant-series-v7-lr",
    k=48.0,
    confidence_threshold=0.03,
    platt_intercept=0.12364433169516242,
    platt_slope=0.857443785955419,
    feature_plan=(
        "patch_context",  # data-gated: patch version per match
        "agent_meta_stability",  # data-gated
        "attack_defense_side",  # data-gated: side results per map
    ),
)
