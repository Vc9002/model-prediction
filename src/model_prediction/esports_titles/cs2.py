"""CS2 per-title config.

K/platt/threshold frozen from the live v6 artifact's own fitted values
(runtime-root models/cs2-tiered-elo-v6.json, fitted by
validate_esports_baseline's walk-forward grid -- k=40.0, threshold=0.03,
platt=(0.108, 0.8807), n=38,971 matches). Engine knobs left at None
(shared defaults) until a title-specific grid says otherwise.
"""

from __future__ import annotations

from .title_config import TitleConfig

CS2_CONFIG = TitleConfig(
    title="cs2",
    model_id="cs2-series-v7-lr",
    k=40.0,
    confidence_threshold=0.03,
    platt_intercept=0.10800469637672444,
    platt_slope=0.8806606861918261,
    feature_plan=(
        "map_elo",  # data-gated: per-map results, not in local cache yet
        "map_pool",  # data-gated
        "roster_stability",  # data-gated
        "lan_vs_online",  # data-gated
        "tier",  # already available in matches.jsonl
        "bo_format",  # already available
    ),
)
