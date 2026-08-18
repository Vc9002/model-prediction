"""Dota2 per-title config.

K/platt/threshold frozen from the live v6 artifact's own fitted values
(runtime-root models/dota2-tiered-elo-v6.json -- k=40.0, threshold=0.0,
platt=(0.0942, 0.8775), n=11,002 matches). Engine knobs left at None
(shared defaults) until a title-specific grid says otherwise.
"""

from __future__ import annotations

from .title_config import TitleConfig

DOTA2_CONFIG = TitleConfig(
    title="dota2",
    model_id="dota2-series-v7-lr",
    k=40.0,
    confidence_threshold=0.0,
    platt_intercept=0.09415415776238314,
    platt_slope=0.8774558221462619,
    feature_plan=(
        "radiant_dire_side",  # data-gated: side results
        "draft_hero_pool",  # data-gated
        "patch",  # data-gated
    ),
)
