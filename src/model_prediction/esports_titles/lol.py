"""LoL per-title config.

K/platt/threshold frozen from the live v6 artifact's own fitted values
(runtime-root models/lol-tiered-elo-v6.json -- k=40.0, threshold=0.03,
platt=(0.1074, 0.783), n=12,572 matches). Engine knobs left at None
(shared defaults) until a title-specific grid says otherwise.
"""

from __future__ import annotations

from .title_config import TitleConfig

LOL_CONFIG = TitleConfig(
    title="lol",
    model_id="lol-series-v7-lr",
    k=40.0,
    confidence_threshold=0.03,
    platt_intercept=0.1074464956457067,
    platt_slope=0.7830444177583121,
    feature_plan=(
        "region",  # data-gated
        "patch",  # data-gated
        "blue_red_side",  # data-gated: side results
        "pre_vs_post_draft",  # data-gated: draft timelines
    ),
)
